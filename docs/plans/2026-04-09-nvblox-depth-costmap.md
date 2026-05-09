# nvblox 3D Depth Costmap + slam_toolbox Migration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace RTAB-Map with slam_toolbox (2D LiDAR SLAM) + nvblox (3D depth from OAK-D Lite), giving Nav2 a merged costmap that detects floor clutter, overhangs, and obstacles the 2D LiDAR misses.

**Architecture:** LiDAR owns 2D mapping/localization via slam_toolbox. OAK-D Lite owns 3D reconstruction via nvblox GPU-accelerated TSDF. Both feed independent layers into Nav2's costmap, which merges them (MAX cost per cell). A new Docker container is built from dustynv's jetson-containers with Isaac ROS nvblox + ROS2 Humble desktop as the base, plus all existing UGV packages layered on top.

**Tech Stack:** Isaac ROS 3.2 nvblox, slam_toolbox, Nav2, depthai 3.5, CUDA 12.6, JetPack 6.2 (L4T R36.4.7), Jetson Orin Nano 8GB

---

## Current State

**Jetson:** 192.168.1.155, JetPack 6.2, L4T R36.4.7
**Container:** `ugv_jetson_ros_humble:built` (16.4GB), base: `dustynv/ros:humble-desktop-l4t-r36.4.0`
**Host binds:** `/dev:/dev`, `/home/jetson/ugv_ws:/home/ws/ugv_ws`, `/tmp/.X11-unix`
**Network:** `--network host`

**OAK-D Lite topics (from ugv_depth_node.py):**
- `/oak/rgb/image_rect` (Image, 640x480 RGB)
- `/oak/stereo/image_raw` (Image, 16UC1 depth in mm, 640x480)
- `/oak/rgb/camera_info` (CameraInfo, from EEPROM calibration)
- `/depth/image/compressed` (CompressedImage, colorized JET for Vizanti)
- `/depth/rgb/compressed` (CompressedImage, RGB for Vizanti)
- Frame ID: `oak_rgb_camera_optical_frame` (published by ugv_depth_node.py)

**LiDAR:** `/scan` (LaserScan, D500 LD19, ~10Hz, 12m range)
**Odometry:** `/odom` (filtered rf2o from ugv_odom_filter.py)
**SLAM (currently):** RTAB-Map → `/map`, `map→odom` TF, `/cloud_map` PointCloud2

---

## Task Overview

| Task | What | Where |
|------|------|-------|
| 1 | Build new Docker container with nvblox | Host Jetson |
| 2 | Create slam_toolbox launch + config | Container `/home/ws/ugv_ws/` |
| 3 | Create nvblox launch + config | Container `/home/ws/ugv_ws/` |
| 4 | Update Nav2 costmap config for dual-layer | Container `/home/ws/ugv_ws/nav2_explore_params.yaml` |
| 5 | Modify ugv_depth_node.py for nvblox compatibility | Container `/home/ws/ugv_ws/ugv_depth_node.py` |
| 6 | Update start_ros2.sh boot sequence | Container `/home/ws/ugv_ws/start_ros2.sh` |
| 7 | Test and validate | Full system test |

---

### Task 1: Build New Docker Container with nvblox

**Files:**
- Create: `/home/jetson/ugv_ws/Dockerfile.nvblox`
- Create: `/home/jetson/ugv_ws/build_container.sh`

**Context:** The existing container is based on `dustynv/ros:humble-desktop-l4t-r36.4.0`. We need to add Isaac ROS nvblox. The cleanest path is using NVIDIA's Isaac Apt Repository inside a new Dockerfile that extends the existing base.

**Step 1: Create the Dockerfile**

