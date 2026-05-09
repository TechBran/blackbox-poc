"""Raw-WebSocket adapter for Gemini Live API.

Replaces the buggy `google.genai` SDK `client.aio.live.connect` Live-session
wrapper (python-genai issue #1224) with hand-crafted JSON over `websockets`,
mirroring the proven pattern in `Orchestrator/routes/gemini_live_routes.py`.

The class exposes a SDK-compatible surface so `session.py` only needs minor
edits (no shape change) at call sites:

    sess.send_realtime_input(audio=bytes_pcm16_16k, mime_type="audio/pcm;rate=16000")
    sess.send_realtime_input(video=jpeg_bytes,      mime_type="image/jpeg")
    sess.send_tool_response(function_responses=[
        {"id": "...", "name": "...", "response": {...},
         "will_continue": True, "scheduling": "SILENT"},  # snake_case -> camelCase here
    ])
    async for event in sess.receive():
        ...

Lifecycle: open via async context manager; receive iterator yields raw event
dicts (parsed JSON) until the WS closes. Caller distinguishes goAway-driven
close (in event["goAway"]) from natural close.
"""
from __future__ import annotations
import asyncio
import base64
import json
from typing import AsyncIterator, Optional

import websockets


def build_setup_payload(
    *, model: str, voice: str, system_instruction: str,
    tools: list[dict], resume_handle: Optional[str],
) -> dict:
    """Return the JSON dict to send as the first WS message after connect.

    Matches Orchestrator/routes/gemini_live_routes.py:configure_gemini_session
    with two deliberate departures already encoded as defaults:
      * NO `inputAudioTranscription` / `outputAudioTranscription`
        (suspected cycling-bug trigger)
      * NO `realtimeInputConfig` (default VAD works fine in the
        Orchestrator's non-phone path)
    sessionResumption is only sent when resume_handle is non-empty,
    matching the Orchestrator's conditional pattern.
    """
    setup: dict = {
        "model": f"models/{model}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
            },
        },
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "tools": [{"functionDeclarations": tools}] if tools else [],
        "contextWindowCompression": {"slidingWindow": {}},
    }
    if resume_handle:
        setup["sessionResumption"] = {"handle": resume_handle}
    return {"setup": setup}


class RawLiveSession:
    """SDK-compatible session over a raw `websockets` WS.

    Designed for direct substitution at SDK call sites. The constructor
    is internal — use `connect_raw_live(...)` async context manager below
    for production code paths.
    """
    def __init__(self, *, _ws):
        self._ws = _ws

    async def send_realtime_input(
        self, *, audio: Optional[bytes] = None, video: Optional[bytes] = None,
        mime_type: str,
    ) -> None:
        # Symmetric exactly-one-of guard. Without this, passing both audio=
        # and video= silently drops video; passing neither was previously
        # raised but with a different message. One ValueError covers both.
        if (audio is None) == (video is None):
            raise ValueError("send_realtime_input requires exactly one of audio= or video=")
        payload = audio if audio is not None else video
        msg = {"realtimeInput": {"mediaChunks": [{
            "mimeType": mime_type,
            "data": base64.b64encode(payload).decode("ascii"),
        }]}}
        await self._ws.send(json.dumps(msg))

    async def send_tool_response(self, *, function_responses: list[dict]) -> None:
        """function_responses: each dict has keys id, name, response[, will_continue, scheduling]."""
        out = []
        for fr in function_responses:
            entry = {
                "id": fr["id"],
                "name": fr["name"],
                "response": fr["response"],
            }
            if "will_continue" in fr:
                entry["willContinue"] = bool(fr["will_continue"])
            if "scheduling" in fr:
                entry["scheduling"] = fr["scheduling"]  # "SILENT" | "WHEN_IDLE" | "INTERRUPT"
            out.append(entry)
        msg = {"toolResponse": {"functionResponses": out}}
        await self._ws.send(json.dumps(msg))

    async def receive(self) -> AsyncIterator[dict]:
        """Yield parsed JSON events from the WS until it closes naturally
        or with an error. The caller is responsible for inspecting each
        event for `goAway`, `serverContent.turnComplete`, etc. Mirrors
        the Orchestrator's `gemini_listener` pattern (line 1383)."""
        async for raw in self._ws:
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                # Don't crash the session on a malformed frame; log + continue.
                print(f"[raw-live] invalid JSON: {raw[:120] if isinstance(raw, (str, bytes)) else type(raw)}")
                continue


class _RawLiveConnection:
    """Async context manager wrapping `websockets.connect` so we send
    the setup message inside __aenter__ and surface a RawLiveSession to
    the caller. Mirrors the SDK's `client.aio.live.connect` interface
    (`async with client.aio.live.connect(...) as session:`).
    """
    def __init__(self, *, url, api_key, setup_payload, ping_interval_s, ping_timeout_s):
        self._url = f"{url}?key={api_key}"
        self._setup_payload = setup_payload
        self._ping_interval = ping_interval_s
        self._ping_timeout = ping_timeout_s
        self._cm = None
        self._ws = None

    async def __aenter__(self) -> RawLiveSession:
        self._cm = websockets.connect(
            self._url,
            open_timeout=10,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=10,
        )
        self._ws = await self._cm.__aenter__()
        # Send setup as the very first message (Live API protocol requirement).
        await self._ws.send(json.dumps(self._setup_payload))
        return RawLiveSession(_ws=self._ws)

    async def __aexit__(self, exc_type, exc, tb):
        if self._cm is not None:
            return await self._cm.__aexit__(exc_type, exc, tb)


def connect_raw_live(*, url, api_key, setup_payload,
                     ping_interval_s: float = 60.0, ping_timeout_s: float = 60.0):
    """Drop-in replacement for `client.aio.live.connect(...)`.

    Usage:
        async with connect_raw_live(
            url=cfg.gemini_live_url, api_key=cfg.google_api_key,
            setup_payload=build_setup_payload(...),
            ping_interval_s=cfg.ws_ping_interval_s,
            ping_timeout_s=cfg.ws_ping_timeout_s,
        ) as session:
            await session.send_realtime_input(...)
            async for event in session.receive():
                ...
    """
    return _RawLiveConnection(
        url=url, api_key=api_key, setup_payload=setup_payload,
        ping_interval_s=ping_interval_s, ping_timeout_s=ping_timeout_s,
    )
