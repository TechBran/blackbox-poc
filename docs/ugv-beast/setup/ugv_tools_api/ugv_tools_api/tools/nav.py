"""Navigation tools: thin wrapper around the Nav2 navigate_to_pose action."""
import asyncio
import math
import threading
import time
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose, ComputePathToPose
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

_state = {"status": "idle", "distance_remaining": None, "handle": None, "client": None}
_lock = threading.Lock()
_plan_client: ActionClient | None = None
_plan_lock = threading.Lock()


def _client() -> ActionClient:
    with _lock:
        if _state["client"] is None:
            _state["client"] = ActionClient(
                RosBridge.instance().node, NavigateToPose, "navigate_to_pose"
            )
        return _state["client"]


def _plan_action_client() -> ActionClient:
    global _plan_client
    with _plan_lock:
        if _plan_client is None:
            _plan_client = ActionClient(
                RosBridge.instance().node, ComputePathToPose, "compute_path_to_pose"
            )
        return _plan_client


@tool(
    name="nav_goto_point",
    description="Navigate to an (x, y, yaw) pose in the map frame using Nav2.",
    parameters={
        "x": ParamSchema(type="number", description="Map-frame x in meters."),
        "y": ParamSchema(type="number", description="Map-frame y in meters."),
        "yaw_deg": ParamSchema(type="number", default=0.0, minimum=-180, maximum=180,
                               description="Goal heading in degrees."),
    },
    required=["x", "y"],
)
async def nav_goto_point(x: float, y: float, yaw_deg: float = 0.0):
    ac = _client()
    if not ac.wait_for_server(timeout_sec=3.0):
        return {"error": "Nav2 action server not available"}
    g = NavigateToPose.Goal()
    g.pose.header.frame_id = "map"
    g.pose.header.stamp = RosBridge.instance().node.get_clock().now().to_msg()
    g.pose.pose.position.x = float(x)
    g.pose.pose.position.y = float(y)
    yaw = math.radians(float(yaw_deg))
    g.pose.pose.orientation.z = math.sin(yaw / 2)
    g.pose.pose.orientation.w = math.cos(yaw / 2)

    def _feedback(fb):
        with _lock:
            _state["distance_remaining"] = fb.feedback.distance_remaining

    send_fut = ac.send_goal_async(g, feedback_callback=_feedback)
    t0 = time.time()
    while not send_fut.done() and time.time() - t0 < 3.0:
        time.sleep(0.05)
    if not send_fut.done():
        return {"error": "Timed out sending goal"}
    handle = send_fut.result()
    if not (handle and handle.accepted):
        rejection = {"error": "Nav2 rejected goal"}
        status_val = getattr(handle, "status", None) if handle is not None else None
        if status_val is not None:
            rejection["goal_handle_status"] = int(status_val)
        return rejection
    with _lock:
        _state["status"] = "navigating"
        _state["handle"] = handle

    def _monitor(h):
        fut = h.get_result_async()
        while not fut.done():
            time.sleep(0.2)
        r = fut.result()
        status_map = {4: "succeeded", 5: "canceled", 6: "aborted"}
        final_status = status_map.get(r.status, f"ended_{r.status}")
        # Nav2 >= humble surfaces error_code / error_msg on the result payload.
        result_msg = getattr(r, "result", None)
        err_code = getattr(result_msg, "error_code", None)
        err_msg = getattr(result_msg, "error_msg", None)
        with _lock:
            _state["status"] = final_status
            _state["handle"] = None
            if err_code is not None:
                _state["last_error_code"] = int(err_code)
            if err_msg:
                _state["last_error_msg"] = str(err_msg)

    threading.Thread(target=_monitor, args=(handle,), daemon=True).start()
    return {"accepted": True, "goal": {"x": x, "y": y, "yaw_deg": yaw_deg}}


