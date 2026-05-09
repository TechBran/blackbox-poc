import pytest
from fastapi.testclient import TestClient


def _client():
    from Orchestrator.app import app
    return TestClient(app)


def test_list_sessions_empty_returns_empty_list():
    c = _client()
    r = c.get("/cli-agent/sessions", params={"op": "TestOpZzz"})
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_kill_nonexistent_session_returns_idempotent():
    c = _client()
    r = c.delete("/cli-agent/sessions/cli-agent-TestOpZzz__claude__fake")
    assert r.status_code == 200
    body = r.json()
    assert body == {"killed": False, "reason": "not-found"}


def test_kill_invalid_session_id_returns_400():
    c = _client()
    r = c.delete("/cli-agent/sessions/not-a-cli-agent-name")
    assert r.status_code == 400
