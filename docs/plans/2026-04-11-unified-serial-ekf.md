# Unified Serial Node + EKF Sensor Fusion Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the broken two-node serial architecture (ugv_bringup + base_node fighting over `/dev/ttyTHS1`) with a single unified serial node that publishes clean encoder odometry + raw IMU + magnetometer + voltage on separate topics, then fuse encoder + rf2o + IMU via robot_localization EKF for accurate rotational odometry.

**Architecture:** One Python node (`ugv_serial_node.py`) owns the serial port exclusively. It reads ALL ESP32 JSON packets, filters for T=1001, and publishes to 4 separate topics. The `base_node` C++ node is removed entirely — its encoder→odom math is reimplemented in Python using the proven `base_node_ekf.cpp` pattern (encoder-only yaw, no IMU contamination). The `imu_filter_madgwick` node processes raw IMU into filtered orientation. The robot_localization EKF fuses encoder odom + filtered IMU + rf2o laser odom. The odom_filter.py is removed.

**Tech Stack:** Python3, pyserial, ROS2 Humble, robot_localization EKF, imu_filter_madgwick, sensor_msgs/Imu, nav_msgs/Odometry

---

## Current State (verified 2026-04-11)

### Serial Data from ESP32

The ESP32 sends JSON packets over `/dev/ttyTHS1` at 115200 baud, ~40Hz:

```json
{"T":1001,"L":0,"R":0,"ax":309,"ay":15,"az":1088,"gx":46,"gy":26128,"gz":-4,"mx":12,"my":-5,"mz":3,"odl":0,"odr":0,"v":1190}
```

Also sends T=1005 (ESP-NOW status) interleaved ~2:1 with T=1001. Must filter for T=1001.

**Scaling factors** (from ugv_bringup.py source):
- Accelerometer: `9.8 * raw / 8192` → m/s²
- Gyroscope: `pi * raw / (16.4 * 180)` → rad/s
- Magnetometer: `raw * 0.15` → µT
- Encoders: `raw / 100.0` → meters
- Voltage: `raw / 100.0` → volts

**Encoder math** (from base_node_ekf.cpp):
- Track width: `0.175` meters (distance between left and right tracks)
- `dxy_ave = (dright + dleft) / 2.0` — average distance
- `dth = (dright - dleft) / 0.175` — differential heading change
- `vx = dxy_ave / dt`, `vw = dth / dt`
- Position: dead-reckoning integration with encoder-only yaw (NOT IMU)

### Init Commands (sent to ESP32 on boot)

```json
{"T":142,"cmd":50}     // Set feedback interval 50ms
{"T":131,"cmd":1}      // Enable continuous feedback (T=1001 packets)
{"T":143,"cmd":0}      // Disable echo
{"T":4,"cmd":2}        // Module type (2=Camera PT)
{"T":300,"mode":0,"mac":"EF:EF:EF:EF:EF:EF"}  // ESP-NOW lock
```

### Current Broken Architecture

```
ugv_bringup (Python) ──opens──> /dev/ttyTHS1 ──> publishes NOTHING (starved)
base_node (C++)      ──opens──> /dev/ttyTHS1 ──> publishes /odom_raw (IMU-contaminated yaw)
```

### Target Architecture

```
ugv_serial_node.py ──opens──> /dev/ttyTHS1 ──> /encoder/odom    (Odometry, encoder-only)
                                            ──> /imu/data_raw    (Imu, raw accel+gyro)
                                            ──> /imu/mag         (MagneticField)
                                            ──> /battery         (Float32, voltage)
                     ──writes──> cmd_vel, LED, gimbal commands
                          ↓
               imu_filter_madgwick → /imu/data (filtered orientation)
                          ↓
               robot_localization EKF:
                 - /encoder/odom (x, y, yaw, vx, vyaw from encoders)
                 - /imu/data (yaw, vyaw from filtered IMU)
                 - /odom_rf2o (vx, vy from laser scan matching)
                          ↓
                 /odom (fused) + odom→base_footprint TF
```

### Packages Already Installed

- `ros-humble-robot-localization` (3.5.4) — EKF node
- `ros-humble-imu-filter-madgwick` (2.1.5) — IMU orientation filter
- `ros-humble-imu-complementary-filter` — NOT installed (not needed, madgwick is better)

### Reference Files

