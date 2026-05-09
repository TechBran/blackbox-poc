# Odometry & SLAM Accuracy Overhaul

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the entire odometry→SLAM pipeline so the UGV Beast produces accurate maps when driving. Each task is verified before moving to the next.

**Architecture:** Six sequential fixes ordered by impact: (1) correct track width via empirical spin test, (2) increase encoder publish rate from 5Hz to 20Hz+, (3) calibrate IMU gyroscope bias at boot, (4) fix EKF config double-counting bug and re-enable calibrated IMU, (5) upgrade RTAB-Map to use its own visual odometry with encoder odom as a guess, (6) full integration test. Each task includes verification criteria that must pass before proceeding.

**Tech Stack:** Python3, ROS2 Humble, pyserial, robot_localization EKF, imu_filter_madgwick, rtabmap_odom, rtabmap_slam

**Robot:** Waveshare UGV Beast PT, Jetson Orin Nano 8GB, ESP32 serial at 115200 baud, YDLIDAR LD19, OAK-D Lite

**SSH:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23` (container)  
**Host:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no jetson@192.168.1.155 -p 22` (docker host)

---

## Physical Measurements (measured 2026-04-11)

- Outside-to-outside track span: 7.25" = 184.15mm
- Inside-to-inside gap between tracks: 3.75" = 95.25mm
- Each track width: 1.75" = 44.45mm
- **Physical center-to-center: 5.5" = 139.7mm = 0.1397m**
- Waveshare firmware uses: 0.175m (25% larger than physical — their skid-steer estimate)
- Research: effective track width for skid-steer = physical × 1.4–1.47 = **0.196–0.205m**
- **Must calibrate empirically with spin test (Task 1)**

---

### Task 1: Track Width Calibration — Empirical Spin Test

**Why this is first:** Wrong track width = wrong heading per turn = cumulative map error. This is the single highest-impact fix. Every other fix is wasted if this constant is wrong.

**Files:**
- Create: `/home/ws/ugv_ws/track_width_calibration.py` (temporary test script)
- Modify: `/home/ws/ugv_ws/ugv_serial_node.py:38` (TRACK_WIDTH constant)

**Step 1: Create calibration script**

Deploy this script to the Jetson. It records encoder yaw before and after a commanded rotation, then calculates the correction factor.

