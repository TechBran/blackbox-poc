# Waveshare UGV Beast — Stock Docker Rebuild

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the UGV Beast from scratch inside a new Docker container using Waveshare's official repositories, exactly as they intended. Disable the current custom setup completely. Start from a known-good baseline.

**Architecture:** Waveshare's stock ROS2 stack: ESP32 serial (T=13 PID) → ugv_bringup (IMU/encoder publisher) → base_node (C++ differential kinematics) → rf2o laser odometry → Cartographer SLAM → Nav2 (TEB planner + AMCL localization). OAK-D used only for RTAB-Map 3D SLAM (optional). No custom nodes.

**Tech Stack:** ROS2 Humble, dustynv L4T r36.4.0 base image, Waveshare ugv_ws (ros2-humble-develop branch), LD19 LiDAR, OAK-D Lite, ESP32 ROS Driver Board (ICM-20948 IMU)

**SSH (current):** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no jetson@192.168.1.155 -p 22` (Jetson host)
**SSH (new container):** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23` (once old container is stopped)

---

## Reference: Official Waveshare Repositories

| Repo | Branch | Purpose |
|------|--------|---------|
| [waveshareteam/ugv_ws](https://github.com/waveshareteam/ugv_ws) | `ros2-humble-develop` | ROS2 workspace — nodes, launch files, SLAM, Nav2 |
| [waveshareteam/ugv_jetson](https://github.com/waveshareteam/ugv_jetson) | `main` | Flask demo app (app.py), JupyterLab tutorials |
| [waveshareteam/ugv_base_ros](https://github.com/waveshareteam/ugv_base_ros) | `main` | ESP32 firmware (Arduino IDE project) |

## Reference: Hardware

| Component | Details |
|-----------|---------|
| Jetson Orin Nano 8GB | JetPack R36.4.7, 915GB NVMe, 7.4GB RAM |
| ESP32 ROS Driver Board | ICM-20948 IMU, motor PID, encoder reading, `/dev/ttyTHS1` @ 115200 |
| LD19 LiDAR | `/dev/ttyACM0` @ 230400, 360° scan, frame: `base_lidar_link` |
| OAK-D Lite | USB ACM, stereo depth + RGB + BMI270 IMU |
| Motors | 2x DC gear, WHEEL_D=0.0523m, 1092 pulses/rev, TRACK_WIDTH=0.141m |
| Battery | 3S lithium UPS (3x 18650) |

## Reference: Current Docker State

| Item | Value |
|------|-------|
| Running container | `ugv_jetson_ros_humble` on `dustynv/ros:humble-desktop-l4t-r36.4.0` |
| Systemd service | `ugv-ros2.service` (enabled, auto-starts container + `start_ros2.sh`) |
| Bind mounts | `/dev:/dev`, `/home/jetson/ugv_ws:/home/ws/ugv_ws`, `/tmp/.X11-unix:/tmp/.X11-unix` |
| Config | `--runtime nvidia --privileged --network host` |
| Workspace | 18GB at `/home/jetson/ugv_ws` (heavily customized) |

## What Changes

| Component | Current (Custom) | New (Waveshare Stock) |
|-----------|------------------|----------------------|
| Container name | `ugv_jetson_ros_humble` | `ugv_beast_stock` |
| Docker image | `dustynv/ros:humble-desktop-l4t-r36.4.0` (manual installs) | New image: `ugv_beast_stock:v1` (proper Dockerfile) |
| Workspace | `/home/jetson/ugv_ws` (custom nodes) | `/home/jetson/ugv_ws_stock` (fresh Waveshare clone) |
| Serial driver | Custom `ugv_serial_node.py` (T=11 direct PWM) | Waveshare `ugv_bringup.py` + `ugv_driver.py` (T=13 PID) |
| Odometry | Direct encoder TF (bypasses EKF) | `base_node` C++ (wheel odom) + rf2o laser odom |
| IMU | OAK-D BMI270 (wrong IMU, disabled) | ESP32 ICM-20948 (9-axis, via serial) |
| EKF | Disabled (publish_tf: false) | Optional (bringup_imu_ekf mode) |
| SLAM | slam_toolbox | Cartographer (or GMapping) |
| Nav2 planner | MPPI + SmacPlanner2D | TEB + NavfnPlanner (A*) |
| LiDAR driver | Custom D500 config | Waveshare ldlidar (LD19) |

## What Stays

| Component | Why |
|-----------|-----|
| `/dev/ttyTHS1` serial port | Same ESP32, same UART |
| `/dev/ttyACM0` LiDAR | Same LD19 hardware |
| OAK-D Lite USB | Same camera hardware |
| `--runtime nvidia --privileged --network host` | Required for GPU + device access |
| JetPack R36.4.7 | Host OS unchanged |

---

### Task 1: Disable the Current Setup

**Goal:** Stop the existing container and systemd service so nothing interferes with the new build.

**Step 1: Stop the systemd service**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "echo 'jetson' | sudo -S systemctl stop ugv-ros2.service && \
   echo 'jetson' | sudo -S systemctl disable ugv-ros2.service && \
   echo 'Service stopped and disabled'"
```

Expected: `Service stopped and disabled`

**Step 2: Stop the current container**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "docker stop ugv_jetson_ros_humble && echo 'Container stopped'"
```

Expected: `Container stopped`

**Step 3: Verify nothing is running**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "docker ps --format '{{.Names}}' && echo '---' && \
   sudo systemctl is-active ugv-ros2.service || echo 'Service inactive'"
```

Expected: No containers listed, service inactive.

**Step 4: Kill any lingering app.py (Waveshare Flask demo)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "sudo killall -9 python3 2>/dev/null; echo 'Python processes killed'"
```

> **Note:** The old container and workspace are preserved intact. We're not deleting anything — just stopping it. To revert, re-enable `ugv-ros2.service`.

---

### Task 2: Write the Dockerfile

**Goal:** Create a proper, reproducible Dockerfile that installs everything Waveshare's ROS2 stack needs.

**Step 1: Create the Dockerfile locally**

Write to `/tmp/Dockerfile.ugv_beast_stock`:

```dockerfile
# ============================================================
# UGV Beast Stock — Waveshare Official ROS2 Stack
# Base: NVIDIA L4T r36.4.0 + ROS2 Humble Desktop
# ============================================================
FROM dustynv/ros:humble-desktop-l4t-r36.4.0

# Fix potentially expired ROS2 apt signing key
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg 2>/dev/null || true

# ---- ROS2 packages (from Waveshare README + dependencies) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Cartographer SLAM
    ros-humble-cartographer \
    ros-humble-cartographer-ros \
    # Nav2 full stack
    ros-humble-nav2-bringup \
    ros-humble-nav2-amcl \
    ros-humble-nav2-bt-navigator \
    ros-humble-nav2-controller \
    ros-humble-nav2-core \
    ros-humble-nav2-costmap-2d \
    ros-humble-nav2-lifecycle-manager \
    ros-humble-nav2-map-server \
    ros-humble-nav2-msgs \
    ros-humble-nav2-navfn-planner \
    ros-humble-nav2-planner \
    ros-humble-nav2-recoveries \
    ros-humble-nav2-util \
    ros-humble-nav2-velocity-smoother \
    ros-humble-nav2-waypoint-follower \
    ros-humble-nav2-simple-commander \
    ros-humble-nav2-rviz-plugins \
    ros-humble-nav2-regulated-pure-pursuit-controller \
    ros-humble-nav2-behaviors \
    # Sensor fusion
    ros-humble-robot-localization \
    ros-humble-imu-filter-madgwick \
    ros-humble-imu-complementary-filter \
    # Visualization & bridges
    ros-humble-rosbridge-suite \
    ros-humble-foxglove-bridge \
    ros-humble-joint-state-publisher \
    ros-humble-rqt \
    ros-humble-rqt-common-plugins \
    # Camera & depth
    ros-humble-rtabmap-ros \
    ros-humble-usb-cam \
    ros-humble-cv-bridge \
    ros-humble-depth-image-proc \
    # TF & tools
    ros-humble-tf2-tools \
    ros-humble-behaviortree-cpp-v3 \
    # System utilities
    openssh-server \
    python3-serial \
    python3-pip \
    tmux htop nano git wget curl \
    && rm -rf /var/lib/apt/lists/*

# ---- Python packages ----
RUN pip3 install --no-cache-dir \
    pyserial flask mediapipe requests \
    scipy transforms3d numpy

# ---- depthai SDK (OAK-D Lite) ----
# pip version works on Jetson; apt version may not exist for arm64
RUN pip3 install --no-cache-dir depthai || echo "depthai pip install failed — will retry in container"

# ---- SSH server setup ----
RUN mkdir -p /var/run/sshd && \
    echo 'root:jetson' | chpasswd && \
    sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/#Port 22/Port 22/' /etc/ssh/sshd_config

# ---- Environment ----
ENV UGV_MODEL=ugv_beast
ENV LDLIDAR_MODEL=ld19
ENV ROS_DOMAIN_ID=0
ENV SHELL=/bin/bash

# ---- Workspace directory ----
RUN mkdir -p /home/ws/ugv_ws
WORKDIR /home/ws/ugv_ws

# ---- Entrypoint ----
# Source ROS2 on every bash session
RUN echo "source /opt/ros/humble/setup.bash" >> /root/.bashrc && \
    echo "[ -f /home/ws/ugv_ws/install/setup.bash ] && source /home/ws/ugv_ws/install/setup.bash" >> /root/.bashrc && \
    echo "export UGV_MODEL=ugv_beast" >> /root/.bashrc && \
    echo "export LDLIDAR_MODEL=ld19" >> /root/.bashrc

CMD ["/bin/bash"]
```

**Step 2: SCP to Jetson**

```bash
sshpass -p 'jetson' scp -P 22 /tmp/Dockerfile.ugv_beast_stock \
  jetson@192.168.1.155:/home/jetson/Dockerfile.ugv_beast_stock
```

Expected: File transferred.

---

### Task 3: Build the Docker Image on Jetson

**Goal:** Build the new image on the Jetson. This takes 10-20 minutes.

**Step 1: Build the image**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "cd /home/jetson && docker build -t ugv_beast_stock:v1 -f Dockerfile.ugv_beast_stock . 2>&1 | tail -20"
```

Expected: `Successfully tagged ugv_beast_stock:v1`

**Step 2: Verify the image**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "docker images ugv_beast_stock"
```

Expected: Image listed with ~14-16 GB size.

**Troubleshooting:** If apt packages fail to install (key errors, 404s), the ROS2 apt key fix in the Dockerfile should handle it. If depthai pip install fails on arm64, we'll install it manually inside the container later.

---

### Task 4: Clone the Fresh Waveshare Workspace

**Goal:** Get a pristine copy of Waveshare's ugv_ws on the Jetson host.

**Step 1: Clone the repo to a NEW directory**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "git clone -b ros2-humble-develop https://github.com/waveshareteam/ugv_ws.git \
   /home/jetson/ugv_ws_stock 2>&1 | tail -5"
```

Expected: `Cloning into '/home/jetson/ugv_ws_stock'...`

**Step 2: Verify the workspace structure**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "ls /home/jetson/ugv_ws_stock/src/ugv_main/ && echo '---' && \
   ls /home/jetson/ugv_ws_stock/src/ugv_else/"
```

Expected: Both `ugv_main` (11 packages) and `ugv_else` (11 dependency packages) directories listed.

**Step 3: Unzip the URDF description models**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "cd /home/jetson/ugv_ws_stock && \
   unzip -o ugv_description.zip -d src/ugv_main/ugv_description/ 2>/dev/null; \
   ls src/ugv_main/ugv_description/urdf/ugv_beast.urdf && echo 'URDF OK'"
```

Expected: `URDF OK`

---

### Task 5: Create and Start the New Container

**Goal:** Launch a new Docker container with proper flags, bind-mounting the fresh workspace.

**Step 1: Create and start the container**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 "
docker run -d \
  --name ugv_beast_stock \
  --runtime nvidia \
  --privileged \
  --network host \
  -v /dev:/dev \
  -v /home/jetson/ugv_ws_stock:/home/ws/ugv_ws \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -e DISPLAY=:0 \
  -e UGV_MODEL=ugv_beast \
  -e LDLIDAR_MODEL=ld19 \
  --restart unless-stopped \
  ugv_beast_stock:v1 \
  sleep infinity
"
```

Expected: Container ID printed.

**Step 2: Start SSH inside the container**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "docker exec ugv_beast_stock bash -c 'service ssh start && echo SSH_OK'"
```

Expected: `SSH_OK`

**Step 3: Verify SSH access to the new container (port 22 = container SSH, mapped via host network)**

Since we're using `--network host`, the container's SSH runs on port 22 which conflicts with the host. We need to configure it on port 23:

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 "
docker exec ugv_beast_stock bash -c \"
  sed -i 's/#Port 22/Port 23/' /etc/ssh/sshd_config &&
  sed -i 's/Port 22/Port 23/' /etc/ssh/sshd_config &&
  service ssh restart &&
  echo SSH_PORT_23_OK
\"
"
```

**Step 4: Test SSH into new container**

```bash
sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23 \
  "echo 'Hello from ugv_beast_stock' && cat /etc/os-release | head -3"
```

Expected: `Hello from ugv_beast_stock` + Ubuntu 22.04.

---

### Task 6: Build the ROS2 Workspace

**Goal:** Compile all Waveshare packages inside the new container. This is the big step (~15-30 min).

**Step 1: Install pip requirements**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "cd /home/ws/ugv_ws && pip3 install -r requirements.txt 2>&1 | tail -5"
```

**Step 2: Run the first-time build (Waveshare's build_first.sh)**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  cd /home/ws/ugv_ws &&
  echo '=== Phase 1: Building dependency packages ===' &&
  colcon build --packages-select \
    apriltag apriltag_msgs apriltag_ros \
    cartographer \
    costmap_converter_msgs costmap_converter \
    emcl2 explore_lite \
    openslam_gmapping slam_gmapping \
    ldlidar rf2o_laser_odometry \
    robot_pose_publisher \
    teb_msgs teb_local_planner \
    vizanti vizanti_cpp vizanti_demos vizanti_msgs vizanti_server \
    ugv_base_node ugv_interface \
    2>&1 | tail -20 &&
  echo '=== Phase 2: Building main packages ===' &&
  source install/setup.bash &&
  colcon build --packages-select \
    ugv_bringup ugv_chat_ai ugv_description ugv_gazebo \
    ugv_nav ugv_slam ugv_tools ugv_vision ugv_web_app \
    --symlink-install \
    2>&1 | tail -20 &&
  echo '=== BUILD COMPLETE ==='
"
```

Expected: `BUILD COMPLETE` with no errors. Some warnings are normal.

**Step 3: Verify key packages built**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 pkg list | grep -E '(ugv_|ldlidar|rf2o|cartographer|teb|vizanti|explore)' | sort
"
```

Expected packages:
- `ugv_base_node`, `ugv_bringup`, `ugv_description`, `ugv_nav`, `ugv_slam`, `ugv_tools`, `ugv_vision`
- `ldlidar`, `rf2o_laser_odometry`, `cartographer`, `teb_local_planner`, `vizanti_server`
- `explore_lite`

**Step 4: Source workspace in bashrc**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  echo 'source /opt/ros/humble/setup.bash' >> ~/.bashrc
  echo 'source /home/ws/ugv_ws/install/setup.bash' >> ~/.bashrc
  echo 'export UGV_MODEL=ugv_beast' >> ~/.bashrc
  echo 'export LDLIDAR_MODEL=ld19' >> ~/.bashrc
  echo 'Bashrc updated'
"
```

**Troubleshooting:**
- If `cartographer` fails to build: it needs `libabsl-dev`, `libceres-dev`. Install with `apt-get install -y libabsl-dev libceres-dev`
- If `teb_local_planner` fails: needs `libeigen3-dev`. Install with `apt-get install -y libeigen3-dev`
- If `vizanti` fails: needs `rosbridge_suite` (should be installed from Dockerfile)
- If `apriltag` fails: needs `cmake` and build tools. Install with `apt-get install -y cmake build-essential`

---

### Task 7: Verify ESP32 Communication

**Goal:** Confirm the ESP32 is configured correctly for UGV Beast and responds to commands.

**Step 1: Set the robot type to Beast**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
python3 -c \"
import serial, json, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=2)
time.sleep(1)

# Set robot type: main=3 (Beast), module=2 (Camera PT)
cmd = json.dumps({'T': 900, 'main': 3, 'module': 2})
s.write(cmd.encode() + b'\n')
time.sleep(0.5)
print('Sent T=900 (Beast config)')

# Request feedback to confirm
s.write(json.dumps({'T': 130}).encode() + b'\n')
time.sleep(0.5)

# Read all available responses
while s.in_waiting:
    line = s.readline().decode().strip()
    if line:
        print('ESP32:', line)

s.close()
\"
"
```

Expected: ESP32 acknowledges the config and returns feedback data.

**Step 2: Test T=13 motor command (brief forward pulse)**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
python3 -c \"
import serial, json, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=2)
time.sleep(0.5)

# Brief forward: 0.05 m/s for 0.5 seconds
s.write(json.dumps({'T': 13, 'X': 0.05, 'Z': 0.0}).encode() + b'\n')
print('Sent forward 0.05 m/s')
time.sleep(0.5)

# Stop
s.write(json.dumps({'T': 0}).encode() + b'\n')
print('Sent stop')

# Read encoder feedback
s.write(json.dumps({'T': 130}).encode() + b'\n')
time.sleep(0.5)
while s.in_waiting:
    line = s.readline().decode().strip()
    if line:
        print('Feedback:', line)

s.close()
\"
"
```

Expected: Robot briefly moves forward, then stops. Encoder values show movement.

**Step 3: Test T=13 rotation (brief left turn)**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
python3 -c \"
import serial, json, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=2)
time.sleep(0.5)

# Brief left turn: angular 0.3 rad/s for 0.5 seconds
s.write(json.dumps({'T': 13, 'X': 0.0, 'Z': 0.3}).encode() + b'\n')
print('Sent left turn 0.3 rad/s')
time.sleep(0.5)

# Stop
s.write(json.dumps({'T': 0}).encode() + b'\n')
print('Sent stop')
s.close()
\"
"
```

Expected: Robot rotates left briefly. **If tracks don't reverse (same issue as before), note it — this means the ESP32 PID firmware genuinely has this bug and we may need to flash updated firmware from `ugv_base_ros`.**

> **Critical checkpoint:** If T=13 rotation works (both tracks move in opposite directions), Waveshare's stock stack will work. If it doesn't, we need Task 7b (ESP32 firmware flash).

---

### Task 7b (Conditional): Flash ESP32 Firmware

**Only if T=13 rotation fails in Task 7.**

The ESP32 firmware source is at [waveshareteam/ugv_base_ros](https://github.com/waveshareteam/ugv_base_ros). It requires Arduino IDE to compile and flash via USB.

**Step 1: Check current firmware version**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
python3 -c \"
import serial, json, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=2)
time.sleep(0.5)
# Try getting firmware info
s.write(b'{\"T\":130}\n')
time.sleep(1)
while s.in_waiting:
    print(s.readline().decode().strip())
s.close()
\"
"
```

**Step 2: If firmware is outdated**
- Clone `ugv_base_ros` repo
- Open in Arduino IDE
- Verify `ugv_config.h` has: `mainType=3`, `WHEEL_D=0.0523`, `ONE_CIRCLE_PLUSES=1092`, `TRACK_WIDTH=0.141`
- Flash via USB connection to ESP32

> This requires physical USB access to the ESP32 board or OTA if supported. Document findings and decide whether to proceed.

---

### Task 8: Test Basic Bringup (rf2o mode)

**Goal:** Launch Waveshare's stock bringup and verify all topics and TF are working.

**Step 1: Launch bringup_lidar (the simpler mode)**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  export UGV_MODEL=ugv_beast &&
  export LDLIDAR_MODEL=ld19 &&
  ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false &
  sleep 10 &&
  echo '=== NODES ===' &&
  ros2 node list &&
  echo '=== TOPICS ===' &&
  ros2 topic list
"
```

Expected nodes:
- `/ugv_bringup` — ESP32 serial comms (IMU, encoder, voltage)
- `/ugv_driver` — cmd_vel → T=13 serial
- `/base_node` — wheel odometry + TF
- `/ldlidar_node` — LD19 LiDAR scans
- `/rf2o_laser_odometry` — laser-based odometry
- `/robot_state_publisher` — URDF TF tree

Expected topics:
- `/scan` — LiDAR scans
- `/odom` — odometry from base_node
- `/imu/data_raw` — raw IMU from ESP32
- `/odom/odom_raw` — raw encoder data [left_m, right_m]
- `/cmd_vel` — velocity commands
- `/voltage` — battery voltage

**Step 2: Verify TF tree**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | head -10
"
```

Expected: Valid transform (base_node publishes odom → base_footprint in this mode).

**Step 3: Verify LiDAR scans**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 topic hz /scan --window 5
"
```

Expected: ~7-10 Hz scan rate.

**Step 4: Test keyboard teleop**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 topic pub /cmd_vel geometry_msgs/Twist \
    '{linear: {x: 0.05}, angular: {z: 0.0}}' --once
"
```

Expected: Robot moves forward briefly (single message, then stops due to timeout).

---

### Task 9: Test SLAM Mapping with Cartographer

**Goal:** Build a map using Waveshare's stock Cartographer setup.

**Step 1: Kill bringup from Task 8 and launch Cartographer**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  # Kill previous launch
  pkill -f 'ros2 launch' 2>/dev/null
  sleep 3

  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  export UGV_MODEL=ugv_beast &&
  export LDLIDAR_MODEL=ld19 &&

  # Launch Cartographer (includes bringup_lidar)
  ros2 launch ugv_slam cartographer.launch.py use_rviz:=false &
  sleep 15 &&

  echo '=== NODES ===' &&
  ros2 node list | grep -E '(cartographer|base_node|ldlidar|bringup)'
"
```

Expected: Cartographer node + all bringup nodes running.

**Step 2: Verify /map topic is publishing**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 topic hz /map --window 3
"
```

Expected: Map updates at 0.5-2 Hz.

**Step 3: Verify full TF chain (map → odom → base_footprint)**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 run tf2_ros tf2_echo map base_footprint 2>&1 | head -10
"
```

Expected: Valid transform — Cartographer provides map→odom, base_node provides odom→base_footprint.

**Step 4: Launch Vizanti for web visualization**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 launch vizanti_server vizanti_server.launch.py &
  sleep 5 &&
  echo 'Vizanti running at http://192.168.1.155:5000'
"
```

**Step 5: Drive around and build a map**

Use Vizanti's teleoperation or keyboard_ctrl to drive the robot around the room/corridor. Watch the map build in real-time.

**Step 6: Save the map**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 run nav2_map_server map_saver_cli -f /home/ws/ugv_ws/maps/test_map &&
  echo 'Map saved' &&
  ls -la /home/ws/ugv_ws/maps/test_map*
"
```

Expected: `test_map.pgm` and `test_map.yaml` saved.

---

### Task 10: Test Autonomous Navigation

**Goal:** Load the saved map and navigate to a waypoint autonomously.

**Step 1: Kill SLAM and launch navigation**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  pkill -f 'ros2 launch' 2>/dev/null
  sleep 3

  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  export UGV_MODEL=ugv_beast &&
  export LDLIDAR_MODEL=ld19 &&

  # Launch Nav2 with saved map
  ros2 launch ugv_nav nav.launch.py \
    use_rviz:=false \
    map:=/home/ws/ugv_ws/maps/test_map.yaml \
    use_localization:=amcl \
    use_localplan:=teb &
  sleep 20 &&

  echo '=== NAV2 NODES ===' &&
  ros2 node list | grep -E '(amcl|controller|planner|bt_nav|smoother|lifecycle)'
"
```

Expected nodes:
- `/amcl` — particle filter localization
- `/controller_server` — TEB path following
- `/planner_server` — NavfnPlanner global path
- `/bt_navigator` — behavior tree
- `/lifecycle_manager_navigation`

**Step 2: Verify Nav2 is ready**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 topic echo /amcl_pose --once 2>&1 | head -5
"
```

Expected: Valid pose estimate (may need initial pose set via Vizanti or `/initialpose`).

**Step 3: Set initial pose and send a navigation goal**

Via Vizanti web UI:
1. Open `http://192.168.1.155:5000`
2. Click "2D Pose Estimate" and set the robot's current position on the map
3. Click "2D Nav Goal" and set a target ~1-2m away
4. Watch the robot plan and execute the path

**Step 4: Verify robot navigates successfully**

Watch for:
- Path planned through open space (not through walls)
- Robot follows the planned path
- Robot reaches the goal and stops
- No "failed to make progress" errors

---

### Task 11: Create Systemd Service for Stock Container

**Goal:** Auto-start the new container on boot.

**Step 1: Write the new service file**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 "
cat > /tmp/ugv-beast-stock.service << 'SVCEOF'
[Unit]
Description=UGV Beast Stock ROS2 (Waveshare Official)
After=docker.service
Requires=docker.service

[Service]
Type=forking
ExecStartPre=/usr/bin/docker start ugv_beast_stock
ExecStart=/usr/bin/docker exec -d ugv_beast_stock bash -c 'service ssh start && source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && export UGV_MODEL=ugv_beast && export LDLIDAR_MODEL=ld19 && cd /home/ws/ugv_ws && bash start_ros2.sh'
ExecStop=/usr/bin/docker stop ugv_beast_stock
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SVCEOF

echo 'jetson' | sudo -S cp /tmp/ugv-beast-stock.service /etc/systemd/system/
echo 'jetson' | sudo -S systemctl daemon-reload
echo 'jetson' | sudo -S systemctl enable ugv-beast-stock.service
echo 'Service installed and enabled'
"
```

> **Note:** The `start_ros2.sh` referenced here is Waveshare's stock startup script. We may need to write a minimal one if theirs doesn't exist in the fresh clone, or adapt it for our specific setup (Cartographer vs GMapping, with/without RTAB-Map, etc.)

**Step 2: Create a minimal start_ros2.sh for the stock container**

Write a clean startup script that launches the stock bringup + SLAM + Nav2:

```bash
#!/bin/bash
# UGV Beast Stock — Waveshare Official Startup
# This script runs inside the ugv_beast_stock container

set -e
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
export UGV_MODEL=ugv_beast
export LDLIDAR_MODEL=ld19

echo "=== UGV Beast Stock — Starting ROS2 Stack ==="

# 1. Bringup: serial comms + LiDAR + odometry
echo "=== Starting bringup (lidar mode) ==="
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false &
sleep 10

# 2. SLAM or Localization (choose one)
# For mapping mode:
# echo "=== Starting Cartographer SLAM ==="
# ros2 launch ugv_slam cartographer.launch.py use_rviz:=false &

# For navigation on existing map:
# echo "=== Starting Nav2 ==="
# ros2 launch ugv_nav nav.launch.py use_rviz:=false map:=/home/ws/ugv_ws/maps/test_map.yaml &

# 3. Vizanti web UI
echo "=== Starting Vizanti ==="
ros2 launch vizanti_server vizanti_server.launch.py &
sleep 5

# 4. Foxglove bridge (optional)
ros2 launch foxglove_bridge foxglove_bridge_launch.xml port:=8765 &

echo "=== All nodes started ==="
echo "Vizanti: http://$(hostname -I | awk '{print $1}'):5000"
echo "Foxglove: ws://$(hostname -I | awk '{print $1}'):8765"

# Keep script alive
wait
```

**Step 3: Reboot and verify auto-start**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 \
  "echo 'jetson' | sudo -S reboot"
```

Wait 120 seconds, then verify:

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "
  source /opt/ros/humble/setup.bash &&
  source /home/ws/ugv_ws/install/setup.bash &&
  ros2 node list
"
```

Expected: All bringup nodes running.

---

## Architecture Diagram (Stock Waveshare)

```
┌──────────────────────────────────────────────────────────────────┐
│  UGV Beast Stock — Waveshare Official Architecture                │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ESP32 ROS Driver Board (/dev/ttyTHS1 @ 115200)                  │
│  ├─ ICM-20948 IMU (accel + gyro + mag)                            │
│  ├─ Quadrature encoders (1092 pulses/rev)                         │
│  ├─ Motor PID controller (T=13 cmd_vel interface)                 │
│  └─ JSON serial protocol                                          │
│       │                                                            │
│       ├─ T=1001 feedback → ugv_bringup.py                         │
│       │   ├─ /imu/data_raw (ICM-20948, base_imu_link)            │
│       │   ├─ /imu/mag (magnetometer)                              │
│       │   ├─ /odom/odom_raw ([left_m, right_m])                   │
│       │   └─ /voltage (battery)                                    │
│       │                                                            │
│       └─ T=13 ← ugv_driver.py ← /cmd_vel                         │
│                                                                    │
│  LD19 LiDAR (/dev/ttyACM0 @ 230400)                              │
│  └─ ldlidar_node → /scan (360°, ~8Hz, base_lidar_link)           │
│                                                                    │
│  OAK-D Lite (USB) — for RTAB-Map 3D SLAM only (optional)         │
│  └─ IMU disabled in stock config                                   │
│                                                                    │
├──────────────────────────────────────────────────────────────────┤
│  ODOMETRY (bringup_lidar mode — no EKF)                           │
│                                                                    │
│  /odom/odom_raw ──→ base_node (C++) ──→ /odom                    │
│  /imu/data_raw  ──┘  (differential kinematics)                    │
│                       └─ TF: odom → base_footprint                │
│                                                                    │
│  /scan ──→ rf2o_laser_odometry (supplementary, not fused)         │
│                                                                    │
│  ODOMETRY (bringup_imu_ekf mode — with EKF)                      │
│                                                                    │
│  /odom/odom_raw ──→ base_node_ekf ──→ /odom_raw                  │
│  /imu/data_raw ──→ complementary_filter ──→ /imu/data             │
│  Both ──→ robot_localization EKF ──→ /odom + TF                   │
│                                                                    │
├──────────────────────────────────────────────────────────────────┤
│  SLAM (choose one)                                                 │
│                                                                    │
│  Cartographer: /scan + /odom → map→odom TF + /map                │
│  GMapping:     /scan + /odom → map→odom TF + /map                │
│  RTAB-Map:     /scan + depth + /odom → map→odom TF + /map (3D)   │
│                                                                    │
├──────────────────────────────────────────────────────────────────┤
│  NAVIGATION (Nav2)                                                 │
│                                                                    │
│  Localization: AMCL (particle filter) on saved map                │
│  Global planner: NavfnPlanner (Dijkstra/A*)                       │
│  Local planner: TEB (Time Elastic Band) via RotationShimController│
│  Recovery: ClearCostmaps → Spin → Backup → Wait                  │
│  /cmd_vel → velocity_smoother → ugv_driver → ESP32 (T=13)        │
│                                                                    │
├──────────────────────────────────────────────────────────────────┤
│  VISUALIZATION                                                     │
│                                                                    │
│  Vizanti: http://<IP>:5000 (web-based ROS viz, teleoperation)     │
│  Foxglove: ws://<IP>:8765 (Foxglove Studio bridge)                │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘

TF Chain: map → odom → base_footprint → base_link → base_lidar_link
          (SLAM)  (base_node)  (URDF static)   (URDF, yaw=90°)
                               → base_imu_link (URDF static)
                               → 3d_camera_link (URDF static)
```

---

## Track Width Note

Waveshare's `base_node.cpp` hardcodes the track width as **0.175m**. The ESP32 firmware uses **0.141m**. These serve different purposes:

- **0.141m** (ESP32) = physical track center-to-center, used for motor differential drive
- **0.175m** (base_node) = effective track width for odometry, accounts for track slippage during turns (tracked vehicles slip more than wheeled)

If odometry heading is inaccurate after testing, this is the first tuning knob. Increase for less turn sensitivity, decrease for more.

---

## Rollback

To restore the old custom setup:

```bash
# On Jetson host:
docker stop ugv_beast_stock
sudo systemctl disable ugv-beast-stock.service
sudo systemctl enable ugv-ros2.service
sudo systemctl start ugv-ros2.service
```

The old container `ugv_jetson_ros_humble` and workspace `/home/jetson/ugv_ws` are untouched.

---

## After Stock Works: Iteration Path

Once the stock Waveshare setup is confirmed working:

1. **Test T=13 PID thoroughly** — if rotation genuinely fails, consider ESP32 firmware update
2. **Try bringup_imu_ekf mode** — adds ESP32 IMU to odometry (may improve heading)
3. **Try GMapping** — simpler than Cartographer, might map better in tight corridors
4. **Add slam_toolbox** — if preferred over Cartographer (already installed in apt)
5. **Tune Nav2 params** — inflation radius, speeds, recovery behavior
6. **Re-enable OAK-D depth** — for RTAB-Map 3D SLAM or obstacle avoidance
7. **Add MPPI controller** — if TEB doesn't perform well enough
8. **Integrate with BlackBox** — connect robot telemetry to the flight recorder