- ESP32 serial protocol: `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/ugv_bringup/ugv_bringup.py`
- Encoder math (clean): `/home/ws/ugv_ws/src/ugv_main/ugv_base_node/src/base_node_ekf.cpp`
- Waveshare EKF params: `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/param/ekf.yaml`
- Waveshare EKF launch: `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/launch/bringup_imu_ekf.launch.py`
- Waveshare IMU filter: `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/param/imu_filter_param.yaml`
- Current odom filter: `/home/ws/ugv_ws/ugv_odom_filter.py`

---

### Task 1: Create unified serial node — ugv_serial_node.py

**Files:**
- Create: `/home/ws/ugv_ws/ugv_serial_node.py`

This node replaces BOTH `ugv_bringup` (Python) AND `base_node` (C++). It is the SOLE owner of `/dev/ttyTHS1`.

```python
#!/usr/bin/env python3
"""
UGV Beast Unified Serial Node
Single owner of /dev/ttyTHS1 — reads ALL ESP32 data, publishes to separate topics.
Replaces: ugv_bringup (Python) + base_node (C++)

Publishes:
  /encoder/odom    (Odometry)       - Encoder-only odometry (NO IMU contamination)
  /imu/data_raw    (Imu)            - Raw accelerometer + gyroscope
  /imu/mag         (MagneticField)  - Raw magnetometer
  /battery         (Float32)        - Battery voltage

Subscribes:
  /cmd_vel         (Twist)          - Motor velocity commands
  /ugv/led_ctrl    (Float32MultiArray) - LED brightness

Serial protocol: JSON over UART 115200. ESP32 sends T=1001 sensor packets at ~20Hz.
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu, MagneticField
from std_msgs.msg import Float32, Float32MultiArray
import serial
import json
import math
import time
import threading
import queue


# Serial port
SERIAL_PORT = '/dev/ttyTHS1'
SERIAL_BAUD = 115200

# Robot physical constants
TRACK_WIDTH = 0.175  # meters between left and right tracks

# IMU scaling (from Waveshare ugv_bringup.py)
ACCEL_SCALE = 9.8 / 8192.0        # raw → m/s²
GYRO_SCALE = math.pi / (16.4 * 180.0)  # raw → rad/s
MAG_SCALE = 0.15                   # raw → µT
ENCODER_SCALE = 1.0 / 100.0       # raw → meters
VOLTAGE_SCALE = 1.0 / 100.0       # raw → volts


class ReadLine:
    """Buffered serial line reader (from Waveshare base_ctrl.py)."""
    def __init__(self, s):
        self.buf = bytearray()
        self.s = s

    def readline(self):
        i = self.buf.find(b"\n")
        if i >= 0:
            r = self.buf[:i+1]
            self.buf = self.buf[i+1:]
            return r
        while True:
            i = max(1, min(512, self.s.in_waiting))
            data = self.s.read(i)
            i = data.find(b"\n")
            if i >= 0:
                r = self.buf + data[:i+1]
                self.buf[0:] = data[i+1:]
                return r
            else:
                self.buf.extend(data)

    def clear_buffer(self):
        self.s.reset_input_buffer()
        self.buf = bytearray()


class UgvSerialNode(Node):
    def __init__(self):
        super().__init__('ugv_serial')

        # ── Serial setup ──
        self.ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        self.rl = ReadLine(self.ser)
        self.cmd_queue = queue.Queue()

        # ── Publishers ──
        self.encoder_odom_pub = self.create_publisher(Odometry, '/encoder/odom', 10)
        self.imu_raw_pub = self.create_publisher(Imu, '/imu/data_raw', 10)
        self.mag_pub = self.create_publisher(MagneticField, '/imu/mag', 10)
        self.battery_pub = self.create_publisher(Float32, '/battery', 10)

        # ── Subscribers ──
        self.create_subscription(Twist, 'cmd_vel', self.cmd_vel_cb, 10)
        self.create_subscription(Float32MultiArray, 'ugv/led_ctrl', self.led_ctrl_cb, 10)

        # ── Encoder odometry state (from base_node_ekf.cpp) ──
        self.x_pos = 0.0
        self.y_pos = 0.0
        self.yaw = 0.0  # Encoder-only yaw (NO IMU)
        self.vx = 0.0
        self.vw = 0.0
        self.prev_odl = 0.0
        self.prev_odr = 0.0
        self.init_odl = None
        self.init_odr = None
        self.last_odom_time = None

        # ── Covariance matrices (from Waveshare base_node_ekf.cpp) ──
        self.POSE_COV_MOVING = [
            1e-3, 0, 0, 0, 0, 0,
            0, 1e-3, 0, 0, 0, 0,
            0, 0, 1e6, 0, 0, 0,
            0, 0, 0, 1e6, 0, 0,
            0, 0, 0, 0, 1e6, 0,
            0, 0, 0, 0, 0, 1e3,
        ]
        self.POSE_COV_STILL = [
            1e-9, 0, 0, 0, 0, 0,
            0, 1e-3, 1e-9, 0, 0, 0,
            0, 0, 1e6, 0, 0, 0,
            0, 0, 0, 1e6, 0, 0,
            0, 0, 0, 0, 1e6, 0,
            0, 0, 0, 0, 0, 1e-9,
        ]
        self.TWIST_COV_MOVING = self.POSE_COV_MOVING.copy()
        self.TWIST_COV_STILL = self.POSE_COV_STILL.copy()

        # ── Threads ──
        self.write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self.write_thread.start()

        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()

        self.get_logger().info('Unified serial node started on {}'.format(SERIAL_PORT))

    # ── Serial Init ──

    def _send_init(self):
        """Send ESP32 initialization commands."""
        init_cmds = [
            {"T": 142, "cmd": 50},   # Feedback interval 50ms
            {"T": 131, "cmd": 1},    # Enable continuous feedback
            {"T": 143, "cmd": 0},    # Disable echo
            {"T": 4, "cmd": 2},      # Module type: Camera PT
            {"T": 300, "mode": 0, "mac": "EF:EF:EF:EF:EF:EF"},
        ]
        for cmd in init_cmds:
            self.ser.write((json.dumps(cmd) + '\n').encode())
            time.sleep(0.05)
        self.get_logger().info('ESP32 init commands sent')

    # ── Write Thread ──

    def _write_loop(self):
        """Process command queue → serial writes."""
        while rclpy.ok():
            try:
                cmd = self.cmd_queue.get(timeout=1.0)
                self.ser.write((json.dumps(cmd) + '\n').encode())
            except queue.Empty:
                pass
            except Exception as e:
                self.get_logger().error('Serial write error: {}'.format(e))

    # ── Read Thread ──

    def _read_loop(self):
        """Read serial data from ESP32, publish to ROS2 topics."""
        time.sleep(2)  # Let ESP32 boot
        self.rl.clear_buffer()
        self._send_init()
        time.sleep(1)  # Let init take effect
        self.rl.clear_buffer()  # Clear any echo/response

        # Retry init up to 5 times if we only get T=1005
        got_sensor = False
        for attempt in range(5):
            for _ in range(10):
                data = self._read_packet()
                if data and data.get('T') == 1001:
                    got_sensor = True
                    break
                time.sleep(0.05)
            if got_sensor:
                self.get_logger().info('ESP32 sensor data active (T=1001)')
                break
            self.get_logger().warn('No T=1001 data, retrying init (attempt {})'.format(attempt + 2))
            self._send_init()
            time.sleep(1)
            self.rl.clear_buffer()

        if not got_sensor:
            self.get_logger().error('ESP32 not sending sensor data after 5 attempts')

        # Main read loop
        while rclpy.ok():
            try:
                data = self._read_packet()
                if data and data.get('T') == 1001:
                    now = self.get_clock().now()
                    self._publish_encoder_odom(data, now)
                    self._publish_imu_raw(data, now)
                    self._publish_mag(data, now)
                    self._publish_battery(data, now)
            except Exception as e:
                self.get_logger().error('Read loop error: {}'.format(e))
                self.rl.clear_buffer()
            time.sleep(0.01)  # ~100Hz read rate (T=1001 comes at ~20Hz)

    def _read_packet(self):
        """Read one JSON packet from serial. Returns dict or None."""
        try:
            if self.ser.in_waiting > 0:
                line = self.rl.readline().decode('utf-8', errors='replace').strip()
                if line:
                    return json.loads(line)
        except json.JSONDecodeError:
            pass
        except Exception:
            self.rl.clear_buffer()
        return None

    # ── Publishers ──

    def _publish_encoder_odom(self, data, stamp):
        """Compute encoder-only odometry and publish.
        Math from base_node_ekf.cpp — uses ENCODER yaw, NOT IMU."""
        odl = data.get('odl', 0) * ENCODER_SCALE
        odr = data.get('odr', 0) * ENCODER_SCALE

        # Initialize on first reading
        if self.init_odl is None:
            self.init_odl = odl
            self.init_odr = odr
            self.prev_odl = 0.0
            self.prev_odr = 0.0
            self.last_odom_time = stamp
            return

        # Subtract initial offset
        odl -= self.init_odl
        odr -= self.init_odr

        # Time delta
        now_sec = stamp.nanoseconds / 1e9
        if self.last_odom_time is not None:
            last_sec = self.last_odom_time.nanoseconds / 1e9
            dt = now_sec - last_sec
        else:
            dt = 0.05
        self.last_odom_time = stamp

        if dt <= 0 or dt > 1.0:
            dt = 0.05  # Clamp to reasonable value

        # Differential distances
        dleft = odl - self.prev_odl
        dright = odr - self.prev_odr
        self.prev_odl = odl
        self.prev_odr = odr

        # Average distance and heading change
        dxy_ave = (dright + dleft) / 2.0
        dth = (dright - dleft) / TRACK_WIDTH

        # Velocities
        self.vx = dxy_ave / dt
        self.vw = dth / dt

        # Dead reckoning (encoder-only yaw)
        if dxy_ave != 0:
            dx = math.cos(dth / 2.0) * dxy_ave
            dy = math.sin(dth / 2.0) * dxy_ave
            self.x_pos += math.cos(self.yaw) * dx - math.sin(self.yaw) * dy
            self.y_pos += math.sin(self.yaw) * dx + math.cos(self.yaw) * dy

        if dth != 0:
            self.yaw += dth

        # Build message
        msg = Odometry()
        msg.header.stamp = stamp.to_msg()
        msg.header.frame_id = 'odom'
        msg.child_frame_id = 'base_footprint'

        msg.pose.pose.position.x = self.x_pos
        msg.pose.pose.position.y = self.y_pos
        msg.pose.pose.position.z = 0.0
        msg.pose.pose.orientation.z = math.sin(self.yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(self.yaw / 2.0)

        msg.twist.twist.linear.x = self.vx
        msg.twist.twist.angular.z = self.vw

        # Covariance (from Waveshare)
        if self.vx == 0 and self.vw == 0:
            msg.pose.covariance = self.POSE_COV_STILL
            msg.twist.covariance = self.TWIST_COV_STILL
        else:
            msg.pose.covariance = self.POSE_COV_MOVING
            msg.twist.covariance = self.TWIST_COV_MOVING

        self.encoder_odom_pub.publish(msg)

    def _publish_imu_raw(self, data, stamp):
        """Publish raw IMU data (accelerometer + gyroscope)."""
        msg = Imu()
        msg.header.stamp = stamp.to_msg()
        msg.header.frame_id = 'base_imu_link'

        msg.linear_acceleration.x = data.get('ax', 0) * ACCEL_SCALE
        msg.linear_acceleration.y = data.get('ay', 0) * ACCEL_SCALE
        msg.linear_acceleration.z = data.get('az', 0) * ACCEL_SCALE

        msg.angular_velocity.x = data.get('gx', 0) * GYRO_SCALE
        msg.angular_velocity.y = data.get('gy', 0) * GYRO_SCALE
        msg.angular_velocity.z = data.get('gz', 0) * GYRO_SCALE

        # No orientation from raw IMU (madgwick filter computes it)
        msg.orientation_covariance[0] = -1  # Flag: orientation not available

        # Set covariance (nonzero so EKF accepts the data)
        msg.angular_velocity_covariance = [
            0.01, 0.0, 0.0,
            0.0, 0.01, 0.0,
            0.0, 0.0, 0.01,
        ]
        msg.linear_acceleration_covariance = [
            0.1, 0.0, 0.0,
            0.0, 0.1, 0.0,
            0.0, 0.0, 0.1,
        ]

        self.imu_raw_pub.publish(msg)

    def _publish_mag(self, data, stamp):
        """Publish magnetometer data."""
        msg = MagneticField()
        msg.header.stamp = stamp.to_msg()
        msg.header.frame_id = 'base_imu_link'

        msg.magnetic_field.x = data.get('mx', 0) * MAG_SCALE
        msg.magnetic_field.y = data.get('my', 0) * MAG_SCALE
        msg.magnetic_field.z = data.get('mz', 0) * MAG_SCALE

        self.mag_pub.publish(msg)

    def _publish_battery(self, data, stamp):
        """Publish battery voltage."""
        msg = Float32()
        msg.data = data.get('v', 0) * VOLTAGE_SCALE
        self.battery_pub.publish(msg)

    # ── Callbacks ──

    def cmd_vel_cb(self, msg):
        linear = msg.linear.x
        angular = msg.angular.z
        if linear == 0:
            if 0 < angular < 0.2:
                angular = 0.2
            elif -0.2 < angular < 0:
                angular = -0.2
        self.cmd_queue.put({'T': 13, 'X': linear, 'Z': angular})

    def led_ctrl_cb(self, msg):
        try:
            self.cmd_queue.put({'T': 132, 'IO4': msg.data[0], 'IO5': msg.data[1]})
        except Exception:
            pass


def main(args=None):
    rclpy.init(args=args)
    node = UgvSerialNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
```