```python
#!/usr/bin/env python3
"""Track width calibration: spin robot N rotations, compare reported vs actual yaw."""
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
import math
import time
import sys


class TrackWidthCalibrator(Node):
    def __init__(self):
        super().__init__('track_width_calibrator')
        self.yaw = 0.0
        self.got_odom = False
        self.sub = self.create_subscription(Odometry, '/encoder/odom', self.odom_cb, 10)
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)

    def odom_cb(self, msg):
        q = msg.pose.pose.orientation
        # Extract yaw from quaternion (2D: only z and w matter)
        self.yaw = 2.0 * math.atan2(q.z, q.w)
        self.got_odom = True

    def wait_for_odom(self, timeout=5.0):
        start = time.time()
        while not self.got_odom and time.time() - start < timeout:
            rclpy.spin_once(self, timeout_sec=0.1)
        return self.got_odom

    def spin_robot(self, angular_vel, duration):
        """Command the robot to spin at angular_vel for duration seconds."""
        twist = Twist()
        twist.angular.z = angular_vel
        end_time = time.time() + duration
        while time.time() < end_time:
            self.cmd_pub.publish(twist)
            rclpy.spin_once(self, timeout_sec=0.05)
        # Stop
        twist.angular.z = 0.0
        self.cmd_pub.publish(twist)
        time.sleep(0.5)
        rclpy.spin_once(self, timeout_sec=0.1)


def main():
    rclpy.init()
    node = TrackWidthCalibrator()

    if not node.wait_for_odom():
        print("ERROR: No /encoder/odom data received")
        return

    print("=== Track Width Calibration ===")
    print("Current TRACK_WIDTH = 0.175m")
    print("Physical center-to-center = 0.1397m")
    print("")

    # Record starting yaw
    rclpy.spin_once(node, timeout_sec=0.5)
    start_yaw = node.yaw
    print("Starting yaw: {:.4f} rad ({:.1f} deg)".format(start_yaw, math.degrees(start_yaw)))

    # Spin: 0.5 rad/s for enough time to do ~2 full rotations
    # 2 rotations = 4*pi rad. At 0.5 rad/s = ~25 seconds
    ANGULAR_VEL = 0.5  # rad/s (positive = CCW)
    DURATION = 25.0    # seconds (~2 full rotations)
    TARGET_ROTATIONS = 2

    print("")
    print("Spinning robot at {:.1f} rad/s for {:.0f}s (~{} rotations)...".format(
        ANGULAR_VEL, DURATION, TARGET_ROTATIONS))
    print("IMPORTANT: Count the actual number of rotations visually!")
    print("")

    node.spin_robot(ANGULAR_VEL, DURATION)

    # Record ending yaw
    rclpy.spin_once(node, timeout_sec=0.5)
    end_yaw = node.yaw
    print("Ending yaw: {:.4f} rad ({:.1f} deg)".format(end_yaw, math.degrees(end_yaw)))

    # Calculate reported rotation
    # The yaw wraps at +-pi, so we need total accumulated rotation
    # The encoder odom in ugv_serial_node.py accumulates yaw without wrapping
    reported_rad = end_yaw - start_yaw
    reported_deg = math.degrees(reported_rad)
    reported_rotations = reported_rad / (2 * math.pi)

    print("")
    print("=== RESULTS ===")
    print("Reported rotation: {:.4f} rad = {:.1f} deg = {:.2f} rotations".format(
        reported_rad, reported_deg, reported_rotations))
    print("")
    print("NOW: Enter the ACTUAL number of rotations you counted:")
    print("  (e.g., 2.0 if robot did exactly 2 full turns)")
    print("")

    try:
        actual = float(input("Actual rotations: "))
    except (ValueError, EOFError):
        print("Using TARGET_ROTATIONS = {}".format(TARGET_ROTATIONS))
        actual = TARGET_ROTATIONS

    actual_rad = actual * 2 * math.pi
    correction = actual_rad / reported_rad if reported_rad != 0 else 1.0

    # New track width = old track width * correction factor
    old_tw = 0.175
    new_tw = old_tw * correction

    print("")
    print("=== CALIBRATION RESULT ===")
    print("Correction factor: {:.4f}".format(correction))
    print("Old TRACK_WIDTH:   {:.4f}m".format(old_tw))
    print("New TRACK_WIDTH:   {:.4f}m".format(new_tw))
    print("Physical c-to-c:   0.1397m")
    print("Effective/Physical: {:.2f}x".format(new_tw / 0.1397))
    print("")
    print("UPDATE ugv_serial_node.py line 38:")
    print("  TRACK_WIDTH = {:.4f}  # calibrated {}".format(new_tw, time.strftime('%Y-%m-%d')))

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
```

**Step 2: Deploy and run the calibration**

```bash
# SCP to Jetson
sshpass -p 'jetson' scp -P 23 /tmp/track_width_calibration.py root@192.168.1.155:/home/ws/ugv_ws/

# SSH in and run interactively (need to count rotations visually)
sshpass -p 'jetson' ssh -t root@192.168.1.155 -p 23
source /opt/ros/humble/install/setup.bash
source /home/ws/ugv_ws/install/setup.bash
python3 /home/ws/ugv_ws/track_width_calibration.py
```

**IMPORTANT:** The user must physically observe the robot and count the actual number of rotations. Place a marker/tape on the robot to track one full turn.

**Step 3: Update TRACK_WIDTH in ugv_serial_node.py**

Change line 38 from:
```python
TRACK_WIDTH = 0.175  # meters between left and right tracks
```
To the calibrated value (expected range 0.19–0.25m):
```python
TRACK_WIDTH = 0.XXXX  # calibrated 2026-04-11 (effective, includes skid-steer correction)
```

**Step 4: Redeploy and verify**

