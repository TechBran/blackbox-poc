# slam_toolbox Migration — LiDAR-Native SLAM with OAK-D IMU

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace RTAB-Map with slam_toolbox for 2D SLAM mapping, producing clean, accurate occupancy grids from the D500 LiDAR with OAK-D IMU providing heading via EKF.

**Architecture:** slam_toolbox (async online mode) consumes `/scan` + the `odom→base_footprint` TF to build a 2D occupancy grid. Its internal Karto scan matcher aggressively corrects odometry errors by aligning each new scan against the existing map. The EKF still fuses rf2o + OAK-D IMU gyro into `/odom` TF. slam_toolbox publishes the `map→odom` correction TF and `/map` occupancy grid. Nav2 costmaps consume both `/map` (static layer) and `/scan` (obstacle layer) for navigation. RTAB-Map is removed entirely — the OAK-D camera is freed for future depth obstacle avoidance only.

**Tech Stack:** slam_toolbox (ros-humble-slam-toolbox, Karto scan matcher), robot_localization EKF, imu_filter_madgwick, Nav2

**SSH:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23` (container)
**Host:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no jetson@192.168.1.155 -p 22` (docker host)

---

## Current State (verified 2026-04-11)

### What's Working
- OAK-D BMI270 IMU at 60Hz — factory-calibrated, clean gyro data
- IMU axes remapped to ROS convention (camera→robot frame)
- Madgwick filter producing `/imu/data` at ~69Hz
- EKF fusing rf2o + IMU gyro → `/odom` at ~12Hz
- Nav2 navigation functional with correct headings
- D500 LiDAR publishing `/scan` at 10Hz
- ESP32 handling cmd_vel motor control

### What's Broken
- RTAB-Map produces blurry maps — poor translation odometry from rf2o means scans placed at wrong positions
- RTAB-Map's `RGBD/LinearUpdate` threshold prevented stationary map updates (fixed to 0.0 but map quality still poor)
- ESP32 encoders frozen (hardware issue) — no wheel odometry
- Spin recovery behavior fails without encoder feedback (replaced with wait+clear)

### Why slam_toolbox Fixes This
- slam_toolbox's Karto scan matcher does **scan-to-map matching** at every frame — it doesn't blindly trust the odom
- Even with poor rf2o translation, slam_toolbox aligns scans against the existing map using gradient-based optimization
- 2D-native: doesn't process camera images, lower CPU, faster map updates
- Default Nav2 SLAM solution — best tested with Nav2 stack
- Publishes `/map` at configurable rate (every 0.5-2s)

### Installed Packages
- `ros-humble-slam-toolbox` (2.6.10) — verified installed
- Default config: `/opt/ros/humble/share/slam_toolbox/config/mapper_params_online_async.yaml`
- Default launch: `/opt/ros/humble/share/slam_toolbox/launch/`

---

### Task 1: Create slam_toolbox parameter file tuned for UGV Beast

**Files:**
- Create: `/home/ws/ugv_ws/slam_toolbox_params.yaml`

The default slam_toolbox config needs tuning for our robot:
- LiDAR range: D500 max ~12m, not 20m
- Travel thresholds lowered: robot moves slowly, need more frequent scan processing
- Map update interval lowered: near-real-time map updates
- Transform timeout increased: our EKF has some jitter
- Scan matching tuned for indoor environments