**Verify:**
```bash
python3 -c "compile(open('/home/ws/ugv_ws/ugv_serial_node.py').read(), 'test', 'exec'); print('OK')"
```

---

### Task 2: Disable base_node AND ugv_bringup in the launch file

**Files:**
- Modify: `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/launch/bringup_lidar.launch.py`

Comment out BOTH `bringup_node` and `base_node` from the launch — our `ugv_serial_node.py` replaces them:

**Find and comment out:**
```python
    bringup_node = Node(
        package='ugv_bringup',
        executable='ugv_bringup',
    )
```
→ Comment it out

**And in the return LaunchDescription list, remove both:**
```python
        # bringup_node,     # Replaced by ugv_serial_node.py
        # base_node,        # Replaced by ugv_serial_node.py
```

Then rebuild:
```bash
cd /home/ws/ugv_ws && colcon build --packages-select ugv_bringup --symlink-install
```

---

### Task 3: Create IMU filter launch config

**Files:**
- Create: `/home/ws/ugv_ws/imu_filter.launch.py`

The Madgwick filter takes raw IMU (accel + gyro) and produces filtered orientation (quaternion):

```python
#!/usr/bin/env python3
"""IMU Madgwick filter — converts raw accel+gyro to filtered orientation."""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='imu_filter_madgwick',
            executable='imu_filter_madgwick_node',
            name='imu_filter',
            output='screen',
            parameters=[{
                'fixed_frame': 'base_footprint',
                'use_mag': False,
                'publish_tf': False,
                'world_frame': 'enu',
                'orientation_stddev': 0.05,
            }],
            # Input: /imu/data_raw → Output: /imu/data
        ),
    ])
```

