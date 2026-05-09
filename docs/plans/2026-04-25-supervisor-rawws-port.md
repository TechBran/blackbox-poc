# Supervisor Raw-WebSocket Gemini Live Port — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the supervisor's `google.genai` SDK Live transport (`client.aio.live.connect` + `LiveConnectConfig`) with a raw `websockets` JSON transport that mirrors the proven Orchestrator pattern in `Orchestrator/routes/gemini_live_routes.py`, eliminating the post-`turn_complete` session-cycling bug (43 reconnects in 2 minutes on `gemini-2.5-flash-native-audio-latest`) by sidestepping the known-buggy SDK `aio.live` wrapper (python-genai issue #1224).

**Architecture:** Introduce a `RawLiveSession` adapter class in `supervisor/raw_session.py` that exposes the same surface as the SDK's session (`send_realtime_input(audio=...|video=...)`, `send_tool_response(function_responses=...)`, `receive()` async iterator). Rewrite `_build_config()` as `_build_setup_payload()` emitting hand-crafted JSON matching the Orchestrator's setup. Reshape the outer reconnect loop to fire on explicit `goAway` events only — never on iterator-end-after-`turn_complete`. The poller, watch stream, AEC3, and token budget are *transport-agnostic* and stay intact; the only sub-module touched outside `session.py` is `watch_stream.py` which currently imports `google.genai.types.Blob` (replaced by a `send_video` callback injection).

**Tech Stack:**
- Python 3.10 inside the `ugv_waveshare` Docker container on Jetson Orin Nano
- `websockets` (already a transitive dep of `google-genai`; verify with `pip show websockets` returns ≥12.0)
- `pytest` + `pytest-asyncio` (already configured)
- Reference: `Orchestrator/routes/gemini_live_routes.py` lines 169-411 (connect + setup), 546-653 (handle_portal_message), 655-816 (handle_gemini_message turn handling), 1180-1216 (toolResponse format), 1218-1322 (gemini_reconnect)
- Source-of-truth: laptop at `docs/ugv-beast/setup/ugv_tools_api/`, deploys to Jetson via `scripts/sync-ugv-tools.sh`

