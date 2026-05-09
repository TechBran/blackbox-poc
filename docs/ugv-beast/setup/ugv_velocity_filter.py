#!/usr/bin/env python3
"""
UGV Velocity Safety Gate

Simple safety-stop filter. Does NOT steer — just prevents collisions.
If an obstacle is within the stop distance in the direction of travel,
velocity is zeroed. Otherwise, the command passes through unchanged.

All intelligent obstacle avoidance is handled by Nav2 (costmap + DWB controller).
This filter is a last-resort safety net for joystick/manual control.

Architecture:
    Joystick/Foxglove/Nav2/ER → /cmd_vel → [THIS] → /cmd_vel_safe → ugv_bringup
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
import threading
import math
import time


# ── Safety Parameters ──
ROBOT_RADIUS = 0.20      # 20cm (~8in) from LiDAR to robot edge
STOP_CLEARANCE = 0.18    # 18cm (~7in) clearance from robot edge to wall
STOP_DIST = ROBOT_RADIUS + STOP_CLEARANCE  # 38cm (~15in) from LiDAR = 7in from robot edge

# Only check a 120° arc in the direction of travel (±60° from heading)
FRONT_ARC_DEG = 120
CMD_TIMEOUT = 0.5
FILTER_HZ = 20


class VelocitySafetyGate(Node):
    def __init__(self):
        super().__init__('ugv_velocity_filter')

        self.create_subscription(Twist, '/cmd_vel', self._cmd_cb, 10)
        self.create_subscription(LaserScan, '/scan', self._scan_cb, 10)
        self.safe_pub = self.create_publisher(Twist, '/cmd_vel_safe', 10)

        self._cmd_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._latest_cmd = Twist()
        self._latest_cmd_time = 0.0
        self._latest_scan = None

        self.create_timer(1.0 / FILTER_HZ, self._tick)

        self.get_logger().info(f'Safety gate: /cmd_vel → /cmd_vel_safe @ {FILTER_HZ}Hz')
        self.get_logger().info(f'  Stop dist: {STOP_DIST:.2f}m ({STOP_DIST*39.37:.0f}in from LiDAR, '
                               f'{STOP_CLEARANCE*39.37:.0f}in from edge)')
        self.get_logger().info(f'  Front arc: {FRONT_ARC_DEG}°  Robot radius: {ROBOT_RADIUS}m')

    def _cmd_cb(self, msg):
        with self._cmd_lock:
            self._latest_cmd = msg
            self._latest_cmd_time = time.monotonic()

    def _scan_cb(self, msg):
        with self._scan_lock:
            self._latest_scan = msg

    def _tick(self):
        now = time.monotonic()
        with self._cmd_lock:
            cmd = self._latest_cmd
            cmd_age = now - self._latest_cmd_time

        # Timeout: no recent command → zero
        if cmd_age > CMD_TIMEOUT:
            self.safe_pub.publish(Twist())
            return

        # Zero command → pass through
        if abs(cmd.linear.x) < 0.001 and abs(cmd.angular.z) < 0.001:
            self.safe_pub.publish(cmd)
            return

        # Get scan
        with self._scan_lock:
            scan = self._latest_scan

        # No LiDAR → pass through (don't block if LiDAR is offline)
        if scan is None:
            self.safe_pub.publish(cmd)
            return

        # Check if movement direction is clear
        if cmd.linear.x > 0.001:
            # Moving forward: check front arc
            blocked = self._check_arc_blocked(scan, center_deg=0.0, arc_deg=FRONT_ARC_DEG)
        else:
            # Backward and rotation: ALWAYS allowed
            # You must always be able to back away from a wall or turn to escape
            blocked = False

        if blocked:
            # Stop linear movement, allow rotation (so user can turn away)
            safe = Twist()
            safe.angular.z = cmd.angular.z  # Let them turn to escape
            self.safe_pub.publish(safe)
        else:
            # Clear — pass through unchanged
            self.safe_pub.publish(cmd)

    def _check_arc_blocked(self, scan, center_deg, arc_deg):
        """Check if any obstacle is within STOP_DIST in the specified arc.
        Uses a robust check: blocked only if 3+ consecutive readings are close
        (filters phantom single-point noise)."""
        ranges = scan.ranges
        n = len(ranges)
        if n == 0:
            return False

        r_min = scan.range_min
        r_max = scan.range_max
        angle_inc_deg = math.degrees(scan.angle_increment)

        # Convert arc to index range
        half_arc = arc_deg / 2.0
        start_deg = center_deg - half_arc
        end_deg = center_deg + half_arc

        start_idx = int((start_deg % 360) / angle_inc_deg) % n
        end_idx = int((end_deg % 360) / angle_inc_deg) % n

        # Count consecutive close readings (filters noise)
        consecutive_close = 0
        CONSECUTIVE_THRESHOLD = 3  # Need 3+ adjacent close readings to trigger

        # Iterate through the arc
        idx = start_idx
        checked = 0
        max_checks = int(arc_deg / angle_inc_deg) + 2

        while checked < max_checks:
            r = ranges[idx % n]
            if r > r_min and r < r_max and not math.isnan(r):
                if r < STOP_DIST:
                    consecutive_close += 1
                    if consecutive_close >= CONSECUTIVE_THRESHOLD:
                        return True  # Real obstacle confirmed
                else:
                    consecutive_close = 0
            else:
                # NaN/invalid: don't count as obstacle, but reset consecutive
                consecutive_close = 0

            idx += 1
            checked += 1

        return False


def main(args=None):
    rclpy.init(args=args)
    node = VelocitySafetyGate()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
