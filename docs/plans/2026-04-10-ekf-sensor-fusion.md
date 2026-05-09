# EKF Sensor Fusion Odometry Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the single-source rf2o odom filter with a `robot_localization` EKF that fuses wheel encoders + laser odometry + IMU for accurate rotational odometry, eliminating the wall-thickening and room-drift problems.

**Architecture:** The `robot_localization` EKF node replaces `ugv_odom_filter.py`. It subscribes to `/odom_raw` (ESP32 encoder odom — trusted for rotation), `/odom_rf2o` (laser odom — trusted for translation), and `/imu/data` (IMU gyro — trusted for angular velocity). The EKF publishes fused `/odom` + `odom->base_footprint` TF. All downstream consumers (RTAB-Map, Nav2, explore) see the fused output transparently.

**Tech Stack:** ros-humble-robot-localization (EKF), ROS2 Humble, nav_msgs/Odometry, sensor_msgs/Imu

---

## Current Sensor Landscape (verified 2026-04-10)

| Topic | Source | Rate | Frame | What it contains |
|-------|--------|------|-------|-----------------|
| `/odom_raw` | ESP32 base_node | 10Hz | odom->base_footprint | Encoder XY + fused yaw. Covariance: x=0.001, y=0.001, yaw=1000 |
| `/odom_rf2o` | rf2o_laser_odometry | 10Hz | odom->base_footprint | LiDAR scan-matching XY + yaw. Covariance: all zeros |
| `/imu/data` | rf2o (NOT ESP32) | 10Hz | odom (wrong) | Orientation only, gyro/accel all zeros. Published by rf2o node |
| `/odom/odom_raw` | ESP32 serial | 10Hz | N/A | Float32MultiArray — raw encoder data consumed by base_node |

**Key finding:** `/imu/data` is published by rf2o, NOT the ESP32. The ESP32's IMU data is fused internally into `/odom_raw` by base_node. For the EKF, we'll use encoders + rf2o initially. When we fix the ESP32 to publish raw IMU, we add it as a third source.

## Architecture

```
BEFORE (single source):
  rf2o -> ugv_odom_filter.py -> /odom + TF

AFTER (EKF fusion):
  /odom_raw (encoders) --.
                          |--> robot_localization EKF -> /odometry/filtered + TF
  /odom_rf2o (laser)   --'
  /imu/data (future)   --'
```

---

### Task 1: Install robot_localization

**Files:** None (apt install)

**Step 1: Install the package**

```bash
# Inside container (SSH to root@192.168.1.155 -p 23):
apt-get update
apt-get install -y ros-humble-robot-localization
```

**Step 2: Verify**

```bash
source /opt/ros/humble/setup.bash
ros2 pkg list | grep robot_localization
# Expected: robot_localization
```

**Step 3: Commit the container**

```bash
# From host Jetson:
docker commit ugv_jetson_ros_humble ugv_jetson_ros_humble:ekf
```

---

### Task 2: Create EKF configuration file

**Files:**
- Create: `/home/ws/ugv_ws/ekf_params.yaml`

**The EKF config tells robot_localization which axes to trust from each sensor.**

Each sensor's data is configured with a boolean array of 15 values:
`[x, y, z, roll, pitch, yaw, vx, vy, vz, vroll, vpitch, vyaw, ax, ay, az]`

