"""ROS-subscriber-backed sensor hub for the supervisor.

Originally a camera-frame cache (Task 6). Now a broader sensor hub: it
also caches the global costmap (rendered PNG), the SLAM map message
(raw OccupancyGrid; rasterized on-demand by the get_slam_map_view tool
because the model only asks for it occasionally), and the latest
robot_pose (for the SLAM marker overlay). All four data sources live on
a single private rclpy context inside this thread — keeps the Live
session loop free of ROS DDS noise.

The get_camera_view and get_slam_map_view tools fetch the latest data
from these caches on demand and push it to Gemini Live via the
realtime_input video sidechannel (T2 / T3 of the embodied-observer
plan). Watch mode pushes camera frames continuously at a configurable
ambient rate (T1).
"""
import io
import math
import threading
import time
from typing import Optional


class FrameCache:
    """Thread-safe latest-only cache for opaque bytes.

    Writers (rclpy subscriber callback, or the renderer invoked from any
    thread) call set(); the async tool handler reads via get(). get() never
    blocks on set() and vice-versa beyond a tiny lock.

    Also tracks the monotonic wall-clock time of the most recent set() so
    callers can ask "how stale is this frame?". age_s() returns the elapsed
    seconds since the last set(), or None if no frame has ever been cached.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Optional[bytes] = None
        self._received = 0
        self._last_set_monotonic: Optional[float] = None

    def set(self, data: bytes) -> None:
        with self._lock:
            self._data = data
            self._received += 1
            self._last_set_monotonic = time.monotonic()

    def get(self) -> Optional[bytes]:
        with self._lock:
            return self._data

    def age_s(self) -> Optional[float]:
        """Seconds since the most recent set(); None if never set."""
        with self._lock:
            if self._last_set_monotonic is None:
                return None
            return time.monotonic() - self._last_set_monotonic

    @property
    def received(self) -> int:
        with self._lock:
            return self._received

    def __repr__(self) -> str:
        with self._lock:
            size = len(self._data) if self._data is not None else 0
            return f"FrameCache(received={self._received}, bytes={size})"


def render_costmap_png(msg) -> bytes:
    """Render a nav_msgs/OccupancyGrid to PNG bytes.

    Args:
        msg: Any object duck-typed to the OccupancyGrid shape. Required
            attributes: ``msg.info.width`` (int), ``msg.info.height`` (int),
            ``msg.data`` (iterable of ints in [-1, 100], length width*height).

    Returns:
        PNG-encoded bytes. Callers push these to Gemini as video sidechannel.

    Raises:
        ValueError: if ``len(msg.data) != width * height`` (upstream publisher
            is misbehaving). Raised with a diagnostic message; far more
            actionable than the bare numpy reshape error.

    Color scheme matches RViz-style convention:
        * ``0`` (free)         -> light grey (200, 200, 200)
        * ``100`` (lethal)     -> red (200, 30, 30)
        * ``-1`` (unknown)     -> dark grey (60, 60, 60)
        * ``1..99`` (inflation) -> yellow-to-red gradient
          (value=1 is pure yellow (255, 255, 30);
           value=99 approaches red (204, 0, 30))
        * Anything else       -> black (0, 0, 0), treated as an out-of-band
          sentinel. Should not occur with a well-formed OccupancyGrid.

    Flipped vertically so north-up in map frame renders as up in the image
    (OccupancyGrid's origin is bottom-left; PNG's origin is top-left).

    Performance note:
        Current implementation uses four mask passes + one flipud + PNG encode
        at compress_level=1. Fine for the UGV's ~100x100 local costmap and
        ~200x200 global costmap at 5 Hz. If map dimensions grow to the order
        of 1000x1000 cells, replace the mask passes with a module-level
        lookup table (LUT) keyed by (value + 1) for a single-pass rendering.
    """
    import numpy as np
    from PIL import Image

    w = msg.info.width
    h = msg.info.height

    # Diagnostic guard: catch malformed upstream messages with a readable
    # error instead of numpy's bare reshape ValueError.
    if len(msg.data) != w * h:
        raise ValueError(
            f"OccupancyGrid data length {len(msg.data)} does not match "
            f"width*height ({w}*{h}={w*h})"
        )

    arr = np.array(msg.data, dtype=np.int16).reshape(h, w)
    rgb = np.zeros((h, w, 3), dtype=np.uint8)

    mask_unknown = arr == -1
    rgb[mask_unknown] = (60, 60, 60)

    mask_free = arr == 0
    rgb[mask_free] = (200, 200, 200)

    mask_lethal = arr == 100
    rgb[mask_lethal] = (200, 30, 30)

    mask_inf = (arr > 0) & (arr < 100)
    if mask_inf.any():
        # t in [0.0, 1.0] so value=1 -> pure yellow, value=99 -> near red.
        # Previously t was arr/99 which left value=1 slightly off-yellow
        # (R=254, G=252, B=30) — minor but visible in Gemini's interpretation.
        t = (arr[mask_inf].astype(np.float32) - 1.0) / 98.0
        rgb[mask_inf, 0] = (255 * (1 - 0.2 * t)).astype(np.uint8)
        rgb[mask_inf, 1] = (255 * (1 - t)).astype(np.uint8)
        rgb[mask_inf, 2] = 30

    img = Image.fromarray(np.flipud(rgb), mode="RGB")
    buf = io.BytesIO()
    # compress_level=1 trades 10-30% larger PNG for ~30-50% faster encode.
    # Costmaps are ephemeral (sent once per tool call), so the size hit
    # is immaterial vs. the per-render CPU savings on the rclpy thread.
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _yaw_from_quat(q) -> float:
    """Z-axis (yaw) extraction from a unit quaternion.

    Local copy of er.sensors._yaw_from_quat — keeps the supervisor
    module independent of er/. Inlined intentionally (3 lines, no
    state) so renaming or refactoring either side stays cheap.
    """
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class RosCamera:
    """Background rclpy subscriber that owns the supervisor's ROS sensor hub.

    Subscribes to four topics on a single private rclpy context:
      * camera_topic (sensor_msgs/CompressedImage) -> JPEG bytes cached
        as-is (passthrough).
      * costmap_topic (nav_msgs/OccupancyGrid) -> PNG rendered in the
        subscriber callback via render_costmap_png and cached.
      * /map (nav_msgs/OccupancyGrid) -> raw message cached. The SLAM
        map updates infrequently and the get_slam_map_view tool needs
        the message itself (not a pre-rendered PNG) so it can rasterize
        on-demand with the latest robot pose marker. Latched
        (TRANSIENT_LOCAL durability) so a fresh subscriber gets the
        most-recent map immediately.
      * /robot_pose (geometry_msgs/PoseStamped) -> raw message cached.
        Published by robot_localization's EKF at ~10 Hz; consumed by
        get_slam_map_view to draw the cyan marker.

    Uses a PRIVATE rclpy.Context so this thread's rclpy activity does not
    collide with any other rclpy node in the same process (in particular,
    ugv_tools_api_bridge which runs in the default context).

    Lifetime: call start() to spawn the spin thread, stop() to tear it down.
    """

    def __init__(self, camera_topic: str, costmap_topic: str) -> None:
        self._camera_topic = camera_topic
        self._costmap_topic = costmap_topic
        self._camera_cache = FrameCache()
        self._costmap_cache = FrameCache()
        # SLAM map and pose are not bytes — keep raw msgs under a lock.
        # The SLAM rasterizer needs msg.info.{width,height,resolution,
        # origin.position.{x,y}} + msg.data; the marker draw needs
        # pose.pose.{position,orientation}.
        self._slam_map_lock = threading.Lock()
        self._slam_map_msg: Optional[object] = None
        self._pose_lock = threading.Lock()
        self._pose_msg: Optional[object] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._context = None
        self._node = None

    def start(self) -> None:
        """Spawn the rclpy spin thread. Non-blocking; returns immediately.

        True idempotency: a no-op only if the current thread is still alive.
        If _run crashed (e.g. during rclpy.init), start() will respawn rather
        than sitting dead forever. Clears the stop event so a restart after
        stop()+start() actually runs.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ugv-supervisor-ros",
        )
        self._thread.start()

    def _run(self) -> None:
        """Owns a private rclpy context for the life of this thread.

        All rclpy.init / create_node / create_subscription happen here so
        they bind to the same context the spin loop uses. Shutdown is
        best-effort — failure to cleanly shut down is swallowed because a
        dead supervisor is worse than a leaked context.

        Subscriber callbacks are WRAPPED IN try/except. In rclpy, an
        unhandled exception in a callback leaves the subscription in an
        undefined state (logs once in some versions, silently stalls in
        others). We swallow so the spin loop stays live; Task 8 adds
        structured logging that will eventually replace the bare pass.
        """
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import (
            qos_profile_sensor_data,
            QoSProfile,
            QoSDurabilityPolicy,
            QoSReliabilityPolicy,
        )
        from sensor_msgs.msg import CompressedImage
        from nav_msgs.msg import OccupancyGrid
        from geometry_msgs.msg import PoseStamped

        self._context = rclpy.Context()
        rclpy.init(args=None, context=self._context)

        cam_cache = self._camera_cache
        map_cache = self._costmap_cache
        slam_map_lock = self._slam_map_lock
        pose_lock = self._pose_lock
        camera_topic = self._camera_topic
        costmap_topic = self._costmap_topic
        outer = self  # closure handle for slam-map / pose attribute writes

        def _on_camera(m):
            try:
                cam_cache.set(bytes(m.data))
            except Exception:
                # Callbacks must never escape the executor. Task 8 will
                # wire structured logging; for now preserve spin loop.
                pass

        def _on_costmap(m):
            try:
                map_cache.set(render_costmap_png(m))
            except Exception:
                pass

        def _on_slam_map(m):
            # Cache the raw msg; rasterization happens lazily when
            # get_slam_map_view is called (the model fetches infrequently).
            try:
                with slam_map_lock:
                    outer._slam_map_msg = m
            except Exception:
                pass

        def _on_pose(m):
            try:
                with pose_lock:
                    outer._pose_msg = m
            except Exception:
                pass

        # SLAM /map is published latched (TRANSIENT_LOCAL + RELIABLE) so
        # a fresh subscriber receives the most-recent map immediately
        # rather than waiting for the next SLAM update (which can be
        # minutes apart in a static environment).
        slam_map_qos = QoSProfile(
            depth=1,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )

        class _Sub(Node):
            def __init__(sub):
                super().__init__("ugv_supervisor_ros", context=self._context)
                sub.create_subscription(
                    CompressedImage, camera_topic,
                    _on_camera,
                    qos_profile_sensor_data,
                )
                sub.create_subscription(
                    OccupancyGrid, costmap_topic,
                    _on_costmap,
                    1,  # costmap updates are slow (~5Hz); depth 1 is enough
                )
                sub.create_subscription(
                    OccupancyGrid, "/map",
                    _on_slam_map,
                    slam_map_qos,
                )
                sub.create_subscription(
                    PoseStamped, "/robot_pose",
                    _on_pose,
                    10,  # default RELIABLE depth; EKF publishes ~10Hz
                )

        # Init to None BEFORE try so finally can reference safely even if
        # _Sub() or the executor constructor raises — otherwise we'd hit
        # UnboundLocalError in the finally block.
        executor = None
        try:
            self._node = _Sub()
            executor = SingleThreadedExecutor(context=self._context)
            executor.add_node(self._node)
            while not self._stop.is_set() and self._context.ok():
                executor.spin_once(timeout_sec=0.1)
        finally:
            if executor is not None:
                try:
                    executor.shutdown()
                except Exception:
                    pass
            if self._node is not None:
                try:
                    self._node.destroy_node()
                except Exception:
                    pass
            if self._context is not None:
                try:
                    rclpy.shutdown(context=self._context)
                except Exception:
                    pass

    def stop(self) -> None:
        """Signal the spin thread to exit and wait (with timeout) for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def get_camera_jpeg(self) -> Optional[bytes]:
        return self._camera_cache.get()

    def get_camera_age_s(self) -> Optional[float]:
        """Seconds since the most recent /camera frame landed in the cache.

        Returned in the get_camera_view ack so Gemini Live can judge frame
        freshness ("you saw this 0.4 s ago vs. 8 s ago"). None if no frame
        has ever been received (subscriber thread cold-starting or topic
        unhealthy) — get_camera_view returns an error in that case anyway.
        """
        return self._camera_cache.age_s()

    def get_costmap_png(self) -> Optional[bytes]:
        return self._costmap_cache.get()

    def get_slam_map_msg(self):
        """Return the most recent /map OccupancyGrid, or None if SLAM
        has not yet published one. Returned object is the raw rclpy
        message — callers (e.g. get_slam_map_view tool handler) pass
        it to ``er.sensors.rasterize_slam_map``.
        """
        with self._slam_map_lock:
            return self._slam_map_msg

    def get_robot_pose(self) -> Optional[tuple]:
        """Return ``(x, y, yaw_rad)`` from the most recent /robot_pose
        message, or None if the EKF has not yet published one.

        Yaw is extracted from the quaternion via the inline siny/cosy
        helper at module scope. Floats are returned as-is (no rounding
        — callers can quantize if they need cache stability).
        """
        with self._pose_lock:
            msg = self._pose_msg
        if msg is None:
            return None
        try:
            p = msg.pose.position
            yaw = _yaw_from_quat(msg.pose.orientation)
            return (float(p.x), float(p.y), float(yaw))
        except Exception:
            # Defensive: a malformed message shouldn't take the handler
            # down. Falling back to None lets the handler use the origin
            # default and still push the map.
            return None
