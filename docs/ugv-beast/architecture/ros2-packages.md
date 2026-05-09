# UGV Beast ROS2 Packages Reference

## Workspace: ugv_ws

All ROS2 packages run inside a Docker container on the Jetson.
- Host path: `/home/jetson/ugv_ws/`
- Docker path: `/home/ws/ugv_ws/`
- Distribution: ROS2 Humble (Ubuntu 22.04)

## Custom Packages (ugv_main)

| Package | Purpose |
|---------|---------|
| `ugv_base_node` | Two-wheel differential kinematics (C++), UART bridge to ESP32 |
| `ugv_bringup` | Driver launch, LiDAR bringup, IMU/EKF fusion |
| `ugv_description` | URDF/XACRO robot model |
| `ugv_interface` | Custom message/action/service definitions |
| `ugv_nav` | Navigation (Nav2) with AMCL/EMCL + DWA/TEB planners |
| `ugv_slam` | SLAM (GMapping, Cartographer, RTAB-Map) |
| `ugv_gazebo` | Gazebo simulation |
| `ugv_vision` | Camera, AprilTag, color tracking, gesture recognition |
| `ugv_tools` | Keyboard/joystick teleop, behavior controller |
| `ugv_chat_ai` | Web AI chat (Ollama LLM -> JSON robot commands) |
| `ugv_web_app` | Vizanti web-based control interface |

## Vendored Dependencies (ugv_else)

| Package | Purpose |
|---------|---------|
| `apriltag_ros` | AprilTag visual marker detection |
| `cartographer` | Google Cartographer 2D SLAM |
| `costmap_converter` | Costmap conversion for TEB planner |
| `emcl2_ros2` | Enhanced Monte Carlo Localization |
| `explore_lite` | Autonomous frontier exploration |
| `gmapping` | GMapping 2D SLAM (openslam_gmapping + slam_gmapping) |
| `ldlidar` | LDRobot LiDAR driver (LD06, LD19, STL27L) |
| `rf2o_laser_odometry` | Laser scan-based odometry |
| `robot_pose_publisher` | TF to pose publisher |
| `teb_local_planner` | Time-Elastic Band local planner |
| `vizanti` | Web-based ROS2 visualization |

## apt Dependencies

```
ros-humble-cartographer-*
ros-humble-desktop-*
ros-humble-joint-state-publisher-*
ros-humble-nav2-*
ros-humble-rosbridge-*
ros-humble-rqt-*
ros-humble-rtabmap-*
ros-humble-usb-cam
ros-humble-depthai-*
```

## Model Selection

Environment variable `UGV_MODEL` selects the URDF:
```bash
export UGV_MODEL=ugv_beast    # Our model (type code: 3)
# Other options:
# rasp_rover (type code: 1)
# ugv_rover  (type code: 2)
```

LiDAR model via `LDLIDAR_MODEL`:
```bash
export LDLIDAR_MODEL=ld19     # Default
# Other: ld06, stl27l
```

## Key Launch Commands

### Bringup (driver + LiDAR + RViz)
```bash
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=true
```

### 2D SLAM
```bash
# GMapping
ros2 launch ugv_slam gmapping.launch.py use_rviz:=true
# Save map:
chmod +x ./save_2d_gmapping_map.sh && ./save_2d_gmapping_map.sh

# Cartographer
ros2 launch ugv_slam cartographer.launch.py use_rviz:=true
# Save map:
chmod +x ./save_2d_cartographer_map.sh && ./save_2d_cartographer_map.sh
```

### 3D SLAM (RTAB-Map with depth camera)
```bash
ros2 launch ugv_slam rtabmap_rgbd.launch.py use_rviz:=true
# Map auto-saves to ~/.ros/rtabmap.db on Ctrl+C
```

### Navigation (requires a saved map)
```bash
# AMCL localization + default planner
ros2 launch ugv_nav nav.launch.py use_localization:=amcl use_rviz:=true

# EMCL localization
ros2 launch ugv_nav nav.launch.py use_localization:=emcl use_rviz:=true

# Cartographer pure positioning
ros2 launch ugv_nav nav.launch.py use_localization:=cartographer use_rviz:=true

# DWA local planner
ros2 launch ugv_nav nav.launch.py use_localplan:=dwa use_rviz:=true

# TEB local planner
ros2 launch ugv_nav nav.launch.py use_localplan:=teb use_rviz:=true
```

### Simultaneous SLAM + Navigation
```bash
ros2 launch ugv_nav slam_nav.launch.py use_rviz:=true
```

### Autonomous Exploration
```bash
ros2 launch explore_lite explore.launch.py
```

### 3D Navigation
```bash
ros2 launch ugv_nav rtabmap_localization_launch.py
ros2 launch ugv_nav nav_rtabmap.launch.py use_localplan:=dwa use_rviz:=true
```

### Teleop
```bash
# Joystick (verify /dev/input/js0 exists)
ros2 launch ugv_tools teleop_twist_joy.launch.py

# Keyboard
ros2 run ugv_tools keyboard_ctrl
```

### Web Tools
```bash
# Vizanti web control
ros2 launch ugv_web_app bringup.launch.py host:=<jetson_ip>
# Access at http://<jetson_ip>:5100

# AI Chat
ros2 run ugv_chat_ai app
ros2 run ugv_tools behavior_ctrl
```

### Gazebo Simulation
```bash
ros2 launch ugv_gazebo bringup.launch.py
```

## ROS2 Command Interaction

### Movement via Actions
```bash
# Start behavior controller first:
ros2 run ugv_tools behavior_ctrl

# Move forward 0.5m
ros2 action send_goal /behavior ugv_interface/action/Behavior \
  "{command: '[{\"T\": 1, \"type\": \"drive_on_heading\", \"data\": 0.5}]'}"

# Move backward 0.5m
ros2 action send_goal /behavior ugv_interface/action/Behavior \
  "{command: '[{\"T\": 1, \"type\": \"back_up\", \"data\": 0.5}]'}"

# Rotate -50 degrees (negative=right, positive=left)
ros2 action send_goal /behavior ugv_interface/action/Behavior \
  "{command: '[{\"T\": 1, \"type\": \"spin\", \"data\": -50}]'}"

# Stop
ros2 action send_goal /behavior ugv_interface/action/Behavior \
  "{command: '[{\"T\": 1, \"type\": \"stop\", \"data\": 0}]'}"
```

### Save/Navigate to Points
```bash
# Get current pose
ros2 topic echo /robot_pose --once

# Save point (a through g)
ros2 action send_goal /behavior ugv_interface/action/Behavior \
  "{command: '[{\"T\": 1, \"type\": \"save_map_point\", \"data\": \"a\"}]'}"

# Navigate to saved point
ros2 action send_goal /behavior ugv_interface/action/Behavior \
  "{command: '[{\"T\": 1, \"type\": \"pub_nav_point\", \"data\": \"a\"}]'}"
```
- Points saved in `/home/ws/ugv_ws/map_points.txt`

## Map Storage

| Type | Path |
|------|------|
| 2D maps (GMapping/Cartographer) | `/home/ws/ugv_ws/src/ugv_main/ugv_nav/maps/` |
| 3D maps (RTAB-Map) | `~/.ros/rtabmap.db` |
| Navigation points | `/home/ws/ugv_ws/map_points.txt` |
| Nav launch config | `/home/ws/ugv_ws/src/ugv_main/ugv_nav/launch/nav.launch.py` |
