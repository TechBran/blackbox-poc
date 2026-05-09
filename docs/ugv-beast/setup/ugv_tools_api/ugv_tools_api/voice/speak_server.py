"""FastAPI service on the Jetson host: receives text, plays it on the JBL speaker.

Architecture (v0.4.0, post-BlackBox-decoupling):
    caller → /speak → THIS service → OpenAI /v1/audio/speech (onyx HD)
    → MP3 bytes → mpg123 → ALSA hw → JBL

Jetson now holds its own OpenAI key (deploy/er.env). No BlackBox TTS hop.

Queue model (preserved from v0.3.0):
    /speak ENQUEUES the request and returns immediately (in ms). A single
    background worker task drains the queue serially, so multiple rapid POSTs
    during streaming (sentence-level TTS) do NOT race on the ALSA device.

Runs on port 8081 (container — host network mode).
Endpoint: POST /speak {text, voice?='onyx', rate?=1.0}
Required env: OPENAI_API_KEY
              OPENAI_TTS_MODEL (default tts-1-hd)
              OPENAI_TTS_VOICE (default onyx)
              UGV_ALSA_DEVICE (e.g. plughw:CARD=Device)
              UGV_VOICE_PORT (default 8081)
"""
import asyncio
import logging
import os
import pathlib
import subprocess
import tempfile

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

logger = logging.getLogger("speak_server")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "tts-1-hd")
OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "onyx")
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"

DEFAULT_VOICE = os.environ.get("UGV_VOICE", OPENAI_TTS_VOICE)
ALSA_DEVICE = os.environ.get("UGV_ALSA_DEVICE", "plughw:CARD=Device")
PLAY_TIMEOUT_S = float(os.environ.get("UGV_PLAY_TIMEOUT_S", "120"))
SPEAK_QUEUE_MAX = int(os.environ.get("UGV_SPEAK_QUEUE_MAX", "20"))

# Filesystem lock coordinated with ugv-ears (see ears.py). While this flag
# exists, the ears loop skips mic capture -- prevents the robot from hearing
# (and re-triggering on) its own voice.
MUTE_FLAG = pathlib.Path(os.environ.get("MUTE_FLAG_PATH", "/tmp/ugv_ears_muted"))


class SpeakRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    rate: float = 1.0


app = FastAPI(title="UGV Voice", version="0.4.0")

# Single-worker queue — serializes ALSA access
_speak_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None

# Startup TTS probe result — decides /health status code
_startup_tts_ok: bool = False
_startup_tts_error: str | None = None