```bash
sshpass -p 'jetson' scp -P 23 /tmp/ugv_serial_node.py root@192.168.1.155:/home/ws/ugv_ws/
# Kill and restart just the serial node
docker exec ugv_jetson_ros_humble bash -c 'kill $(pgrep -f ugv_serial_node)'
docker exec -d ugv_jetson_ros_humble bash -c 'source /opt/ros/humble/install/setup.bash && source /home/ws/ugv_ws/install/setup.bash && python3 /home/ws/ugv_ws/ugv_serial_node.py'
```

**Verification:** Run the calibration script again with the new track width. Reported rotations should match actual rotations within 5%.

---

### Task 2: Increase Encoder Rate from 5Hz to 20Hz+

**Why:** 5Hz odometry is too slow for SLAM. The ESP32 sends T=1001 (sensor) and T=1005 (ESP-NOW status) at ~2:1 ratio. Reducing the feedback interval from 50ms to 20ms increases total packet rate to ~50Hz, giving ~17Hz T=1001 packets.

**Files:**
- Modify: `/home/ws/ugv_ws/ugv_serial_node.py` — change init command `{"T":142,"cmd":50}` to `{"T":142,"cmd":20}`

**Step 1: Change feedback interval**

In `ugv_serial_node.py`, method `_send_init()`, change:
```python
{"T": 142, "cmd": 50},   # Feedback interval 50ms
```
To:
```python
{"T": 142, "cmd": 20},   # Feedback interval 20ms (~50Hz total, ~17Hz T=1001)
```

**Step 2: Deploy and restart serial node**

```bash
sshpass -p 'jetson' scp -P 23 /tmp/ugv_serial_node.py root@192.168.1.155:/home/ws/ugv_ws/
docker exec ugv_jetson_ros_humble bash -c 'kill $(pgrep -f ugv_serial_node)'
docker exec -d ugv_jetson_ros_humble bash -c 'source /opt/ros/humble/install/setup.bash && source /home/ws/ugv_ws/install/setup.bash && python3 /home/ws/ugv_ws/ugv_serial_node.py'
sleep 8
```

**Verification:**
```bash
# Check encoder odom rate — should be 15-20Hz (was ~5Hz)
ros2 topic hz /encoder/odom
# Expected: average rate: 15.0-20.0

# Check IMU raw rate
ros2 topic hz /imu/data_raw
# Expected: same as encoder (they come from the same T=1001 packet)
```

If rate is still low, try `cmd:10` (10ms interval, 100Hz total). If ESP32 can't keep up, it will naturally throttle.

**Fallback:** If reducing interval causes serial buffer issues (garbled JSON, parse errors), revert to `cmd:30` (30ms) as a compromise.

---

### Task 3: IMU Gyroscope Bias Calibration

**Why:** The raw gyro reads gx≈4095, gz≈-17219 when stationary. These are uncalibrated zero offsets — every MEMS gyro has them. Subtracting the stationary average makes the data usable. A calibrated gyroscope is the #1 way to fix angular odometry on tracked robots (immune to track slippage).

**Files:**
- Modify: `/home/ws/ugv_ws/ugv_serial_node.py` — add calibration phase to `_read_loop()`

**Step 1: Add gyro bias calibration to UgvSerialNode.__init__**

After the existing state variables (around line 106), add:
```python
        # -- Gyroscope bias calibration --
        self.gyro_bias_x = 0.0
        self.gyro_bias_y = 0.0
        self.gyro_bias_z = 0.0
        self.gyro_calibrated = False
        self.GYRO_CAL_SAMPLES = 200  # ~10s at 20Hz
```

**Step 2: Add calibration phase to _read_loop()**

After the init retry loop succeeds (after `self.get_logger().info('ESP32 sensor data active (T=1001)')`) and before the main read loop, add:

