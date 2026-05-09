"""nvblox_costmap_relay.py — DistanceMapSlice → OccupancyGrid bridge.

Subscribes /nvblox_node/static_map_slice (the GPU-computed 2D ESDF slice
published by nvblox_node) and republishes /nvblox_local_costmap as a
nav_msgs/OccupancyGrid that Nav2's static_layer plugin can consume directly.

This is the bridge between the GPU mapping subsystem (nvblox) and the
CPU-side Nav2 costmap stack. It runs co-located with nvblox in the sidecar
container so we don't have to install nvblox_msgs in ugv_waveshare.

Conversion semantics:
    distance == unknown_value (1000.0)  → -1   (unknown)
    distance < LETHAL_THRESHOLD_M       → 100  (occupied — at or near a wall)
    otherwise                           → 0    (free)

The slice geometry (resolution, width, height, origin) carries through
unchanged — the OccupancyGrid sits in the same map frame at the same scale.

QoS: TRANSIENT_LOCAL on the publisher so Nav2's static_layer (which
late-subscribes with TRANSIENT_LOCAL by default) receives the latest map
even if it joins after the first publish.
"""
from __future__ import annotations

import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

from geometry_msgs.msg import Pose
from nav_msgs.msg import OccupancyGrid
from nvblox_msgs.msg import DistanceMapSlice


LETHAL_THRESHOLD_M = 0.05  # one voxel — cells within 5cm of an obstacle are lethal
TOPIC_IN = "/nvblox_node/static_map_slice"
TOPIC_OUT = "/nvblox_local_costmap"

# Nav2's StaticLayer is designed for SLAM-style maps that publish once or
# every few seconds. Streaming at the nvblox slice rate (~5 Hz) causes the
# StaticLayer to call resizeMap() on every callback, which can starve the
# costmap_2d publish thread. Throttle to ~1 Hz to keep the consumer happy.
PUBLISH_PERIOD_S = 1.0


class NvbloxCostmapRelay(Node):
    def __init__(self):
        super().__init__("nvblox_costmap_relay")

        # Match nvblox publisher (RELIABLE/VOLATILE; depth 5 is safe).
        sub_qos = QoSProfile(
            depth=5,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        # Nav2 static_layer late-joins with TRANSIENT_LOCAL — match it so
        # the latest map is delivered to a Nav2 process that starts after us.
        pub_qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.pub = self.create_publisher(OccupancyGrid, TOPIC_OUT, pub_qos)
        self.sub = self.create_subscription(
            DistanceMapSlice, TOPIC_IN, self._on_slice, sub_qos
        )

        # Latest received frame; the publish timer drains it.
        self._latest: DistanceMapSlice | None = None
        self._frame_count = 0
        self._publish_count = 0

        self.timer = self.create_timer(PUBLISH_PERIOD_S, self._publish_latest)
        self.get_logger().info(
            f"nvblox_costmap_relay: bridging {TOPIC_IN} → {TOPIC_OUT} "
            f"(lethal_threshold={LETHAL_THRESHOLD_M}m, throttle={PUBLISH_PERIOD_S}s)"
        )

    def _on_slice(self, msg: DistanceMapSlice) -> None:
        # Cache only — the timer fires the conversion + publish at our throttled rate.
        self._latest = msg
        self._frame_count += 1

    def _publish_latest(self) -> None:
        msg = self._latest
        if msg is None:
            return  # nothing to publish yet
        # Vectorized threshold conversion via numpy. At 5 Hz × ~7000 cells
        # this is microseconds; the alternative list-comprehension is fine
        # too but uglier.
        distances = np.asarray(msg.data, dtype=np.float32)
        out = np.full(distances.shape, -1, dtype=np.int8)
        free_mask = (distances >= LETHAL_THRESHOLD_M) & (distances != msg.unknown_value)
        lethal_mask = (distances < LETHAL_THRESHOLD_M) & (distances != msg.unknown_value)
        out[free_mask] = 0
        out[lethal_mask] = 100

        grid = OccupancyGrid()
        grid.header = msg.header
        grid.info.map_load_time = msg.header.stamp
        grid.info.resolution = msg.resolution
        grid.info.width = msg.width
        grid.info.height = msg.height
        # DistanceMapSlice.origin is geometry_msgs/Point; OccupancyGrid wants
        # geometry_msgs/Pose. The quaternion stays identity (the slice is
        # axis-aligned with its own frame).
        grid.info.origin = Pose()
        grid.info.origin.position.x = msg.origin.x
        grid.info.origin.position.y = msg.origin.y
        grid.info.origin.position.z = msg.origin.z
        grid.info.origin.orientation.w = 1.0

        grid.data = out.tolist()
        self.pub.publish(grid)

        self._publish_count += 1
        if self._publish_count % 5 == 0:
            occupied = int((out == 100).sum())
            free = int((out == 0).sum())
            unknown = int((out == -1).sum())
            self.get_logger().info(
                f"published frame {self._publish_count} "
                f"(received {self._frame_count} since start): "
                f"{msg.width}x{msg.height} cells, "
                f"occupied={occupied} free={free} unknown={unknown}"
            )


def main() -> None:
    rclpy.init()
    node = NvbloxCostmapRelay()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
