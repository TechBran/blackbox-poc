"""Auto-exploration tools — wraps the ugv_explore orchestrator node.

The ugv_explore_node (Python) runs in the container and exposes:
    Service  /explore/start  (std_srvs/Trigger)
    Service  /explore/stop   (std_srvs/Trigger)
    Topic    /explore/status (std_msgs/String, JSON payload)

It stays IDLE until /explore/start is called, then frontier-detects on /map and
fires Nav2 goals until all reachable frontiers are cleared. This module flips
its state and reports status — it does NOT do frontier logic itself.

Node is launched by start_waveshare.sh at container boot (IDLE, no CPU cost).
"""
import asyncio
import json
import time
from typing import Any

from std_srvs.srv import Trigger

from ..registry import tool
from ..ros_bridge import RosBridge


async def _call_trigger(srv_name: str, timeout_s: float = 8.0) -> dict[str, Any]:
    node = RosBridge.instance().node
    cli = node.create_client(Trigger, srv_name)
    try:
        if not cli.wait_for_service(timeout_sec=3.0):
            return {"ok": False, "error": f"service {srv_name} not available (is ugv_explore_node running?)"}
        req = Trigger.Request()
        fut = cli.call_async(req)
        t0 = time.time()
        while not fut.done() and (time.time() - t0) < timeout_s:
            await asyncio.sleep(0.05)
        if not fut.done():
            return {"ok": False, "error": f"service {srv_name} timed out after {timeout_s}s"}
        res = fut.result()
        return {"ok": bool(res.success), "message": res.message}
    finally:
        try:
            node.destroy_client(cli)
        except Exception:
            pass


@tool(
    name="ugv_explore_start",
    description=(
        "Start autonomous frontier exploration. The robot will detect unmapped areas "
        "on its global map, plan Nav2 goals to the most promising frontier, drive "
        "there, and repeat until all reachable frontiers are cleared (map is complete). "
        "Use this for mapping missions like 'explore the room', 'map this floor', or "
        "'find unknown areas'. Do NOT use for targeted goals like 'go to the kitchen' "
        "— use nav_goto_point instead. Once started, you can poll ugv_explore_status "
        "to check progress, or ugv_explore_stop to halt. Mission_done should be called "
        "when exploration reports state='IDLE' (frontiers cleared) or operator-requested."
    ),
)
async def ugv_explore_start():
    return await _call_trigger("/explore/start")


@tool(
    name="ugv_explore_stop",
    description=(
        "Stop autonomous frontier exploration. Cancels any in-flight Nav2 goal and "
        "returns the explorer to IDLE. Use when the operator asks to stop, when you've "
        "gathered enough data, or when you decide exploration should end."
    ),
)
async def ugv_explore_stop():
    return await _call_trigger("/explore/stop")


@tool(
    name="ugv_explore_status",
    description=(
        "Read the auto-explore orchestrator's current state. Returns JSON with: "
        "state (IDLE|EXPLORING|RETURNING|SAVING), elapsed_s, frontiers_found, "
        "goals_sent. Poll this during an exploration mission to know when the map "
        "is complete (state returns to IDLE after EXPLORING)."
    ),
)
async def ugv_explore_status():
    cached = RosBridge.instance().node.get_latest("/explore/status")
    if cached is None:
        return {"state": "UNKNOWN", "note": "no status message received yet (node may be idle or not running)"}
    _, msg = cached
    raw = getattr(msg, "data", "") or ""
    try:
        return json.loads(raw)
    except Exception:
        return {"state": "UNKNOWN", "raw": raw[:500]}
