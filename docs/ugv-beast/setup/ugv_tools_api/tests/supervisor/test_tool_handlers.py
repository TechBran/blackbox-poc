import pytest
from unittest.mock import AsyncMock, patch

from ugv_tools_api.supervisor.tool_handlers import (
    exec_get_robot_state,
    exec_dispatch_er_mission,
    exec_cancel_er_mission,
    exec_get_er_mission_status,
    exec_emergency_stop,
    exec_lights,
    exec_gimbal_look_at,
    MissionTracker,
)


# Patch at the import site (ugv_tools_api.supervisor.tool_handlers.httpx.AsyncClient)
# rather than module-level (httpx.AsyncClient). Narrower scope prevents ghost
# interference with other tests that might import httpx directly.
PATCH_TARGET = "ugv_tools_api.supervisor.tool_handlers.httpx.AsyncClient"


def _mock_ok(status: int = 200, payload=None):
    """Build an AsyncMock response with sync json() and requested status.

    Note: httpx response.json() is sync, so json= is a plain lambda, NOT
    an AsyncMock(return_value=...). Easy to misread.
    """
    m = AsyncMock()
    m.status_code = status
    m.json = lambda: (payload if payload is not None else {})
    m.text = ""
    return m


def _mock_err(status: int, body: str = "boom"):
    m = AsyncMock()
    m.status_code = status
    m.text = body
    m.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    return m


@pytest.fixture
def cfg():
    from ugv_tools_api.supervisor.config import SupervisorConfig
    return SupervisorConfig(
        google_api_key="x", model="m", voice="v", language_code="en-US",
        mic_device="", spk_device="", tools_api_url="http://tools",
        er_url="http://er", camera_topic="", costmap_topic="",
        handle_store_path="",
    )


# ── Happy paths ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_robot_state_calls_both_endpoints(cfg):
    client = AsyncMock()
    client.post.side_effect = [
        _mock_ok(payload={"result": {"x": 1, "y": 2, "yaw_deg": 90}}),
        _mock_ok(payload={"result": {"sectors_m": {"front": 0.5}}}),
    ]
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_get_robot_state(cfg)
    assert res["odom"]["x"] == 1
    assert res["lidar"]["sectors_m"]["front"] == 0.5
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_er_mission_returns_id(cfg):
    tracker = MissionTracker()
    client = AsyncMock()
    client.post.return_value = _mock_ok(payload={"mission_id": "m-123", "status": "pending"})
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_dispatch_er_mission(cfg, tracker, mission="drive forward")
    assert res["mission_id"] == "m-123"
    assert tracker.active_id == "m-123"


@pytest.mark.asyncio
async def test_dispatch_without_replace_fails_if_active(cfg):
    tracker = MissionTracker()
    tracker.active_id = "prior"
    # No patch needed — guard short-circuits before any HTTP call.
    res = await exec_dispatch_er_mission(cfg, tracker, mission="x")
    assert "error" in res
    assert "active" in res["error"].lower()


@pytest.mark.asyncio
async def test_cancel_mission_clears_tracker(cfg):
    tracker = MissionTracker()
    tracker.active_id = "m-7"
    client = AsyncMock()
    client.post.return_value = _mock_ok(payload={"status": "aborted"})
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_cancel_er_mission(cfg, tracker, reason="test")
    assert res["status"] == "aborted"
    assert tracker.active_id is None


@pytest.mark.asyncio
async def test_emergency_stop_posts_to_tools_api(cfg):
    client = AsyncMock()
    client.post.return_value = _mock_ok(payload={"result": "ok"})
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_emergency_stop(cfg)
    args, _ = client.post.call_args
    assert args[0].endswith("/tool/system_emergency_stop")
    assert res["result"] == "ok"


