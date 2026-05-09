#!/usr/bin/env python3
"""
UGV Beast Pan-Tilt Object Tracker
Based on proven gimbal_track() from Waveshare Flask app (cv_ctrl.py).

Subscribes:
  /yolo/pantilt/detections  (Detection2DArray) - YOLO detections from pan-tilt camera

Publishes:
  /gimbal/absolute          (Point)  - Pan/tilt absolute position commands
  /yolo/tracker/status      (String) - Tracking status JSON

Parameters (changeable at runtime via ros2 param set):
  target_class  (str)   - COCO class to track (default: "person")
  iterate       (float) - Step size per pixel of error (default: 0.045, from Flask face tracking)
  spd_rate      (float) - Speed multiplier (default: 60.0, from Flask config)
  acc_rate      (float) - Acceleration multiplier (default: 0.4, from Flask config)
  dead_px       (int)   - Pixel deadzone radius (default: 20)
  enabled       (bool)  - Enable/disable tracking (default: true)

Usage:
  ros2 param set /ugv_tracker target_class "cat"
  ros2 param set /ugv_tracker iterate 0.023  (slower, for color tracking)
  ros2 param set /ugv_tracker enabled false
"""

import rclpy
from rclpy.node import Node
from vision_msgs.msg import Detection2DArray
from geometry_msgs.msg import Point
from std_msgs.msg import String
import json
import math
import time

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coco_names import COCO_NAMES, COCO_INDEX


