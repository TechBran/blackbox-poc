"""Hermetic tests for ugv-voice speak_server.

The new (Task 2.1/2.2 rework) flow:
    POST /speak → POST {BLACKBOX_URL}/tts → GET {BLACKBOX_URL}/ui/uploads/...mp3
    → mpg123 -a $UGV_ALSA_DEVICE <tmpfile>

Tests mock httpx (BlackBox) and subprocess.run (mpg123) so nothing escapes
the test process and no audio actually plays.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

# Ensure env vars are set before importing the module so module-level
# os.environ.get() picks them up deterministically.
os.environ["BLACKBOX_URL"] = "http://blackbox.test:9091"
os.environ["UGV_ALSA_DEVICE"] = "plughw:CARD=Device"
os.environ["UGV_VOICE"] = "onyx"

from fastapi.testclient import TestClient  # noqa: E402

from ugv_tools_api.voice import speak_server  # noqa: E402

c = TestClient(speak_server.app)


def test_speak_health():
    """/health responds 200, ok:true, exposes blackbox_reachable boolean."""
    # Mock the upstream /health probe to succeed
    with patch.object(speak_server.httpx, "Client") as MockClient:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.get.return_value = MagicMock(status_code=200)
        MockClient.return_value = instance

        r = c.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "blackbox_reachable" in body
    assert body["blackbox_reachable"] is True
    assert body["alsa"] == "plughw:CARD=Device"
    assert body["blackbox_url"] == "http://blackbox.test:9091"


def test_speak_rejects_empty_text():
    r = c.post("/speak", json={"text": ""})
    assert r.status_code == 400


def test_speak_posts_to_blackbox_and_plays():
    """Full happy path: BlackBox /tts returns audio_url, GET fetches MP3,
    mpg123 is invoked with the configured ALSA device."""
    fake_mp3 = b"ID3\x03\x00\x00\x00fake-mp3-bytes"

    # Track captured calls
    posted = {}
    fetched = {}

    class _MockResponse:
        def __init__(self, status_code, json_data=None, content=None, text=""):
            self.status_code = status_code
            self._json = json_data
            self.content = content if content is not None else b""
            self.text = text

        def json(self):
            return self._json

    class _MockAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            posted["url"] = url
            posted["json"] = json
            return _MockResponse(
                200,
                json_data={
                    "status": "success",
                    "audio_url": "/ui/uploads/abc-tts.mp3",
                    "voice": json["voice"],
                    "model": json["model"],
                    "format": "mp3",
                    "size_bytes": len(fake_mp3),
                },
            )

        async def get(self, url, **kw):
            fetched["url"] = url
            return _MockResponse(200, content=fake_mp3)

    # subprocess.run mock: capture invocation, simulate mpg123 success
    sub_calls = {}

    def _mock_run(cmd, **kw):
        sub_calls["cmd"] = cmd
        sub_calls["kw"] = kw
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    with patch.object(speak_server.httpx, "AsyncClient", _MockAsyncClient), \
         patch.object(speak_server.subprocess, "run", _mock_run):
        r = c.post("/speak", json={"text": "hello world", "voice": "nova"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["played"] is True
    assert body["chars"] == len("hello world")
    assert body["bytes"] == len(fake_mp3)
    assert body["voice"] == "nova"
    assert body["alsa"] == "plughw:CARD=Device"
    assert body["audio_url"] == "/ui/uploads/abc-tts.mp3"

    # BlackBox /tts was called with right URL + payload
    assert posted["url"] == "http://blackbox.test:9091/tts"
    assert posted["json"]["text"] == "hello world"
    assert posted["json"]["voice"] == "nova"
    assert posted["json"]["return_json"] is True
    assert posted["json"]["format"] == "mp3"

    # MP3 was fetched from absolute BlackBox URL
    assert fetched["url"] == "http://blackbox.test:9091/ui/uploads/abc-tts.mp3"

    # mpg123 was invoked with the configured ALSA device
    cmd = sub_calls["cmd"]
    assert cmd[0] == "mpg123"
    assert "-a" in cmd
    alsa_idx = cmd.index("-a")
    assert cmd[alsa_idx + 1] == "plughw:CARD=Device"
    # last arg should be the tempfile path ending in .mp3
    assert cmd[-1].endswith(".mp3")