```yaml
slam_toolbox:
  ros__parameters:
    # Solver
    solver_plugin: solver_plugins::CeresSolver
    ceres_linear_solver: SPARSE_NORMAL_CHOLESKY
    ceres_preconditioner: SCHUR_JACOBI
    ceres_trust_strategy: LEVENBERG_MARQUARDT
    ceres_dogleg_type: TRADITIONAL_DOGLEG
    ceres_loss_function: None

    # Frame configuration
    odom_frame: odom
    map_frame: map
    base_frame: base_footprint
    scan_topic: /scan
    use_map_saver: true
    mode: mapping

    # Processing
    debug_logging: false
    throttle_scans: 1
    transform_publish_period: 0.02
    map_update_interval: 0.5          # Update map every 0.5s (was 5.0 — much faster)
    resolution: 0.05                  # 5cm grid cells (matches Nav2 costmap)
    min_laser_range: 0.12             # Ignore scans inside robot body (robot_radius)
    max_laser_range: 12.0             # D500 practical indoor range
    minimum_time_interval: 0.2        # Process scans at up to 5Hz (was 0.5)
    transform_timeout: 0.5            # Increased for EKF jitter (was 0.2)
    tf_buffer_duration: 30.0
    stack_size_to_use: 40000000
    enable_interactive_mode: false     # No RViz interaction

    # Travel thresholds — how much movement triggers a new scan processing
    use_scan_matching: true
    use_scan_barycenter: true
    minimum_travel_distance: 0.1      # Process after 10cm movement (was 0.5)
    minimum_travel_heading: 0.15      # Process after ~9° rotation (was 0.5 = ~29°)
    scan_buffer_size: 10
    scan_buffer_maximum_scan_distance: 10.0
    link_match_minimum_response_fine: 0.1
    link_scan_maximum_distance: 1.5
    loop_search_maximum_distance: 3.0
    do_loop_closing: true

    # Loop closure
    loop_match_minimum_chain_size: 10
    loop_match_maximum_variance_coarse: 3.0
    loop_match_minimum_response_coarse: 0.35
    loop_match_minimum_response_fine: 0.45

    # Scan matching correlation — how aggressively it searches for alignment
    correlation_search_space_dimension: 0.5
    correlation_search_space_resolution: 0.01
    correlation_search_space_smear_deviation: 0.1

    # Loop closure correlation
    loop_search_space_dimension: 8.0
    loop_search_space_resolution: 0.05
    loop_search_space_smear_deviation: 0.03

    # Scan matcher tuning
    distance_variance_penalty: 0.5
    angle_variance_penalty: 1.0
    fine_search_angle_offset: 0.00349
    coarse_search_angle_offset: 0.349
    coarse_angle_resolution: 0.0349
    minimum_angle_penalty: 0.9
    minimum_distance_penalty: 0.5
    use_response_expansion: true
    min_pass_through: 2
    occupancy_threshold: 0.1
```

**Step 1: Deploy the config file**
```bash
sshpass -p 'jetson' scp -P 23 /tmp/slam_toolbox_params.yaml root@192.168.1.155:/home/ws/ugv_ws/
```

**Verify:**
```bash
ssh root@192.168.1.155 -p 23 "cat /home/ws/ugv_ws/slam_toolbox_params.yaml | head -5"
# Expected: "slam_toolbox:" header
```

---

### Task 2: Create slam_toolbox launch file

**Files:**
- Create: `/home/ws/ugv_ws/slam_toolbox.launch.py`

```python
#!/usr/bin/env python3
"""slam_toolbox async online SLAM for UGV Beast."""
from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='slam_toolbox',
            executable='async_slam_toolbox_node',
            name='slam_toolbox',
            output='screen',
            parameters=['/home/ws/ugv_ws/slam_toolbox_params.yaml'],
        ),
    ])
```

**Step 1: Deploy the launch file**
```bash
sshpass -p 'jetson' scp -P 23 /tmp/slam_toolbox.launch.py root@192.168.1.155:/home/ws/ugv_ws/
```

**Verify:**
```bash
python3 -c "compile(open('/home/ws/ugv_ws/slam_toolbox.launch.py').read(), 'test', 'exec'); print('OK')"
```

---

### Task 3: Update start_ros2.sh — replace RTAB-Map with slam_toolbox

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

In the `slam_3d` block, replace the RTAB-Map launch with slam_toolbox:

**Replace this line:**
```bash
        # RTAB-Map 3D SLAM (proven working with this container)
        ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &
```

**With:**
```bash
        # slam_toolbox 2D SLAM (Karto scan matcher — robust with poor odom)
        echo "=== Starting slam_toolbox ==="
        ros2 launch /home/ws/ugv_ws/slam_toolbox.launch.py &
```