class TrackerNode(Node):
    def __init__(self):
        super().__init__('ugv_tracker')

        # Parameters — PD controller tuned for ROS2 pub/sub latency
        self.declare_parameter('target_class', 'person')
        self.declare_parameter('iterate', 0.015)         # P gain (low to prevent overshoot)
        self.declare_parameter('damping', 0.4)           # D gain ratio (0=none, 1=heavy damping)
        self.declare_parameter('spd_rate', 25.0)         # Servo speed multiplier
        self.declare_parameter('acc_rate', 0.3)
        self.declare_parameter('dead_px', 35)            # Wide deadzone — stop adjusting when close
        self.declare_parameter('max_delta_deg', 3.0)     # Max angle change per frame
        self.declare_parameter('smoothing', 0.6)         # Heavy EMA on target position
        self.declare_parameter('track_lost_timeout', 2.0)
        self.declare_parameter('enabled', True)

        # Frame center (640x480 pan-tilt camera)
        self.frame_cx = 320.0
        self.frame_cy = 240.0

        # Current gimbal angle
        self.pan_angle = 0.0
        self.tilt_angle = 0.0

        # Smoothed target position (exponential moving average)
        self.smooth_gx = self.frame_cx
        self.smooth_gy = self.frame_cy

        # Previous error for derivative damping
        self.prev_err_x = 0.0
        self.prev_err_y = 0.0

        # Tracking state
        self.last_detection_time = time.time()
        self.tracking_active = False

        # Subscribers
        self.create_subscription(
            Detection2DArray, '/yolo/pantilt/detections',
            self.detection_callback, 10)

        # Publishers
        self.gimbal_pub = self.create_publisher(Point, '/gimbal/absolute', 10)
        self.status_pub = self.create_publisher(String, '/yolo/tracker/status', 10)

        # Status timer (5 Hz)
        self.create_timer(0.2, self.publish_status)

        target = self.get_parameter('target_class').value
        iterate = self.get_parameter('iterate').value
        self.get_logger().info(
            f"Tracker ready — target: '{target}', iterate={iterate} "
            f"(Flask-matched gimbal_track)")

    def detection_callback(self, msg):
        if not self.get_parameter('enabled').value:
            return

        target_class = self.get_parameter('target_class').value
        target_id = COCO_INDEX.get(target_class, -1)

        if target_id < 0:
            self.get_logger().warn(
                f"Unknown class: '{target_class}'", throttle_duration_sec=5.0)
            return

        # Filter detections for target class
        candidates = []
        for det in msg.detections:
            for hyp in det.results:
                if hyp.hypothesis.class_id == str(target_id):
                    if hyp.hypothesis.score >= 0.45:
                        candidates.append(det)

        if not candidates:
            elapsed = time.time() - self.last_detection_time
            timeout = self.get_parameter('track_lost_timeout').value
            if self.tracking_active and elapsed > timeout:
                self.tracking_active = False
                self.get_logger().info(
                    f"Target '{target_class}' lost — resetting gimbal to home")
                self._reset_to_home()
            return

        self.last_detection_time = time.time()
        if not self.tracking_active:
            self.tracking_active = True
            self.get_logger().info(f"Tracking '{target_class}'")

        # Select best: largest bounding box area
        best = max(candidates, key=lambda d: d.bbox.size_x * d.bbox.size_y)
        raw_gx = best.bbox.center.position.x
        raw_gy = best.bbox.center.position.y

        # ── Exponential smoothing on target position ──
        # Prevents jitter from frame-to-frame detection noise
        alpha = self.get_parameter('smoothing').value
        self.smooth_gx = alpha * self.smooth_gx + (1.0 - alpha) * raw_gx
        self.smooth_gy = alpha * self.smooth_gy + (1.0 - alpha) * raw_gy

        gx = self.smooth_gx
        gy = self.smooth_gy
        fx = self.frame_cx
        fy = self.frame_cy

        # Distance from center (for speed/acc calculation)
        distance = math.sqrt((fx - gx) ** 2 + (gy - fy) ** 2)

        # Deadzone — wider to prevent jitter near center
        dead = self.get_parameter('dead_px').value
        if distance < dead:
            return

        # ── PD controller: Proportional + Derivative damping ──
        iterate = self.get_parameter('iterate').value
        damping = self.get_parameter('damping').value

        # Current error (pixels from center)
        err_x = gx - fx
        err_y = fy - gy

        # Derivative: rate of change of error (positive = error growing)
        d_err_x = err_x - self.prev_err_x
        d_err_y = err_y - self.prev_err_y
        self.prev_err_x = err_x
        self.prev_err_y = err_y

        # PD: proportional correction minus derivative damping
        # When overshooting, error flips sign → d_err is large → damping kicks in
        pan_delta = err_x * iterate - d_err_x * iterate * damping
        tilt_delta = err_y * iterate - d_err_y * iterate * damping

        # Clamp max change per frame
        max_delta = self.get_parameter('max_delta_deg').value
        pan_delta = max(-max_delta, min(max_delta, pan_delta))
        tilt_delta = max(-max_delta, min(max_delta, tilt_delta))

        self.pan_angle += pan_delta
        self.tilt_angle += tilt_delta

        # Clamp to servo limits
        self.pan_angle = max(-180.0, min(180.0, self.pan_angle))
        self.tilt_angle = max(-30.0, min(90.0, self.tilt_angle))

        # Dynamic speed — slower when close to target, faster when far
        spd_rate = self.get_parameter('spd_rate').value
        acc_rate = self.get_parameter('acc_rate').value
        gimbal_spd = max(1.0, min(300.0, distance * spd_rate))
        gimbal_acc = max(1.0, min(50.0, distance * acc_rate))

        cmd = Point()
        cmd.x = self.pan_angle
        cmd.y = self.tilt_angle
        cmd.z = gimbal_spd
        self.gimbal_pub.publish(cmd)

    def _reset_to_home(self):
        """Reset gimbal to boot position (0, 0) and clear accumulated angles."""
        self.pan_angle = 0.0
        self.tilt_angle = 0.0
        cmd = Point()
        cmd.x = 0.0   # pan center
        cmd.y = 0.0    # tilt center
        cmd.z = 100.0  # moderate speed for smooth return
        self.gimbal_pub.publish(cmd)

    def publish_status(self):
        """Publish tracking status as JSON."""
        target = self.get_parameter('target_class').value
        enabled = self.get_parameter('enabled').value
        status = {
            'enabled': enabled,
            'target_class': target,
            'tracking': self.tracking_active,
            'pan': round(self.pan_angle, 1),
            'tilt': round(self.tilt_angle, 1),
            'since_last_det': round(time.time() - self.last_detection_time, 1),
        }
        msg = String()
        msg.data = json.dumps(status)
        self.status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = TrackerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
