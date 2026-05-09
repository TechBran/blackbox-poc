"""Async handlers for the supervisor's tool surface.

Each handler is a pure async function: it takes the config, optional state
(MissionTracker for dispatch/cancel correlation), and tool-specific args,
and returns a JSON-serializable dict. The session.py module knows nothing
about what these tools do — it just maps tool_call.name to a handler.

Kept separate from tool_declarations so the schemas can be reused (e.g.
BlackBox integration) without importing runtime deps.

Handlers NEVER raise over HTTP errors. They return a dict that MAY include
an 'error' key when downstream services are unhealthy, so Task 8's session
controller can pass the result straight to Gemini without try/except
scaffolding around every dispatch. Gemini sees the error dict and can
narrate the failure to the operator truthfully instead of hallucinating
success.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import httpx

from .config import SupervisorConfig


# Type alias for the realtime-input push callable threaded down from
# session.py. Same shape session.py passes to WatchStream as send_video:
# `async def(jpeg_bytes, mime_type) -> None`. Defining it here keeps the
# handler signature self-documenting; importing it from session.py would
# create a circular dep.
SendVideo = Callable[[bytes, str], Awaitable[None]]


@dataclass
class MissionTracker:
    """Holds the in-flight ER mission id across turns.

    ER only runs one mission at a time. We track the id so cancel and
    status tools have a target, and so dispatch can refuse to stomp an
    active mission unless replace_current=True.

    Single-writer by design: one supervisor process, one live Gemini
    session, strict turn-by-turn dispatch. Direct mutation by handlers
    is intentional; no lock or async context needed at current scope.
    """
    active_id: Optional[str] = None


def _timeout() -> httpx.Timeout:
    # 5s covers slow nav_status queries during busy periods without hanging
    # the supervisor's turn. Per-call AsyncClient (not a shared long-lived
    # one) — simpler cleanup semantics on crash/cancel. Localhost setup
    # overhead is negligible vs. Gemini Live's 1-2 tool calls per minute
    # cadence, so the simplicity wins.
    return httpx.Timeout(5.0)


async def _post(url: str, payload: dict) -> dict:
    """POST JSON; return parsed body, or an {'error': ...} dict on failure.

    Three failure classes surface as structured errors (never raised):
      - transport (connection refused, timeout): {'error': 'transport: ...'}
      - non-2xx status: {'error': 'http NNN', 'body': '<first 500 chars>'}
      - non-JSON body: {'error': 'non-json response', 'body': '<first 500>'}
    """
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as c:
            r = await c.post(url, json=payload)
    except httpx.HTTPError as e:
        return {"error": f"transport: {type(e).__name__}: {e}"}
    if r.status_code >= 400:
        return {"error": f"http {r.status_code}", "body": r.text[:500]}
    try:
        return r.json()
    except ValueError:
        return {"error": "non-json response", "body": r.text[:500]}


async def _get(url: str) -> dict:
    """GET; same error-dict contract as _post."""
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        return {"error": f"transport: {type(e).__name__}: {e}"}
    if r.status_code >= 400:
        return {"error": f"http {r.status_code}", "body": r.text[:500]}
    try:
        return r.json()
    except ValueError:
        return {"error": "non-json response", "body": r.text[:500]}


async def exec_get_robot_state(cfg: SupervisorConfig) -> dict:
    # Inline gather (not via _post helper) to share one client across two
    # parallel requests. Keep as one-off until a second gather-site appears.
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        try:
            odom, lidar = await asyncio.gather(
                c.post(f"{cfg.tools_api_url}/tool/status_get_odom", json={}),
                c.post(f"{cfg.tools_api_url}/tool/status_get_lidar_summary", json={}),
                return_exceptions=True,
            )
        except Exception as e:
            return {"error": f"transport: {type(e).__name__}: {e}"}

    def _parse(r) -> dict:
        if isinstance(r, Exception):
            return {"error": f"transport: {type(r).__name__}: {r}"}
        if r.status_code >= 400:
            return {"error": f"http {r.status_code}", "body": r.text[:500]}
        try:
            return r.json().get("result", {})
        except ValueError:
            return {"error": "non-json response", "body": r.text[:500]}

    odom_out = _parse(odom)
    lidar_out = _parse(lidar)
    # Either side failing collapses to a single error at the top level so
    # Gemini gets a clean signal rather than half a state reading.
    if "error" in odom_out:
        return {"error": f"get_robot_state odom: {odom_out['error']}"}
    if "error" in lidar_out:
        return {"error": f"get_robot_state lidar: {lidar_out['error']}"}
    return {"odom": odom_out, "lidar": lidar_out}


async def exec_get_camera_view(cam: Any, send_video: SendVideo) -> dict:
    """Push a fresh pantilt JPEG into the realtime_input perception channel.

    Why not return the bytes as a function_response (the original design)?
    Gemini Live treats inline_data inside a function_response as data-to-
    acknowledge ("I received an image"), not perception-to-reason-about.
    That produces a documented two-prompt lag: "what do you see?" → "I
    received an image" → "now describe it" → actual description.

    The fix is to push the JPEG down the SAME realtime_input channel that
    WatchStream uses for ambient frames. Gemini Live's vision encoder
    processes that channel natively, on the same turn — no acknowledgement
    round-trip. The function_response we return is a tiny JSON ack so the
    model still gets a synchronous "tool succeeded" signal but the bytes
    travel a path the encoder actually consumes.

    Args:
        cam: The supervisor's RosCamera (or duck-type with the same
            ``get_camera_jpeg()``+optional ``get_camera_age_s()`` shape).
        send_video: Same callable session.py passes to WatchStream —
            ``async def(jpeg, mime_type)`` that wraps
            ``session.send_realtime_input(video=..., mime_type=...)``.

    Returns:
        On success: ``{"ok": True, "pushed_realtime_input": True,
            "size_bytes": n, "age_s": n_or_None}``.
        On no-frame-cached: ``{"error": "no camera frame available"}`` —
            and send_video is NOT invoked. Pushing zero-byte garbage to the
            realtime channel would corrupt the perception stream.
    """
    jpeg = cam.get_camera_jpeg()
    if not jpeg:
        return {"error": "no camera frame available"}
    await send_video(jpeg, "image/jpeg")
    # Age is informational; Gemini may use it to judge whether the frame
    # is fresh enough for the question. cam may or may not expose it
    # (older fakes / future variants); degrade gracefully.
    age_s: Optional[float] = None
    age_getter = getattr(cam, "get_camera_age_s", None)
    if callable(age_getter):
        try:
            age_s = age_getter()
        except Exception:
            age_s = None
    return {
        "ok": True,
        "pushed_realtime_input": True,
        "size_bytes": len(jpeg),
        "age_s": age_s,
    }


async def exec_get_slam_map_view(cam: Any, send_video: SendVideo) -> dict:
    """Push a top-down SLAM map (with robot marker) to the realtime channel.

    Mirrors exec_get_camera_view's signature so the dispatch site in
    session.py looks symmetric: same `cam`, same `send_video`. Caches
    nothing — the model's choice to call this tool is exactly the
    cadence we want; an LRU cache here would just hide a stale map.

    Args:
        cam: RosCamera (or duck-typed equivalent) exposing
            ``get_slam_map_msg()`` and ``get_robot_pose()``.
        send_video: Same realtime_input push callable threaded into
            WatchStream by session.py — ``async def(png, mime_type)``.

    Returns:
        On success: ``{"ok": True, "pushed_realtime_input": True,
            "map_size_m": [W, H], "robot_at": [x, y], "size_bytes": n}``.
        On no-map-cached: ``{"error": "no slam map yet — has SLAM been
            initialized?"}`` and send_video is NOT invoked. Pushing
            empty bytes to the realtime channel would corrupt the
            perception stream.

    Pose handling: a missing /robot_pose (EKF cold-start) defaults to
    the map origin (0, 0, 0). The map itself still renders — operator
    can read cross-room layout even before the EKF has settled.
    """
    msg = cam.get_slam_map_msg()
    if msg is None:
        return {"error": "no slam map yet — has SLAM been initialized?"}
    pose = cam.get_robot_pose() or (0.0, 0.0, 0.0)
    # Local import: keeps module load light when the SLAM tool isn't
    # called, and preserves the er/<-> supervisor module boundary —
    # er.sensors imports nothing from supervisor, so this lazy direction
    # stays clean.
    from ..er.sensors import rasterize_slam_map
    png = rasterize_slam_map(msg, pose[0], pose[1], pose[2], max_size_px=512)
    await send_video(png, "image/png")
    return {
        "ok": True,
        "pushed_realtime_input": True,
        "map_size_m": [
            round(msg.info.width * msg.info.resolution, 2),
            round(msg.info.height * msg.info.resolution, 2),
        ],
        "robot_at": [round(pose[0], 2), round(pose[1], 2)],
        "size_bytes": len(png),
    }


async def exec_dispatch_er_mission(
    cfg: SupervisorConfig, tracker: MissionTracker,
    *, mission: str, replace_current: bool = False,
) -> dict:
    """Dispatch a mission to ER. Refuse-to-stomp semantics by default.

    If a mission is already active and replace_current is False, returns
    an error telling the operator to opt-in. If replace_current is True,
    the prior mission is aborted FIRST — and we refuse to dispatch the
    new one if the abort fails (otherwise we could have two concurrent
    missions running, with undefined ER behavior).
    """
    if tracker.active_id and not replace_current:
        return {"error": "a mission is already active; set replace_current=true to override"}
    if tracker.active_id and replace_current:
        abort_result = await _post(f"{cfg.er_url}/mission/{tracker.active_id}/abort", {})
        if "error" in abort_result:
            return {"error": (
                f"failed to abort prior mission ({tracker.active_id}); "
                f"new mission not dispatched: {abort_result['error']}"
            )}
        tracker.active_id = None
    body = await _post(f"{cfg.er_url}/mission", {"operator": "supervisor", "mission": mission})
    if "error" in body:
        return body
    mid = body.get("mission_id")
    if mid:
        tracker.active_id = mid
    return {"mission_id": mid, "status": body.get("status", "unknown")}


async def exec_cancel_er_mission(
    cfg: SupervisorConfig, tracker: MissionTracker, *, reason: str = "",
) -> dict:
    """Abort the active mission. On failure, PRESERVE active_id.

    If the abort POST fails, we can't know whether the mission is actually
    dead, so we keep the tracker state so get_er_mission_status still
    points at the right id and the operator can retry or investigate.
    """
    if not tracker.active_id:
        return {"error": "no active mission"}
    body = await _post(
        f"{cfg.er_url}/mission/{tracker.active_id}/abort", {"reason": reason}
    )
    if "error" in body:
        return {
            "error": f"abort failed, mission {tracker.active_id} may still be active: {body['error']}",
            "mission_id": tracker.active_id,
        }
    tracker.active_id = None
    return body


async def exec_get_er_mission_status(
    cfg: SupervisorConfig, tracker: MissionTracker,
) -> dict:
    if not tracker.active_id:
        return {"status": "idle"}
    return await _get(f"{cfg.er_url}/mission/{tracker.active_id}")


async def exec_emergency_stop(cfg: SupervisorConfig) -> dict:
    return await _post(f"{cfg.tools_api_url}/tool/system_emergency_stop", {})


async def exec_lights(cfg: SupervisorConfig, *, on: bool, which: str = "both") -> dict:
    # Single handler; Task 8 dispatches both lights_on and lights_off tool
    # names here by translating name -> bool at the dispatch table.
    tool = "ugv_lights_on" if on else "ugv_lights_off"
    return await _post(f"{cfg.tools_api_url}/tool/{tool}", {"which": which})


async def exec_gimbal_look_at(cfg: SupervisorConfig, *, pan_deg: float, tilt_deg: float) -> dict:
    return await _post(
        f"{cfg.tools_api_url}/tool/gimbal_look_at",
        {"pan_deg": pan_deg, "tilt_deg": tilt_deg, "speed": 100},
    )


async def exec_set_watch_mode(
    *, on: bool, source: str = "pantilt", fps: float | None = None,
) -> dict:
    """Echo the requested state plus optional FPS retune.

    session.py reads this dict to flip WatchStream.set(on=...) and, when
    fps is present, WatchStream.set_period(fps). Operator-tunable only:
    we clamp to [0.1, 1.0] so the model can't accidentally push beyond
    Google's 1 FPS ceiling or stall the loop with a near-zero FPS.

    Kept as a separate handler for symmetry with the other tool dispatch
    branches in session.py.
    """
    out: dict = {"watch_on": bool(on), "source": source}
    if fps is not None:
        clamped = max(0.1, min(1.0, float(fps)))
        out["fps"] = clamped
    return out