```python
        # ── Gyroscope bias calibration ──
        # Robot MUST be stationary during boot. Average N gyro samples.
        self.get_logger().info('Calibrating gyroscope bias ({} samples, keep robot still)...'.format(
            self.GYRO_CAL_SAMPLES))
        gx_sum, gy_sum, gz_sum = 0.0, 0.0, 0.0
        cal_count = 0
        while cal_count < self.GYRO_CAL_SAMPLES and rclpy.ok():
            data = self._read_packet()
            if data and data.get('T') == 1001:
                gx_sum += data.get('gx', 0)
                gy_sum += data.get('gy', 0)
                gz_sum += data.get('gz', 0)
                cal_count += 1
            time.sleep(0.01)

        if cal_count > 0:
            self.gyro_bias_x = gx_sum / cal_count
            self.gyro_bias_y = gy_sum / cal_count
            self.gyro_bias_z = gz_sum / cal_count
            self.gyro_calibrated = True
            self.get_logger().info(
                'Gyro bias calibrated: gx={:.1f} gy={:.1f} gz={:.1f} (raw units, {} samples)'.format(
                    self.gyro_bias_x, self.gyro_bias_y, self.gyro_bias_z, cal_count))
        else:
            self.get_logger().error('Gyro calibration failed — no data')
```

**Step 3: Apply bias correction in _publish_imu_raw()**

Change the angular_velocity lines from:
```python
        msg.angular_velocity.x = data.get('gx', 0) * GYRO_SCALE
        msg.angular_velocity.y = data.get('gy', 0) * GYRO_SCALE
        msg.angular_velocity.z = data.get('gz', 0) * GYRO_SCALE
```
To:
```python
        msg.angular_velocity.x = (data.get('gx', 0) - self.gyro_bias_x) * GYRO_SCALE
        msg.angular_velocity.y = (data.get('gy', 0) - self.gyro_bias_y) * GYRO_SCALE
        msg.angular_velocity.z = (data.get('gz', 0) - self.gyro_bias_z) * GYRO_SCALE
```

**Step 4: Deploy and restart**

```bash
sshpass -p 'jetson' scp -P 23 /tmp/ugv_serial_node.py root@192.168.1.155:/home/ws/ugv_ws/
# Full container restart to test boot calibration (robot must be stationary)
docker restart ugv_jetson_ros_humble
sleep 5
docker exec -d ugv_jetson_ros_humble bash /home/ws/ugv_ws/start_ros2.sh
sleep 120
```

**Verification:**
```bash
# Check the calibration log message
docker exec ugv_jetson_ros_humble bash -c 'source /opt/ros/humble/install/setup.bash && \
  timeout 3 ros2 topic echo /rosout 2>&1 | grep "Gyro bias"'
# Expected: "Gyro bias calibrated: gx=XXXX.X gy=XXXX.X gz=XXXX.X"

# Check raw IMU — angular velocity should be near zero when stationary
ros2 topic echo /imu/data_raw --once | grep -A3 angular_velocity
# Expected: x: ~0.0 (within ±0.05), y: ~0.0, z: ~0.0

# Check Madgwick filter output
ros2 topic echo /imu/data --once | grep -A4 orientation
# Expected: stable quaternion, not spinning
```

**Critical check:** If angular_velocity values are still large (>0.1 rad/s) after calibration, the gyro has non-constant bias (temperature drift or hardware fault). In that case, keep IMU disabled in EKF and skip to Task 5.

---

### Task 4: Fix EKF Config — Re-enable Calibrated IMU

**Why:** (a) The current config double-counts yaw from encoder odom (fuses both pose yaw AND twist yaw_rate from the same source). (b) With calibrated IMU, the gyroscope provides an independent heading reference immune to track slippage.

**Files:**
- Modify: `/home/ws/ugv_ws/ekf_params.yaml`

**Step 1: Fix encoder odom config — remove pose yaw, keep only twist**

For tracked robots, encoder POSE yaw drifts due to track slippage. Trust encoders for position (x, y) and velocity (vx, vyaw), but NOT for absolute yaw. The IMU provides absolute yaw.

Change odom0_config from:
```yaml
    odom0_config: [true, true, false,
                   false, false, true,    # <-- pose yaw = BAD (double-counted)
                   true, false, false,
                   false, false, true,    # <-- twist vyaw = good
                   false, false, false]
```
To:
```yaml
    odom0_config: [true, true, false,
                   false, false, false,   # NO pose yaw (IMU handles absolute heading)
                   true, false, false,
                   false, false, true,    # twist vyaw from encoders (velocity only)
                   false, false, false]
```

**Step 2: Re-enable IMU with only yaw_rate (angular velocity)**

