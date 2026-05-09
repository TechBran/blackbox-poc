import asyncio
import base64
import json
import pytest

# Imports below land progressively as Task 2.1/2.2/2.4 add the symbols.
from ugv_tools_api.supervisor.raw_session import (
    build_setup_payload,
    RawLiveSession,
    connect_raw_live,
)


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
async def test_send_realtime_input_rejects_both_audio_and_video():
    sess = RawLiveSession(_ws=_FakeWS())
    with pytest.raises(ValueError, match="exactly one of audio= or video="):
        await sess.send_realtime_input(
            audio=b"\x01\x02", video=b"\x03\x04", mime_type="audio/pcm;rate=16000",
        )


@pytest.mark.asyncio
async def test_send_realtime_input_rejects_neither_audio_nor_video():
    sess = RawLiveSession(_ws=_FakeWS())
    with pytest.raises(ValueError, match="exactly one of audio= or video="):
        await sess.send_realtime_input(mime_type="audio/pcm;rate=16000")


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

    monkeypatch.setattr(
        "ugv_tools_api.supervisor.raw_session.websockets.connect",
        lambda url, **kw: _StubCM(url, **kw),
    )

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