Also rename the mode from `slam_3d` to reflect reality (it's now 2D SLAM). But keep `slam_3d` as the key for backwards compatibility — just update the comment.

**Step 1: Edit start_ros2.sh**
```bash
# On the Jetson, replace the rtabmap line
sed -i 's|ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &|echo "=== Starting slam_toolbox ==="\n        ros2 launch /home/ws/ugv_ws/slam_toolbox.launch.py \&|' /home/ws/ugv_ws/start_ros2.sh
```

**Step 2: Verify the change**
```bash
grep -n 'slam_toolbox\|rtabmap' /home/ws/ugv_ws/start_ros2.sh
# Expected: slam_toolbox line present, rtabmap line gone
```

---

### Task 4: Update Nav2 global costmap — use slam_toolbox's /map

**Files:**
- Modify: `/home/ws/ugv_ws/nav2_explore_params.yaml`

The global costmap's `static_layer` subscribes to `/map`. slam_toolbox publishes to `/map` by default. Verify the `map_subscribe_transient_local` setting is `True` (so it gets the latest map even if it subscribes after slam_toolbox publishes).

**Check current config:**
```bash
grep -A5 'static_layer:' /home/ws/ugv_ws/nav2_explore_params.yaml
```

This should already be correct since both RTAB-Map and slam_toolbox publish to `/map` as `nav_msgs/msg/OccupancyGrid`. No change needed unless the topic name is different.

**Verify:**
```bash
# After boot, check slam_toolbox publishes /map
ros2 topic info /map
# Expected: Type: nav_msgs/msg/OccupancyGrid, Publisher count: 1
```

---

### Task 5: Tune EKF for slam_toolbox compatibility

**Files:**
- Modify: `/home/ws/ugv_ws/ekf_params.yaml`

slam_toolbox needs the `odom→base_footprint` TF to exist and be reasonably accurate. The EKF currently provides this from rf2o + IMU gyro. Two adjustments:

1. **Increase process noise for position** — tell the EKF that position estimates are less certain (since rf2o is weak), so slam_toolbox's scan matching dominates
2. **Keep gyro trust high** — the OAK-D IMU heading is our best signal

Change the position process noise from 0.05 to 0.5 (10x larger = less position confidence):

```yaml
    process_noise_covariance: [0.5, 0.0, ...  # x position noise: 0.05 → 0.5
                               0.0, 0.5, ...  # y position noise: 0.05 → 0.5
                               ...             # rest stays the same
```

This tells the EKF "I'm not very sure about position" — which is honest given rf2o's weakness. slam_toolbox's scan matching will provide the accurate corrections via the `map→odom` TF.

**Step 1: Update the first two diagonal values of process_noise_covariance**
```bash
# On Jetson:
sed -i 's/process_noise_covariance: \[0.05,/process_noise_covariance: [0.5,/' /home/ws/ugv_ws/ekf_params.yaml
sed -i 's/0.0, 0.05, 0.0/0.0, 0.5, 0.0/' /home/ws/ugv_ws/ekf_params.yaml
```

**Verify:**
```bash
grep -A2 'process_noise_covariance' /home/ws/ugv_ws/ekf_params.yaml | head -3
# Expected: [0.5, ... 0.5, ...
```

---

### Task 6: Full restart and verify slam_toolbox pipeline

**Step 1: Clean restart**
```bash
docker restart ugv_jetson_ros_humble
sleep 5
docker exec -d ugv_jetson_ros_humble bash /home/ws/ugv_ws/start_ros2.sh
# Wait 2.5 minutes for full boot (includes gyro calibration)
sleep 150
```

**Step 2: Verify slam_toolbox is running**
```bash
# Process check
ps aux | grep slam_toolbox | grep -v grep
# Expected: async_slam_toolbox_node process

# Node check
ros2 node list | grep slam_toolbox
# Expected: /slam_toolbox

# No RTAB-Map
ps aux | grep rtabmap | grep -v grep
# Expected: no output
```

**Step 3: Verify /map is publishing**
```bash
ros2 topic hz /map
# Expected: rate depends on movement, but should show updates within seconds

ros2 topic info /map
# Expected: Type: nav_msgs/msg/OccupancyGrid, Publisher count: 1
```

**Step 4: Verify TF chain**
```bash
ros2 run tf2_ros tf2_echo map base_footprint
# Expected: valid transform (slam_toolbox publishes map→odom, EKF publishes odom→base_footprint)
```

**Step 5: Verify EKF still working**
```bash
ros2 topic echo /odom --once | grep -E 'x:|y:|z:|w:' | head -8
# Expected: position near origin, yaw near 0
```

---

### Task 7: Stationary map test

**Step 1: Robot stationary — verify /map publishes**

With `minimum_travel_distance: 0.1`, slam_toolbox should process the first scan immediately (no movement required for the first frame). But subsequent updates need 10cm movement.

```bash
# Check if map has been published
ros2 topic echo /map --once 2>&1 | grep -E 'width:|height:|resolution:'
# Expected: width and height > 0, resolution: 0.05
```

**Step 2: Check map in Vizanti**

Open Vizanti (http://192.168.1.155). The 2D map should show walls around the robot based on the initial LiDAR scan. The map should appear IMMEDIATELY, not after driving around.

**If no map visible:** slam_toolbox might be waiting for the first odom TF. Drive the robot forward 15cm to trigger the first scan processing.

---

### Task 8: Driving map quality test

**Step 1: Drive forward 1 meter**

Using Vizanti joystick, drive the robot straight forward ~1 meter.

**Verify:**
- Robot icon moves forward on the map (not backward, not diagonal)
- Walls on either side appear as clean straight lines (not doubled/blurry)
- The map extends as new areas come into LiDAR view

**Step 2: Rotate 360°**

Spin the robot in place one full turn.

**Verify:**
- Robot icon rotates smoothly in Vizanti
- Walls do NOT get thicker during rotation
- Map should be a clean representation of the room after the rotation

**Step 3: Drive a loop**

Drive the robot around a room and return to the starting position.

**Verify:**
- Map shows clean room outline
- When returning to start, the map "closes" (loop closure)
- Robot icon returns to approximately the starting position
- No ghost walls or doubled walls

**Expected outcomes:**
- slam_toolbox's scan matcher corrects rf2o's poor translation estimates
- The OAK-D IMU provides accurate heading so scans align angularly
- Map quality should be dramatically better than RTAB-Map since slam_toolbox doesn't depend on external odom accuracy for scan registration

---

### Task 9: Nav2 navigation test

**Step 1: Verify Nav2 uses the new map**

```bash
ros2 topic echo /global_costmap/costmap_raw --once 2>&1 | head -5
# Expected: data present (costmap populated from slam_toolbox's /map)
```

**Step 2: Send a Nav2 goal**

In Vizanti, set a navigation goal (click a point on the map). Nav2 should:
- Plan a path through the costmap
- Execute the path with RotationShimController + MPPI
- Arrive at the goal without spin recovery errors

**Verify:**
- Path planning works (green path visible)
- Robot follows the path
- No spin timeout errors
- Robot reaches goal

---

## Rollback

If slam_toolbox doesn't work, revert to RTAB-Map:

```bash
# In start_ros2.sh, replace slam_toolbox line with:
ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &
```

RTAB-Map launch file and config are preserved and unchanged. The `rtabmap_custom.launch.py` file remains on disk.

Also revert EKF process noise if needed:
```bash
# Change 0.5 back to 0.05 in ekf_params.yaml process_noise_covariance
```

---

## Future Improvements (after slam_toolbox is verified)

1. **Save/load maps** — slam_toolbox supports serializing maps to disk and loading them for localization mode
2. **OAK-D depth obstacle avoidance** — add depth pointcloud to Nav2 costmap as a separate layer (now that OAK-D isn't used for SLAM)
3. **Fix ESP32 encoders** — hardware debug/replacement to restore wheel odometry for better translation tracking
4. **Visual odometry** — use OAK-D stereo features for translation estimation (supplement rf2o)
5. **Localization mode** — switch slam_toolbox to localization mode with a saved map for production use