The Madgwick filter outputs orientation (absolute yaw) AND the raw angular velocity passes through. For a tracked robot, trust only the **angular velocity** from the gyro — not absolute orientation (which may drift due to magnetic interference from motors).

Uncomment and modify the imu0 section:
```yaml
    # -- Sensor 1: Calibrated IMU (gyro yaw rate only) --
    imu0: /imu/data
    imu0_config: [false, false, false,
                  false, false, false,    # NO absolute orientation (motor mag interference)
                  false, false, false,
                  false, false, true,     # YES angular velocity z (gyro yaw rate)
                  false, false, false]
    imu0_nodelay: false
    imu0_differential: false
    imu0_relative: false
    imu0_queue_size: 10
    imu0_twist_rejection_threshold: 1.0
    imu0_remove_gravitational_acceleration: false
```

**Step 3: Deploy and restart EKF**

```bash
sshpass -p 'jetson' scp -P 23 /tmp/ekf_params.yaml root@192.168.1.155:/home/ws/ugv_ws/
# Kill and restart just the EKF node
docker exec ugv_jetson_ros_humble bash -c 'kill $(pgrep -f ekf_node)'
docker exec -d ugv_jetson_ros_humble bash -c 'source /opt/ros/humble/install/setup.bash && \
  ros2 launch /home/ws/ugv_ws/ekf_odom.launch.py'
sleep 5
```

**Verification:**
```bash
# Stationary: /odom should be near origin, yaw stable
ros2 topic echo /odom --once | grep -A6 'twist:'
# Expected: linear.x ~0, angular.z ~0

# EKF rate should be 30Hz
ros2 topic hz /odom
# Expected: average rate: 30.0

# TF chain intact
ros2 run tf2_ros tf2_echo odom base_footprint
# Expected: valid transform, not jumping around
```

**If IMU causes phantom rotation again:** The gyro calibration didn't fully work. Revert imu0 to commented-out (disable) and proceed to Task 5 without it. The calibrated track width + higher encoder rate + rf2o should still produce much better maps.

---

### Task 5: Upgrade RTAB-Map to Use Visual Odometry

**Why:** RTAB-Map's maintainer explicitly states that feeding poor EKF-fused odometry degrades map quality. The recommended pattern: let RTAB-Map run its own stereo visual odometry from the OAK-D camera, using wheel encoder odom as a "guess" for feature matching initialization. This gives:
- High-accuracy visual motion estimation from stereo features
- Fallback to wheel odom when visual tracking fails (texture-less walls, fast motion)
- RTAB-Map gets the best possible odometry for scan/feature matching

**Files:**
- Create: `/home/ws/ugv_ws/rtabmap_vo.launch.py` (replaces rtabmap_custom.launch.py)
- Modify: `/home/ws/ugv_ws/start_ros2.sh` — swap rtabmap launch

**Step 1: Create new RTAB-Map launch with visual odometry**