---

### Task 4: Update EKF config for three-source fusion

**Files:**
- Modify: `/home/ws/ugv_ws/ekf_params.yaml`

Three sensors now:
1. `/encoder/odom` — encoder odometry (trust pose x, y, yaw + twist vx, vyaw)
2. `/imu/data` — filtered IMU from Madgwick (trust yaw orientation + vyaw angular velocity)
3. `/odom_rf2o` — laser odometry (trust vx, vy velocity only)

```yaml
ekf_filter_node:
  ros__parameters:
    frequency: 30.0
    sensor_timeout: 0.5
    two_d_mode: true
    world_frame: odom
    odom_frame: odom
    base_link_frame: base_footprint
    map_frame: map
    publish_tf: true
    publish_acceleration: false
    transform_time_offset: 0.0
    transform_timeout: 0.0
    print_diagnostics: false
    reset_on_time_jump: true

    # ── Sensor 0: Encoder odometry (clean, no IMU contamination) ──
    odom0: /encoder/odom
    odom0_config: [true, true, false,
                   false, false, true,
                   true, false, false,
                   false, false, true,
                   false, false, false]
    odom0_queue_size: 10
    odom0_differential: false
    odom0_relative: false
    odom0_pose_rejection_threshold: 20.0
    odom0_twist_rejection_threshold: 1.542

    # ── Sensor 1: Filtered IMU (Madgwick orientation + gyro) ──
    imu0: /imu/data
    imu0_config: [false, false, false,
                  false, false, true,
                  false, false, false,
                  false, false, true,
                  false, false, false]
    imu0_nodelay: false
    imu0_differential: false
    imu0_relative: true
    imu0_queue_size: 10
    imu0_pose_rejection_threshold: 20.0
    imu0_twist_rejection_threshold: 1.542
    imu0_linear_acceleration_rejection_threshold: 10.0
    imu0_remove_gravitational_acceleration: false

    # ── Sensor 2: Laser odometry (linear velocity only) ──
    odom1: /odom_rf2o
    odom1_config: [false, false, false,
                   false, false, false,
                   true,  true,  false,
                   false, false, false,
                   false, false, false]
    odom1_queue_size: 10
    odom1_differential: false
    odom1_relative: false
    odom1_pose_rejection_threshold: 5.0
    odom1_twist_rejection_threshold: 3.0

    # ── Control (from cmd_vel) ──
    use_control: false
    stamped_control: false
    control_timeout: 0.2
    control_config: [true, false, false, false, false, true]
    acceleration_limits: [1.3, 0.0, 0.0, 0.0, 0.0, 3.4]
    deceleration_limits: [1.3, 0.0, 0.0, 0.0, 0.0, 4.5]

    # ── Process noise (from Waveshare ekf.yaml — proven for this robot) ──
    process_noise_covariance: [0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.06, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.03, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.03, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.06, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.025, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.025, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.04, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.02, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01, 0.0,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.015]

    initial_estimate_covariance: [1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9, 0.0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-9]
```

