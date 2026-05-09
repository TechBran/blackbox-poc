"""Singleton bridge: owns rclpy context, spins executor on a daemon thread,
exposes typed publish/subscribe/call helpers to the FastAPI layer."""
from __future__ import annotations
import threading
import time
from typing import Any, Callable, Optional
import rclpy
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, qos_profile_sensor_data

from nav_msgs.msg import Odometry, OccupancyGrid
from sensor_msgs.msg import LaserScan, CompressedImage, Image
from geometry_msgs.msg import PoseStamped, PointStamped, Twist, Point


class _BridgeNode(Node):
    def __init__(self):
        super().__init__("ugv_tools_api_bridge")
        self._latest: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._pub_cache: dict[str, Any] = {}

    def cache_topic(self, topic: str, msg_type, qos: int = 10):
        def cb(msg):
            with self._lock:
                self._latest[topic] = (time.time(), msg)
        self.create_subscription(msg_type, topic, cb, qos)

    def _cache_cb(self, topic: str):
        """Return a callback that stores (timestamp, msg) for topic under lock."""
        def cb(msg):
            with self._lock:
                self._latest[topic] = (time.time(), msg)
        return cb

    def get_latest(self, topic: str):
        with self._lock:
            return self._latest.get(topic)

    def publisher(self, topic: str, msg_type, qos: int = 10):
        if topic not in self._pub_cache:
            self._pub_cache[topic] = self.create_publisher(msg_type, topic, qos)
        return self._pub_cache[topic]


class RosBridge:
    _instance: Optional["RosBridge"] = None

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._executor: Optional[MultiThreadedExecutor] = None
        self._node: Optional[_BridgeNode] = None
        self._running = threading.Event()

    @classmethod
    def instance(cls) -> "RosBridge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self):
        if self._running.is_set():
            return
        if not rclpy.ok():
            rclpy.init()
        self._node = _BridgeNode()
        self._executor = MultiThreadedExecutor(num_threads=4)
        self._executor.add_node(self._node)
        self._running.set()
        self._thread = threading.Thread(target=self._spin, daemon=True, name="ros_bridge")
        self._thread.start()
        self._register_default_topics()

    def _register_default_topics(self):
        """Register canonical subscriptions + publishers for FastAPI handlers.

        Called once from start() after the spin thread is up. Subscriptions added
        after spin begins are picked up on the next spin_once iteration.
        """
        node = self._node
        assert node is not None

        # Default-QoS (reliable, depth=10) subscriptions
        node.cache_topic("/odom", Odometry)
        node.cache_topic("/scan", LaserScan)
        node.cache_topic("/robot_pose", PoseStamped)
        node.cache_topic("/gimbal/state", PointStamped)

        # Auto-explore orchestrator status (JSON in std_msgs/String). Latched
        # so last-known state is always available; explore tools read via
        # get_latest("/explore/status").
        from std_msgs.msg import String
        node.cache_topic("/explore/status", String)

        # /map needs TRANSIENT_LOCAL durability to receive the latched map message
        map_qos = QoSProfile(depth=1)
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        node.create_subscription(
            OccupancyGrid, "/map", node._cache_cb("/map"), map_qos,
        )

        # Nav2's /global_costmap/costmap is typically latched (TRANSIENT_LOCAL + RELIABLE).
        # Subscribe with matching QoS first; if no messages arrive the fallback VOLATILE
        # subscription below will still deliver them (Nav2 publishes both QoS profiles
        # in some configurations).
        costmap_qos = QoSProfile(depth=1)
        costmap_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        node.create_subscription(
            OccupancyGrid, "/global_costmap/costmap",
            node._cache_cb("/global_costmap/costmap"), costmap_qos,
        )
        node.create_subscription(
            OccupancyGrid, "/local_costmap/costmap",
            node._cache_cb("/local_costmap/costmap"), costmap_qos,
        )

        # Image topics — use qos_profile_sensor_data (BEST_EFFORT) to match
        # publishers in pantilt_camera / oakd_camera nodes. If we used RELIABLE
        # here, the QoS handshake would fail and no messages would arrive.
        node.create_subscription(
            CompressedImage, "/camera/image/compressed",
            node._cache_cb("/camera/image/compressed"),
            qos_profile_sensor_data,
        )
        node.create_subscription(
            CompressedImage, "/oak/rgb/image_rect/compressed",
            node._cache_cb("/oak/rgb/image_rect/compressed"),
            qos_profile_sensor_data,
        )

        # OAK-D stereo depth (sensor_msgs/Image, 16UC1). BEST_EFFORT to match
        # the oakd_camera node's publisher QoS — RELIABLE here would silently
        # drop every frame.
        node.create_subscription(
            Image, "/oak/stereo/depth",
            node._cache_cb("/oak/stereo/depth"),
            qos_profile_sensor_data,
        )

        # Pre-create publishers used by tool handlers
        node.publisher("/cmd_vel", Twist)
        node.publisher("/gimbal/absolute", Point)

    def _spin(self):
        try:
            while self._running.is_set() and rclpy.ok():
                try:
                    self._executor.spin_once(timeout_sec=0.1)
                except ExternalShutdownException:
                    break
        finally:
            pass

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._executor:
            self._executor.shutdown()
        if self._node:
            self._node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    def is_running(self) -> bool:
        return self._running.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def node(self) -> _BridgeNode:
        if self._node is None:
            raise RuntimeError("Bridge not started")
        return self._node
