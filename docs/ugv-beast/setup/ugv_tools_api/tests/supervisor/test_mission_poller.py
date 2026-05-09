"""Mission progress streamer: cadence, state transitions, terminal handling."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from ugv_tools_api.supervisor.mission_poller import MissionPoller, PollerCallbacks
from ugv_tools_api.supervisor.config import SupervisorConfig


@pytest.fixture
def cfg():
    return SupervisorConfig(
        google_api_key="x", model="m", voice="v", language_code="en-US",
        mic_device="m", spk_device="s",
        tools_api_url="http://t", er_url="http://e",
        camera_topic="/c", costmap_topic="/m",
        handle_store_path="/tmp/h",
    )


@pytest.mark.asyncio
async def test_silent_ticks_at_1hz_during_active(cfg):
    sent = []
    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=lambda body: sent.append(("silent", body))),
        send_when_idle=AsyncMock(side_effect=lambda body: sent.append(("when_idle", body))),
        send_terminal=AsyncMock(side_effect=lambda body: sent.append(("terminal", body))),
    )
    states = ["active", "active", "active", "completed"]
    pose = {"x": 1.0, "y": 2.0, "yaw": 0.5}

    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status",
               new=AsyncMock(side_effect=[
                   {"status": s, "events": [], "distance_to_goal": 0.3} for s in states
               ])), \
         patch("ugv_tools_api.supervisor.mission_poller._fetch_pose",
               new=AsyncMock(return_value=pose)):
        p = MissionPoller(cfg, fc_id="call-1", fc_name="dispatch_er_mission",
                          mission_id="m-1", callbacks=cb, tick_s=0.05)
        await p.run()
    silent = [s for s in sent if s[0] == "silent"]
    terminal = [s for s in sent if s[0] == "terminal"]
    assert len(silent) >= 3
    assert len(terminal) == 1
    assert terminal[0][1]["mission_status"] == "completed"


@pytest.mark.asyncio
async def test_when_idle_on_state_transition(cfg):
    sent = []
    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=lambda body: sent.append(("silent", body))),
        send_when_idle=AsyncMock(side_effect=lambda body: sent.append(("when_idle", body))),
        send_terminal=AsyncMock(side_effect=lambda body: sent.append(("terminal", body))),
    )
    states = ["pending", "active", "active", "completed"]
    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status",
               new=AsyncMock(side_effect=[
                   {"status": s, "events": [], "distance_to_goal": None} for s in states
               ])), \
         patch("ugv_tools_api.supervisor.mission_poller._fetch_pose",
               new=AsyncMock(return_value={"x":0,"y":0,"yaw":0})):
        p = MissionPoller(cfg, fc_id="call-2", fc_name="dispatch_er_mission",
                          mission_id="m-2", callbacks=cb, tick_s=0.05)
        await p.run()
    when_idle = [s for s in sent if s[0] == "when_idle"]
    transitions = [body for (_, body) in when_idle if body.get("event") == "state_transition"]
    assert any(t["from"] == "pending" and t["to"] == "active" for t in transitions)


@pytest.mark.asyncio
async def test_cancel_stops_poller_quickly(cfg):
    sent = []
    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=lambda body: sent.append(("silent", body))),
        send_when_idle=AsyncMock(side_effect=lambda body: sent.append(("when_idle", body))),
        send_terminal=AsyncMock(side_effect=lambda body: sent.append(("terminal", body))),
    )
    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status",
               new=AsyncMock(return_value={"status": "active", "events": [], "distance_to_goal": 0.5})), \
         patch("ugv_tools_api.supervisor.mission_poller._fetch_pose",
               new=AsyncMock(return_value={"x":0,"y":0,"yaw":0})):
        p = MissionPoller(cfg, fc_id="call-3", fc_name="dispatch_er_mission",
                          mission_id="m-3", callbacks=cb, tick_s=0.02)
        task = asyncio.create_task(p.run())
        await asyncio.sleep(0.1)
        p.cancel()
        await asyncio.wait_for(task, timeout=1.0)
    assert any(s[0] == "terminal" and s[1]["mission_status"] == "cancelled" for s in sent)


@pytest.mark.asyncio
async def test_exception_in_callback_emits_failed_terminal(cfg):
    """If a callback raises, the poller should still emit a failed terminal,
    not silently die leaving the model waiting forever."""
    sent = []
    raise_first = [True]
    async def boom_then_record(body):
        if raise_first[0]:
            raise_first[0] = False
            raise RuntimeError("synthetic test failure")
        sent.append(("terminal", body))

    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=boom_then_record),  # first silent raises
        send_when_idle=AsyncMock(),
        send_terminal=AsyncMock(side_effect=boom_then_record),
    )
    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status",
               new=AsyncMock(return_value={"status": "active", "events": [], "distance_to_goal": None})), \
         patch("ugv_tools_api.supervisor.mission_poller._fetch_pose",
               new=AsyncMock(return_value=None)):
        p = MissionPoller(cfg, fc_id="call-4", fc_name="dispatch_er_mission",
                          mission_id="m-4", callbacks=cb, tick_s=0.02)
        await p.run()
    # The exception during send_silent should hit the outer except,
    # which calls send_terminal with mission_status=failed.
    failed = [s for s in sent if s[1].get("mission_status") == "failed"]
    assert len(failed) == 1