```python
#!/usr/bin/env python3
"""
RTAB-Map with stereo visual odometry for UGV Beast.
Uses OAK-D stereo camera for visual odometry, encoder odom as guess.
TF tree: map -> vo_odom -> odom -> base_footprint

Bringup + LiDAR + depth node + serial node + EKF must already be running.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():

    # RTAB-Map's own stereo visual odometry
    # Uses OAK-D stereo pair for frame-to-map feature matching
    # guess_frame_id='odom' means it uses our EKF odom as initial guess
    stereo_odom = Node(
        package='rtabmap_odom',
        executable='stereo_odometry',
        name='stereo_odometry',
        output='screen',
        parameters=[{
            'frame_id': 'base_footprint',
            'odom_frame_id': 'vo_odom',
            'publish_tf': True,
            'wait_for_transform': 0.3,
            'approx_sync': True,
            'queue_size': 5,
            'guess_frame_id': 'odom',          # Use EKF encoder odom as initial guess
            'Odom/Strategy': '0',               # Frame-to-Map (most robust)
            'Odom/ResetCountdown': '1',
            'Odom/GuessSmoothingDelay': '0.5',
            'Vis/MaxFeatures': '500',
            'Vis/MinInliers': '15',
            'Reg/Force3DoF': 'true',            # Ground robot: x, y, yaw only
            'OdomF2M/MaxSize': '2000',
            'OdomF2M/ScanMaxSize': '5000',
        }],
        remappings=[
            ('left/image_rect', '/oak/left/image_rect'),
            ('right/image_rect', '/oak/right/image_rect'),
            ('left/camera_info', '/oak/left/camera_info'),
            ('right/camera_info', '/oak/right/camera_info'),
            ('odom', '/vo'),
        ],
    )

    # RTAB-Map SLAM node — uses visual odometry for registration
    rtabmap_node = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        parameters=[{
            'frame_id': 'base_footprint',
            'odom_frame_id': 'vo_odom',
            'subscribe_rgb': True,
            'subscribe_depth': True,
            'subscribe_scan': True,
            'approx_sync': True,
            'publish_tf': True,
            'queue_size': 20,
            'Rtabmap/DetectionRate': '3.5',
            'Mem/STMSize': '30',
            'RGBD/LinearUpdate': '0.1',
            'RGBD/AngularUpdate': '0.1',
            'Optimizer/Strategy': '1',
            'Reg/Force3DoF': 'true',
            'RGBD/OptimizeMaxError': '0.5',
            # 2D Grid Map
            'Grid/FromDepth': 'false',
            'Grid/3D': 'false',
            'Grid/CellSize': '0.05',
            'Grid/RangeMax': '5.0',
            'Grid/RangeMin': '0.15',
            'Grid/MaxGroundHeight': '0.05',
            'Grid/MaxObstacleHeight': '0.8',
            'Grid/NormalsSegmentation': 'false',
            'Grid/RayTracing': 'true',
            'Grid/NoiseFilteringRadius': '0.2',
            'Grid/NoiseFilteringMinNeighbors': '5',
            'Grid/DepthDecimation': '4',
            # 3D Point Cloud
            'cloud_decimation': 4,
            'cloud_max_depth': 4.0,
            'cloud_voxel_size': 0.03,
            'cloud_output_voxelized': True,
        }],
        remappings=[
            ('rgb/image', '/oak/rgb/image_rect'),
            ('rgb/camera_info', '/oak/rgb/camera_info'),
            ('depth/image', '/oak/stereo/image_raw'),
            ('scan', '/scan'),
            ('odom', '/vo'),
            ('grid_map', '/map'),
        ],
        arguments=['-d'],
    )

    # Robot pose publisher
    robot_pose_publisher_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(
                get_package_share_directory('robot_pose_publisher'), 'launch'
            ),
            '/robot_pose_publisher_launch.py'
        ])
    )

    return LaunchDescription([
        robot_pose_publisher_launch,
        stereo_odom,
        rtabmap_node,
    ])
```

**NOTE:** The stereo odometry node needs rectified stereo images from OAK-D. Check if the depth node publishes `/oak/left/image_rect` and `/oak/right/image_rect`. If not, we may need to use the RGB-D odometry variant instead:

**Fallback — RGB-D odometry** (if stereo pair topics not available):
```python
    rgbd_odom = Node(
        package='rtabmap_odom',
        executable='rgbd_odometry',
        name='rgbd_odometry',
        output='screen',
        parameters=[{
            'frame_id': 'base_footprint',
            'odom_frame_id': 'vo_odom',
            'publish_tf': True,
            'approx_sync': True,
            'queue_size': 5,
            'guess_frame_id': 'odom',
            'Odom/Strategy': '0',
            'Reg/Force3DoF': 'true',
            'Vis/MaxFeatures': '500',
        }],
        remappings=[
            ('rgb/image', '/oak/rgb/image_rect'),
            ('rgb/camera_info', '/oak/rgb/camera_info'),
            ('depth/image', '/oak/stereo/image_raw'),
            ('odom', '/vo'),
        ],
    )
```

**Step 2: Check OAK-D available topics**

```bash
ros2 topic list | grep oak
# Look for: /oak/left/image_rect, /oak/right/image_rect
# If only /oak/rgb/image_rect + /oak/stereo/image_raw → use rgbd_odometry fallback
```