@tool(name="nav_cancel", description="Cancel the currently active Nav2 goal.")
async def nav_cancel():
    with _lock:
        h = _state["handle"]
    if h is None:
        return {"canceled": False, "reason": "no active goal"}
    h.cancel_goal_async()
    return {"canceled": True}


@tool(name="nav_status", description="Get the current Nav2 goal status and distance remaining.")
async def nav_status():
    with _lock:
        return {"status": _state["status"],
                "distance_remaining_m": _state["distance_remaining"]}


@tool(
    name="nav_plan_preview",
    description=(
        "Test whether Nav2 can compute a path from the current pose to (x, y) in the map "
        "frame, WITHOUT starting navigation. Use this before nav_goto_point on any non-trivial "
        "goal to avoid wasted attempts. Returns feasible=true if a path exists, with its length "
        "in meters. If feasible=false, the reason string explains why (e.g., 'goal in lethal "
        "zone', 'no path found within time limit', 'goal outside costmap')."
    ),
    parameters={
        "x": ParamSchema(type="number", description="Goal X in map frame meters."),
        "y": ParamSchema(type="number", description="Goal Y in map frame meters."),
    },
    required=["x", "y"],
)
async def nav_plan_preview(x: float, y: float):
    ac = _plan_action_client()
    if not ac.wait_for_server(timeout_sec=3.0):
        return {
            "feasible": False,
            "path_length_m": 0.0,
            "num_poses": 0,
            "reason": "compute_path_to_pose action server not available",
        }

    goal = ComputePathToPose.Goal()
    goal.goal.header.frame_id = "map"
    goal.goal.header.stamp = RosBridge.instance().node.get_clock().now().to_msg()
    goal.goal.pose.position.x = float(x)
    goal.goal.pose.position.y = float(y)
    goal.goal.pose.orientation.w = 1.0
    try:
        goal.planner_id = "GridBased"
    except Exception:
        pass
    try:
        goal.use_start = False
    except Exception:
        pass

    def _run() -> dict:
        send_fut = ac.send_goal_async(goal)
        t0 = time.time()
        while not send_fut.done() and time.time() - t0 < 3.0:
            time.sleep(0.05)
        if not send_fut.done():
            return {
                "feasible": False,
                "path_length_m": 0.0,
                "num_poses": 0,
                "reason": "timed out sending planner goal",
            }
        handle = send_fut.result()
        if not (handle and handle.accepted):
            status_val = getattr(handle, "status", None) if handle is not None else None
            reason = "planner rejected goal"
            if status_val is not None:
                reason = f"{reason} (goal_handle_status={int(status_val)})"
            return {
                "feasible": False,
                "path_length_m": 0.0,
                "num_poses": 0,
                "reason": reason,
            }

        result_fut = handle.get_result_async()
        t1 = time.time()
        # Planner max is ~10 s; give a small buffer before we give up.
        while not result_fut.done() and time.time() - t1 < 12.0:
            time.sleep(0.1)
        if not result_fut.done():
            return {
                "feasible": False,
                "path_length_m": 0.0,
                "num_poses": 0,
                "reason": "no path found within time limit",
            }

        r = result_fut.result()
        result_msg = getattr(r, "result", None)
        err_code = getattr(result_msg, "error_code", 0) or 0
        err_msg = getattr(result_msg, "error_msg", "") or ""
        path = getattr(result_msg, "path", None)
        poses = list(getattr(path, "poses", []) or []) if path is not None else []

        if err_code or not poses:
            reason = err_msg or f"planner returned no path (error_code={int(err_code)})"
            return {
                "feasible": False,
                "path_length_m": 0.0,
                "num_poses": len(poses),
                "reason": reason,
            }

        length = 0.0
        prev = None
        for ps in poses:
            p = ps.pose.position
            if prev is not None:
                length += math.hypot(p.x - prev[0], p.y - prev[1])
            prev = (p.x, p.y)

        return {
            "feasible": True,
            "path_length_m": round(float(length), 3),
            "num_poses": len(poses),
            "reason": "ok",
        }

    return await asyncio.to_thread(_run)
