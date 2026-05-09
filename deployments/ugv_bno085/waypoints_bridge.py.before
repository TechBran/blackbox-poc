#!/usr/bin/env python3
"""waypoints_bridge - vizanti Path topic to Nav2 FollowWaypoints action.

Vizanti's waypoint widget publishes a nav_msgs/Path message to /waypoints
(default topic; configurable in vizanti's widget settings). Nav2's
waypoint_follower exposes only an action interface (/follow_waypoints), not
a topic, so nothing in the stock Nav2 stack consumes vizanti's Path.

This bridge subscribes to /waypoints and dispatches each Path as a
FollowWaypoints action goal. Mid-mission re-publication cancels the prior
goal and starts a fresh one, mirroring vizanti's expectation that clicking
"send waypoints" replaces any in-flight set.
"""
from __future__ import annotations

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from nav_msgs.msg import Path
from nav2_msgs.action import FollowWaypoints


class WaypointsBridge(Node):
    def __init__(self) -> None:
        super().__init__("waypoints_bridge")
        self.declare_parameter("input_topic", "/waypoints")
        self.declare_parameter("action_name", "follow_waypoints")
        in_topic = self.get_parameter("input_topic").value
        action_name = self.get_parameter("action_name").value

        self._client = ActionClient(self, FollowWaypoints, action_name)
        self.create_subscription(Path, in_topic, self._on_path, 10)
        self._active_handle = None
        self.get_logger().info(
            f"waypoints_bridge: {in_topic} (Path) -> /{action_name} (FollowWaypoints)"
        )

    def _on_path(self, msg: Path) -> None:
        if not msg.poses:
            self.get_logger().warn("received empty waypoints Path; ignoring")
            return

        if not self._client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error(
                "FollowWaypoints action server unavailable; "
                "is /waypoint_follower lifecycle ACTIVE?"
            )
            return

        # Cancel prior in-flight set (vizanti UX: new path replaces previous)
        if self._active_handle is not None:
            try:
                self._active_handle.cancel_goal_async()
            except Exception as exc:
                self.get_logger().warn(f"cancel of previous goal failed: {exc}")
            self._active_handle = None

        goal = FollowWaypoints.Goal()
        goal.poses = list(msg.poses)
        self.get_logger().info(
            f"dispatching {len(goal.poses)} waypoint(s) (frame_id={msg.header.frame_id})"
        )
        send_future = self._client.send_goal_async(goal)
        send_future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future) -> None:
        try:
            handle = future.result()
            if handle and handle.accepted:
                self.get_logger().info("waypoints goal accepted by waypoint_follower")
                self._active_handle = handle
                # Track completion for diag log
                handle.get_result_async().add_done_callback(self._on_goal_result)
            else:
                self.get_logger().warn("waypoints goal rejected by waypoint_follower")
        except Exception as exc:
            self.get_logger().error(f"goal-response handling error: {exc}")

    def _on_goal_result(self, future) -> None:
        try:
            result = future.result()
            status_map = {
                4: "succeeded",
                5: "canceled",
                6: "aborted",
            }
            label = status_map.get(getattr(result, "status", -1), f"status={result.status}")
            missed = list(getattr(result.result, "missed_waypoints", []))
            if missed:
                self.get_logger().warn(
                    f"waypoints {label}; missed waypoints: {missed}"
                )
            else:
                self.get_logger().info(f"waypoints {label}")
        except Exception as exc:
            self.get_logger().warn(f"result-handling error: {exc}")
        finally:
            self._active_handle = None


def main(args=None) -> None:
    rclpy.init(args=args)
    node = WaypointsBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