**Out of scope (deferred):**
- Removing the `google.genai` Python package from `pyproject.toml` (still useful for `google.genai.types` constants if any remain; remove only if truly unused after Task 8)
- BlackBox snapshot persistence of supervisor transcripts (separate plan; the supervisor doesn't persist transcripts today)
- Whisper-based user transcription (deliberately out of scope per operator decision 2026-04-25 — supervisor's only transcript consumer is the journal log, no need)
- Vertex AI endpoint switch (sticking with AI Studio Gemini API)

---

## Empirical Knowledge Carried Forward

These regression guards from the 2026-04-20 spike + 2026-04-24 plan MUST NOT be reintroduced by this port:

| # | Lesson | Touched by |
|---|---|---|
| 1 | Use a SINGLE concatenated audio buffer per turn for output (model emits audio in `inlineData` parts under `modelTurn.parts`) | Task 4 (port the SDK's `response.data` concatenator pattern to raw JSON) |
| 2 | Mic reads MUST go through a dedicated `ThreadPoolExecutor`; the AEC `process()` call lives inside `pump_mic` on that pool | Untouched |
| 3 | Image-returning tools push via the realtime-input video sidechannel, NOT in `FunctionResponse` (FunctionResponseBlob can't carry bytes through SDK serializer — equally true for raw JSON: tool responses use `response: {...}` JSON, not bytes) | Task 4 (`_vision_tool` uses raw `realtimeInput.mediaChunks` for the JPEG/PNG) |
| 4 | `language_code='en-US'` decision: dropped — Orchestrator does not set it and works fine. Verified absence is intentional. | Task 3 |
| 5 | Server-VAD `END_SENSITIVITY_LOW`: dropped — Orchestrator does not set `realtimeInputConfig` outside phone_mode. Default VAD works. | Task 3 |
| 6 | 880 Hz ready / 660 Hz close chime preserved | Untouched |
| 7 | Speaker bleed: AEC3 (Task 5 of prior plan) handles it — no change here | Untouched |
| 8-11 | ALSA cmds, ESP32 IMU avoidance, EKF /odom — all untouched | Untouched |
| 12 | `_patch_ws_keepalive()` 60s/60s ping patch on `websockets.client.connect` — **STILL USEFUL** for the raw-WS path; keep the patch in place but verify it applies to our `websockets.connect()` call | Task 1 |

**Three Google-docs-grounded production patterns kept:**

| Pattern | JSON shape (raw-WS port) |
|---|---|
| `contextWindowCompression(SlidingWindow())` | `setup.contextWindowCompression = {"slidingWindow": {}}` |
| `sessionResumption` handle (2-hr TTL) | `setup.sessionResumption = {"handle": "..."}` — **only when handle is non-None** (matches Orchestrator) |
| GoAway is NOT terminal — keep pumping | Receive loop checks `if "goAway" in event:` and triggers reconnect; outer loop does NOT exit on iterator-end |

**One Google-docs-grounded pattern preserved from the prior plan:**

| Pattern | JSON shape |
|---|---|
| NON_BLOCKING tool: first `FunctionResponse(willContinue=true, scheduling=SILENT)`, then 1+ updates with same `id`, then terminal `willContinue=false` | `toolResponse.functionResponses[0]` with camelCase fields `willContinue: bool` and `scheduling: "SILENT"\|"WHEN_IDLE"\|"INTERRUPT"` |

---

## Pre-flight: Capture the SDK's actual wire format

Before writing code, we need to know the EXACT JSON the SDK sends today, because the Live API JSON spec docs are spotty (the python-genai source is the ground truth). This is a one-shot diagnostic step, not a task that produces code.

**Step P-1: Patch the SDK's underlying `websockets` send to print outbound JSON.**

```bash
# On laptop, in a venv with google-genai 1.73.1
cat > /tmp/wire_capture.py <<'EOF'
import asyncio, json
from google import genai
from google.genai import types

# Monkeypatch BEFORE creating client
import websockets.asyncio.client as _ws
_orig_connect = _ws.connect

class _SpyWS:
    def __init__(self, inner): self._inner = inner
    async def send(self, msg):
        try:
            preview = msg if isinstance(msg, (str, bytes)) else str(msg)
            print(f"[WIRE-OUT] {preview[:500]}")
        except Exception: pass
        return await self._inner.send(msg)
    def __getattr__(self, k): return getattr(self._inner, k)
    def __aiter__(self): return self._inner.__aiter__()
    async def recv(self):
        msg = await self._inner.recv()
        try: print(f"[WIRE-IN]  {msg[:500] if isinstance(msg,(str,bytes)) else str(msg)[:500]}")
        except: pass
        return msg

class _SpyConnect:
    def __init__(self, real_cm): self._real = real_cm
    async def __aenter__(self): self._ws = await self._real.__aenter__(); return _SpyWS(self._ws)
    async def __aexit__(self, *a): return await self._real.__aexit__(*a)

def _patched(*args, **kwargs):
    real = _orig_connect(*args, **kwargs)
    return _SpyConnect(real) if hasattr(real, "__aenter__") else real
_ws.connect = _patched

async def main():
    client = genai.Client(api_key="${GOOGLE_API_KEY}")
    cfg = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        tools=[types.Tool(function_declarations=[{"name":"noop","description":"","parameters":{"type":"object","properties":{}}}])],
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
    )
    async with client.aio.live.connect(model="gemini-2.5-flash-native-audio-latest", config=cfg) as session:
        await session.send_tool_response(function_responses=[types.FunctionResponse(
            id="x", name="noop", response={"ok": True},
            will_continue=True,
            scheduling=types.FunctionResponseScheduling.SILENT,
        )])
        await asyncio.sleep(1.0)

asyncio.run(main())
EOF

GOOGLE_API_KEY="$(grep ^GOOGLE_API_KEY= path/to/.env | cut -d= -f2-)" python3 /tmp/wire_capture.py 2>&1 | tee /tmp/wire_capture.log | head -50
```

**Step P-2: Save the output to `docs/plans/notes/2026-04-25-rawws-wire-format.md`** including the exact JSON shapes for `setup`, `realtimeInput`, `toolResponse` (especially the camelCase / snake_case form of `willContinue` / `will_continue` and the scheduling enum string values). This file is the spec the rest of the plan implements against.

**Why pre-flight:** without this we're guessing on `willContinue` vs `will_continue` and `"SILENT"` vs `"FunctionResponseScheduling.SILENT"`. One commit-and-test cycle saves multiple bench attempts.

---

## Target Repository Layout (after this plan)

```
docs/ugv-beast/setup/ugv_tools_api/
├── ugv_tools_api/
│   └── supervisor/
│       ├── raw_session.py          # NEW: RawLiveSession adapter
│       ├── session.py              # MODIFIED: uses RawLiveSession instead of SDK
│       ├── watch_stream.py         # MODIFIED: takes send_video callback (no Blob import)
│       ├── config.py               # MODIFIED: + GEMINI_LIVE_URL, WS keepalive constants
│       ├── audio_io.py             # untouched
│       ├── aec.py                  # untouched
│       ├── budget.py               # untouched
│       ├── handle_store.py         # untouched
│       ├── mission_poller.py       # untouched (transport-agnostic via PollerCallbacks)
│       ├── tool_declarations.py    # untouched (already JSON-shaped)
│       └── tool_handlers.py        # untouched
└── tests/
    └── supervisor/
        ├── test_raw_session.py     # NEW: RawLiveSession unit tests with mock WS
        ├── test_session.py         # MODIFIED: stub RawLiveSession instead of SDK
        ├── test_watch_stream.py    # MODIFIED: pass send_video callback
        └── (everything else untouched)
```

`docs/plans/notes/2026-04-25-rawws-wire-format.md` — pre-flight capture artifact.
`docs/plans/notes/2026-04-25-rawws-bench.md` — Task 7 bench record.

---

## Implementation Tasks

### Task 1: Add raw-WS configuration constants

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py`
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_config.py`

**Step 1: Write the failing test**

Append to `tests/supervisor/test_config.py`:

```python
def test_load_includes_gemini_live_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.gemini_live_url.startswith("wss://generativelanguage.googleapis.com/")
    assert "BidiGenerateContent" in cfg.gemini_live_url


def test_load_includes_ws_keepalive_defaults(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.ws_ping_interval_s == 60.0
    assert cfg.ws_ping_timeout_s == 60.0
```

**Step 2: Run test to verify it fails**

```
pytest tests/supervisor/test_config.py::test_load_includes_gemini_live_url -v
```
Expected: FAIL — `AttributeError: 'SupervisorConfig' object has no attribute 'gemini_live_url'`.

**Step 3: Implement**

Add to `SupervisorConfig` dataclass (alongside existing fields):

```python
    # Raw-WebSocket transport for Gemini Live (replaces google.genai SDK).
    # URL matches the Orchestrator's bridge (gemini_live_routes.py line 351).
    gemini_live_url: str = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )
    # WebSocket keepalive — empirically tuned for long-lived Live sessions
    # where Gemini's audio replies create natural >20s gaps in client-bound
    # traffic. The default 20s/20s settings drop sessions on every long
    # response. Carried forward from regression guard #12.
    ws_ping_interval_s: float = 60.0
    ws_ping_timeout_s: float = 60.0
```

And in the `load()` function (or wherever env-overrides are wired):

```python
    return SupervisorConfig(
        ...,
        gemini_live_url=os.environ.get("SUPERVISOR_GEMINI_LIVE_URL", SupervisorConfig.gemini_live_url),
        ws_ping_interval_s=float(os.environ.get("SUPERVISOR_WS_PING_INTERVAL_S", "60")),
        ws_ping_timeout_s=float(os.environ.get("SUPERVISOR_WS_PING_TIMEOUT_S", "60")),
    )
```

**Step 4: Run tests to verify pass**

```
pytest tests/supervisor/test_config.py -v
```
Expected: ALL PASS, including the two new tests.

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/config.py tests/supervisor/test_config.py
git commit -m "feat(supervisor): add gemini_live_url + ws keepalive config (raw-WS port prep)"
```

---

### Task 2: `RawLiveSession` adapter — connect, setup, send paths

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/raw_session.py`
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_raw_session.py`

**Goal:** A class that exposes `send_realtime_input(audio=bytes,mime_type=str)`, `send_realtime_input(video=bytes,mime_type=str)`, and `send_tool_response(function_responses=list[dict])` — same call sites as the SDK — but emits raw JSON over a `websockets` connection.

**Step 1: Write the failing test for `_build_setup_payload`**

Create `tests/supervisor/test_raw_session.py`:

```python
import pytest
from ugv_tools_api.supervisor.raw_session import build_setup_payload


def test_build_setup_payload_minimal_no_resumption():
    payload = build_setup_payload(
        model="gemini-2.5-flash-native-audio-latest",
        voice="Puck",
        system_instruction="You are a robot supervisor.",
        tools=[{"name": "ping", "description": "p", "parameters": {"type": "object", "properties": {}}}],
        resume_handle=None,
    )
    assert payload["setup"]["model"] == "models/gemini-2.5-flash-native-audio-latest"
    assert payload["setup"]["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert payload["setup"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"
    assert payload["setup"]["systemInstruction"]["parts"][0]["text"].startswith("You are")
    assert payload["setup"]["tools"] == [{"functionDeclarations": [{"name": "ping", "description": "p", "parameters": {"type": "object", "properties": {}}}]}]
    assert payload["setup"]["contextWindowCompression"] == {"slidingWindow": {}}
    assert "sessionResumption" not in payload["setup"]
    assert "realtimeInputConfig" not in payload["setup"]
    assert "inputAudioTranscription" not in payload["setup"]
    assert "outputAudioTranscription" not in payload["setup"]


def test_build_setup_payload_with_resume_handle():
    payload = build_setup_payload(
        model="m", voice="v", system_instruction="s", tools=[], resume_handle="abc123",
    )
    assert payload["setup"]["sessionResumption"] == {"handle": "abc123"}


def test_build_setup_payload_drops_empty_resume_handle():
    payload = build_setup_payload(
        model="m", voice="v", system_instruction="s", tools=[], resume_handle="",
    )
    assert "sessionResumption" not in payload["setup"]
```

**Step 2: Run test to verify it fails**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'ugv_tools_api.supervisor.raw_session'`.

**Step 3: Implement `build_setup_payload`**

Create `raw_session.py`:

```python
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
         "will_continue": True, "scheduling": "SILENT"},  # snake_case → camelCase here
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
```

**Step 4: Run tests, verify pass**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: 3 PASS.

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/raw_session.py tests/supervisor/test_raw_session.py
git commit -m "feat(supervisor): build_setup_payload helper for raw-WS port"
```

---

### Task 2.5: `RawLiveSession.send_realtime_input` + `send_tool_response`

**Files:**
- Modify: `ugv_tools_api/supervisor/raw_session.py`
- Modify: `tests/supervisor/test_raw_session.py`

**Step 1: Write failing tests for the send paths**

Append to `test_raw_session.py`:

```python
import asyncio
import pytest
from ugv_tools_api.supervisor.raw_session import RawLiveSession


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        if self.closed:
            raise RuntimeError("ws closed")
        self.sent.append(msg)

    async def close(self) -> None:
        self.closed = True


def _last_json(ws: _FakeWS) -> dict:
    return json.loads(ws.sent[-1])


@pytest.mark.asyncio
async def test_send_realtime_input_audio_emits_realtimeInput_mediaChunks():
    ws = _FakeWS()
    sess = RawLiveSession(_ws=ws)
    await sess.send_realtime_input(audio=b"\x01\x02\x03\x04", mime_type="audio/pcm;rate=16000")
    msg = _last_json(ws)
    assert "realtimeInput" in msg
    chunks = msg["realtimeInput"]["mediaChunks"]
    assert len(chunks) == 1
    assert chunks[0]["mimeType"] == "audio/pcm;rate=16000"
    assert base64.b64decode(chunks[0]["data"]) == b"\x01\x02\x03\x04"


@pytest.mark.asyncio
async def test_send_realtime_input_video_emits_realtimeInput_with_jpeg():
    ws = _FakeWS()
    sess = RawLiveSession(_ws=ws)
    await sess.send_realtime_input(video=b"jpegbytes", mime_type="image/jpeg")
    msg = _last_json(ws)
    assert msg["realtimeInput"]["mediaChunks"][0]["mimeType"] == "image/jpeg"


@pytest.mark.asyncio
async def test_send_tool_response_camelcases_will_continue_and_scheduling():
    ws = _FakeWS()
    sess = RawLiveSession(_ws=ws)
    await sess.send_tool_response(function_responses=[{
        "id": "fc1", "name": "ping", "response": {"ok": True},
        "will_continue": True, "scheduling": "SILENT",
    }])
    msg = _last_json(ws)
    fr = msg["toolResponse"]["functionResponses"][0]
    assert fr["id"] == "fc1"
    assert fr["name"] == "ping"
    assert fr["response"] == {"ok": True}
    assert fr["willContinue"] is True
    assert fr["scheduling"] == "SILENT"


@pytest.mark.asyncio
async def test_send_tool_response_omits_will_continue_when_not_set():
    ws = _FakeWS()
    sess = RawLiveSession(_ws=ws)
    await sess.send_tool_response(function_responses=[{
        "id": "fc1", "name": "ping", "response": {"ok": True},
    }])
    fr = _last_json(ws)["toolResponse"]["functionResponses"][0]
    assert "willContinue" not in fr
    assert "scheduling" not in fr
```

**Step 2: Run tests to verify they fail**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: FAIL on the new tests (`AttributeError: type 'RawLiveSession' has no attribute 'send_realtime_input'`).

**Step 3: Implement `RawLiveSession` send methods**

Append to `raw_session.py`:

```python
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
        payload = audio if audio is not None else video
        if payload is None:
            raise ValueError("send_realtime_input requires audio= or video=")
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
```

**Step 4: Run tests, verify pass**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/raw_session.py tests/supervisor/test_raw_session.py
git commit -m "feat(supervisor): RawLiveSession send_realtime_input + send_tool_response"
```

---

### Task 2.75: `RawLiveSession.receive` async iterator

**Files:**
- Modify: `ugv_tools_api/supervisor/raw_session.py`
- Modify: `tests/supervisor/test_raw_session.py`

**Step 1: Write failing tests**

Append:

```python
class _FakeReceiveWS:
    def __init__(self, messages: list[str]):
        self._messages = list(messages)
        self.closed = False
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)
    async def send(self, _msg): pass
    async def close(self): self.closed = True


@pytest.mark.asyncio
async def test_receive_yields_parsed_events_from_ws_stream():
    ws = _FakeReceiveWS([
        json.dumps({"setupComplete": {}}),
        json.dumps({"serverContent": {"modelTurn": {"parts": [{"text": "hi"}]}}}),
        json.dumps({"serverContent": {"turnComplete": True}}),
    ])
    sess = RawLiveSession(_ws=ws)
    events = []
    async for ev in sess.receive():
        events.append(ev)
    assert events == [
        {"setupComplete": {}},
        {"serverContent": {"modelTurn": {"parts": [{"text": "hi"}]}}},
        {"serverContent": {"turnComplete": True}},
    ]


@pytest.mark.asyncio
async def test_receive_skips_non_json_messages():
    ws = _FakeReceiveWS(["not json", json.dumps({"x": 1})])
    sess = RawLiveSession(_ws=ws)
    events = [ev async for ev in sess.receive()]
    assert events == [{"x": 1}]
```

**Step 2: Run, verify fail**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: FAIL — `AttributeError: 'RawLiveSession' object has no attribute 'receive'`.

**Step 3: Implement**

Add to `RawLiveSession`:

```python
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
```

**Step 4: Run, verify pass**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: ALL PASS.

**Step 5: Commit**

```bash
git commit -am "feat(supervisor): RawLiveSession.receive async iterator"
```

---

### Task 2.9: `connect_raw_live` async context manager

**Files:**
- Modify: `ugv_tools_api/supervisor/raw_session.py`
- Modify: `tests/supervisor/test_raw_session.py`

**Step 1: Write failing test**

Append (uses `pytest.MonkeyPatch` to avoid hitting the real Google API):

```python
@pytest.mark.asyncio
async def test_connect_raw_live_sends_setup_then_yields_session(monkeypatch):
    sent_url = []
    sent_msgs = []

    class _StubWS:
        def __init__(self): self.closed = False
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
        async def send(self, m): sent_msgs.append(m)
        async def close(self): self.closed = True

    class _StubCM:
        def __init__(self, url, **kwargs):
            sent_url.append(url)
            self._kwargs = kwargs
            self._ws = _StubWS()
        async def __aenter__(self): return self._ws
        async def __aexit__(self, *a): await self._ws.close()

    monkeypatch.setattr("ugv_tools_api.supervisor.raw_session.websockets.connect",
                        lambda url, **kw: _StubCM(url, **kw))

    from ugv_tools_api.supervisor.raw_session import connect_raw_live, build_setup_payload
    setup = build_setup_payload(model="m", voice="v", system_instruction="s",
                                tools=[], resume_handle=None)

    async with connect_raw_live(
        url="wss://example/x", api_key="KKK",
        setup_payload=setup, ping_interval_s=60.0, ping_timeout_s=60.0,
    ) as sess:
        assert isinstance(sess, RawLiveSession)

    # URL appended ?key=KKK
    assert sent_url[0].endswith("?key=KKK")
    # First message is the setup we passed
    assert json.loads(sent_msgs[0]) == setup
```

**Step 2: Run, fail**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: FAIL — `ImportError: cannot import name 'connect_raw_live'`.

**Step 3: Implement**

Append to `raw_session.py`:

```python
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
```

**Step 4: Run, verify pass**

```
pytest tests/supervisor/test_raw_session.py -v
```
Expected: ALL PASS.

**Step 5: Commit**

```bash
git commit -am "feat(supervisor): connect_raw_live async context manager"
```

---

### Task 3: Port `watch_stream.py` to a `send_video` callback

**Goal:** Remove `from google.genai import types` and `session.send_realtime_input(video=types.Blob(...))` from `watch_stream.py` so it's transport-agnostic. Inject a `send_video(jpeg_bytes, mime_type)` async callback at construction.

**Files:**
- Modify: `ugv_tools_api/supervisor/watch_stream.py`
- Modify: `tests/supervisor/test_watch_stream.py`

**Step 1: Write the failing test**

Adjust the existing watch-stream test (or add a new one) to verify a new construction signature:

```python
@pytest.mark.asyncio
async def test_watch_stream_calls_send_video_callback_at_1fps():
    sent = []
    async def fake_send_video(jpeg, mime_type):
        sent.append((len(jpeg), mime_type))

    cam = _StubCam(jpeg=b"jpegdata")
    ws = WatchStream(cam, fps=10.0, on_frame=None, send_video=fake_send_video)
    ws.set(on=True, source="pantilt")

    task = asyncio.create_task(ws.run_with_callback())
    await asyncio.sleep(0.25)
    ws.stop()
    await task

    assert any(mt == "image/jpeg" for _, mt in sent)
    assert any(n == len(b"jpegdata") for n, _ in sent)
```

(Keep the existing `run(session)` test marked xfail or delete it — it'll be removed in Step 3.)

**Step 2: Run, fail**

Expected: `TypeError: __init__() got an unexpected keyword argument 'send_video'`.

**Step 3: Implement — replace `run(session)` with `run_with_callback()` taking the injected callback**

Modify `watch_stream.py`:

```python
class WatchStream:
    def __init__(
        self,
        camera: _CameraLike,
        fps: float = 1.0,
        on_frame: Optional[Callable[[], None]] = None,
        send_video: Optional[Callable[[bytes, str], "asyncio.Future"]] = None,
    ) -> None:
        self._cam = camera
        self._period = 1.0 / fps
        self._on = False
        self._source = "pantilt"
        self._stop = asyncio.Event()
        self._bytes_sent = 0
        self._frames_sent = 0
        self._on_frame = on_frame
        self._send_video = send_video
        # ... rest unchanged

    # Old method:
    # async def run(self, session) -> None: ...
    # New method (drop the SDK-typed `session` param):
    async def run_with_callback(self) -> None:
        if self._send_video is None:
            raise RuntimeError("WatchStream requires send_video= callback when run_with_callback() is used")
        while not self._stop.is_set():
            t0 = asyncio.get_running_loop().time()
            if self._on:
                jpeg = self._cam.get_camera_jpeg() if self._source == "pantilt" else None
                if jpeg:
                    try:
                        await self._send_video(jpeg, "image/jpeg")
                        self._bytes_sent += len(jpeg)
                        self._frames_sent += 1
                        if self._on_frame is not None:
                            try: self._on_frame()
                            except Exception: pass
                    except Exception as e:
                        print(f"[watch] send error swallowed: {type(e).__name__}: {e}")
            elapsed = asyncio.get_running_loop().time() - t0
            sleep = max(0.0, self._period - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep or 0.001)
                break
            except asyncio.TimeoutError:
                pass
```

Delete the old `run(session)` method. (The `from google.genai import types` import inside the old method goes too — that's the WHOLE point.)

**Step 4: Run tests, verify pass**

```
pytest tests/supervisor/test_watch_stream.py -v
```
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/watch_stream.py tests/supervisor/test_watch_stream.py
git commit -m "refactor(supervisor): WatchStream uses send_video callback (transport-agnostic)"
```

---

### Task 4: Replace `_build_config` and `_run_one_session` SDK calls with `RawLiveSession`

**Files:**
- Modify: `ugv_tools_api/supervisor/session.py`
- Modify: `tests/supervisor/test_session.py` (if it references `_build_config` directly)

**Goal:** Make this the smallest possible diff to `session.py`. The shape of `_run_one_session` stays. The only changes:

1. `_build_config(...)` → `_build_setup_payload(...)` returning a JSON dict.
2. `self._client.aio.live.connect(model=..., config=cfg)` → `connect_raw_live(url=..., api_key=..., setup_payload=...)`.
3. `session.send_realtime_input(audio=Blob(data, mime_type=...))` → `session.send_realtime_input(audio=data, mime_type="audio/pcm;rate=16000")` — RawLiveSession takes raw bytes, not Blob.
4. `session.send_realtime_input(video=Blob(...))` → `session.send_realtime_input(video=jpeg, mime_type="image/jpeg")`.
5. `session.send_tool_response(function_responses=[FunctionResponse(...)])` → `session.send_tool_response(function_responses=[{"id":..., "name":..., "response":..., "will_continue":bool, "scheduling":"SILENT"|...}])` (plain dicts).
6. `async for response in session.receive():` → `async for event in session.receive():` then dispatch by JSON top-level keys (`serverContent`, `toolCall`, `toolCallCancellation`, `sessionResumptionUpdate`, `goAway`).
7. `WatchStream(...)` constructor: pass `send_video=lambda j, mt: session.send_realtime_input(video=j, mime_type=mt)`.
8. `watch_stream_task = asyncio.create_task(watch.run(session))` → `watch.run_with_callback()` (no session arg).
9. **Drop** the outer `try ... except` reconnect-on-iterator-end behavior. Replace with: `_run_one_session` returns cleanly when receive iterator ends, and the inner `while not _close_session.is_set():` loop ONLY reconnects if the previous session set `should_reconnect = True` (set when a `goAway` event was seen). Iterator-end-without-goAway is treated as session-end → exit to wake-wait, NOT reconnect.

**Step 1: Read current `_build_config` (lines 148-205) and `_run_one_session` (lines 464-1003) carefully** so the diff is mechanical not creative. No new behaviors; only transport substitution.

**Step 2: Write a unit-level smoke test that `session.py` imports cleanly without `from google.genai import ...` for transport**

Add to `tests/supervisor/test_session.py`:

```python
def test_session_module_does_not_import_google_genai_live_at_module_level():
    """The raw-WS port should remove direct dependence on google.genai for
    transport. types.Blob and FunctionResponse references must be gone."""
    import ugv_tools_api.supervisor.session as sess_mod
    src = open(sess_mod.__file__).read()
    # Permit `# legacy` comments referencing the removed code, but NOT live imports.
    assert "from google.genai" not in src
    assert "from google import genai" not in src
    assert "client.aio.live.connect" not in src
```

**Step 3: Run, verify it fails**

```
pytest tests/supervisor/test_session.py::test_session_module_does_not_import_google_genai_live_at_module_level -v
```
Expected: FAIL.

**Step 4: Implement the port in `session.py`**

The diff is large but mechanical. Below is the structural shape (excerpts; the agent reads the full file and applies them line-by-line):

**Top-of-file imports change:**
```python
# REMOVED:
# from google import genai
# from google.genai import types

# ADDED:
from .raw_session import build_setup_payload, connect_raw_live
```

**`_build_config` becomes `_build_setup_payload_from_cfg`:**
```python
def _build_setup_payload_from_cfg(cfg: SupervisorConfig, resume_handle: Optional[str]) -> dict:
    return build_setup_payload(
        model=cfg.model,
        voice=cfg.voice,
        system_instruction=_SYSTEM_PROMPT,
        tools=list(ALL_TOOLS),
        resume_handle=resume_handle,
    )
```

(`ALL_TOOLS` is already a list of dicts shaped as Gemini function declarations — verify in `tool_declarations.py`.)

**`_run_one_session` connect block:**
```python
setup = _build_setup_payload_from_cfg(self._cfg, self._handle_store.get())
sess_open_t = loop_pre.time()
async with connect_raw_live(
    url=self._cfg.gemini_live_url,
    api_key=self._cfg.google_api_key,
    setup_payload=setup,
    ping_interval_s=self._cfg.ws_ping_interval_s,
    ping_timeout_s=self._cfg.ws_ping_timeout_s,
) as session:
    print("[supervisor] session opened (raw-WS)")
    ...
```

**`pump_mic` outgoing send:**
```python
await session.send_realtime_input(
    audio=outgoing,
    mime_type="audio/pcm;rate=16000",
)
```

**`pump_responses` event-dispatch — replace SDK's response-object access with raw JSON keys:**
```python
async for event in session.receive():
    _diag_responses_received += 1

    # 1. tool calls
    tc = event.get("toolCall")
    if tc and tc.get("functionCalls"):
        function_calls = tc["functionCalls"]  # list of {"id","name","args"} dicts
        mark_active()
        await self._dispatch_tool_calls(
            session, function_calls,
            spawn_poller=_spawn_poller, poller_tasks=poller_tasks,
            pollers=pollers, watch=watch, operator_override=operator_override,
        )
        continue

    sc = event.get("serverContent")
    if sc:
        # Concatenate all inlineData audio bytes from modelTurn.parts
        # (matches the SDK's response.data concatenator semantics).
        model_turn = sc.get("modelTurn", {})
        audio_chunks: list[bytes] = []
        for part in model_turn.get("parts", []):
            inline = part.get("inlineData") or {}
            mt = inline.get("mimeType", "")
            if "audio" in mt and inline.get("data"):
                audio_chunks.append(base64.b64decode(inline["data"]))
        if audio_chunks:
            mark_active()
            audio = b"".join(audio_chunks)
            _diag_audio_bytes_received += len(audio)
            await loop.run_in_executor(None, self._spk.write, audio)
            budget.audio_seconds(len(audio) / 48000.0)

        if sc.get("turnComplete"):
            print(f"[diag] sess#{sess_n} server_content.turn_complete=True (received so far: {_diag_responses_received})")

    upd = event.get("sessionResumptionUpdate")
    if upd and upd.get("newHandle"):
        self._handle_store.set(upd["newHandle"])

    if "goAway" in event:
        left = event["goAway"].get("timeLeft")
        print(f"[supervisor] GoAway received, time_left={left}; continuing until server closes")
        # Mark that the upcoming iterator-end is goAway-driven, so the
        # outer loop knows to reconnect (not exit to wake-wait).
        nonlocal_should_reconnect[0] = True

    if (session_stop.is_set()
            or self._stop.is_set()
            or self._close_session.is_set()):
        _exit_reason = "stop-event"
        return
# end async for
_exit_reason = (
    "natural-iterator-end (goAway-driven, will reconnect)"
    if nonlocal_should_reconnect[0]
    else "natural-iterator-end (no goAway, session ended)"
)
```

**Outer reconnect loop in `run()` (around line 348-371) becomes goAway-only:**
```python
backoff = _BACKOFF_INITIAL
while not self._stop.is_set() and not self._close_session.is_set():
    nonlocal_should_reconnect = [False]   # set inside _run_one_session
    t0 = loop.time()
    try:
        await self._run_one_session(should_reconnect=nonlocal_should_reconnect)
    except Exception as e:
        if loop.time() - t0 > _HEALTHY_SESSION_S:
            backoff = _BACKOFF_INITIAL
        print(f"[supervisor] session error, reconnect in {backoff:.1f}s: {e}")
        try:
            await asyncio.wait_for(self._close_session.wait(), timeout=backoff)
            break
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
        continue
    # Successful return — distinguish goAway-driven (reconnect) from natural-end (close).
    if nonlocal_should_reconnect[0]:
        backoff = _BACKOFF_INITIAL
        # Loop iterates and re-opens the session. The handle stored via
        # sessionResumptionUpdate keeps the conversation continuity.
        continue
    else:
        # Server closed without goAway — treat as session-end. Exit the
        # inner reconnect loop and return to wake-wait. This is the new
        # behavior (vs prior plan): the cycling-bug symptom of repeated
        # natural-iterator-end → reopen → cold-VAD storm STOPS HERE.
        break
```

(Note: `_run_one_session` signature changes to take a `should_reconnect: list[bool]` mutable wrapper — simpler than `nonlocal` across async-method boundaries.)

**`_dispatch_tool_calls`** — replace each `types.FunctionResponse(...)` and `types.FunctionResponseScheduling.X` with plain dict:

```python
# Old:
# await session.send_tool_response(function_responses=[types.FunctionResponse(
#     id=fc.id, name=fc.name, response=resp,
#     will_continue=True,
#     scheduling=types.FunctionResponseScheduling.SILENT,
# )])

# New (note: `fc` is now a dict from event["toolCall"]["functionCalls"][i]):
await session.send_tool_response(function_responses=[{
    "id": fc["id"], "name": fc["name"], "response": resp,
    "will_continue": True, "scheduling": "SILENT",
}])
```

The same pattern applies to `_make_callbacks` inside `_run_one_session`:
```python
async def _send(body: dict, *, will_continue: bool, scheduling: str) -> None:
    await session.send_tool_response(function_responses=[{
        "id": fc_id, "name": fc_name, "response": body,
        "will_continue": will_continue, "scheduling": scheduling,
    }])
```

And the strings replace the enum members:
```python
send_silent=lambda b: _send(b, will_continue=True, scheduling="SILENT"),
send_when_idle=lambda b: _send(b, will_continue=True, scheduling="WHEN_IDLE"),
send_terminal=lambda b: _send(b, will_continue=False, scheduling="WHEN_IDLE"),
```

**`_vision_tool`** — push the image via raw realtimeInput:
```python
async def _vision_tool(self, session, fc, payload, mime, label):
    if not payload:
        await self._respond(session, fc, {"error": f"no {label} frame available"})
        return
    await self._respond(session, fc, {"status": "ok", "note": f"{label} image delivered as video frame"})
    await session.send_realtime_input(video=payload, mime_type=mime)
```

**`_respond`** stays a thin wrapper:
```python
async def _respond(self, session, fc, resp: dict) -> None:
    await session.send_tool_response(function_responses=[
        {"id": fc["id"], "name": fc["name"], "response": resp},
    ])
```

**Watch wiring — line ~899-902:**
```python
async def _send_video(jpeg, mime_type):
    await session.send_realtime_input(video=jpeg, mime_type=mime_type)

watch = WatchStream(
    self._cam, fps=1.0,
    on_frame=lambda: budget.jpeg_frames(1),
    send_video=_send_video,
)
operator_override: list = [None]
...
watch_stream_task = asyncio.create_task(watch.run_with_callback())
```

**Step 5: Run tests**

```
pytest tests/supervisor/ -v
```
Expected: ALL PASS, including the new "no `from google.genai` in session.py" test.

**Step 6: Commit (one big, atomic commit for the substitution)**

```bash
git add -p ugv_tools_api/supervisor/session.py  # review the diff carefully
git add tests/supervisor/test_session.py
git commit -m "feat(supervisor): port _run_one_session to raw-WS via RawLiveSession"
```

---

### Task 5: Make outer reconnect loop goAway-only (regression guard against cycling-bug recurrence)

This was implemented as part of Task 4 — Step 4's outer-loop changes. This task adds a unit test that locks the behavior in.

**Files:**
- Modify: `tests/supervisor/test_session.py`

**Step 1: Write the regression test**

```python
@pytest.mark.asyncio
async def test_outer_loop_does_NOT_reopen_when_server_closes_without_goAway(monkeypatch):
    """If the SDK Live cycling bug ever returns (server closes WS after
    turn_complete with no goAway), the outer reconnect loop must EXIT to
    wake-wait rather than firing 43 reconnects in 2 minutes.
    """
    # Stub _run_one_session: simulate one iterator-end with should_reconnect=False
    from ugv_tools_api.supervisor.session import Supervisor
    sup = Supervisor.__new__(Supervisor)  # bypass __init__
    # ... (full setup with mocks; or use fixtures that exist for other tests)
    # Assert: after _run_one_session returns False-flagged, _close_session is set
    # and no second _run_one_session call occurs.
```

(The existing test fixtures in `tests/supervisor/` already mock the session lifecycle for other tests — pattern after `test_close_session_request_unwinds_inner_loop` if it exists. Subagent should look first.)

**Step 2: Run, fail (or pass — depending on Step-4 implementation correctness)**

If Step 4 was implemented correctly the test should pass on first run, which is fine.

**Step 3: Commit**

```bash
git commit -am "test(supervisor): regression test for goAway-only reconnect"
```

---

### Task 6: Wire format verification — capture from real Gemini Live

**Files:**
- Create: `docs/plans/notes/2026-04-25-rawws-wire-format.md`

**Step 1: Run pre-flight Step P-1 (the SDK wire-capture script)**

This was deferred from the pre-flight section. Run it now that we have the raw_session module to compare against.

**Step 2: Run the equivalent capture against our `RawLiveSession`**

Create `/tmp/rawws_wire_capture.py` similar to P-1 but using `connect_raw_live` and a stub print at every `_ws.send()` call. Run it, capture the JSON.

**Step 3: Diff the two captures**

```bash
diff <(grep '^\[WIRE-OUT\]' /tmp/wire_capture.log | head -10) \
     <(grep '^\[WIRE-OUT\]' /tmp/rawws_wire_capture.log | head -10)
```

The diff is what we EXPECT to remove (the cycling-bug-triggering fields). Specifically expect to see:
- SDK sends extra fields (e.g. `"inputAudioTranscription": {}`, `"outputAudioTranscription": {}`, possibly empty `"realtimeInputConfig": {}`).
- Our `RawLiveSession` sends only model + generationConfig + systemInstruction + tools + contextWindowCompression (and `sessionResumption` only if handle-set).

If unexpected fields ARE sent by `RawLiveSession`, fix `build_setup_payload` (Task 2) before bench.

**Step 4: Save the diff + analysis to `docs/plans/notes/2026-04-25-rawws-wire-format.md`** as the bench-record's first artifact.

**Step 5: Commit**

```bash
git add docs/plans/notes/2026-04-25-rawws-wire-format.md
git commit -m "docs(supervisor): wire-format diff between SDK and RawLiveSession"
```

---

### Task 7: Sync to Jetson and live bench verification

**Files:**
- Modify: `docs/plans/notes/2026-04-25-rawws-bench.md`

**Step 1: Run the post-sync ritual (from MEMORY.md feedback)**

```bash
# From laptop
LOCAL=/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/docs/ugv-beast/setup/ugv_tools_api
bash $LOCAL/scripts/sync-ugv-tools.sh

# CRITICAL: re-substitute GOOGLE_API_KEY (sync-ugv-tools.sh clobbers it).
# Run on Jetson:
sshpass -p 'jetson' ssh jetson@192.168.1.155 'cd /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy && \
  KEY=$(grep ^GOOGLE_API_KEY= er.env | cut -d= -f2-); \
  sed -i "s|^GOOGLE_API_KEY=.*|GOOGLE_API_KEY=$KEY|" supervisor.env && \
  echo "GOOGLE_API_KEY=$(grep ^GOOGLE_API_KEY= supervisor.env | cut -d= -f2- | head -c 8)..."'
```

**Step 2: Restart the supervisor service**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl restart ugv-supervisor.service'
```

**Step 3: Tail journal for `[supervisor] session opened (raw-WS)` to confirm raw-WS path is live**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo -n journalctl -u ugv-supervisor.service -f' | tee /tmp/bench-rawws.log &
TAIL_PID=$!
sleep 5
# Operator says wake phrase + 2-3 conversational turns
# Expect to see in journal:
#   [supervisor] session opened (raw-WS)
#   [diag] mic upload sess#1: ...
#   [diag] sess#1 server_content.turn_complete=True (received so far: N)
#   ... (NO [diag] session #2 opening within the same wake cycle)
#   [user]/[model] transcript lines as before (or absence is OK if we dropped transcription)
```

**Step 4: Verification criteria — record in `bench.md`**

| Check | Expected | How to verify |
|---|---|---|
| C1: Session opens via raw-WS | `[supervisor] session opened (raw-WS)` appears | grep journal |
| C2: No cycling on turn_complete | sess#1 stays open across ≥3 user turns | count `session #N opening` events: should be 1 per wake cycle |
| C3: Tool calls work | `[supervisor] tool_call ...` appears + tool returns | issue a "what time is it?" or "list mission" |
| C4: NON_BLOCKING dispatch works | dispatch_er_mission returns immediately + poller emits ticks | issue a small mission, watch for `[diag]` poller logs |
| C5: Watch mode pushes JPEGs | `bytes_sent` incrementing in WatchStream stats | use a test endpoint or instrument stats log |
| C6: AEC3 still enabled | `[supervisor] AEC3 enabled` appears | grep journal |
| C7: GoAway triggers reconnect | when server sends goAway, supervisor reconnects, conversation continues | longer session test or simulate via fault-injection (deferred) |

**Step 5: Commit bench record**

```bash
git add docs/plans/notes/2026-04-25-rawws-bench.md
git commit -m "docs(supervisor): raw-WS port bench record"
```

---

### Task 8: Cleanup

**Goal:** Remove dead SDK transport code paths now that raw-WS is proven.

**Files:**
- Modify: `ugv_tools_api/supervisor/session.py` (drop `_patch_ws_keepalive` if unused after Task 4 — but verify `connect_raw_live` doesn't ALSO need it; `websockets.connect` keepalives are configured via kwargs already in Task 2.9, so the patch is dead)
- Modify: `pyproject.toml` (do NOT remove `google-genai` — still useful for any remaining `types.*` constants or documentation; only strip if `grep -r 'from google.*genai' ugv_tools_api/` is fully empty)

**Step 1: Find any remaining `google.genai` references**

```bash
grep -rn "google.genai\|from google import genai\|google-genai" ugv_tools_api/
```

**Step 2: Remove dead code**

- `_patch_ws_keepalive()` and its invocation: DELETE — `websockets.connect()` accepts `ping_interval` and `ping_timeout` kwargs directly (Task 2.9 wires them in).
- Any `from google.genai import types` left over: DELETE.

**Step 3: Run full test suite**

```bash
pytest tests/supervisor/ -v
```
Expected: ALL PASS (56+ tests).

**Step 4: Commit**

```bash
git commit -am "chore(supervisor): remove dead SDK transport code paths"
```

---

## Post-Implementation Verification

Before considering this plan done:

- [ ] All Task 1-8 tests pass on the laptop (`pytest tests/supervisor/`).
- [ ] Wire-format capture (Task 6) shows `RawLiveSession` sends a SUBSET of what the SDK sent (specifically: no `inputAudioTranscription`, no `outputAudioTranscription`, no `realtimeInputConfig`, no `sessionResumption` when handle is None).
- [ ] Live bench (Task 7) shows C1-C6 all pass on the Jetson.
- [ ] Operator confirms multi-turn conversation works end-to-end without the previous "model repeats greeting forever" symptom.
- [ ] A `/snapshot-dev` is minted at the end recording: model still on `gemini-2.5-flash-native-audio-latest`, transport now raw-WS, cycling bug closed, NON_BLOCKING/AEC3/watch/budget all green.

## Out-of-Plan Follow-Ups

Once this plan lands, follow up with:

1. **Whisper-via-HTTP for journal `[user]` transcripts** — currently dropped. If the operator misses them in the journal, port Orchestrator's `transcribe_user_audio()` pattern (HTTP POST to `/stt/json`) with a short ~2 s buffer. Adds a network dep, gated by an env flag `SUPERVISOR_USER_TRANSCRIPTS=on`.
2. **GoAway fault-injection test (C7)** — simulate via a proxy MITM or by waiting for an organic GoAway event during a long bench. Currently untested in CI.
3. **Token usage from `usageMetadata` events** — the raw-WS receive loop can now read `event["usageMetadata"]` directly when present. Wire it into TokenBudget for live (vs estimated) usage.
4. **Server-side input transcription as the alternative to Whisper** — try adding `inputAudioTranscription: {}` back into the setup payload in *isolation* (after the cycling-bug fix is confirmed unrelated) to see whether it's tolerable on its own. If yes, it's cheaper than the Whisper round-trip for journal logs.