---

### Task 5: Update start_ros2.sh boot sequence

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

Replace the odom_filter line in the slam_3d block with the full EKF pipeline:

**Replace:**
```bash
        # Odom filter: smooths rf2o jitter, publishes filtered /odom + TF
        python3 /home/ws/ugv_ws/ugv_odom_filter.py &
        sleep 2
```

**With:**
```bash
        # Unified serial node: reads ESP32, publishes encoder/odom + imu/data_raw + battery
        python3 /home/ws/ugv_ws/ugv_serial_node.py &
        sleep 3

        # IMU Madgwick filter: raw accel+gyro → filtered orientation
        ros2 launch /home/ws/ugv_ws/imu_filter.launch.py &
        sleep 2

        # EKF sensor fusion: encoder odom + filtered IMU + rf2o → /odom + TF
        ros2 launch /home/ws/ugv_ws/ekf_odom.launch.py &
        sleep 5
```

Also update the EKF launch file to source the EKF params:

**Modify `/home/ws/ugv_ws/ekf_odom.launch.py`** — the remapping stays `odometry/filtered → /odom`.

---

### Task 6: Add static TF for IMU frame

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

The IMU is mounted on the ESP32 board inside the robot chassis. Add a static TF:

```bash
# IMU static TF (mounted inside chassis, aligned with base)
ros2 run tf2_ros static_transform_publisher \
    0.0 0.0 0.05 0.0 0.0 0.0 \
    base_footprint base_imu_link &
sleep 1
```

