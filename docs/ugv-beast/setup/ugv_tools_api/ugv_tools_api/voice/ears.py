"""Continuous microphone listen loop on the Jetson (v0.6.0 — mic handoff).

Flow:
    PyAudio capture (USB mic, native rate)
    -> openWakeWord scorer (black_box_flight_recorder.onnx)
    -> on trigger, POST /open_session to the local supervisor service
    -> supervisor sets MUTE_FLAG
    -> ears closes PyAudio stream (releases USB mic to arecord)
    -> ears polls flag; when cleared, reopens stream and resumes

Architectural rule (v0.6.0): only one process ever holds the USB mic.
Ears owns it in idle; supervisor owns it during a Gemini Live session.
The MUTE_FLAG file (/tmp/ugv_ears_muted) is the atomic handoff primitive.

Ears is a dumb wake-word poller. It does NOT:
  - transcribe with Whisper (no STT)
  - dispatch missions to ER
  - speak via ugv-voice (/speak)
  - talk to BlackBox
  - hold the mic during a Live session (that's the whole change in 0.6.0)

On wake, ears plays a short local chime (latency-sensitive UX cue) and
POSTs to SUPERVISOR_URL/open_session. Anything the supervisor returns is
logged and ignored. Any failure is logged at WARNING and we keep listening.

Mic handoff (0.6.0 change): while MUTE_FLAG exists, ears CLOSES its
PyAudio stream rather than just skipping reads. Before 0.6.0, both
processes held ALSA open on the same plughw: device, causing capture
contention and dropouts. Now only the flag-holder reads the device.
"""

from __future__ import annotations

import collections
import logging
import math
import os
import struct
import subprocess
import threading
import time
import wave

import httpx
import numpy as np
import pyaudio
from openwakeword.model import Model
from scipy.signal import resample_poly

logger = logging.getLogger(__name__)

__version__ = "0.6.0"

# --- Audio / wake-word frame geometry ---
SAMPLE_RATE = 16000
FRAME_MS = 30                                               # capture granularity
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)          # 480
FRAME_BYTES = FRAME_SAMPLES * 2                             # 16-bit mono
WW_FRAME_MS = 80                                            # openWakeWord native chunk size
WW_FRAME_SAMPLES = int(SAMPLE_RATE * WW_FRAME_MS / 1000)    # 1280

# --- Integration config (all env-overridable) ---
SUPERVISOR_URL = os.environ.get(
    "SUPERVISOR_URL", "http://localhost:8083"
).rstrip("/")
OPEN_SESSION_PATH = os.environ.get("OPEN_SESSION_PATH", "/open_session")
OPEN_SESSION_TIMEOUT_SEC = float(os.environ.get("OPEN_SESSION_TIMEOUT_SEC", "5.0"))

WAKEWORD_MODEL = os.environ.get(
    "WAKEWORD_MODEL",
    "/home/ws/ugv_ws/ugv_tools_api/voice_models/black_box_flight_recorder.onnx",
)
WAKEWORD_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.5"))

# Substring match for PyAudio input device name (e.g. "USB Camera").
# If unset, falls back to the default input device.
MIC_DEVICE_HINT = os.environ.get("MIC_DEVICE_HINT", "USB Camera")

# USB mics often only speak their hardware-native rate (44100/48000).
# We open at that rate and resample to 16 kHz mono in numpy.
# Override with MIC_CAPTURE_RATE if autodetection misbehaves.
_MIC_RATE_OVERRIDE = os.environ.get("MIC_CAPTURE_RATE")
MIC_CAPTURE_RATE = int(_MIC_RATE_OVERRIDE) if _MIC_RATE_OVERRIDE else 0  # 0 = auto

# Coordinates with the supervisor: while the flag file exists we pause
# capture to avoid the robot re-triggering on its own voice. Same
# convention ugv-voice used; the supervisor now owns writing this flag.
MUTE_FLAG = os.environ.get("MUTE_FLAG_PATH", "/tmp/ugv_ears_muted")

# --- Wake chime (user-visible cue) ---
# Ascending pair on wake ("I heard you, supervisor session opening").
# No "done" chime — the supervisor owns the conversation from here.
CHIME_DEVICE = os.environ.get("UGV_ALSA_DEVICE", "plughw:CARD=Device")
WAKE_CHIME_PATH = os.environ.get("WAKE_CHIME_PATH", "/tmp/ugv_wake_chime.wav")
CHIME_ENABLED = os.environ.get("EARS_CHIME", "1") not in ("0", "false", "no", "")
CHIME_VOLUME = float(os.environ.get("EARS_CHIME_VOLUME", "0.22"))