# ── Error paths (new in the quality fix pass) ────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_replace_current_aborts_then_dispatches(cfg):
    tracker = MissionTracker()
    tracker.active_id = "old-id"
    client = AsyncMock()
    # First call: abort prior (returns 200). Second call: create new.
    client.post.side_effect = [
        _mock_ok(payload={"status": "aborted"}),
        _mock_ok(payload={"mission_id": "new-id", "status": "pending"}),
    ]
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_dispatch_er_mission(
            cfg, tracker, mission="new mission", replace_current=True,
        )
    assert res["mission_id"] == "new-id"
    assert tracker.active_id == "new-id"
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_replace_current_refuses_when_abort_fails(cfg):
    """If abort fails, do NOT dispatch a new mission — avoids two concurrent missions."""
    tracker = MissionTracker()
    tracker.active_id = "old-id"
    client = AsyncMock()
    client.post.side_effect = [_mock_err(503, body="ER unavailable")]
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_dispatch_er_mission(
            cfg, tracker, mission="new mission", replace_current=True,
        )
    assert "error" in res
    assert "abort" in res["error"].lower() or "old-id" in res["error"]
    # tracker retains old-id since we don't know if it's really dead
    assert tracker.active_id == "old-id"
    # Only one POST was made (the abort). No dispatch follow-up.
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_cancel_preserves_id_on_failure(cfg):
    """If cancel fails, active_id MUST stay so operator can retry/investigate."""
    tracker = MissionTracker()
    tracker.active_id = "m-7"
    client = AsyncMock()
    client.post.return_value = _mock_err(500, body="ER crashed")
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_cancel_er_mission(cfg, tracker, reason="test")
    assert "error" in res
    assert res.get("mission_id") == "m-7"
    # Tracker NOT cleared
    assert tracker.active_id == "m-7"


@pytest.mark.asyncio
async def test_get_robot_state_surfaces_downstream_http_error(cfg):
    """5xx on either gathered endpoint collapses to a single error dict."""
    client = AsyncMock()
    client.post.side_effect = [
        _mock_ok(payload={"result": {"x": 1}}),
        _mock_err(503, body="lidar service down"),
    ]
    with patch(PATCH_TARGET) as client_cls:
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_get_robot_state(cfg)
    assert "error" in res
    assert "lidar" in res["error"].lower()


@pytest.mark.asyncio
async def test_emergency_stop_transport_error_returns_error_dict(cfg):
    """Transport failure (connection refused) returns error dict, does NOT raise."""
    import httpx
    with patch(PATCH_TARGET) as client_cls:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("connection refused")
        client_cls.return_value.__aenter__.return_value = client
        res = await exec_emergency_stop(cfg)
    assert "error" in res
    assert "transport" in res["error"].lower()


# ── set_watch_mode (T1: optional FPS override) ──────────────────────────

def test_exec_set_watch_mode_accepts_fps_and_clamps_range():
    """T1 of embodied-observer plan: operator can ask Gemini Live to
    speed up or slow down the ambient feed via fps=. Handler clamps
    to [0.1, 1.0] and echoes the (possibly clamped) value back so
    session.py knows what to push into WatchStream.set_period.

    Wrapped in asyncio.run() rather than @pytest.mark.asyncio because
    pytest-asyncio is an optional dev dep not present in the container's
    pinned environment (see pyproject.toml `[project.optional-dependencies]
    dev`). Sync wrapping keeps this test runnable without that plugin.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_set_watch_mode
    # Within range — passes through.
    res = asyncio.run(exec_set_watch_mode(on=True, source="pantilt", fps=0.5))
    assert res["watch_on"] is True
    assert res["source"] == "pantilt"
    assert res["fps"] == 0.5
    # Above the ceiling — clamped to 1.0.
    res = asyncio.run(exec_set_watch_mode(on=True, fps=2.5))
    assert res["fps"] == 1.0
    # Below the floor — clamped to 0.1.
    res = asyncio.run(exec_set_watch_mode(on=True, fps=0.01))
    assert res["fps"] == 0.1
    # fps omitted → not present (session.py leaves WatchStream._period alone).
    res = asyncio.run(exec_set_watch_mode(on=False))
    assert "fps" not in res or res.get("fps") is None


# ── get_camera_view (T2: realtime_input push, not function_response data) ──

class _FakeCam:
    """Minimal cam stub for exec_get_camera_view tests.

    Mirrors the shape relied on by exec_get_camera_view: get_camera_jpeg()
    returns bytes-or-None, and an optional get_camera_age_s() returns the
    cached frame's age in seconds (or None if untracked / no frame).
    """
    def __init__(self, jpeg=b"\xff\xd8\xff\xe0fake-jpeg-bytes", age_s: float = 0.5):
        self._jpeg = jpeg
        self._age_s = age_s

    def get_camera_jpeg(self):
        return self._jpeg

    def get_camera_age_s(self):
        return self._age_s


def test_exec_get_camera_view_pushes_via_realtime_input_and_returns_ack():
    """T2 of embodied-observer plan: get_camera_view stops returning the
    JPEG as a function_response (Gemini treats inline_data there as
    data-to-acknowledge, producing the two-prompt lag) and instead pushes
    the frame down the SAME realtime_input channel WatchStream uses.

    The handler must:
      1. Grab the latest pantilt JPEG from the camera.
      2. await send_video(jpeg, "image/jpeg") — same callable session.py
         passes to WatchStream — so Gemini's vision encoder reasons about
         it on the same turn.
      3. Return a small JSON ack (NOT the bytes) so the function_response
         is small and the perception path is the realtime channel.

    Sync-wrapped via asyncio.run() — see the set_watch_mode test above.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_get_camera_view

    cam = _FakeCam(jpeg=b"\xff\xd8\xff\xe0demo-jpeg", age_s=0.42)
    sent: list[tuple[bytes, str]] = []

    async def fake_send_video(jpeg: bytes, mime_type: str) -> None:
        sent.append((jpeg, mime_type))

    res = asyncio.run(exec_get_camera_view(cam, fake_send_video))

    # send_video was called exactly once, with the JPEG bytes + image/jpeg mime.
    assert len(sent) == 1, f"expected one realtime_input push, got {len(sent)}"
    assert sent[0] == (b"\xff\xd8\xff\xe0demo-jpeg", "image/jpeg")

    # Response shape: small ack, NOT the bytes.
    assert res.get("ok") is True
    assert res.get("pushed_realtime_input") is True
    assert res.get("size_bytes") == len(b"\xff\xd8\xff\xe0demo-jpeg")
    assert res.get("age_s") == 0.42
    # Critically: NO image bytes in the function_response payload.
    assert "image" not in res
    assert "jpeg" not in res
    assert "data" not in res