```dockerfile
# Dockerfile.nvblox — UGV Beast ROS2 + nvblox container
# Extends existing ROS2 Humble desktop with Isaac ROS nvblox
FROM dustynv/ros:humble-desktop-l4t-r36.4.0

ENV DEBIAN_FRONTEND=noninteractive

# ── Isaac ROS Apt Repository ──
RUN curl -fsSL https://isaac.download.nvidia.com/isaac-ros/repos.key \
      | gpg --dearmor -o /usr/share/keyrings/nvidia-isaac-ros.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/nvidia-isaac-ros.gpg] https://isaac.download.nvidia.com/isaac-ros/release-3 jammy release-3.0" \
      > /etc/apt/sources.list.d/nvidia-isaac-ros.list \
    && apt-get update

# ── Core nvblox packages (avoid pulling the full Isaac meta-packages) ──
# Install nvblox_ros and nvblox_nav2 specifically.
# If the apt install fails due to driver 560 conflict, we fall back to
# building the core nvblox library from source (see Step 1b below).
RUN apt-get install -y --no-install-recommends \
      ros-humble-nvblox-ros \
      ros-humble-nvblox-nav2 \
      ros-humble-nvblox-msgs \
    2>/dev/null || echo "NVBLOX_APT_FAILED=1" > /tmp/nvblox_apt_status

# ── slam_toolbox (lightweight 2D SLAM, replaces RTAB-Map for localization) ──
RUN apt-get install -y --no-install-recommends \
      ros-humble-slam-toolbox

# ── Nav2 (if not already in base) ──
RUN apt-get install -y --no-install-recommends \
      ros-humble-navigation2 \
      ros-humble-nav2-bringup

# ── depth_image_proc (PointCloud2 generation from depth, needed if nvblox wants it) ──
RUN apt-get install -y --no-install-recommends \
      ros-humble-depth-image-proc

# ── depthai (OAK-D Lite SDK) ──
RUN pip3 install --no-cache-dir depthai==3.5.1

# ── YOLO dependencies ──
RUN pip3 install --no-cache-dir 'numpy<2' opencv-contrib-python

# ── Foxglove Bridge ──
RUN apt-get install -y --no-install-recommends \
      ros-humble-foxglove-bridge

# ── Vizanti web UI ──
RUN apt-get install -y --no-install-recommends \
      ros-humble-rosbridge-suite \
    2>/dev/null || true

# ── Utilities ──
RUN apt-get install -y --no-install-recommends \
      tmux htop nano sshpass openssh-server python3-serial \
      python3-scipy python3-transforms3d \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Source ROS2 on shell entry ──
RUN echo 'source /opt/ros/humble/setup.bash' >> /root/.bashrc \
    && echo 'source /opt/ros/humble/install/setup.bash 2>/dev/null' >> /root/.bashrc \
    && echo 'source /home/ws/ugv_ws/install/setup.bash 2>/dev/null' >> /root/.bashrc

WORKDIR /home/ws/ugv_ws
```

**Step 1b: Fallback — build nvblox core from source (if apt fails)**

If the apt install reports `NVBLOX_APT_FAILED=1` due to driver 560 conflicts, add this block to the Dockerfile instead:

```dockerfile
# ── Build nvblox core library from source ──
RUN apt-get install -y --no-install-recommends \
      cmake libgoogle-glog-dev libgflags-dev libeigen3-dev libsqlite3-dev \
    && git clone --recursive https://github.com/nvidia-isaac/nvblox.git /tmp/nvblox \
    && mkdir -p /tmp/nvblox/nvblox/build \
    && cd /tmp/nvblox/nvblox/build \
    && cmake .. -DBUILD_PYTORCH_WRAPPER=0 -DCMAKE_INSTALL_PREFIX=/usr/local \
    && make -j$(nproc) && make install \
    && rm -rf /tmp/nvblox

# ── Build nvblox_ros from source ──
RUN mkdir -p /opt/nvblox_ws/src \
    && cd /opt/nvblox_ws/src \
    && git clone -b release-3.2 https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_nvblox.git \
    && cd /opt/nvblox_ws \
    && source /opt/ros/humble/setup.bash \
    && colcon build --packages-select nvblox_msgs nvblox_ros nvblox_nav2 \
       --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && echo 'source /opt/nvblox_ws/install/setup.bash' >> /root/.bashrc
```

**Step 2: Create build script**

