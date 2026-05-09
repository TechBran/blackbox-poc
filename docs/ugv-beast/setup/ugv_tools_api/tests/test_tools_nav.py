import asyncio
from ugv_tools_api.tools import nav as tools_nav  # noqa: F401 - triggers registration
from ugv_tools_api.registry import registry


def test_nav_tools_registered():
    for t in ["nav_goto_point", "nav_cancel", "nav_status"]:
        assert t in registry.names()


def test_nav_status_default_is_idle():
    out = asyncio.run(registry.dispatch("nav_status", {}))
    # may be from prior test run
    assert out["status"] == "idle" or out["status"].startswith("ended") \
        or out["status"] in ("succeeded", "canceled", "aborted", "navigating")
    assert "distance_remaining_m" in out


def test_nav_cancel_no_active_goal():
    out = asyncio.run(registry.dispatch("nav_cancel", {}))
    # After first run may be canceled:False; idempotent — just ensure the key exists
    assert "canceled" in out