def test_exec_get_camera_view_no_frame_returns_error_and_does_not_push():
    """When the camera cache is empty (subscriber thread hasn't caught
    a frame yet, or topic unhealthy), the handler must return a clean
    error dict and NOT call send_video — pushing zero-byte garbage to
    Gemini's realtime channel would corrupt the perception stream.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_get_camera_view

    class _EmptyCam:
        def get_camera_jpeg(self):
            return None
        def get_camera_age_s(self):
            return None

    sent: list[tuple[bytes, str]] = []

    async def fake_send_video(jpeg: bytes, mime_type: str) -> None:
        sent.append((jpeg, mime_type))

    res = asyncio.run(exec_get_camera_view(_EmptyCam(), fake_send_video))

    assert "error" in res
    assert sent == [], "must NOT push when no frame is available"


def test_exec_get_camera_view_works_without_age_helper():
    """get_camera_age_s() is optional (older cam stubs / future cam variants
    may not have it). Handler must degrade gracefully: omit age_s or set
    it to None rather than raising AttributeError. The push must still
    happen and the ack shape must still be valid.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_get_camera_view

    class _CamWithoutAge:
        def get_camera_jpeg(self):
            return b"\xff\xd8\xff\xe0jpg"
        # NOTE: no get_camera_age_s

    sent: list[tuple[bytes, str]] = []

    async def fake_send_video(jpeg: bytes, mime_type: str) -> None:
        sent.append((jpeg, mime_type))

    res = asyncio.run(exec_get_camera_view(_CamWithoutAge(), fake_send_video))

    assert sent == [(b"\xff\xd8\xff\xe0jpg", "image/jpeg")]
    assert res["ok"] is True
    assert res["pushed_realtime_input"] is True
    assert res["size_bytes"] == len(b"\xff\xd8\xff\xe0jpg")
    # age_s missing or None — both acceptable.
    assert res.get("age_s") is None


# ── get_slam_map_view (T3: on-demand SLAM map → realtime_input) ─────────

def _fake_occupancy_grid(width=20, height=20, resolution=0.05,
                         origin_x=0.0, origin_y=0.0, data=None):
    """Build a duck-typed nav_msgs/OccupancyGrid for handler tests.

    Mirrors the shape the rasterizer reads: msg.info.{width,height,
    resolution,origin.position.{x,y}} + msg.data (bytes or list of int8).
    Default 20x20 grid full of free cells (zeros) at 5 cm resolution =
    1 m × 1 m map — keeps PNG output tiny while still exercising the
    rasterize path end-to-end (real PNG bytes, real header).
    """
    from unittest.mock import MagicMock
    msg = MagicMock()
    msg.info.width = width
    msg.info.height = height
    msg.info.resolution = resolution
    msg.info.origin.position.x = origin_x
    msg.info.origin.position.y = origin_y
    if data is None:
        data = bytes(width * height)  # all zeros = "free"
    msg.data = data
    return msg