# Cooldown after a wake event. Prevents back-to-back wake events from
# spamming /open_session while the supervisor is still negotiating its
# own session. The supervisor also owns MUTE_FLAG for longer guards.
WAKE_COOLDOWN_SEC = float(os.environ.get("WAKE_COOLDOWN_SEC", "2.0"))


def _gen_chime_wav(path: str, freqs_hz: list[int], dur_per_tone_ms: int = 80,
                   sample_rate: int = 48000, volume: float = CHIME_VOLUME) -> None:
    samples_per_tone = int(sample_rate * dur_per_tone_ms / 1000)
    fade_n = max(1, int(samples_per_tone * 0.15))
    out: list[int] = []
    for f in freqs_hz:
        for i in range(samples_per_tone):
            fade = min(1.0, i / fade_n, (samples_per_tone - i) / fade_n)
            s = volume * fade * math.sin(2.0 * math.pi * f * i / sample_rate)
            out.append(max(-32767, min(32767, int(s * 32767))))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{len(out)}h", *out))


def _ensure_chimes() -> None:
    try:
        _gen_chime_wav(WAKE_CHIME_PATH, [660, 990])
    except Exception as e:  # noqa: BLE001 -- advisory, keep ears alive
        logger.warning("ears: chime generation failed (%s); disabling chimes", e)
        globals()["CHIME_ENABLED"] = False


def _play_chime_async(path: str) -> None:
    if not CHIME_ENABLED:
        return

    def _run() -> None:
        try:
            subprocess.run(
                ["aplay", "-q", "-D", CHIME_DEVICE, path],
                timeout=2, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def pick_input_device(pa: pyaudio.PyAudio, hint: str = "") -> tuple[int, dict]:
    """Find the mic. If MIC_DEVICE_HINT matches a device, prefer it; else default."""
    if hint:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0 and hint.lower() in info.get("name", "").lower():
                logger.info("mic: device %d (%s) matched hint '%s'", i, info["name"], hint)
                return i, info
        logger.warning("mic: no device matched hint '%s'; falling back to default", hint)
    default = pa.get_default_input_device_info()
    logger.info("mic: default device %d (%s)", default["index"], default["name"])
    return int(default["index"]), default


def _hint_matches_any_device(pa: pyaudio.PyAudio, hint: str) -> bool:
    """Cheap probe: does any input device name contain the hint?"""
    if not hint:
        return True
    for i in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(i)
        except Exception:  # noqa: BLE001
            continue
        if info.get("maxInputChannels", 0) > 0 and hint.lower() in info.get("name", "").lower():
            return True
    return False


def find_mic_with_retry(
    pa: pyaudio.PyAudio,
    hint: str,
    total_timeout_s: float = 30.0,
    poll_interval_s: float = 2.0,
) -> tuple[pyaudio.PyAudio, int, dict]:
    """Wait up to `total_timeout_s` for a device matching `hint` to appear.

    USB enumeration can lag ~20s after boot. If we pick the device before the
    USB mic shows up, we end up on the Jetson APE onboard mic (hw:3,1) and the
    wake word never triggers. This helper polls every `poll_interval_s` and
    re-inits PyAudio between polls so PortAudio re-scans /proc/asound.

    Returns a (pa, dev_idx, dev_info) tuple. If the hint never matches, falls
    back to the default input device with a warning.

    NOTE: Returns a (possibly-new) PyAudio instance — caller must use the
    returned one and not the one passed in (PyAudio caches the device list at
    init time, so we must re-instantiate it to pick up late-enumerated USB
    devices).
    """
    if not hint:
        default = pa.get_default_input_device_info()
        logger.info("mic: no hint set; using default device %d (%s)", default["index"], default["name"])
        return pa, int(default["index"]), default

    deadline = time.monotonic() + total_timeout_s
    attempt = 0
    while True:
        attempt += 1
        if _hint_matches_any_device(pa, hint):
            dev_idx, dev_info = pick_input_device(pa, hint)
            if hint.lower() in str(dev_info.get("name", "")).lower():
                logger.info(
                    "mic: hint '%s' matched on attempt %d (device %d: %s)",
                    hint, attempt, dev_idx, dev_info.get("name"),
                )
                return pa, dev_idx, dev_info

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "mic: hint '%s' never matched after %.1fs (%d attempts); "
                "falling back to default device",
                hint, total_timeout_s, attempt,
            )
            dev_idx, dev_info = pick_input_device(pa, "")  # empty hint → default
            return pa, dev_idx, dev_info

        logger.info(
            "mic: hint '%s' not yet visible (attempt %d, %.0fs remaining); "
            "re-scanning in %.1fs...",
            hint, attempt, remaining, poll_interval_s,
        )
        try:
            pa.terminate()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(poll_interval_s)
        pa = pyaudio.PyAudio()


