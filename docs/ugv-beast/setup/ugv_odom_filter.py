#!/usr/bin/env python3
"""
UGV Beast Odometry Filter
Subscribes to rf2o laser odometry, applies exponential smoothing,
and republishes filtered odom + TF. Eliminates jitter from LiDAR scan noise.

Subscribes:
  /odom_rf2o  (Odometry) - Raw rf2o laser odometry

Publishes:
  /odom       (Odometry) - Smoothed odometry
  /tf         (TF)       - Smoothed odom→base_footprint transform

The key insight: rf2o jitters ~1.2° while stationary due to LD19 scan noise.
This filter applies exponential moving average + minimum motion threshold
to produce stable odom that only updates when the robot actually moves.
"""

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped
from tf2_ros import TransformBroadcaster
import math
import time


class OdomFilter(Node):
    def __init__(self):
        super().__init__('ugv_odom_filter')

        # Parameters
        self.declare_parameter('alpha', 0.3)              # Light smoothing (lower = more responsive to real movement)
        self.declare_parameter('min_linear', 0.002)       # 2mm minimum movement to register
        self.declare_parameter('min_angular', 0.003)      # ~0.17° minimum rotation to register

        # State
        self.smooth_x = None
        self.smooth_y = None
        self.smooth_yaw = None
        self.last_published_x = 0.0
        self.last_published_y = 0.0
        self.last_published_yaw = 0.0
        self.initialized = False

        # TF broadcaster
        self.tf_broadcaster = TransformBroadcaster(self)

        # Publisher (filtered odom)
        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)

        # Subscriber (raw rf2o)
        self.create_subscription(Odometry, '/odom_rf2o', self.odom_callback, 10)

        self.get_logger().info("Odom filter ready (smoothing rf2o laser odometry)")

    def odom_callback(self, msg):
        alpha = self.get_parameter('alpha').value

        # Extract position and yaw from quaternion
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        yaw = math.atan2(2.0 * qw * qz, 1.0 - 2.0 * qz * qz)

        # Initialize on first message
        if not self.initialized:
            self.smooth_x = x
            self.smooth_y = y
            self.smooth_yaw = yaw
            self.last_published_x = x
            self.last_published_y = y
            self.last_published_yaw = yaw
            self.initialized = True

        # Exponential moving average
        self.smooth_x = alpha * self.smooth_x + (1.0 - alpha) * x
        self.smooth_y = alpha * self.smooth_y + (1.0 - alpha) * y
        self.smooth_yaw = alpha * self.smooth_yaw + (1.0 - alpha) * yaw

        # Minimum motion threshold — don't update if change is below noise floor
        min_lin = self.get_parameter('min_linear').value
        min_ang = self.get_parameter('min_angular').value

        dx = self.smooth_x - self.last_published_x
        dy = self.smooth_y - self.last_published_y
        dyaw = self.smooth_yaw - self.last_published_yaw

        linear_change = math.sqrt(dx * dx + dy * dy)
        angular_change = abs(dyaw)

        if linear_change < min_lin and angular_change < min_ang:
            # Below noise floor — republish last known good pose (keeps TF alive)
            out_x = self.last_published_x
            out_y = self.last_published_y
            out_yaw = self.last_published_yaw
        else:
            # Real movement detected — update
            out_x = self.smooth_x
            out_y = self.smooth_y
            out_yaw = self.smooth_yaw
            self.last_published_x = out_x
            self.last_published_y = out_y
            self.last_published_yaw = out_yaw

        # Rebuild quaternion from yaw
        out_qz = math.sin(out_yaw / 2.0)
        out_qw = math.cos(out_yaw / 2.0)

        # Publish filtered odometry
        out = Odometry()
        out.header = msg.header
        out.header.frame_id = 'odom'
        out.child_frame_id = 'base_footprint'
        out.pose.pose.position.x = out_x
        out.pose.pose.position.y = out_y
        out.pose.pose.position.z = 0.0
        out.pose.pose.orientation.z = out_qz
        out.pose.pose.orientation.w = out_qw
        out.twist = msg.twist  # Pass through velocity as-is
        self.odom_pub.publish(out)

        # Publish TF
        t = TransformStamped()
        t.header = out.header
        t.child_frame_id = 'base_footprint'
        t.transform.translation.x = out_x
        t.transform.translation.y = out_y
        t.transform.translation.z = 0.0
        t.transform.rotation.z = out_qz
        t.transform.rotation.w = out_qw
        self.tf_broadcaster.sendTransform(t)


def main(args=None):
    rclpy.init(args=args)
    node = OdomFilter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