**Step 3: Update start_ros2.sh — swap RTAB-Map launch**

In the slam_3d block, change:
```bash
        ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &
```
To:
```bash
        ros2 launch /home/ws/ugv_ws/rtabmap_vo.launch.py &
```

**Step 4: Deploy and full restart**

```bash
sshpass -p 'jetson' scp -P 23 /tmp/rtabmap_vo.launch.py root@192.168.1.155:/home/ws/ugv_ws/
# Update start_ros2.sh with new launch file name
sshpass -p 'jetson' scp -P 23 /tmp/start_ros2.sh root@192.168.1.155:/home/ws/ugv_ws/
# Full restart
docker restart ugv_jetson_ros_humble
sleep 5
docker exec -d ugv_jetson_ros_humble bash /home/ws/ugv_ws/start_ros2.sh
sleep 120
```

**Verification:**
```bash
# Visual odometry node running
ros2 node list | grep stereo_odometry   # or rgbd_odometry

# VO topic publishing
ros2 topic hz /vo
# Expected: 5-15Hz (depends on camera framerate and processing power)

# TF chain: map -> vo_odom -> odom -> base_footprint
ros2 run tf2_ros tf2_echo map base_footprint
# Expected: valid transform, smooth motion

# Map publishing
ros2 topic hz /map
# Expected: occasional updates
```

**If stereo/RGBD odometry fails** (too slow on Jetson, OAK-D topics wrong): Keep `rtabmap_custom.launch.py` but with the corrected EKF odometry from Tasks 1-4. The track width fix alone should dramatically improve mapping.

---

### Task 6: Full Integration Test — Drive and Map

**Step 1: Clean restart**

```bash
docker restart ugv_jetson_ros_humble
sleep 5
docker exec -d ugv_jetson_ros_humble bash /home/ws/ugv_ws/start_ros2.sh
# Wait for full boot (~2.5 minutes with calibration phase)
sleep 150
```

**Step 2: Stationary verification**

```bash
# All critical topics publishing
for t in /encoder/odom /imu/data_raw /imu/data /odom; do
  echo -n "$t: "; timeout 4 ros2 topic hz $t 2>&1 | grep 'average rate' | tail -1
done

# Encoder rate: 15-20Hz (was 5Hz)
# EKF rate: 30Hz
# Odom: stable near origin, no drift

# Gyro calibration succeeded
timeout 5 ros2 topic echo /imu/data_raw --once | grep -A3 angular_velocity
# Expected: all near 0.0 (±0.05 rad/s)
```

**Step 3: Slow drive test**

- Open Vizanti (http://192.168.1.155)
- Use joystick to drive slowly in a straight line (~2 meters)
- Verify: map shows a straight wall (not curved or doubled)
- Return to start position
- Verify: robot icon returns to near-origin

**Step 4: Rotation test**

- Spin the robot 360° in place using joystick
- Verify: walls do NOT get thicker
- Verify: robot yaw returns to approximately the starting orientation

**Step 5: Full room mapping**

- Drive slowly around a room, keeping close to walls
- Verify: map shows clean wall outlines (not smeared or doubled)
- Verify: when returning to start, the map "closes" properly (loop closure)

**Expected outcomes after all fixes:**
- Track width calibrated → heading per turn is correct → scans align
- 20Hz encoder rate → SLAM gets fresh pose data between every scan
- Calibrated gyro in EKF → heading immune to track slippage during turns
- Visual odometry (or corrected EKF) → RTAB-Map registration dramatically improved
- Clean, usable map for Nav2 navigation

---

## Rollback

If anything goes wrong, the proven working configuration is:

1. Restore old serial node: `TRACK_WIDTH = 0.175`, `cmd:50`, no gyro calibration
2. Restore EKF: IMU disabled, odom0 uses pose yaw + twist vyaw
3. Restore RTAB-Map: `rtabmap_custom.launch.py` (no visual odometry)
4. `ugv_odom_filter.py` is preserved and unchanged as ultimate fallback

The old files are backed up on the Jetson:
- `/home/ws/ugv_ws/start_ros2.sh.bak`
- `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/launch/bringup_lidar.launch.py.bak`