```bash
#!/bin/bash
# build_container.sh — Build the nvblox-enabled UGV container
# Run on HOST Jetson (not inside container)

set -e
cd /home/jetson/ugv_ws

echo "=== Building UGV nvblox container ==="
echo "This will take 15-30 minutes..."

docker build \
    -f Dockerfile.nvblox \
    -t ugv_jetson_nvblox:latest \
    --network=host \
    .

echo "=== Build complete ==="
echo "Image: ugv_jetson_nvblox:latest"
docker images | grep ugv_jetson_nvblox
```

**Step 3: Run the build on host Jetson**

```bash
ssh jetson@192.168.1.155
cd /home/jetson/ugv_ws
chmod +x build_container.sh
./build_container.sh
```

Expected: 15-30 minutes build time. Image size ~18-20GB.

**Step 4: Update the systemd service to use the new image**

The systemd service file at `/etc/systemd/system/ugv-ros2.service` references the container by name. After building, update the docker run command to use `ugv_jetson_nvblox:latest`.

```bash
# On host Jetson:
# Stop current container
docker stop ugv_jetson_ros_humble

# Start new container with same bind mounts + name
docker run -d --name ugv_jetson_nvblox \
    --runtime nvidia \
    --network host \
    --privileged \
    -v /dev:/dev \
    -v /home/jetson/ugv_ws:/home/ws/ugv_ws \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -e DISPLAY=:0 \
    ugv_jetson_nvblox:latest \
    /bin/bash -c "/home/ws/ugv_ws/start_ros2.sh"
```

Update the systemd service file to reference the new container name/image.

**Verify:**
```bash
docker exec -it ugv_jetson_nvblox bash
ros2 pkg list | grep nvblox
# Should show: nvblox_msgs, nvblox_nav2, nvblox_ros (or just nvblox_msgs if source-built)
ros2 pkg list | grep slam_toolbox
# Should show: slam_toolbox
```

---

### Task 2: Create slam_toolbox Launch + Config

**Files:**
- Create: `/home/ws/ugv_ws/slam_toolbox_params.yaml`
- Create: `/home/ws/ugv_ws/slam_toolbox.launch.py`

**Step 1: Create slam_toolbox parameter file**

```yaml
# slam_toolbox_params.yaml — 2D LiDAR SLAM for UGV Beast
# Replaces RTAB-Map for localization + /map generation.
# Lightweight: ~150MB RAM, CPU-only, no GPU needed.
slam_toolbox:
  ros__parameters:
    # Solver
    solver_plugin: solver_plugins::CeresSolver
    ceres_linear_solver: SPARSE_NORMAL_CHOLESKY
    ceres_preconditioner: SCHUR_JACOBI
    ceres_trust_strategy: LEVENBERG_MARQUARDT
    ceres_dogleg_type: TRADITIONAL_DOGLEG
    ceres_loss_function: None

    # Frame IDs (must match UGV Beast TF tree)
    odom_frame: odom
    map_frame: map
    base_frame: base_footprint
    scan_topic: /scan

    # Mode: "mapping" for SLAM, "localization" for saved map
    mode: mapping

    # Timing
    transform_publish_period: 0.02       # 50Hz map→odom TF
    map_update_interval: 3.0             # Update /map every 3s (save CPU)
    resolution: 0.05                     # 5cm grid (matches Nav2 costmaps)

    # Laser parameters (D500 LD19)
    max_laser_range: 12.0                # D500 max range
    minimum_time_interval: 0.5           # Process scans every 0.5s
    transform_timeout: 0.5
    tf_buffer_duration: 30.0

    # Scan matching
    use_scan_matching: true
    use_scan_barycenter: true
    minimum_travel_distance: 0.3         # Only add scan after 30cm movement
    minimum_travel_heading: 0.3          # Or 0.3 rad (~17°) rotation

    # Loop closure
    do_loop_closing: true
    loop_search_maximum_distance: 3.0
    loop_match_minimum_chain_size: 10
    loop_match_minimum_response_coarse: 0.35
    loop_match_minimum_response_fine: 0.45

    # Scan buffer
    scan_buffer_size: 10
    scan_buffer_maximum_scan_distance: 10.0
    link_match_minimum_response_fine: 0.1
    link_scan_maximum_distance: 1.5

    # Correlation search
    correlation_search_space_dimension: 0.5
    correlation_search_space_resolution: 0.01
    correlation_search_space_smear_deviation: 0.1

    # Loop search
    loop_search_space_dimension: 8.0
    loop_search_space_resolution: 0.05
    loop_search_space_smear_deviation: 0.03

    # Penalties
    distance_variance_penalty: 0.5
    angle_variance_penalty: 1.0
    fine_search_angle_offset: 0.00349
    coarse_search_angle_offset: 0.349
    coarse_angle_resolution: 0.0349
    minimum_angle_penalty: 0.9
    minimum_distance_penalty: 0.5
    use_response_expansion: true

    # Stack
    stack_size_to_use: 40000000

    # Map saving
    use_map_saver: true
    map_file_name: ""                    # Empty = auto timestamp

    # Misc
    debug_logging: false
    throttle_scans: 1
    enable_interactive_mode: false       # No RViz needed
    use_sim_time: false
```