def open_input_stream(
    pa: pyaudio.PyAudio, dev_idx: int, dev_info: dict
) -> tuple[pyaudio.Stream, int, int]:
    """Open input at the device's native rate + channel count.

    USB mics typically only accept their hardware-native sample rate; trying
    to open at 16 kHz raises 'Invalid sample rate'. We capture at the native
    rate and resample to 16 kHz mono in the caller.
    """
    if MIC_CAPTURE_RATE:
        capture_rate = MIC_CAPTURE_RATE
    else:
        capture_rate = SAMPLE_RATE
    native_channels = int(dev_info.get("maxInputChannels", 1)) or 1
    channels = min(native_channels, 2)

    def _frames_per_buffer(rate: int) -> int:
        return int(rate * FRAME_MS / 1000)

    preferred_rates = [SAMPLE_RATE, 48000, 32000, 22050, 16000, 44100]
    if MIC_CAPTURE_RATE:
        preferred_rates = [MIC_CAPTURE_RATE] + [r for r in preferred_rates if r != MIC_CAPTURE_RATE]
    default_rate = int(dev_info.get("defaultSampleRate") or 44100)
    if default_rate not in preferred_rates:
        preferred_rates.append(default_rate)

    supported: list[tuple[int, int]] = []
    for rate in preferred_rates:
        for ch in (channels, 1, 2):
            try:
                if pa.is_format_supported(
                    rate,
                    input_device=dev_idx,
                    input_channels=ch,
                    input_format=pyaudio.paInt16,
                ):
                    supported.append((rate, ch))
                    break
            except Exception:  # noqa: BLE001
                pass
    if not supported:
        raise RuntimeError(f"mic: no supported rate/channel combo on dev {dev_idx}")

    last_err: Exception | None = None
    for rate, ch in supported:
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=ch,
                rate=rate,
                input=True,
                input_device_index=dev_idx,
                frames_per_buffer=_frames_per_buffer(rate),
            )
            logger.info(
                "mic: opened dev=%d rate=%d channels=%d (target 16kHz mono)",
                dev_idx,
                rate,
                ch,
            )
            return stream, rate, ch
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.info("mic: open rate=%d ch=%d failed (%s); trying next", rate, ch, e)
    raise RuntimeError(f"mic: could not open any rate on dev {dev_idx}: {last_err}")


def read_frame(
    stream: pyaudio.Stream, capture_rate: int, channels: int
) -> bytes:
    """Read one FRAME_MS-sized chunk and return 16kHz mono int16 bytes."""
    frames_in = int(capture_rate * FRAME_MS / 1000)
    raw = stream.read(frames_in, exception_on_overflow=False)
    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if capture_rate != SAMPLE_RATE:
        from math import gcd

        g = gcd(SAMPLE_RATE, capture_rate)
        up = SAMPLE_RATE // g
        down = capture_rate // g
        resampled = resample_poly(samples.astype(np.float32), up, down)
        resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
        samples = resampled
    # Pad/trim to FRAME_SAMPLES (exactly 480 samples / 30ms @ 16kHz)
    if len(samples) < FRAME_SAMPLES:
        samples = np.concatenate([samples, np.zeros(FRAME_SAMPLES - len(samples), dtype=np.int16)])
    elif len(samples) > FRAME_SAMPLES:
        samples = samples[:FRAME_SAMPLES]
    return samples.tobytes()


def notify_supervisor() -> None:
    """POST SUPERVISOR_URL/open_session. Log + swallow all errors.

    Fire-and-forget: the supervisor's lifespan might be mid-shutdown (503)
    or docker net might burp — either way we keep listening. The supervisor
    owns the mic; we do nothing else on wake.
    """
    url = f"{SUPERVISOR_URL}{OPEN_SESSION_PATH}"
    try:
        with httpx.Client(timeout=OPEN_SESSION_TIMEOUT_SEC) as c:
            r = c.post(url, json={})
        if r.status_code >= 400:
            logger.warning(
                "ears: supervisor %s returned %s: %s",
                url, r.status_code, r.text[:200],
            )
        else:
            logger.info("ears: supervisor %s ok (HTTP %s)", url, r.status_code)
    except httpx.HTTPError as e:
        logger.warning("ears: supervisor %s unreachable: %s", url, e)
    except Exception as e:  # noqa: BLE001 -- keep ears alive no matter what
        logger.warning("ears: unexpected error posting to %s: %s", url, e)


def _notify_supervisor_async() -> None:
    """Run notify_supervisor() on a daemon thread so capture doesn't block."""
    threading.Thread(target=notify_supervisor, daemon=True).start()