def test_exec_get_slam_map_view_pushes_via_realtime_input_and_returns_ack():
    """T3 of embodied-observer plan: get_slam_map_view fetches the SLAM
    OccupancyGrid + robot pose, rasterizes via er.sensors.rasterize_slam_map
    (renamed from _rasterize_slam_map in this task), pushes the PNG via
    the realtime_input channel WatchStream uses, and returns a tiny ack.

    Sync-wrapped via asyncio.run() — pytest-asyncio is not installed in
    the container; same constraint as T1 / T2.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_get_slam_map_view

    class _CamWithMap:
        def __init__(self, msg, pose):
            self._msg = msg
            self._pose = pose
        def get_slam_map_msg(self):
            return self._msg
        def get_robot_pose(self):
            return self._pose

    msg = _fake_occupancy_grid()  # 20x20 @ 0.05 m → 1m x 1m
    cam = _CamWithMap(msg, (1.0, 2.0, 0.5))

    sent: list[tuple[bytes, str]] = []

    async def fake_send_video(png: bytes, mime_type: str) -> None:
        sent.append((png, mime_type))

    res = asyncio.run(exec_get_slam_map_view(cam, fake_send_video))

    # send_video called exactly once with PNG bytes + image/png mime.
    assert len(sent) == 1, f"expected one realtime_input push, got {len(sent)}"
    png, mime = sent[0]
    assert png[:8] == b'\x89PNG\r\n\x1a\n', "bytes pushed are not a valid PNG"
    assert mime == "image/png"

    # Response shape: small ack, NOT the bytes.
    assert res.get("ok") is True
    assert res.get("pushed_realtime_input") is True
    assert res.get("robot_at") == [1.0, 2.0]
    # 20 cells × 0.05 m = 1.0 m on each side.
    assert res.get("map_size_m") == [1.0, 1.0]
    assert res.get("size_bytes") == len(png)
    # No raw image bytes leaked into the function_response payload.
    assert "image" not in res
    assert "data" not in res
    assert "png" not in res


def test_exec_get_slam_map_view_no_map_returns_error_and_does_not_push():
    """When SLAM has not yet published a map (cold start), the handler
    must return a clean error dict and NOT call send_video — pushing
    nothing-or-empty would corrupt Gemini's perception stream.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_get_slam_map_view

    class _CamNoMap:
        def get_slam_map_msg(self):
            return None
        def get_robot_pose(self):
            return (0.0, 0.0, 0.0)

    sent: list[tuple[bytes, str]] = []

    async def fake_send_video(png: bytes, mime_type: str) -> None:
        sent.append((png, mime_type))

    res = asyncio.run(exec_get_slam_map_view(_CamNoMap(), fake_send_video))

    assert "error" in res
    # Sanity: the error mentions slam map so Gemini can narrate it.
    assert "slam" in res["error"].lower() or "map" in res["error"].lower()
    assert sent == [], "must NOT push when no SLAM map is available"


def test_exec_get_slam_map_view_handles_missing_pose_with_origin_default():
    """If /robot_pose hasn't published yet (EKF cold-start), the handler
    must still render the map using the origin (0, 0, 0) as the marker
    position — the map itself is the more important data and the operator
    can still get cross-room spatial information from the rendered grid.
    """
    import asyncio
    from ugv_tools_api.supervisor.tool_handlers import exec_get_slam_map_view

    msg = _fake_occupancy_grid()

    class _CamMapNoPose:
        def get_slam_map_msg(self):
            return msg
        def get_robot_pose(self):
            return None

    sent: list[tuple[bytes, str]] = []

    async def fake_send_video(png: bytes, mime_type: str) -> None:
        sent.append((png, mime_type))

    res = asyncio.run(exec_get_slam_map_view(_CamMapNoPose(), fake_send_video))

    # Image still pushed; pose default = origin.
    assert len(sent) == 1
    assert sent[0][1] == "image/png"
    assert res.get("ok") is True
    assert res.get("robot_at") == [0.0, 0.0]