async def _synth_openai(text: str, voice: str) -> bytes:
    """POST OpenAI /v1/audio/speech and return MP3 bytes. Raises RuntimeError on failure."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_TTS_MODEL,
        "voice": voice or OPENAI_TTS_VOICE,
        "input": text,
        "response_format": "mp3",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(OPENAI_TTS_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise RuntimeError(f"OpenAI TTS request failed: {e}")
    if r.status_code != 200:
        raise RuntimeError(
            f"OpenAI TTS HTTP {r.status_code}: {r.text[:200]}"
        )
    mp3 = r.content
    if not mp3:
        raise RuntimeError("OpenAI TTS returned empty body")
    return mp3


@app.get("/health")
def health():
    """Health reflects the cached startup synth probe.

    Probing on every call against a paid API is wasteful and slow; the boot-time
    probe proves the key works and the wire is reachable. If OpenAI goes down
    mid-run, individual /speak calls will surface the error in logs.
    """
    queue_depth = _speak_queue.qsize() if _speak_queue is not None else None
    body = {
        "ok": _startup_tts_ok,
        "tts_reachable": _startup_tts_ok,
        "tts_error": _startup_tts_error,
        "alsa": ALSA_DEVICE,
        "voice": DEFAULT_VOICE,
        "tts_model": OPENAI_TTS_MODEL,
        "queue_depth": queue_depth,
        "queue_max": SPEAK_QUEUE_MAX,
    }
    if not _startup_tts_ok:
        raise HTTPException(status_code=503, detail=body)
    return body


async def _play_one(text: str, voice: str) -> None:
    """Synthesize via OpenAI TTS, play the MP3 through ALSA.

    Raises on failure (caught by worker). Mute flag is touched around the
    mpg123 invocation so ugv-ears skips capture while we play.
    """
    mp3 = await _synth_openai(text, voice)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3)
        path = f.name

    # Raise mute flag so ugv-ears stops listening during playback.
    try:
        MUTE_FLAG.touch()
    except OSError:
        pass

    try:
        # Run mpg123 in a thread so the event loop stays responsive (other
        # queue items can be accepted while audio is playing; they'll be
        # processed in order by this same worker once mpg123 exits).
        def _run_mpg123() -> subprocess.CompletedProcess:
            return subprocess.run(
                ["mpg123", "-q", "-o", "alsa", "-a", ALSA_DEVICE, path],
                check=False,
                timeout=PLAY_TIMEOUT_S,
                capture_output=True,
                text=True,
            )

        completed = await asyncio.to_thread(_run_mpg123)
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()[:300]
            raise RuntimeError(
                f"mpg123 exit {completed.returncode} on device {ALSA_DEVICE}: {err}"
            )
    except FileNotFoundError:
        raise RuntimeError("mpg123 not installed in container")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"mpg123 timed out after {PLAY_TIMEOUT_S}s")
    finally:
        try:
            MUTE_FLAG.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass


async def _speak_worker() -> None:
    """Single background worker — drains the queue serially.

    Serializing here (not at the HTTP layer) means /speak can return in
    milliseconds while the robot audibly plays requests in order. This
    prevents the 'ALSA device busy → HTTP 500' cascade seen during
    sentence-level streaming TTS.
    """
    assert _speak_queue is not None
    while True:
        item = await _speak_queue.get()
        text = item["text"]
        voice = item["voice"]
        try:
            logger.info("speak worker: playing %d chars (voice=%s)", len(text), voice)
            await _play_one(text, voice)
            logger.info("speak worker: done (%d chars)", len(text))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never let a single failure kill the worker.
            logger.warning("speak worker error (%d chars): %s", len(text), e)
        finally:
            _speak_queue.task_done()


@app.on_event("startup")
async def _startup() -> None:
    global _speak_queue, _worker_task, _startup_tts_ok, _startup_tts_error
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "speak_server v0.4.0: model=%s voice=%s alsa=%s (direct OpenAI TTS)",
        OPENAI_TTS_MODEL,
        DEFAULT_VOICE,
        ALSA_DEVICE,
    )
    _speak_queue = asyncio.Queue(maxsize=SPEAK_QUEUE_MAX)
    _worker_task = asyncio.create_task(_speak_worker())
    logger.info("speak queue initialized (max=%d), worker started", SPEAK_QUEUE_MAX)

    # Boot-time probe: single-char synth proves key+network are sane.
    try:
        await _synth_openai(".", DEFAULT_VOICE)
        _startup_tts_ok = True
        _startup_tts_error = None
        logger.info("speak_server: OpenAI TTS probe OK")
    except Exception as e:
        _startup_tts_ok = False
        _startup_tts_error = f"{type(e).__name__}: {str(e)[:200]}"
        logger.error("speak_server: OpenAI TTS probe FAILED: %s", _startup_tts_error)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


@app.post("/speak")
async def speak(req: SpeakRequest):
    """Enqueue a speak request. Returns immediately with queue depth.

    The background worker handles TTS synthesis + ALSA playback serially.
    Multiple rapid POSTs (e.g. from sentence-level streaming) queue up and
    play in order without racing on the sound device.
    """
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")
    if _speak_queue is None:
        raise HTTPException(status_code=503, detail="speak queue not initialized")

    try:
        _speak_queue.put_nowait({"text": text, "voice": req.voice or DEFAULT_VOICE})
    except asyncio.QueueFull:
        raise HTTPException(status_code=503, detail="speak queue full")

    return {
        "queued": True,
        "chars": len(text),
        "voice": req.voice or DEFAULT_VOICE,
        "queue_depth": _speak_queue.qsize(),
    }


def main():
    import uvicorn
    port = int(os.environ.get("UGV_VOICE_PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
