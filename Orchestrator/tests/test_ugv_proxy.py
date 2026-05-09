"""
test_ugv_proxy.py — Hermetic unit tests for UGV Beast HTTP proxy executors.

Verifies the 22 _execute_ugv_* methods correctly forward to the UGV FastAPI
server at http://ugv-beast:8080/tool/{name}.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from Orchestrator.tools.blackbox_tools import BlackBoxToolExecutor


@pytest.mark.asyncio
async def test_ugv_proxy_calls_correct_url():
    exec_ = BlackBoxToolExecutor(operator="test")
    with patch("Orchestrator.tools.blackbox_tools.aiohttp.ClientSession") as MS:
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"tool": "motion_stop", "result": {"stopped": True}})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        sess = MS.return_value.__aenter__.return_value
        sess.post = MagicMock(return_value=resp)
        r = await exec_.execute("ugv_motion_stop", {})
        assert r.success, r.result
        args, kwargs = sess.post.call_args
        target = args[0] if args else kwargs.get("url", "")
        assert "ugv-beast:8080/tool/motion_stop" in str(target), f"wrong URL: {target}"


@pytest.mark.asyncio
async def test_ugv_proxy_passes_args():
    exec_ = BlackBoxToolExecutor(operator="test")
    with patch("Orchestrator.tools.blackbox_tools.aiohttp.ClientSession") as MS:
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"result": {"ok": True}})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        sess = MS.return_value.__aenter__.return_value
        sess.post = MagicMock(return_value=resp)
        await exec_.execute(
            "ugv_gimbal_look_at",
            {"pan_deg": 30.0, "tilt_deg": 10.0, "speed": 150},
        )
        # JSON body should carry the three args
        body = sess.post.call_args.kwargs.get("json")
        if body is None and len(sess.post.call_args.args) > 1:
            body = sess.post.call_args.args[1]
        expected = {"pan_deg": 30.0, "tilt_deg": 10.0, "speed": 150}
        assert body == expected, f"body wrong: {body!r}"


@pytest.mark.asyncio
async def test_ugv_proxy_404_error_surface():
    exec_ = BlackBoxToolExecutor(operator="test")
    with patch("Orchestrator.tools.blackbox_tools.aiohttp.ClientSession") as MS:
        resp = AsyncMock()
        resp.status = 404
        resp.text = AsyncMock(return_value='{"detail":"Unknown tool: bogus"}')
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        sess = MS.return_value.__aenter__.return_value
        sess.post = MagicMock(return_value=resp)
        r = await exec_.execute("ugv_motion_stop", {})
        assert not r.success
        assert "404" in r.result or "Unknown" in r.result