**Step 2: Create launch file**

```python
#!/usr/bin/env python3
"""slam_toolbox launch for UGV Beast — 2D LiDAR SLAM only."""

from launch import LaunchDescription
from launch_ros.actions import Node


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

**Verify:**
```bash
ros2 launch /home/ws/ugv_ws/slam_toolbox.launch.py &
sleep 5
# Check /map is publishing
ros2 topic hz /map
# Check map→odom TF
ros2 topic echo /tf --once | grep -A2 'map'
```

---

### Task 3: Create nvblox Launch + Config

**Files:**
- Create: `/home/ws/ugv_ws/nvblox_params.yaml`
- Create: `/home/ws/ugv_ws/nvblox.launch.py`

**Step 1: Create nvblox parameter file**

```yaml
# nvblox_params.yaml — 3D depth reconstruction for UGV Beast
# Consumes OAK-D Lite depth + RGB, produces 2D costmap slice for Nav2.
# GPU-accelerated TSDF integration on Jetson Orin Nano.
nvblox_node:
  ros__parameters:
    # ── Voxel grid ──
    voxel_size: 0.05                                    # 5cm voxels (matches costmap resolution)

    # ── ESDF mode ──
    esdf_mode: "2d"                                     # 2D slice for Nav2 costmap (lighter than full 3D ESDF)

    # ── Mapping type ──
    mapping_type: "static_tsdf"                         # Static environment (no human detection needed)

    # ── Input sources ──
    use_depth: true
    use_color: false                                    # Save GPU — no colored mesh needed for costmap
    use_lidar: false                                    # LiDAR goes to its own costmap layer
    num_cameras: 1

    # ── Processing rates ──
    integrate_depth_rate_hz: 15.0                       # Match OAK-D Lite depth FPS on USB 2.0
    integrate_color_rate_hz: 0.0                        # Disabled
    integrate_lidar_rate_hz: 0.0                        # Disabled
    update_esdf_rate_hz: 5.0                            # 5Hz ESDF → costmap update (saves GPU)
    update_mesh_rate_hz: 0.0                            # Disabled — no mesh visualization needed
    publish_layer_rate_hz: 1.0                          # Slow publishing for debug only

    # ── Map bounds ──
    map_clearing_radius_m: 5.0                          # Only keep 5m radius around robot (bound memory)
    map_clearing_frame_id: "base_footprint"

    # ── Depth integration ──
    projective_integrator_max_integration_distance_m: 4.0   # OAK-D Lite reliable to ~4m at 640x480
    projective_integrator_truncation_distance_vox: 4.0
    max_back_projection_distance: 5.0
    weighting_mode: "inverse_square"

    # ── Costmap slice ──
    # Height bounds for the 2D costmap slice — what counts as an obstacle
    slice_height: 0.15                                  # Slice at 15cm above ground (catches floor obstacles)
    min_height: 0.05                                    # Ignore anything below 5cm (ground noise)
    max_height: 1.5                                     # Ignore anything above 1.5m (ceiling, high shelves)

    # ── TF ──
    global_frame: "odom"                                # Must match Nav2 local costmap global_frame
    pose_frame: "base_footprint"
    use_tf_transforms: true                             # Get pose from TF tree (slam_toolbox provides map→odom)

    # ── QoS ──
    depth_qos: "SENSOR_DATA"
    color_qos: "SENSOR_DATA"
