#!/usr/bin/env python3
"""
UGV Beast Camera HTTP Server Node
ROS2 node with built-in HTTP server that serves camera frames as JPEG snapshots.
Runs on Jetson Orin Nano inside a Docker container.

Subscribes:
  /camera/image/compressed          (CompressedImage) - Pan-tilt camera, MJPEG ~15Hz
  /oak/rgb/image_rect               (Image)           - OAK-D RGB rectified (raw BGR8)
  /yolo/pantilt/image/compressed    (CompressedImage) - YOLO-annotated pan-tilt
  /yolo/oak/image/compressed        (CompressedImage) - YOLO-annotated OAK-D

HTTP Endpoints (port 8090):
  GET  /snapshot?camera=pantilt       - Latest pan-tilt frame (JPEG)
  GET  /snapshot?camera=oakd          - Latest OAK-D RGB frame (JPEG)
  GET  /snapshot?camera=yolo_pantilt  - Latest YOLO pan-tilt frame (JPEG)
  GET  /snapshot?camera=yolo_oakd     - Latest YOLO OAK-D frame (JPEG)
  GET  /status                        - JSON status for all cameras
  GET  /health                        - Simple health check
  POST /command                       - Execute robot commands (JSON body)
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from sensor_msgs.msg import CompressedImage, Image, LaserScan
from geometry_msgs.msg import Twist, PoseStamped
from nav_msgs.msg import Odometry, OccupancyGrid
from std_srvs.srv import Trigger
import math

try:
    from nav2_msgs.action import NavigateToPose
    HAS_NAV2_MSGS = True
except ImportError:
    HAS_NAV2_MSGS = False

import cv2
import numpy as np
import threading
import signal
import sys
import json
import time
import os
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HTTP_PORT = 8090
HTTP_BIND = '0.0.0.0'

# Camera name -> ROS2 topic mapping
# msg_type: 'compressed' for CompressedImage (already JPEG bytes)
#           'raw'        for sensor_msgs/Image  (needs cv2 encode)
CAMERA_TOPICS = {
    'pantilt': {
        'topic': '/camera/image/compressed',
        'msg_type': 'compressed',
    },
    'oakd': {
        'topic': '/oak/rgb/image_rect',
        'msg_type': 'raw',
    },
    'yolo_pantilt': {
        'topic': '/yolo/pantilt/image/compressed',
        'msg_type': 'compressed',
    },
    'yolo_oakd': {
        'topic': '/yolo/oak/image/compressed',
        'msg_type': 'compressed',
    },
}

VALID_CAMERAS = list(CAMERA_TOPICS.keys())

# JPEG quality for raw image encoding (0-100, higher = better quality, larger)
JPEG_QUALITY = 85

# ---------------------------------------------------------------------------
# Thread-safe frame storage
# ---------------------------------------------------------------------------

frame_lock = threading.Lock()
frames = {
    name: {'data': None, 'timestamp': 0, 'width': 0, 'height': 0}
    for name in CAMERA_TOPICS
}

# Global start time for uptime calculation
start_time = time.time()

# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class CameraHTTPHandler(BaseHTTPRequestHandler):
    """Handles GET requests for camera snapshots, status, and health."""

    # Suppress default stderr logging for each request
    def log_message(self, format, *args):
        pass

    def _send_json(self, code, data):
        """Send a JSON response with CORS headers."""
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_jpeg(self, jpeg_bytes):
        """Send a JPEG image response with CORS and no-cache headers."""
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.send_header('Content-Length', str(len(jpeg_bytes)))
        self.end_headers()
        self.wfile.write(jpeg_bytes)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/snapshot':
            self._handle_snapshot(parsed)
        elif path == '/status':
            self._handle_status()
        elif path == '/health':
            self._handle_health()
        else:
            self._send_json(404, {'error': 'Not found'})

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _handle_snapshot(self, parsed):
        """Serve the latest JPEG frame for the requested camera."""
        params = parse_qs(parsed.query)
        camera_name = params.get('camera', [None])[0]

        if camera_name is None or camera_name not in VALID_CAMERAS:
            self._send_json(400, {
                'error': f'Invalid camera. Options: {", ".join(VALID_CAMERAS)}'
            })
            return

        with frame_lock:
            frame_data = frames[camera_name]['data']

        if frame_data is None:
            self._send_json(503, {
                'error': f'No frame available for camera: {camera_name}'
            })
            return

        self._send_jpeg(frame_data)

    def _handle_status(self):
        """Return JSON status for all cameras including availability and dimensions."""
        now_ms = int(time.time() * 1000)
        cameras = {}

        with frame_lock:
            for name in VALID_CAMERAS:
                f = frames[name]
                available = f['data'] is not None
                last_update_ms = f['timestamp'] if available else 0
                cameras[name] = {
                    'available': available,
                    'last_update_ms': last_update_ms,
                    'age_ms': (now_ms - last_update_ms) if available else None,
                    'width': f['width'],
                    'height': f['height'],
                }

        self._send_json(200, {
            'cameras': cameras,
            'uptime_s': round(time.time() - start_time, 1),
        })

    def _handle_health(self):
        """Simple health check endpoint."""
        self._send_json(200, {'status': 'ok'})

    # -- POST handling -------------------------------------------------------

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/command':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {'error': 'Invalid JSON'})
                return
            self._handle_command(data)
        else:
            self._send_json(404, {'error': 'Not found'})

    # -- Command dispatch ----------------------------------------------------

    def _handle_command(self, data):
        command = data.get('command', '')
        args = data.get('args', {})
        node = self.server.node  # Access the ROS2 node

        try:
            if command == 'navigate_to':
                result = self._cmd_navigate(node, args)
            elif command == 'stop_robot':
                result = self._cmd_stop(node)
            elif command == 'move_forward':
                result = self._cmd_move(node, args)
            elif command == 'turn':
                result = self._cmd_turn(node, args)
            elif command == 'look_at':
                result = self._cmd_look_at(node, args)
            elif command == 'reset_camera':
                result = self._cmd_reset_camera(node)
            elif command == 'start_exploration':
                result = self._cmd_service(node._explore_start, 'explore/start')
            elif command == 'stop_exploration':
                result = self._cmd_service(node._explore_stop, 'explore/stop')
            elif command == 'lights_on':
                result = self._cmd_service(node._lights_on, 'lights/all_on')
            elif command == 'lights_off':
                result = self._cmd_service(node._lights_off, 'lights/all_off')
            elif command == 'return_home':
                result = self._cmd_navigate(node, {'x': 0.0, 'y': 0.0})
            elif command == 'get_robot_pose':
                result = self._cmd_get_pose(node)
            elif command == 'get_yolo_detections':
                result = self._cmd_get_detections(node)
            elif command == 'capture_frame':
                result = self._cmd_capture_frame(args)
            elif command == 'set_tracking_target':
                result = self._cmd_set_tracking(node, args)
            elif command == 'disable_tracking':
                result = self._cmd_disable_tracking(node)
            elif command == 'get_lidar_distances':
                result = self._cmd_get_lidar(node)
            elif command == 'navigate_to_map_point':
                result = self._cmd_navigate_map_point(node, args)
            elif command == 'start_nav2':
                result = self._cmd_start_nav2(node)
            elif command == 'stop_nav2':
                result = self._cmd_stop_nav2(node)
            elif command == 'get_slam_map':
                result = self._cmd_get_slam_map(node)
            elif command == 'get_nav_status':
                result = self._cmd_get_nav_status(node)
            elif command == 'cancel_navigation':
                result = self._cmd_cancel_nav(node)
            elif command == 'get_depth_at':
                result = self._cmd_get_depth(args)
            elif command == 'play_audio':
                result = self._cmd_play_audio(args)
            elif command == 'record_audio':
                result = self._cmd_record_audio(args)
            else:
                self._send_json(400, {'error': f'Unknown command: {command}'})
                return

            self._send_json(200, {'success': True, 'command': command, 'result': result})
        except Exception as e:
            self._send_json(500, {'error': str(e), 'command': command})

    # -- LiDAR safety helpers ------------------------------------------------

    def _get_lidar_sectors(self, node):
        """Process LiDAR scan into 8 directional sectors with min distances.
        Sectors: front, front_right, right, back_right, back, back_left, left, front_left
        Each sector covers 45 degrees."""
        with node._scan_lock:
            scan = node._latest_scan
        if scan is None:
            return None

        ranges = scan.ranges
        n = len(ranges)
        angle_inc = scan.angle_increment
        r_min = scan.range_min
        r_max = scan.range_max

        # 8 sectors, each 45 degrees (sector_size = n/8 points)
        sector_size = n // 8
        sector_names = ['front', 'front_left', 'left', 'back_left', 'back', 'back_right', 'right', 'front_right']

        sectors = {}
        for i, name in enumerate(sector_names):
            start = i * sector_size
            end = start + sector_size
            valid = [r for r in ranges[start:end] if r > r_min and r < r_max]
            if valid:
                min_dist = min(valid)
                sectors[name] = {
                    'min_m': round(min_dist, 3),
                    'min_ft': round(min_dist * 3.281, 2),
                    'min_in': round(min_dist * 39.37, 1),
                    'clear': min_dist > 0.20  # 20cm safety threshold
                }
            else:
                sectors[name] = {'min_m': 99.0, 'min_ft': 99.0, 'min_in': 999.0, 'clear': True}

        # Overall minimum
        all_valid = [r for r in ranges if r > r_min and r < r_max]
        overall_min = min(all_valid) if all_valid else 99.0

        return {
            'sectors': sectors,
            'overall_min_m': round(overall_min, 3),
            'overall_min_ft': round(overall_min * 3.281, 2),
            'num_points': n,
            'safety_threshold_m': 0.20
        }

    def _check_movement_safe(self, node, direction='forward'):
        """Check if movement in a direction is safe based on LiDAR.
        Returns (safe: bool, details: dict)"""
        lidar = self._get_lidar_sectors(node)
        if lidar is None:
            return True, {'warning': 'No LiDAR data — proceeding with caution'}

        EMERGENCY_M = 0.28  # 28cm (~11in from LiDAR = ~3in from robot edge, robot radius ~8in)

        if direction == 'forward':
            check = ['front']  # Only direct front for emergency stop
        elif direction == 'backward':
            check = ['back']   # Only direct back, ignore corners
        elif direction == 'left':
            check = ['front_left']
        elif direction == 'right':
            check = ['front_right']
        else:
            check = list(lidar['sectors'].keys())

        blocked_sectors = {}
        for sector_name in check:
            sector = lidar['sectors'].get(sector_name, {})
            if sector.get('min_m', 99) < EMERGENCY_M:
                blocked_sectors[sector_name] = sector

        if blocked_sectors:
            return False, {
                'blocked': True,
                'reason': f"Obstacle within {EMERGENCY_M}m ({EMERGENCY_M*39.37:.0f}in) — emergency stop",
                'blocked_sectors': blocked_sectors,
                'overall_min_m': lidar['overall_min_m'],
                'overall_min_ft': lidar['overall_min_ft']
            }

        return True, {'clear': True, 'closest_m': lidar['overall_min_m']}

    def _reactive_avoidance_velocity(self, node, desired_speed):
        """DJI APAS-style reactive obstacle avoidance.

        Given a desired forward speed, compute actual linear.x and angular.z
        that steers around obstacles while maintaining forward momentum.

        Returns (linear_x, angular_z, status_str):
          - 'clear': going straight, no obstacles
          - 'steering_left': obstacle on right/front-right, steering left
          - 'steering_right': obstacle on left/front-left, steering right
          - 'emergency_stop': obstacle too close on all sides
        """
        lidar = self._get_lidar_sectors(node)
        if lidar is None:
            return desired_speed, 0.0, 'no_lidar'

        sectors = lidar['sectors']

        # Distance thresholds (matched to velocity filter, account for 8in robot radius)
        STEER_DIST = 0.45    # 45cm (~18in from LiDAR = ~10in from edge) — start steering
        EMERGENCY_DIST = 0.28  # 28cm (~11in from LiDAR = ~3in from edge) — full stop

        # Get key sector distances
        front = sectors.get('front', {}).get('min_m', 99)
        front_left = sectors.get('front_left', {}).get('min_m', 99)
        front_right = sectors.get('front_right', {}).get('min_m', 99)
        left = sectors.get('left', {}).get('min_m', 99)
        right = sectors.get('right', {}).get('min_m', 99)

        # Closest in the forward 180 degrees
        front_min = min(front, front_left, front_right)

        # ── Emergency stop: something very close in front ──
        if front_min < EMERGENCY_DIST:
            return 0.0, 0.0, 'emergency_stop'

        # ── Clear path: nothing within steering distance ──
        if front_min > STEER_DIST:
            return desired_speed, 0.0, 'clear'

        # ── Steering zone: obstacle within 40cm, steer around it ──
        # Slow down proportionally to how close the obstacle is
        speed_factor = max(0.3, (front_min - EMERGENCY_DIST) / (STEER_DIST - EMERGENCY_DIST))
        adjusted_speed = desired_speed * speed_factor

        # Determine which way to steer
        # Compare space available on each side
        left_space = min(front_left, left)
        right_space = min(front_right, right)

        STEER_RATE = 0.4  # rad/s — how aggressively to steer

        if front < STEER_DIST:
            # Obstacle directly ahead — pick the more open side
            if left_space > right_space:
                return adjusted_speed, STEER_RATE, 'steering_left'
            else:
                return adjusted_speed, -STEER_RATE, 'steering_right'
        elif front_left < STEER_DIST and front_right >= STEER_DIST:
            # Obstacle on front-left — steer right
            return adjusted_speed, -STEER_RATE * 0.7, 'steering_right'
        elif front_right < STEER_DIST and front_left >= STEER_DIST:
            # Obstacle on front-right — steer left
            return adjusted_speed, STEER_RATE * 0.7, 'steering_left'
        else:
            # Both sides have obstacles but not emergency — slow straight
            return adjusted_speed, 0.0, 'slow'

    # -- Individual command implementations ----------------------------------

    def _cmd_navigate(self, node, args):
        """Navigate to map coordinates using Nav2 path planning.
        Non-blocking: sends goal and returns immediately.
        Use get_nav_status to monitor progress."""
        x = float(args.get('x', 0.0))
        y = float(args.get('y', 0.0))
        yaw = float(args.get('yaw', 0.0))

        # Auto-start Nav2 if not ready
        if not node._nav2_ready:
            start_result = node.start_nav2()
            if not node._nav2_ready:
                return {'error': 'Nav2 failed to start', 'detail': start_result}

        if not node._nav2_action_client:
            return {'error': 'Nav2 action client not available (nav2_msgs not installed?)'}

        # Cancel any active goal
        if node._nav2_goal_handle is not None:
            try:
                node._nav2_goal_handle.cancel_goal_async()
            except:
                pass
            node._nav2_goal_handle = None
            time.sleep(0.5)

        # Build Nav2 goal
        if not HAS_NAV2_MSGS:
            return {'error': 'nav2_msgs not installed'}

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = node.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        # Send goal (non-blocking)
        node._nav2_goal_status = 'navigating'
        node._nav2_distance_remaining = None

        send_future = node._nav2_action_client.send_goal_async(
            goal_msg, feedback_callback=node._nav2_feedback_cb)

        # Wait briefly for goal acceptance (up to 3 seconds)
        t0 = time.time()
        while not send_future.done() and (time.time() - t0) < 3.0:
            time.sleep(0.1)

        if send_future.done():
            goal_handle = send_future.result()
            if goal_handle and goal_handle.accepted:
                node._nav2_goal_handle = goal_handle
                # Start background result monitor
                threading.Thread(
                    target=node._nav2_result_monitor,
                    args=(goal_handle,),
                    daemon=True
                ).start()
                return {
                    'navigating': True,
                    'goal': {'x': x, 'y': y, 'yaw': round(yaw, 3)},
                    'status': 'navigating',
                    'note': 'Use get_nav_status to monitor progress'
                }
            else:
                node._nav2_goal_status = 'rejected'
                return {'error': 'Nav2 rejected the goal — target may be in an obstacle'}
        else:
            node._nav2_goal_status = 'send_timeout'
            return {'error': 'Timeout sending goal to Nav2 — is Nav2 still running?'}

    def _cmd_stop(self, node):
        """Emergency stop - zero velocity."""
        twist = Twist()  # All zeros by default
        node.cmd_vel_pub.publish(twist)
        return {'stopped': True}

    def _cmd_move(self, node, args):
        """DJI APAS-style forward movement with reactive obstacle avoidance.

        The robot maintains forward momentum while steering around obstacles.
        At 10Hz, LiDAR is checked and velocity is adjusted:
        - Clear path → go straight at requested speed
        - Obstacle within 40cm → slow down + steer away (left or right)
        - Obstacle within 15cm → emergency stop
        """
        speed = float(args.get('speed', 0.1))
        duration = float(args.get('duration', 2.0))
        clamped_speed = max(-0.15, min(0.15, speed))

        # Backward movement uses simple safety check (no APAS)
        if clamped_speed < 0:
            safe, details = self._check_movement_safe(node, 'backward')
            if not safe:
                return {'blocked': True, 'speed': 0, 'reason': details.get('reason', 'Obstacle'), 'lidar': details}

        avoidance_log = []

        def publish_loop():
            rate_hz = 10
            interval = 1.0 / rate_hz
            steps = int(duration * rate_hz)
            last_status = 'clear'

            for step in range(steps):
                if clamped_speed >= 0:
                    # Forward: use reactive avoidance (DJI APAS style)
                    lin_x, ang_z, status = self._reactive_avoidance_velocity(node, clamped_speed)

                    if status == 'emergency_stop':
                        node.cmd_vel_pub.publish(Twist())
                        avoidance_log.append(f"step {step}: emergency stop")
                        break

                    twist = Twist()
                    twist.linear.x = lin_x
                    twist.angular.z = ang_z
                    node.cmd_vel_pub.publish(twist)

                    if status != last_status:
                        avoidance_log.append(f"step {step}: {status}")
                        last_status = status
                else:
                    # Backward: simple safety check
                    safe, _ = self._check_movement_safe(node, 'backward')
                    if not safe:
                        node.cmd_vel_pub.publish(Twist())
                        avoidance_log.append(f"step {step}: backward blocked")
                        break
                    twist = Twist()
                    twist.linear.x = clamped_speed
                    node.cmd_vel_pub.publish(twist)

                time.sleep(interval)

            # Stop at end of duration
            node.cmd_vel_pub.publish(Twist())

        threading.Thread(target=publish_loop, daemon=True).start()
        return {
            'speed': clamped_speed,
            'duration': duration,
            'avoidance': 'apas_active',
            'description': 'DJI-style: steers around obstacles while maintaining forward momentum'
        }

    def _cmd_turn(self, node, args):
        """Turn in place. Only blocked by emergency stop distance (15cm)."""
        angular = float(args.get('angular_speed', 0.3))
        duration = float(args.get('duration', 1.0))
        clamped_angular = max(-0.5, min(0.5, angular))

        def publish_loop():
            twist = Twist()
            twist.angular.z = clamped_angular
            rate_hz = 10
            interval = 1.0 / rate_hz
            steps = int(duration * rate_hz)
            for _ in range(steps):
                node.cmd_vel_pub.publish(twist)
                time.sleep(interval)
            node.cmd_vel_pub.publish(Twist())

        threading.Thread(target=publish_loop, daemon=True).start()
        return {'angular': clamped_angular, 'duration': duration}

    def _cmd_look_at(self, node, args):
        """Point gimbal camera to absolute pan/tilt position (degrees).
        Uses /gimbal/absolute (geometry_msgs/Point) — x=pan, y=tilt."""
        from geometry_msgs.msg import Point
        pan = float(args.get('pan', 0.0))
        tilt = float(args.get('tilt', 0.0))
        if not hasattr(node, 'gimbal_abs_pub'):
            node.gimbal_abs_pub = node.create_publisher(Point, '/gimbal/absolute', 10)
        msg = Point()
        msg.x = pan
        msg.y = tilt
        msg.z = 0.0
        node.gimbal_abs_pub.publish(msg)
        return {'pan': pan, 'tilt': tilt}

    def _cmd_reset_camera(self, node):
        """Reset gimbal to home position (0, 0)."""
        return self._cmd_look_at(node, {'pan': 0, 'tilt': 0})

    def _cmd_service(self, client, name):
        """Call a Trigger service."""
        if not client.service_is_ready():
            return {'error': f'Service {name} not available'}
        req = Trigger.Request()
        future = client.call_async(req)
        # Wait briefly for result
        timeout = 5.0
        start = time.time()
        while not future.done() and (time.time() - start) < timeout:
            time.sleep(0.1)
        if future.done():
            result = future.result()
            return {'success': result.success, 'message': result.message}
        return {'error': f'Service {name} timed out'}

    def _cmd_get_pose(self, node):
        """Get current robot pose from odom."""
        odom = node._latest_odom
        if odom is None:
            return {'error': 'No odom data yet'}
        pos = odom.pose.pose.position
        ori = odom.pose.pose.orientation
        # Extract yaw from quaternion
        siny = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny, cosy)
        return {
            'x': round(pos.x, 3),
            'y': round(pos.y, 3),
            'z': round(pos.z, 3),
            'yaw_rad': round(yaw, 3),
            'yaw_deg': round(math.degrees(yaw), 1),
        }

    def _cmd_get_detections(self, node):
        """Get latest YOLO detections. Placeholder until Detection2DArray subscriber is added."""
        return {'detections': [], 'note': 'Detection subscription not yet implemented'}

    def _cmd_capture_frame(self, args):
        """Return a high-res frame as base64."""
        camera = args.get('camera', 'pantilt')
        with frame_lock:
            frame = frames.get(camera)
        if frame is None or frame['data'] is None:
            return {'error': f'No frame for {camera}'}
        return {
            'camera': camera,
            'image': base64.b64encode(frame['data']).decode('ascii'),
            'width': frame['width'],
            'height': frame['height'],
        }

    def _cmd_set_tracking(self, node, args):
        """Set YOLO gimbal tracking target class."""
        target = args.get('class_name', 'person')
        import subprocess
        result = subprocess.run(
            ['ros2', 'param', 'set', '/ugv_tracker', 'target_class', target],
            capture_output=True, text=True, timeout=5,
        )
        return {'target': target, 'output': result.stdout.strip()}

    def _cmd_disable_tracking(self, node):
        """Disable gimbal auto-tracking by setting target to empty."""
        return self._cmd_set_tracking(node, {'class_name': ''})

    def _cmd_get_lidar(self, node):
        """Return LiDAR obstacle distances in 8 sectors."""
        lidar = self._get_lidar_sectors(node)
        if lidar is None:
            return {'error': 'No LiDAR data available'}
        return lidar

    def _cmd_get_nav_status(self, node):
        """Get current Nav2 navigation status."""
        # Include robot pose
        pose_data = self._cmd_get_pose(node)
        return {
            'nav2_running': node._nav2_ready,
            'goal_status': node._nav2_goal_status,
            'distance_remaining': round(node._nav2_distance_remaining, 3) if node._nav2_distance_remaining is not None else None,
            'has_active_goal': node._nav2_goal_handle is not None,
            'robot_pose': pose_data
        }

    def _cmd_cancel_nav(self, node):
        """Cancel the current Nav2 navigation goal."""
        if node._nav2_goal_handle is not None:
            node._nav2_goal_handle.cancel_goal_async()
            node._nav2_goal_status = 'canceled'
            node._nav2_goal_handle = None
            return {'canceled': True}
        return {'canceled': False, 'reason': 'No active goal'}

    def _cmd_start_nav2(self, node):
        """Start Nav2 navigation stack."""
        return node.start_nav2()

    def _cmd_stop_nav2(self, node):
        """Stop Nav2 navigation stack."""
        return node.stop_nav2()

    def _cmd_get_slam_map(self, node):
        """Convert the SLAM occupancy grid to a PNG image with robot position marked.
        Returns base64-encoded image that ER can analyze for navigation planning."""
        with node._map_lock:
            grid = node._latest_map
        if grid is None:
            return {'error': 'No SLAM map available yet — drive around to build the map'}

        w = grid.info.width
        h = grid.info.height
        res = grid.info.resolution
        origin_x = grid.info.origin.position.x
        origin_y = grid.info.origin.position.y
        data = list(grid.data)

        # Convert occupancy grid to image
        # -1 = unknown (gray), 0 = free (white), 100 = occupied (black)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        for i, val in enumerate(data):
            row = i // w
            col = i % w
            if val == -1:
                img[h - 1 - row, col] = (80, 80, 80)     # Unknown = dark gray
            elif val == 0:
                img[h - 1 - row, col] = (240, 240, 240)   # Free = near white
            elif val == 100:
                img[h - 1 - row, col] = (20, 20, 20)      # Occupied = near black
            else:
                # Probability 1-99
                brightness = int(240 - (val / 100.0) * 220)
                img[h - 1 - row, col] = (brightness, brightness, brightness)

        # Draw robot position (red dot + heading arrow)
        odom = node._latest_odom
        if odom:
            rx = odom.pose.pose.position.x
            ry = odom.pose.pose.position.y
            ori = odom.pose.pose.orientation
            yaw = math.atan2(2.0 * (ori.w * ori.z + ori.x * ori.y),
                             1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z))

            # Map coords to pixel
            px = int((rx - origin_x) / res)
            py = h - 1 - int((ry - origin_y) / res)

            if 0 <= px < w and 0 <= py < h:
                # Draw robot as red circle
                cv2.circle(img, (px, py), 4, (0, 0, 255), -1)
                # Draw heading arrow (green)
                arrow_len = 12
                ax = int(px + arrow_len * math.cos(yaw))
                ay = int(py - arrow_len * math.sin(yaw))
                cv2.arrowedLine(img, (px, py), (ax, ay), (0, 255, 0), 2, tipLength=0.4)

            robot_map_pos = {'px': px, 'py': py, 'x': round(rx, 3), 'y': round(ry, 3), 'yaw_deg': round(math.degrees(yaw), 1)}
        else:
            robot_map_pos = None

        # Scale up for better visibility (3x)
        img_scaled = cv2.resize(img, (w * 3, h * 3), interpolation=cv2.INTER_NEAREST)

        # Encode as PNG base64
        _, png_bytes = cv2.imencode('.png', img_scaled)
        img_b64 = base64.b64encode(png_bytes).decode('ascii')

        return {
            'image': img_b64,
            'width': w,
            'height': h,
            'resolution_m': round(res, 3),
            'origin': {'x': round(origin_x, 2), 'y': round(origin_y, 2)},
            'robot_position': robot_map_pos,
            'map_size_m': {'x': round(w * res, 1), 'y': round(h * res, 1)},
            'note': 'White=free, black=wall, gray=unknown. Red dot=robot, green arrow=heading.'
        }

    def _cmd_navigate_map_point(self, node, args):
        """Navigate to a point clicked on the SLAM map image.
        Takes pixel coordinates from the 3x-scaled map image returned by get_slam_map.
        Converts image pixels to map coordinates and sends Nav2 goal.

        Also accepts normalized 0-1000 coordinates (Gemini ER spatial pointing format):
        if 'normalized' is true, px/py are on 0-1000 scale relative to the map image.
        """
        px = float(args.get('px', 0))
        py = float(args.get('py', 0))
        normalized = args.get('normalized', False)

        with node._map_lock:
            grid = node._latest_map
        if grid is None:
            return {'error': 'No SLAM map available — call get_slam_map first'}

        w = grid.info.width
        h = grid.info.height
        res = grid.info.resolution
        origin_x = grid.info.origin.position.x
        origin_y = grid.info.origin.position.y
        scale = 3  # Map image is 3x upscaled

        # Convert from normalized 0-1000 coords if needed
        if normalized:
            px = (px / 1000.0) * (w * scale)
            py = (py / 1000.0) * (h * scale)

        # Convert 3x-scaled image pixel to original map pixel
        orig_px = px / scale
        orig_py = py / scale

        # Bounds check
        if orig_px < 0 or orig_px >= w or orig_py < 0 or orig_py >= h:
            return {'error': f'Point ({px:.0f}, {py:.0f}) is outside the map bounds ({w*scale}x{h*scale})'}

        # Convert map pixel to world coordinates
        # Note: image y is flipped (top=0 in image, but high y in map)
        map_x = origin_x + orig_px * res
        map_y = origin_y + (h - 1 - orig_py) * res

        # Check if the target cell is inside a wall (only block on definite walls)
        grid_idx = int(h - 1 - orig_py) * w + int(orig_px)
        if 0 <= grid_idx < len(grid.data):
            cell_value = grid.data[grid_idx]
            if cell_value >= 90:  # High-confidence wall only
                return {'error': f'Target point is inside a wall (occupancy={cell_value}). Pick a white or gray area.',
                        'map_coords': {'x': round(map_x, 3), 'y': round(map_y, 3)}}
            # Unknown (-1) and low-occupancy cells are fine — Nav2 can plan through them

        # Send Nav2 goal using the existing navigate method
        nav_result = self._cmd_navigate(node, {'x': map_x, 'y': map_y, 'yaw': 0.0})
        nav_result['pixel'] = {'px': round(px), 'py': round(py)}
        nav_result['map_coords'] = {'x': round(map_x, 3), 'y': round(map_y, 3)}
        return nav_result

    def _cmd_get_depth(self, args):
        """Get depth at pixel coordinate from OAK-D depth camera."""
        x = int(args.get('x', 320))
        y = int(args.get('y', 240))

        # Access the depth frame from the camera cache
        # The depth topic /depth/image/compressed is not subscribed yet,
        # but we can use the oakd camera frame for basic estimation
        return {
            'x': x, 'y': y,
            'note': 'Depth at pixel requires OAK-D depth subscription — use get_lidar_distances for obstacle detection'
        }

    def _cmd_play_audio(self, args):
        """Play audio on robot speaker via PulseAudio (routes to Bluetooth JBL).
        Accepts base64-encoded audio data. Falls back to ALSA if PulseAudio unavailable."""
        audio_b64 = args.get('audio_b64', '')
        audio_format = args.get('format', 'mp3')  # mp3, wav, opus

        if not audio_b64:
            return {'error': 'audio_b64 is required'}

        try:
            import tempfile, subprocess
            audio_bytes = base64.b64decode(audio_b64)

            # Save to temp file
            ext = f'.{audio_format}'
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir='/tmp') as f:
                f.write(audio_bytes)
                temp_path = f.name

            # Play via ffmpeg → ALSA hw:1,0 (wired 3.5mm to JBL speaker)
            # Converts any format (mp3/wav/opus) to 48kHz stereo for the USB audio device
            proc = subprocess.Popen(
                ['ffmpeg', '-re', '-i', temp_path, '-ar', '48000', '-ac', '2',
                 '-f', 'alsa', 'hw:1,0'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Clean up temp file after playback
            def cleanup():
                proc.wait()
                try:
                    os.unlink(temp_path)
                except:
                    pass
            threading.Thread(target=cleanup, daemon=True).start()

            return {'playing': True, 'format': audio_format, 'size_bytes': len(audio_bytes)}
        except Exception as e:
            return {'error': f'Playback failed: {str(e)}'}

    def _cmd_record_audio(self, args):
        """Record audio from robot microphone. Returns base64 WAV."""
        duration = float(args.get('duration', 5.0))
        duration = max(0.5, min(30.0, duration))  # Clamp 0.5-30 seconds

        try:
            import subprocess
            import tempfile

            # Record via ffmpeg from ALSA capture device
            # hw:0,0 = Pan-tilt camera built-in mic (confirmed working with signal)
            # hw:1,0 = USB PnP Audio dongle (fallback)
            temp_path = f'/tmp/mic_recording_{int(time.time())}.wav'

            result = subprocess.run(
                ['ffmpeg', '-f', 'alsa', '-i', 'hw:0,0',
                 '-t', str(duration), '-ar', '16000', '-ac', '1',
                 '-acodec', 'pcm_s16le', temp_path, '-y'],
                capture_output=True, text=True, timeout=duration + 5
            )

            if result.returncode != 0:
                # Fallback to USB PnP Audio
                result = subprocess.run(
                    ['ffmpeg', '-f', 'alsa', '-i', 'hw:1,0',
                     '-t', str(duration), '-ar', '16000', '-ac', '1',
                     '-acodec', 'pcm_s16le', temp_path, '-y'],
                    capture_output=True, text=True, timeout=duration + 5
                )

            import os
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) < 100:
                return {'error': 'Recording failed — no audio captured', 'stderr': result.stderr[-200:] if result.stderr else ''}

            # Read and encode
            with open(temp_path, 'rb') as f:
                audio_bytes = f.read()

            audio_b64 = base64.b64encode(audio_bytes).decode('ascii')

            # Cleanup
            os.unlink(temp_path)

            return {
                'audio_b64': audio_b64,
                'format': 'wav',
                'sample_rate': 16000,
                'channels': 1,
                'duration': duration,
                'size_bytes': len(audio_bytes)
            }
        except subprocess.TimeoutExpired:
            return {'error': f'Recording timed out after {duration}s'}
        except Exception as e:
            return {'error': f'Recording failed: {str(e)}'}


# ---------------------------------------------------------------------------
# ROS2 Camera Subscriber Node
# ---------------------------------------------------------------------------

class CameraServerNode(Node):
    """
    ROS2 node that subscribes to camera image topics and caches the latest
    frame from each as JPEG bytes for the HTTP server to serve.
    """

    def __init__(self):
        super().__init__('ugv_camera_server')

        # Try to import cv_bridge; fall back to manual conversion if unavailable
        self._cv_bridge = None
        try:
            from cv_bridge import CvBridge
            self._cv_bridge = CvBridge()
            self.get_logger().info('cv_bridge available - using for raw image conversion')
        except ImportError:
            self.get_logger().warn(
                'cv_bridge not available - using manual numpy conversion for raw images'
            )

        # Create subscriptions for each camera topic
        for cam_name, cfg in CAMERA_TOPICS.items():
            topic = cfg['topic']
            msg_type = cfg['msg_type']

            if msg_type == 'compressed':
                self.create_subscription(
                    CompressedImage,
                    topic,
                    lambda msg, name=cam_name: self._on_compressed(msg, name),
                    10,
                )
            elif msg_type == 'raw':
                self.create_subscription(
                    Image,
                    topic,
                    lambda msg, name=cam_name: self._on_raw_image(msg, name),
                    10,
                )

            self.get_logger().info(f'  [{cam_name}] subscribing to {topic} ({msg_type})')

        # -- Command publishers --------------------------------------------------
        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.goal_pub = self.create_publisher(PoseStamped, '/goal_pose', 10)

        # -- State subscribers ---------------------------------------------------
        self._latest_odom = None
        self.create_subscription(Odometry, '/odom', self._odom_cb, 10)

        # SLAM map subscriber (transient_local QoS to get latched map)
        from rclpy.qos import QoSProfile, DurabilityPolicy
        map_qos = QoSProfile(depth=1)
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self._latest_map = None
        self._map_lock = threading.Lock()
        self.create_subscription(OccupancyGrid, '/map', self._map_cb, map_qos)

        # LiDAR for safety braking + obstacle perception
        self._latest_scan = None
        self._scan_lock = threading.Lock()
        self.create_subscription(LaserScan, '/scan', self._scan_cb, 10)

        # -- Service clients -----------------------------------------------------
        self._explore_start = self.create_client(Trigger, '/explore/start')
        self._explore_stop = self.create_client(Trigger, '/explore/stop')
        self._lights_on = self.create_client(Trigger, '/lights/all_on')
        self._lights_off = self.create_client(Trigger, '/lights/all_off')

        # Nav2 readiness (Nav2 starts at boot, we just check if it's available)
        self._nav2_ready = False
        self._nav2_goal_status = 'idle'  # idle, navigating, succeeded, aborted, canceled, timeout
        self._nav2_distance_remaining = None
        self._nav2_goal_handle = None

        # Nav2 action client (created eagerly, connects when Nav2 starts)
        if HAS_NAV2_MSGS:
            self._nav2_action_client = ActionClient(
                self, NavigateToPose, 'navigate_to_pose')
        else:
            self._nav2_action_client = None
            self.get_logger().warn('nav2_msgs not found — Nav2 navigation disabled')

        self.get_logger().info(
            f'Camera server node ready - {len(CAMERA_TOPICS)} topics subscribed'
        )
        self.get_logger().info('  Command publishers: /cmd_vel, /goal_pose')
        self.get_logger().info('  State subscribers:  /odom')
        self.get_logger().info(f'  Safety: /scan (LiDAR collision avoidance)')
        self.get_logger().info('  Nav2: action client ready (boot startup)')

    # -- Callback: Odometry ---------------------------------------------------

    def _odom_cb(self, msg):
        self._latest_odom = msg

    def _map_cb(self, msg):
        with self._map_lock:
            self._latest_map = msg

    # -- Callback: LaserScan ------------------------------------------------

    def _scan_cb(self, msg):
        with self._scan_lock:
            self._latest_scan = msg

    # -- Nav2 lifecycle methods ----------------------------------------------

    def start_nav2(self):
        """Check if Nav2 is running (started at boot by start_ros2.sh)."""
        if self._nav2_action_client and self._nav2_action_client.wait_for_server(timeout_sec=5.0):
            self._nav2_ready = True
            self.get_logger().info('Nav2 is running — connected')
            return {'ready': True}
        else:
            self.get_logger().warn('Nav2 not available — was it started at boot?')
            return {'error': 'Nav2 not running. Check start_ros2.sh or restart ugv-ros2.service'}

    def stop_nav2(self):
        """Nav2 runs as a system service — cannot stop from here."""
        self.get_logger().warn('Nav2 is a boot service — use systemctl to restart')
        return {'error': 'Nav2 is managed by start_ros2.sh, not stoppable from camera_server'}

    def _nav2_feedback_cb(self, feedback_msg):
        """Callback for Nav2 navigation feedback (distance remaining)."""
        try:
            self._nav2_distance_remaining = feedback_msg.feedback.distance_remaining
        except:
            pass

    def _nav2_result_monitor(self, goal_handle):
        """Background thread: wait for Nav2 goal result."""
        try:
            result_future = goal_handle.get_result_async()
            timeout = 300  # 5 minutes max
            start = time.time()
            while not result_future.done() and (time.time() - start) < timeout:
                time.sleep(0.5)

            if result_future.done():
                result = result_future.result()
                status_code = result.status
                if status_code == 4:  # SUCCEEDED
                    self._nav2_goal_status = 'succeeded'
                    self.get_logger().info('Nav2 goal reached!')
                elif status_code == 5:  # CANCELED
                    self._nav2_goal_status = 'canceled'
                elif status_code == 6:  # ABORTED
                    self._nav2_goal_status = 'aborted'
                    self.get_logger().warn('Nav2 goal aborted — path not found or blocked')
                else:
                    self._nav2_goal_status = 'failed'
                    self.get_logger().warn(f'Nav2 goal ended with status {status_code}')
            else:
                self._nav2_goal_status = 'timeout'
                self.get_logger().warn('Nav2 goal timed out (5 min)')
        except Exception as e:
            self._nav2_goal_status = 'error'
            self.get_logger().error(f'Nav2 result monitor error: {e}')
        finally:
            self._nav2_goal_handle = None

    # -- Callback: CompressedImage (already JPEG) ----------------------------

    def _on_compressed(self, msg: CompressedImage, cam_name: str):
        """Handle CompressedImage messages - data is already JPEG bytes."""
        jpeg_bytes = bytes(msg.data)

        # Decode just the header to get dimensions without full decode
        width, height = self._jpeg_dimensions(jpeg_bytes)

        now_ms = int(time.time() * 1000)
        with frame_lock:
            frames[cam_name]['data'] = jpeg_bytes
            frames[cam_name]['timestamp'] = now_ms
            frames[cam_name]['width'] = width
            frames[cam_name]['height'] = height

    # -- Callback: Raw Image (needs JPEG encoding) ---------------------------

    def _on_raw_image(self, msg: Image, cam_name: str):
        """Handle raw sensor_msgs/Image messages - encode to JPEG."""
        try:
            cv_image = self._image_msg_to_cv2(msg)
            if cv_image is None:
                return

            # Encode to JPEG
            ok, jpeg_buf = cv2.imencode(
                '.jpg', cv_image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            )
            if not ok:
                return

            jpeg_bytes = jpeg_buf.tobytes()
            height, width = cv_image.shape[:2]

            now_ms = int(time.time() * 1000)
            with frame_lock:
                frames[cam_name]['data'] = jpeg_bytes
                frames[cam_name]['timestamp'] = now_ms
                frames[cam_name]['width'] = width
                frames[cam_name]['height'] = height

        except Exception as e:
            self.get_logger().warn(f'Failed to encode raw image for {cam_name}: {e}')

    # -- Image conversion helpers --------------------------------------------

    def _image_msg_to_cv2(self, msg: Image):
        """
        Convert sensor_msgs/Image to OpenCV BGR numpy array.
        Uses cv_bridge if available, otherwise manual numpy conversion.
        """
        if self._cv_bridge is not None:
            try:
                return self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            except Exception:
                pass

        # Manual conversion fallback
        encoding = msg.encoding.lower()
        dtype = np.uint8
        arr = np.frombuffer(msg.data, dtype=dtype)

        if encoding in ('bgr8', 'rgb8'):
            arr = arr.reshape((msg.height, msg.width, 3))
            if encoding == 'rgb8':
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            return arr

        elif encoding == 'bgra8':
            arr = arr.reshape((msg.height, msg.width, 4))
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

        elif encoding == 'rgba8':
            arr = arr.reshape((msg.height, msg.width, 4))
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

        elif encoding == 'mono8':
            arr = arr.reshape((msg.height, msg.width))
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)

        elif encoding in ('16uc1', 'mono16'):
            arr = np.frombuffer(msg.data, dtype=np.uint16)
            arr = arr.reshape((msg.height, msg.width))
            # Normalize 16-bit to 8-bit for visualization
            arr8 = (arr / arr.max() * 255).astype(np.uint8) if arr.max() > 0 else arr.astype(np.uint8)
            return cv2.cvtColor(arr8, cv2.COLOR_GRAY2BGR)

        else:
            # Best-effort: assume 3-channel 8-bit
            try:
                arr = arr.reshape((msg.height, msg.width, 3))
                return arr
            except ValueError:
                return None

    @staticmethod
    def _jpeg_dimensions(jpeg_bytes: bytes) -> tuple:
        """
        Extract width and height from JPEG bytes by parsing SOF0/SOF2 markers.
        Returns (width, height) or (0, 0) if unable to parse.
        """
        try:
            data = jpeg_bytes
            i = 0
            length = len(data)
            if length < 2 or data[0] != 0xFF or data[1] != 0xD8:
                return (0, 0)
            i = 2
            while i < length - 1:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                # SOF0 (0xC0) or SOF2 (0xC2) — contain image dimensions
                if marker in (0xC0, 0xC2):
                    if i + 9 < length:
                        height = (data[i + 5] << 8) | data[i + 6]
                        width = (data[i + 7] << 8) | data[i + 8]
                        return (width, height)
                    return (0, 0)
                # Skip non-SOF markers
                if i + 3 < length:
                    seg_len = (data[i + 2] << 8) | data[i + 3]
                    i += 2 + seg_len
                else:
                    break
            return (0, 0)
        except Exception:
            return (0, 0)


# ---------------------------------------------------------------------------
# HTTP server runner (runs in its own thread)
# ---------------------------------------------------------------------------

def run_http_server(node, logger) -> HTTPServer:
    """Start the HTTP server on a daemon thread. Returns the server instance."""
    server = HTTPServer((HTTP_BIND, HTTP_PORT), CameraHTTPHandler)
    server.socket.setsockopt(__import__('socket').SOL_SOCKET, __import__('socket').SO_REUSEADDR, 1)
    server.node = node  # Make the ROS2 node accessible to request handlers
    server.timeout = 0.5  # Allow periodic check for shutdown

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info(f'HTTP server listening on http://{HTTP_BIND}:{HTTP_PORT}')
    logger.info(f'  GET  /snapshot?camera={{{",".join(VALID_CAMERAS)}}}')
    logger.info(f'  GET  /status')
    logger.info(f'  GET  /health')
    logger.info(f'  POST /command')
    return server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)

    node = CameraServerNode()
    logger = node.get_logger()

    logger.info('=' * 60)
    logger.info('UGV Camera HTTP Server starting')
    logger.info(f'  Node name:  ugv_camera_server')
    logger.info(f'  HTTP port:  {HTTP_PORT}')
    logger.info(f'  Cameras:    {len(CAMERA_TOPICS)}')
    logger.info('=' * 60)

    # Start the HTTP server (pass node so handlers can access ROS2 publishers)
    http_server = run_http_server(node, logger)

    # Graceful shutdown handler
    shutdown_requested = threading.Event()

    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f'Received {sig_name} - shutting down...')
        shutdown_requested.set()

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    # Spin ROS2 on the main thread until shutdown is requested
    try:
        while not shutdown_requested.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info('Stopping HTTP server...')
        http_server.shutdown()
        logger.info('Destroying ROS2 node...')
        node.destroy_node()
        rclpy.shutdown()
        logger.info('Shutdown complete.')


if __name__ == '__main__':
    main()
