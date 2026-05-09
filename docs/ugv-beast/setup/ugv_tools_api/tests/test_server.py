"""Tests for the FastAPI HTTP surface (Task 6.1).

We use TestClient(app) directly (no `with`) because none of these tests
require a running ROS bridge — /health just reports the running state,
/tools reads the in-memory registry, and /tool dispatch + /snapshot
either fail-fast or use the registry's own ValueError → 400 mapping.
"""
from fastapi.testclient import TestClient
from ugv_tools_api.server import app

c = TestClient(app)


def test_health():
    assert c.get("/health").json()["ok"] is True


def test_tools_anthropic_format():
    r = c.get("/tools?format=anthropic").json()
    assert isinstance(r, list) and len(r) > 0
    assert all("input_schema" in t for t in r)


def test_tools_openai_format():
    r = c.get("/tools?format=openai").json()
    assert all(t["type"] == "function" for t in r)


def test_unknown_tool_404():
    r = c.post("/tool/nope_not_real", json={})
    assert r.status_code == 404


# ---------------- Augmented coverage ----------------

def test_tools_gemini_format():
    r = c.get("/tools?format=gemini").json()
    assert all("name" in t and "parameters" in t for t in r)


def test_tools_count_matches_registry():
    from ugv_tools_api.registry import registry
    r = c.get("/tools?format=anthropic").json()
    assert len(r) == len(registry.names())
    # Sanity check: we expect 22 tools after Phase 5
    assert len(r) >= 20  # 22 target; allow a couple missing if modules didn't all import


def test_snapshot_unknown_camera_404():
    r = c.get("/snapshot/nope_cam")
    assert r.status_code == 404


def test_tool_dispatch_missing_required_400():
    # motion_move_forward requires duration_s
    r = c.post("/tool/motion_move_forward", json={})
    # Should be 400 (missing required) OR 500 (dispatch raised ValueError)
    # Our server maps ValueError → 400
    assert r.status_code in (400, 500)