```yaml
# ekf_params.yaml — EKF sensor fusion for UGV Beast
# Fuses encoder odometry + laser odometry for accurate pose estimation.
# Encoders: trusted for rotation (differential track = precise yaw)
# rf2o: trusted for translation (scan-matching anchors XY to environment)

ekf_filter_node:
  ros__parameters:
    # Output
    frequency: 30.0                    # EKF update rate (higher than input sensors)
    sensor_timeout: 0.5
    two_d_mode: true                   # Ground robot — no roll/pitch/z
    
    # Frames
    world_frame: odom
    odom_frame: odom
    base_link_frame: base_footprint
    map_frame: map                     # Not used by this node, but set for clarity
    
    # TF publishing
    publish_tf: true                   # EKF publishes odom->base_footprint TF
    publish_acceleration: false
    
    # Transform tolerance
    transform_time_offset: 0.0
    transform_timeout: 0.5
    
    # ── Sensor 0: Wheel Encoders (ESP32 /odom_raw) ──
    # Encoders are GOLD for rotation on a tracked robot.
    # Differential track displacement directly gives yaw.
    # Also decent for short-term XY (before slip accumulates).
    odom0: /odom_raw
    odom0_config: [true,  true,  false,    # x, y, z
                   false, false, true,     # roll, pitch, YAW (trust encoder yaw)
                   false, false, false,    # vx, vy, vz
                   false, false, true,     # vroll, vpitch, VYAW (trust encoder yaw rate)
                   false, false, false]    # ax, ay, az
    odom0_queue_size: 10
    odom0_differential: false              # Absolute pose from encoders
    odom0_relative: false
    odom0_remove_gravitational_acceleration: false
    # Override covariance — the ESP32 sets yaw cov to 1000 (too pessimistic for encoders)
    # Tracked robot encoders are much better than that for yaw
    odom0_pose_rejection_threshold: 5.0
    odom0_twist_rejection_threshold: 3.0
    
    # ── Sensor 1: Laser Odometry (rf2o /odom_rf2o) ──
    # rf2o is excellent for XY translation — scan-matching anchors to walls.
    # BAD for pure rotation (all LiDAR points move, no anchor).
    # Trust X, Y velocities only. Do NOT trust yaw from rf2o.
    odom1: /odom_rf2o
    odom1_config: [false, false, false,    # Don't trust rf2o absolute pose (drifts)
                   false, false, false,
                   true,  true,  false,    # VX, VY — trust linear velocity from scan matching
                   false, false, false,    # Don't trust angular velocity from rf2o
                   false, false, false]
    odom1_queue_size: 10
    odom1_differential: false
    odom1_relative: false
    odom1_remove_gravitational_acceleration: false
    odom1_pose_rejection_threshold: 5.0
    odom1_twist_rejection_threshold: 3.0
    
    # ── Sensor 2: IMU (future — currently rf2o publishes /imu/data, not ESP32) ──
    # Uncomment when ESP32 raw IMU is available:
    # imu0: /imu/data_raw
    # imu0_config: [false, false, false,
    #               false, false, false,
    #               false, false, false,
    #               false, false, true,   # VYAW — trust gyro angular velocity
    #               false, false, false]
    # imu0_queue_size: 10
    # imu0_differential: false
    # imu0_relative: false
    # imu0_remove_gravitational_acceleration: true
    
    # ── Process noise (how fast the state can change) ──
    # Smaller values = smoother output, slower response
    # Larger values = noisier output, faster response
    # Tuned for slow tracked robot (max 0.18 m/s, max 1.0 rad/s)
    process_noise_covariance:
      [0.05,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.05,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.06,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.03,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.03,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.03,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.025, 0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.025, 0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.04,  0.0,   0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.01,  0.0,   0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.01,  0.0,   0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.02,  0.0,   0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.01,  0.0,   0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.01,  0.0,
       0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.015]
    
    # ── Initial covariance (uncertainty at startup) ──
    initial_estimate_covariance:
      [1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,  0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9, 0.0,
       0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  1e-9]
```

**Key design decisions in this config:**
- `odom0` (encoders): Trust x, y, yaw position + yaw velocity. Encoders on tracked robots are the most reliable rotation source.
- `odom1` (rf2o): Trust ONLY vx, vy velocity. rf2o's scan-matching gives excellent instantaneous linear velocity, but its absolute pose drifts and its rotation is unreliable during spins.
- `two_d_mode: true` — ignores z, roll, pitch (ground robot).
- `frequency: 30` — EKF runs 3x faster than inputs for smooth interpolation.
- Process noise tuned for slow tracked robot (max 0.18 m/s linear, 1.0 rad/s angular).

---

### Task 3: Create EKF launch file

**Files:**
- Create: `/home/ws/ugv_ws/ekf_odom.launch.py`

```python
#!/usr/bin/env python3
"""EKF odometry fusion for UGV Beast.
Replaces ugv_odom_filter.py with robot_localization EKF.
Fuses encoder odom + laser odom for accurate rotation."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=['/home/ws/ugv_ws/ekf_params.yaml'],
            remappings=[
                # EKF output -> /odom (same topic the rest of the stack expects)
                ('odometry/filtered', '/odom'),
            ],
        ),
    ])
```

---

### Task 4: Update start_ros2.sh — replace odom_filter with EKF

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

In the `slam_3d` case block, replace the odom_filter line with the EKF launch:

**Find:**
```bash
        # Odom filter: smooths rf2o jitter, publishes filtered /odom + TF
        python3 /home/ws/ugv_ws/ugv_odom_filter.py &
        sleep 2
```

**Replace with:**
```bash
        # EKF sensor fusion: encoders (rotation) + rf2o (translation) -> /odom + TF
        ros2 launch /home/ws/ugv_ws/ekf_odom.launch.py &
        sleep 5  # EKF needs a moment to initialize and receive first messages
```

**Why sleep 5:** The EKF needs to receive at least one message from each configured sensor before it starts publishing. With sensors at 10Hz, this takes ~200ms, but we pad to 5s for safety.

---

### Task 5: Disable base_node and rf2o TF publishing (EKF owns TF now)

**Files:**
- Verify: `/home/ws/ugv_ws/start_ros2.sh`

The bringup already has `pub_odom_tf:=false` and rf2o has `publish_tf: false`. Verify these are still set:

```bash
# In start_ros2.sh:
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false pub_odom_tf:=false &
```

The EKF node's `publish_tf: true` in `ekf_params.yaml` means ONLY the EKF publishes `odom -> base_footprint`. No conflicts.

---

### Task 6: Test the EKF

**Step 1: Restart the service**
```bash
# From host Jetson:
sudo systemctl restart ugv-ros2.service
# Wait 2+ minutes for full boot
```

**Step 2: Verify EKF is running**
```bash
# Inside container:
ros2 node list | grep ekf
# Expected: /ekf_filter_node

ros2 topic hz /odom
# Expected: ~30Hz (EKF output frequency)

ros2 topic echo /odom --once | grep -A3 'position:'
# Should show non-zero x, y values matching robot position
```

**Step 3: Verify TF chain**
```bash
ros2 topic echo /tf | grep child_frame_id | sort -u
# Must include: base_footprint (from EKF), odom (from RTAB-Map)
```

**Step 4: Rotation test — THE KEY TEST**
1. Open Vizanti at http://192.168.1.155:5100
2. Note the current wall thickness in the map
3. Use Vizanti joystick or publish: `ros2 topic pub /cmd_vel geometry_msgs/msg/Twist '{angular: {z: 0.5}}' -r 10`
4. Let the robot spin 360 degrees, then stop
5. Check the map — walls should NOT get thicker
6. Compare encoder yaw vs rf2o yaw:
```bash
# Run the odom_inspect.py script from earlier to see both sources
python3 /tmp/odom_inspect.py
```

**Step 5: Navigation test**
1. Send a Nav2 goal via Vizanti to a different room
2. The robot should navigate there without the costmap becoming unreliable
3. Check that it can navigate BACK — if odom is accurate, the return path should work

---

### Task 7: Tune if needed

If the EKF output is jittery or drifts:

**Too jittery (oscillating):**
- Increase process noise values (allows faster state changes)
- Or decrease sensor covariance (trust sensors more)

**Yaw still drifting during spins:**
- Encoder yaw covariance too high. Lower odom0's yaw trust by changing the rejection threshold
- Or add the IMU as a third source (requires fixing ESP32 firmware)

**XY position drifting:**
- rf2o velocity covariance too low. Increase odom1's vx/vy trust
- This usually means the environment is featureless (long corridor)

**Diagnostic tool:**
```bash
# robot_localization publishes diagnostics:
ros2 topic echo /diagnostics
# Look for "data delay" or "sensor timeout" warnings
```

---

## Rollback

If the EKF causes issues, swap back to the odom_filter in `start_ros2.sh`:

```bash
# Replace the EKF line with:
python3 /home/ws/ugv_ws/ugv_odom_filter.py &
sleep 2
```

The odom_filter.py is still on disk, unchanged.

---

## Future: Adding ESP32 Raw IMU (Task for later)

The ESP32 has an onboard IMU but currently doesn't publish raw gyro/accel on `/imu/data`. The `/imu/data` topic is actually published by the rf2o node (not ESP32).

When we fix the ESP32 firmware to publish raw IMU data on a separate topic (e.g., `/imu/data_raw`):

1. Uncomment the `imu0` section in `ekf_params.yaml`
2. Set `imu0_remove_gravitational_acceleration: true`
3. Fix the frame_id to `base_imu_link`
4. Set proper covariance values (not zeros)

This will give three-source fusion: encoders (yaw) + rf2o (vx/vy) + IMU (angular velocity). The IMU fills the gap between encoder ticks with high-frequency angular rate data.