```

**Step 2: Create launch file**

```python
#!/usr/bin/env python3
"""nvblox launch for UGV Beast — 3D depth → costmap slice."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='nvblox_ros',
            executable='nvblox_node',
            name='nvblox_node',
            output='screen',
            parameters=['/home/ws/ugv_ws/nvblox_params.yaml'],
            remappings=[
                # Map OAK-D Lite topics to nvblox expected names
                ('depth/image', '/oak/stereo/image_raw'),
                ('depth/camera_info', '/oak/rgb/camera_info'),
                # Color disabled, but remap anyway for future use
                ('color/image', '/oak/rgb/image_rect'),
                ('color/camera_info', '/oak/rgb/camera_info'),
            ],
        ),
    ])
```

**Verify:**
```bash
ros2 launch /home/ws/ugv_ws/nvblox.launch.py &
sleep 5
# Check nvblox is publishing the costmap slice
ros2 topic list | grep nvblox
# Should see: /nvblox_node/static_map_slice
ros2 topic hz /nvblox_node/static_map_slice
# Should show ~5Hz
```

---

### Task 4: Update Nav2 Costmap for Dual-Layer (LiDAR + nvblox)

**Files:**
- Modify: `/home/ws/ugv_ws/nav2_explore_params.yaml`

**What to change in local_costmap:**

Replace the current `plugins` list and `obstacle_layer` with a dual-source setup:

```yaml
local_costmap:
  local_costmap:
    ros__parameters:
      update_frequency: 5.0
      publish_frequency: 2.0
      global_frame: odom
      robot_base_frame: base_footprint
      use_sim_time: false
      rolling_window: true
      width: 3
      height: 3
      resolution: 0.05
      robot_radius: 0.12
      plugins: ["obstacle_layer", "nvblox_layer", "inflation_layer"]

      # ── LiDAR 2D obstacles (360°, 12m range) ──
      obstacle_layer:
        plugin: "nav2_costmap_2d::ObstacleLayer"
        enabled: True
        observation_sources: scan
        footprint_clearing_enabled: true
        max_obstacle_height: 2.0
        scan:
          topic: /scan
          max_obstacle_height: 2.0
          clearing: True
          marking: True
          data_type: "LaserScan"
          raytrace_max_range: 3.5
          raytrace_min_range: 0.12
          obstacle_max_range: 2.5
          obstacle_min_range: 0.12
          inf_is_valid: false

      # ── OAK-D 3D depth obstacles (73° FOV, floor+overhead detection) ──
      nvblox_layer:
        plugin: "nvblox::nav2::NvbloxCostmapLayer"
        enabled: True
        nav2_costmap_global_frame: odom
        nvblox_map_slice_topic: "/nvblox_node/static_map_slice"
        convert_to_binary_costmap: True

      # ── Inflation (MUST be last) ──
      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        cost_scaling_factor: 5.0
        inflation_radius: 0.30

      always_send_full_costmap: True
      transform_tolerance: 2.0
```

**Same change for global_costmap** — add `nvblox_layer` between `obstacle_layer` and `inflation_layer`:

```yaml
global_costmap:
  global_costmap:
    ros__parameters:
      update_frequency: 2.0
      publish_frequency: 2.0
      global_frame: map
      robot_base_frame: base_footprint
      use_sim_time: false
      robot_radius: 0.12
      transform_tolerance: 2.0
      resolution: 0.05
      track_unknown_space: true
      plugins: ["static_layer", "obstacle_layer", "nvblox_layer", "inflation_layer"]

      static_layer:
        plugin: "nav2_costmap_2d::StaticLayer"
        map_subscribe_transient_local: True

      obstacle_layer:
        plugin: "nav2_costmap_2d::ObstacleLayer"
        enabled: True
        observation_sources: scan
        footprint_clearing_enabled: true
        scan:
          topic: /scan
          max_obstacle_height: 2.0
          clearing: True
          marking: True
          data_type: "LaserScan"
          raytrace_max_range: 3.0
          raytrace_min_range: 0.12
          obstacle_max_range: 2.5
          obstacle_min_range: 0.12

      nvblox_layer:
        plugin: "nvblox::nav2::NvbloxCostmapLayer"
        enabled: True
        nav2_costmap_global_frame: map
        nvblox_map_slice_topic: "/nvblox_node/static_map_slice"
        convert_to_binary_costmap: True

      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        cost_scaling_factor: 5.0
        inflation_radius: 0.30

      always_send_full_costmap: True
```

**Verify:**
```bash
# After restarting Nav2, check that both layers are loaded
ros2 param get /local_costmap/local_costmap plugins
# Should include: obstacle_layer, nvblox_layer, inflation_layer
```

---

### Task 5: Update ugv_depth_node.py for nvblox Compatibility

**Files:**
- Modify: `/home/ws/ugv_ws/ugv_depth_node.py`

**What to change:**

nvblox expects depth images with a valid `CameraInfo` on the same timestamp. The current ugv_depth_node.py already publishes:
- `/oak/stereo/image_raw` (16UC1 depth in mm)
- `/oak/rgb/camera_info` (CameraInfo from EEPROM)

nvblox needs the **depth camera_info**, not the RGB camera_info, on a matching topic. Since the OAK-D Lite depth is aligned to RGB (`setDepthAlign(CAM_A)`), the RGB intrinsics ARE the correct ones for the depth image.

**The one change needed:** Publish a copy of camera_info on `/oak/stereo/camera_info` so nvblox can find it via the standard naming convention. This is just an alias — same data, different topic name.

In `ugv_depth_node.py` `__init__`, add:

```python
        # Depth camera_info (same as RGB since depth is aligned to RGB)
        self.depth_camera_info_pub = self.create_publisher(CameraInfo, '/oak/stereo/camera_info', 10)
```

And wherever `camera_info_pub.publish(info)` is called, also publish to the depth topic:

```python
        self.camera_info_pub.publish(info)
        self.depth_camera_info_pub.publish(info)
```

This ensures nvblox can remap `depth/camera_info` to `/oak/stereo/camera_info` or `/oak/rgb/camera_info` — both work.

**Also verify the frame_id in the published images matches the TF tree:**

The Image messages must have `header.frame_id = 'oak_rgb_camera_optical_frame'` and a valid static TF from `base_footprint` → `oak_rgb_camera_optical_frame` must exist.

If there is no static TF published for the OAK-D mount position, add one to the launch or start_ros2.sh:

```bash
# Add to start_ros2.sh before the OAK-D depth node
ros2 run tf2_ros static_transform_publisher \
    0.15 0.0 0.12 0.0 0.0 0.0 \
    base_footprint oak_camera_link &
```

The OAK-D Lite internal TF (`oak_camera_link → oak_rgb_camera_optical_frame`) should be published by ugv_depth_node.py or a separate static publisher.

---

### Task 6: Update start_ros2.sh Boot Sequence

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

**What to change:**

Replace RTAB-Map (step 1 under slam_3d) with slam_toolbox, add nvblox after OAK-D depth, and add the OAK-D static TF.

**In the `slam_3d` case block, replace RTAB-Map with slam_toolbox:**

Old:
```bash
    slam_3d)
        ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false pub_odom_tf:=false &
        sleep 10
        python3 /home/ws/ugv_ws/ugv_odom_filter.py &
        sleep 2
        ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &
        ;;
```

New:
```bash
    slam_3d)
        # Bringup (LiDAR + motors + TF) — disable base_node odom TF
        ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false pub_odom_tf:=false &
        sleep 10
        # Odom filter: smooths rf2o jitter, publishes filtered /odom + TF
        python3 /home/ws/ugv_ws/ugv_odom_filter.py &
        sleep 2
        # slam_toolbox: 2D LiDAR SLAM (replaces RTAB-Map — lighter, CPU-only)
        ros2 launch /home/ws/ugv_ws/slam_toolbox.launch.py &
        ;;
```

**After the OAK-D depth node (step 9), add OAK-D static TF and nvblox:**

```bash
# 9b. OAK-D camera static TF (mount position on robot)
echo "=== Publishing OAK-D static TF ==="
ros2 run tf2_ros static_transform_publisher \
    0.15 0.0 0.12 0.0 0.0 0.0 \
    base_footprint oak_camera_link &
sleep 1

# 9c. nvblox 3D depth reconstruction (OAK-D → TSDF → Nav2 costmap)
echo "=== Starting nvblox ==="
ros2 launch /home/ws/ugv_ws/nvblox.launch.py &
sleep 5
```

**Note:** The static TF values (0.15m forward, 0.12m up) are approximate. Measure the actual OAK-D Lite mount position on the UGV Beast and adjust x/y/z and any pitch angle.

---

### Task 7: Test and Validate

**Step 1: Build and start the new container (host Jetson)**

```bash
ssh jetson@192.168.1.155
cd /home/jetson/ugv_ws
./build_container.sh
# Update systemd service or docker run command
# Restart
sudo systemctl restart ugv-ros2.service
```

**Step 2: Verify all nodes are running (inside container)**

```bash
ros2 node list | grep -E 'slam_toolbox|nvblox|controller|planner|bt_nav'
```

Expected output:
```
/slam_toolbox
/nvblox_node
/controller_server
/planner_server
/bt_navigator
```

**Step 3: Verify topics**

```bash
# slam_toolbox producing /map
ros2 topic hz /map
# Expected: ~0.3Hz (every 3s per config)

# nvblox producing costmap slice
ros2 topic hz /nvblox_node/static_map_slice
# Expected: ~5Hz

# LiDAR still publishing
ros2 topic hz /scan
# Expected: ~10Hz
```

**Step 4: Verify Nav2 costmap has both layers**

```bash
ros2 param get /local_costmap/local_costmap plugins
# Expected: ['obstacle_layer', 'nvblox_layer', 'inflation_layer']
```

**Step 5: Test obstacle detection**

1. Place a low object (shoe, book) on the floor in front of the robot
2. The 2D LiDAR should NOT detect it (below scan plane)
3. The OAK-D via nvblox SHOULD mark it in the costmap
4. Check in Vizanti — the object should appear as a costmap obstacle

**Step 6: Test navigation**

```bash
# From Vizanti, publish a waypoint that requires passing near the floor obstacle
# The robot should plan around it
```

**Step 7: Monitor memory**

```bash
# Inside container
free -h
# Expected: ~3-4GB used, ~3-4GB free

# GPU memory
tegrastats | head -5
# Watch for CUDA OOM errors in nvblox output
```

---

## Rollback Plan

If nvblox causes memory issues or instability:

1. In `start_ros2.sh`, comment out the nvblox launch line
2. In `nav2_explore_params.yaml`, remove `nvblox_layer` from plugins lists
3. Restart — the robot runs with LiDAR-only costmap (same as before today)
4. slam_toolbox stays (it's lighter than RTAB-Map regardless)

To fully rollback to RTAB-Map:
```bash
# Stop new container, start old one
docker stop ugv_jetson_nvblox
docker start ugv_jetson_ros_humble
```

The old container and all its data are untouched.

---

## Memory Budget

| Component | Before (RTAB-Map) | After (slam_toolbox + nvblox) |
|-----------|-------------------|-------------------------------|
| SLAM | RTAB-Map: ~800MB-1.5GB | slam_toolbox: ~150MB |
| 3D Depth | (part of RTAB-Map) | nvblox: ~500-800MB |
| Nav2 | ~400MB | ~400MB |
| YOLO + OAK-D | ~500MB | ~500MB |
| OS + Docker | ~800MB | ~800MB |
| **Total** | **~3-3.5GB** | **~2.3-2.7GB** |
| **Headroom** | **~4-4.5GB** | **~4.5-5GB** |

Net effect: **more headroom** than before, with better obstacle detection.