def capture_loop() -> None:
    pa = pyaudio.PyAudio()
    # Wait up to 30s for the USB mic (MIC_DEVICE_HINT) to enumerate. On a
    # cold boot the USB subsystem can lag ~20s behind systemd service start.
    pa, dev_idx, dev_info = find_mic_with_retry(
        pa, MIC_DEVICE_HINT, total_timeout_s=30.0, poll_interval_s=2.0
    )

    def _open_stream():
        return open_input_stream(pa, dev_idx, dev_info)

    def _close_stream(s):
        """Best-effort stop+close of a PyAudio stream. Logs but never raises,
        because we MUST release the ALSA device before the supervisor's
        arecord tries to open it."""
        if s is None:
            return
        try:
            s.stop_stream()
        except Exception as e:  # noqa: BLE001 — stop may already be stopped
            logger.debug("ears: stop_stream: %s", e)
        try:
            s.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("ears: close stream: %s", e)

    stream, capture_rate, channels = _open_stream()

    # inference_framework='onnx' -- our trained model is .onnx (not tflite).
    ww = Model(wakeword_models=[WAKEWORD_MODEL], inference_framework="onnx")
    ww_buffer = b""

    logger.info(
        "ears v%s: listening for wake word "
        "(model=%s threshold=%.2f capture=%dHz ch=%d supervisor=%s)",
        __version__,
        os.path.basename(WAKEWORD_MODEL),
        WAKEWORD_THRESHOLD,
        capture_rate,
        channels,
        SUPERVISOR_URL,
    )

    cooldown_until = 0.0
    # muted=True means we have RELEASED the PyAudio stream so the supervisor
    # can open arecord on the same USB mic. Only one process owns the ALSA
    # hardware at a time — that's the whole point of this handoff.
    muted = False

    while True:
        flag_present = os.path.exists(MUTE_FLAG)

        # Transition: flag appeared → release the mic to supervisor.
        if flag_present and not muted:
            _close_stream(stream)
            stream = None
            ww_buffer = b""
            muted = True
            logger.info("ears: MUTE_FLAG set — released mic to supervisor")
            continue

        # Transition: flag cleared → reopen and resume wake-word listening.
        if not flag_present and muted:
            try:
                stream, capture_rate, channels = _open_stream()
                ww_buffer = b""
                try:
                    ww.reset()
                except Exception:  # pragma: no cover — older openwakeword APIs
                    pass
                muted = False
                logger.info(
                    "ears: MUTE_FLAG cleared — reacquired mic at %dHz ch=%d, listening",
                    capture_rate, channels,
                )
            except Exception as e:  # noqa: BLE001 — retry on busy/transient errors
                # Arecord may still hold the device for a few ms during the
                # supervisor's teardown; back off and retry.
                logger.warning("ears: could not reopen mic after mute: %s; retrying", e)
                time.sleep(1.0)
                continue

        if muted:
            # Mic belongs to supervisor. Idle poll the flag.
            time.sleep(0.1)
            continue

        try:
            frame = read_frame(stream, capture_rate, channels)
        except Exception as e:  # noqa: BLE001 -- PyAudio can raise many C errors
            logger.warning("mic read error: %s", e)
            time.sleep(0.2)
            continue

        ww_buffer += frame

        # openWakeWord wants 80ms chunks of int16 samples
        while len(ww_buffer) >= WW_FRAME_SAMPLES * 2:
            chunk = ww_buffer[: WW_FRAME_SAMPLES * 2]
            ww_buffer = ww_buffer[WW_FRAME_SAMPLES * 2 :]
            samples = np.frombuffer(chunk, dtype=np.int16)
            scores = ww.predict(samples)
            top = max(scores.values()) if scores else 0.0

            if top > WAKEWORD_THRESHOLD:
                now = time.monotonic()
                if now < cooldown_until:
                    # Still inside cooldown window — drop this trigger.
                    continue
                cooldown_until = now + WAKE_COOLDOWN_SEC
                logger.info("ears: WAKE (score=%.2f) -> notifying supervisor", top)
                _play_chime_async(WAKE_CHIME_PATH)
                _notify_supervisor_async()
                # Reset wake-word state so the trailing echo of the phrase
                # doesn't retrigger the next predict() call.
                try:
                    ww.reset()
                except Exception:  # pragma: no cover -- older openwakeword APIs
                    pass
                ww_buffer = b""
                break


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info(
        "ears v%s: supervisor=%s ww=%s threshold=%.2f chime=%s",
        __version__,
        SUPERVISOR_URL,
        WAKEWORD_MODEL,
        WAKEWORD_THRESHOLD,
        "on" if CHIME_ENABLED else "off",
    )
    if CHIME_ENABLED:
        _ensure_chimes()
    capture_loop()


if __name__ == "__main__":
    main()