Add this BEFORE the unified serial node in the boot sequence.

---

### Task 7: Test — stationary robot

**Step 1: Restart**
```bash
sudo systemctl restart ugv-ros2.service
# Wait 2+ minutes
```

**Step 2: Verify serial node publishes**
```bash
ros2 node list | grep ugv_serial
ros2 topic hz /encoder/odom      # Expected: ~20Hz
ros2 topic hz /imu/data_raw      # Expected: ~20Hz
ros2 topic hz /battery            # Expected: ~20Hz
```

**Step 3: Verify IMU filter**
```bash
ros2 topic hz /imu/data           # Expected: ~20Hz (filtered output)
ros2 topic echo /imu/data --once | grep orientation
# Should show non-trivial quaternion values
```

**Step 4: Verify EKF**
```bash
ros2 topic hz /odom               # Expected: ~30Hz
ros2 topic echo /odom --once | grep -A3 position
# Robot should be near (0,0) if stationary since boot
```

**Step 5: Stationary stability test**
```bash
# Run odom_inspect.py and check:
# - /encoder/odom vx=0, wz=0 when still
# - /imu/data_raw gyro_z near 0 (may have small bias)
# - /odom position stable, not drifting
```

### Task 8: Test — rotation accuracy

**Step 1: Note starting yaw**
```bash
ros2 topic echo /odom --once | grep -A4 orientation
```

**Step 2: Spin robot 360° via Vizanti or cmd_vel**

**Step 3: Check ending yaw**
```bash
ros2 topic echo /odom --once | grep -A4 orientation
# Yaw should be within ~5° of starting value after full rotation
```

**Step 4: Check walls in Vizanti**
- Drive the robot around to build a map
- Spin 360° in place
- Walls should NOT get thicker

---

## Rollback

If anything fails, swap back to the proven odom_filter in `start_ros2.sh`:
```bash
# Replace the serial+IMU+EKF block with:
python3 /home/ws/ugv_ws/ugv_odom_filter.py &
sleep 2
```

And re-enable both `bringup_node` and `base_node` in `bringup_lidar.launch.py`.

The odom_filter.py is preserved and unchanged.
