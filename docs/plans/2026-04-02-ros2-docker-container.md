# ROS2 Humble Docker Container for UGV Beast Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a ROS2 Humble Docker container on the Jetson Orin Nano 8GB that enables SLAM mapping, autonomous navigation with obstacle avoidance, and full sensor integration (LiDAR + depth camera + IMU) for the Waveshare UGV Beast PT.

**Architecture:** Use `dustynv/ros:humble-desktop-l4t-r36.4.0` as the base image (CUDA-enabled, Jetson-native with GPU acceleration). Create a Docker container named `ugv_jetson_ros_humble` that matches Waveshare's expected naming. Mount the ugv_ws workspace, install Nav2/SLAM/sensor packages, compile with colcon, and configure SSH on port 23. The container uses `--runtime nvidia --privileged --network host` for GPU, sensor, and DDS access.

**Tech Stack:** Docker, ROS2 Humble, Nav2, Cartographer, RTAB-Map, DepthAI (OAK-D Lite), dustynv Jetson containers, colcon, SSH

**Target Machine:** Jetson Orin Nano 8GB @ `192.168.1.155` (user: jetson, pass: jetson)

**SSH Command Pattern:** `sshpass -p 'jetson' ssh jetson@192.168.1.155 '<command>'`

---

## Task 1: Install Docker Engine on Jetson

**Why:** Docker is not currently installed on the Jetson (fresh JetPack 6.2 flash).

**Step 1: Install Docker and nvidia-container-runtime**

```bash
sudo apt-get update
sudo apt-get install -y docker.io nvidia-container-runtime
```

**Step 2: Configure NVIDIA runtime as default**

Create/edit `/etc/docker/daemon.json`:
```json
{
    "runtimes": {
        "nvidia": {
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }
    },
    "default-runtime": "nvidia"
}
```

**Step 3: Enable Docker and add user to docker group**

```bash
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker jetson
```

**Step 4: Verify Docker works**

```bash
# Need to use sudo until re-login picks up docker group
sudo docker run --rm --runtime nvidia hello-world
```

Expected: "Hello from Docker!" message and no NVIDIA runtime errors.

**Step 5: Verify nvidia runtime**

```bash
sudo docker info | grep -i runtime
```

Expected: Should show `nvidia` in the runtimes list.

---

## Task 2: Pull the Jetson-Optimized ROS2 Base Image

**Why:** The dustynv image includes CUDA, cuDNN, TensorRT + ROS2 Humble compiled from source with GPU awareness. The official `ros:humble` image has no CUDA support.

**Step 1: Pull the image**

```bash
sudo docker pull dustynv/ros:humble-desktop-l4t-r36.4.0
```

This is ~6.5 GB. Will take 10-20 minutes depending on network speed.

**Step 2: Verify the image**

```bash
sudo docker images | grep dustynv
```

Expected: `dustynv/ros  humble-desktop-l4t-r36.4.0  <id>  6.5GB`

**Step 3: Quick test -- verify ROS2 + CUDA inside image**

```bash
sudo docker run --rm --runtime nvidia dustynv/ros:humble-desktop-l4t-r36.4.0 \
    bash -c "source /opt/ros/humble/setup.bash && ros2 --help && nvcc --version"
```

Expected: ROS2 help text followed by CUDA compiler version.

---

## Task 3: Create the UGV Beast ROS2 Container

**Why:** We need a persistent container named `ugv_jetson_ros_humble` that matches what `ros2_humble.sh` expects. It needs device access, host networking, and the workspace mounted.

**Step 1: Create the container**

```bash
sudo docker create -it \
    --name ugv_jetson_ros_humble \
    --runtime nvidia \
    --network host \
    --privileged \
    -v /dev:/dev \
    -v /home/jetson/ugv_ws:/home/ws/ugv_ws \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -e DISPLAY=$DISPLAY \
    -e UGV_MODEL=ugv_beast \
    -e LDLIDAR_MODEL=ld19 \
    -e ROS_DOMAIN_ID=0 \
    --restart unless-stopped \
    dustynv/ros:humble-desktop-l4t-r36.4.0 \
    /bin/bash
```

Key flags:
- `--name ugv_jetson_ros_humble` -- matches ros2_humble.sh expected name
- `--privileged -v /dev:/dev` -- access to LiDAR (/dev/ttyACM0), UART (/dev/ttyTHS1), cameras (/dev/video*), OAK-D (/dev/bus/usb)
- `--network host` -- ROS2 DDS multicast discovery works across host/container
- `-v /home/jetson/ugv_ws:/home/ws/ugv_ws` -- workspace shared between host and container at the path the build scripts expect
- `-e UGV_MODEL=ugv_beast` -- selects the correct URDF model
- `-e LDLIDAR_MODEL=ld19` -- selects the D500/LD19 LiDAR driver

**Step 2: Start the container**

```bash
sudo docker start ugv_jetson_ros_humble
```

**Step 3: Verify container is running**

```bash
sudo docker ps | grep ugv_jetson
```

Expected: Container `ugv_jetson_ros_humble` with status "Up".

---

## Task 4: Install ROS2 Navigation & SLAM Packages Inside Container

**Why:** The base image has core ROS2 but not the navigation, SLAM, or sensor-specific packages.

**Step 1: Enter the container**

```bash
sudo docker exec -it ugv_jetson_ros_humble /bin/bash
```

**Step 2: Add ROS2 apt repository (if not present)**

```bash
# Inside the container
apt-get update
apt-get install -y software-properties-common curl
curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=arm64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu jammy main" > /etc/apt/sources.list.d/ros2.list
apt-get update
```

**Step 3: Install navigation and SLAM packages**

```bash
apt-get install -y --no-install-recommends \
    ros-humble-navigation2 \
    ros-humble-nav2-bringup \
    ros-humble-cartographer \
    ros-humble-cartographer-ros \
    ros-humble-slam-toolbox \
    ros-humble-rtabmap \
    ros-humble-rtabmap-ros \
    ros-humble-usb-cam \
    ros-humble-rosbridge-server \
    ros-humble-rosbridge-suite \
    ros-humble-joint-state-publisher \
    ros-humble-joint-state-publisher-gui \
    ros-humble-robot-state-publisher \
    ros-humble-xacro \
    ros-humble-tf2-tools \
    ros-humble-robot-localization \
    ros-humble-rqt \
    ros-humble-rqt-graph \
    ros-humble-rqt-tf-tree \
    python3-colcon-common-extensions \
    python3-rosdep \
    cmake \
    build-essential \
    git
```

Note: Some of these may already be in the dustynv image or may not have arm64 apt binaries. If a package fails, it will be built from source in a later task.

**Step 4: Install Python dependencies**

```bash
pip3 install pyserial flask mediapipe requests
```

**Step 5: Exit container**

```bash
exit
```

**Step 6: Commit the state (save installed packages)**

```bash
sudo docker commit ugv_jetson_ros_humble ugv_jetson_ros_humble:with-ros-pkgs
```

This creates a checkpoint so we don't lose apt install progress if something goes wrong later.

---

## Task 5: Install DepthAI (OAK-D Lite) Support

**Why:** The OAK-D Lite depth camera driver (depthai-ros) is not in the ROS2 package repositories. Must be installed from pip + source.

**Step 1: Enter the container**

```bash
sudo docker exec -it ugv_jetson_ros_humble /bin/bash
```

**Step 2: Install DepthAI Python SDK**

```bash
pip3 install depthai
```

**Step 3: Install USB rules for OAK-D**

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' > /etc/udev/rules.d/80-movidius.rules
```

Note: udev rules are on the host, not container. We'll also need to add this on the host side.

**Step 4: Install depthai-ros from source (if ros-humble-depthai-ros apt fails)**

```bash
cd /home/ws/ugv_ws/src
# Only if not already present in ugv_else
if [ ! -d "depthai-ros" ]; then
    git clone -b humble https://github.com/luxonis/depthai-ros.git
fi
```

**Step 5: Exit container**

```bash
exit
```

---

## Task 6: Configure SSH Server Inside Container

**Why:** Waveshare's workflow uses SSH on port 23 to access the ROS2 container from external machines (MobaXterm, terminal, etc.).

**Step 1: Enter the container**

```bash
sudo docker exec -it ugv_jetson_ros_humble /bin/bash
```

**Step 2: Install and configure SSH**

```bash
apt-get update && apt-get install -y openssh-server
mkdir -p /run/sshd

# Configure SSH on port 23
sed -i 's/#Port 22/Port 23/' /etc/ssh/sshd_config
sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Set root password
echo 'root:jetson' | chpasswd
```

**Step 3: Test SSH starts**

```bash
service ssh start
service ssh status
```

Expected: "sshd is running"

**Step 4: Exit and test from host**

```bash
exit
# From Jetson host:
sudo docker exec ugv_jetson_ros_humble service ssh start
ssh root@localhost -p 23  # password: jetson
```

Expected: Root shell inside the container.

---

## Task 7: Build the ugv_ws Workspace (AprilTag + All Packages)

**Why:** The ROS2 packages in ugv_ws need to be compiled with colcon. This is the most time-consuming step (~30-60 minutes on ARM).

**Step 1: Enter the container**

```bash
sudo docker exec -it ugv_jetson_ros_humble /bin/bash
```

**Step 2: Source ROS2 environment**

```bash
source /opt/ros/humble/setup.bash
```

**Step 3: Build AprilTag native library first**

```bash
cd /home/ws/ugv_ws/src/ugv_else/apriltag_ros/apriltag/
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target install
cd /home/ws/ugv_ws/
```

**Step 4: Build vendored dependency packages (first colcon build)**

```bash
cd /home/ws/ugv_ws
colcon build --packages-select \
    apriltag apriltag_msgs apriltag_ros \
    cartographer \
    costmap_converter_msgs costmap_converter \
    emcl2 explore_lite \
    openslam_gmapping slam_gmapping \
    ldlidar \
    rf2o_laser_odometry \
    robot_pose_publisher \
    teb_msgs teb_local_planner \
    vizanti vizanti_cpp vizanti_demos vizanti_msgs vizanti_server \
    ugv_base_node ugv_interface
```

This is the long build -- the Cartographer and TEB planner take the most time.

**Step 5: Build UGV application packages (second colcon build)**

```bash
colcon build --packages-select \
    ugv_bringup ugv_chat_ai ugv_description ugv_gazebo \
    ugv_nav ugv_slam ugv_tools ugv_vision ugv_web_app \
    --symlink-install
```

**Step 6: Configure bashrc inside container**

```bash
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
echo 'eval "$(register-python-argcomplete3 ros2)"' >> ~/.bashrc
echo 'eval "$(register-python-argcomplete3 colcon)"' >> ~/.bashrc
echo "source /home/ws/ugv_ws/install/setup.bash" >> ~/.bashrc
echo "export UGV_MODEL=ugv_beast" >> ~/.bashrc
echo "export LDLIDAR_MODEL=ld19" >> ~/.bashrc
source ~/.bashrc
```

**Step 7: Verify build succeeded**

```bash
source /home/ws/ugv_ws/install/setup.bash
ros2 pkg list | grep ugv
```

Expected:
```
ugv_base_node
ugv_bringup
ugv_chat_ai
ugv_description
ugv_gazebo
ugv_interface
ugv_nav
ugv_slam
ugv_tools
ugv_vision
ugv_web_app
```

**Step 8: Exit container**

```bash
exit
```

---

## Task 8: Save Final Container State

**Why:** Commit the fully-built container so all compilation and configuration persists across restarts.

**Step 1: Commit the container**

```bash
sudo docker commit ugv_jetson_ros_humble ugv_jetson_ros_humble:built
```

**Step 2: Tag as latest for ros2_humble.sh compatibility**

The `ros2_humble.sh` script uses `docker start ugv_jetson_ros_humble` -- it starts the named container, not an image. Since we created the container with `docker create --name`, it already persists. This step is just a safety backup:

```bash
sudo docker tag ugv_jetson_ros_humble:built ugv_jetson_ros_humble:latest
```

---

## Task 9: Add OAK-D Lite udev Rules on Host

**Why:** The OAK-D Lite (Movidius MyriadX) needs USB permissions on the host for the container to access it.

**Step 1: Create udev rule on Jetson host**

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

**Step 2: Verify OAK-D is accessible**

```bash
lsusb | grep Movidius
```

Expected: `Intel Movidius MyriadX`

---

## Task 10: Test ros2_humble.sh Startup Script

**Why:** This is the moment of truth -- verify the Waveshare startup script works with our custom-built container.

**Step 1: Stop the Flask app (it holds the serial port)**

```bash
kill -9 $(pgrep -f app.py)
```

**Step 2: Run the Waveshare startup script**

```bash
cd /home/jetson/ugv_ws
sudo chmod +x ros2_humble.sh
sudo ./ros2_humble.sh
```

Expected output:
```
Entering the container...
Container started successfully.
Executing docker exec command to open a bash shell in the container...
Opened bash shell in the container.
```

**Step 3: SSH into the container**

```bash
ssh root@localhost -p 23  # password: jetson
```

**Step 4: Verify ROS2 environment**

```bash
source ~/.bashrc
ros2 pkg list | grep ugv | head -5
echo $UGV_MODEL   # Should print: ugv_beast
echo $LDLIDAR_MODEL  # Should print: ld19
```

---

## Task 11: Test LiDAR + Driver Bringup

**Why:** First real sensor test in ROS2. Verify the D500 LiDAR and ESP32 driver communicate through the container.

**Step 1: Inside the container, launch the driver + LiDAR**

```bash
source ~/.bashrc
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false
```

**Step 2: In a second terminal (SSH into container port 23), check topics**

```bash
source ~/.bashrc
ros2 topic list
```

Expected topics include:
```
/scan
/odom_combined
/imu
/cmd_vel
/tf
/tf_static
```

**Step 3: Verify LiDAR data**

```bash
ros2 topic echo /scan --once
```

Expected: LaserScan message with ranges array (non-zero values = seeing obstacles).

**Step 4: Verify motor control**

```bash
ros2 topic echo /odom_combined --once
```

Expected: Odometry message with position and orientation data.

**Step 5: Test keyboard control**

```bash
ros2 run ugv_tools keyboard_ctrl
```

Drive the robot with I/J/K/L keys. Verify wheels respond.

---

## Task 12: Test SLAM Mapping

**Why:** The ultimate goal -- build a map of the environment using LiDAR while driving.

**Step 1: Launch GMapping SLAM**

```bash
ros2 launch ugv_slam gmapping.launch.py use_rviz:=false
```

**Step 2: In second terminal, drive with keyboard**

```bash
ros2 run ugv_tools keyboard_ctrl
```

Drive slowly around the room. The SLAM algorithm builds a map in the background.

**Step 3: Verify map topic exists**

```bash
ros2 topic echo /map --once
```

Expected: OccupancyGrid message with data array.

**Step 4: Save the map**

```bash
cd /home/ws/ugv_ws
chmod +x save_2d_gmapping_map.sh
./save_2d_gmapping_map.sh
```

Map saved to `/home/ws/ugv_ws/src/ugv_main/ugv_nav/maps/`

---

## Task 13: Test Autonomous Navigation

**Why:** Use the saved map to navigate autonomously with obstacle avoidance.

**Step 1: Launch navigation with AMCL localization**

```bash
ros2 launch ugv_nav nav.launch.py use_localization:=amcl use_rviz:=false
```

**Step 2: Launch Vizanti web control (browser-based map + navigation)**

```bash
ros2 launch ugv_web_app bringup.launch.py host:=192.168.1.155
```

Access at `http://192.168.1.155:5100` in browser.

**Step 3: In browser (Vizanti)**

1. Add a "2D Pose Estimate" widget -- click on map to set robot's initial position
2. Add a "Nav2 Goal" widget -- click on map to set navigation target
3. Watch the robot navigate autonomously, avoiding obstacles using LiDAR data

**Step 4: Command-line navigation test**

```bash
# Get current position
ros2 topic echo /robot_pose --once

# Send a navigation goal via action
ros2 action send_goal /behavior ugv_interface/action/Behavior \
    "{command: '[{\"T\": 1, \"type\": \"drive_on_heading\", \"data\": 0.5}]'}"
```

---

## Build Time Estimates

| Task | Estimated Time |
|------|---------------|
| 1. Install Docker | 5 min |
| 2. Pull base image (6.5 GB) | 10-20 min |
| 3. Create container | 2 min |
| 4. Install ROS2 packages (apt) | 10-15 min |
| 5. Install DepthAI | 5 min |
| 6. Configure SSH | 3 min |
| 7. Build ugv_ws (colcon) | 30-60 min |
| 8. Save container | 2 min |
| 9. Host udev rules | 1 min |
| 10. Test ros2_humble.sh | 3 min |
| 11. Test LiDAR + driver | 5 min |
| 12. Test SLAM mapping | 15 min |
| 13. Test navigation | 10 min |
| **Total** | **~1.5-2.5 hours** |

## Rollback Plan

If anything goes wrong during the build:
- The container can be deleted: `sudo docker rm ugv_jetson_ros_humble`
- The image is untouched: just recreate the container from Task 3
- Checkpoints are saved at Task 4 Step 6 and Task 8
- The Flask app is unaffected (separate from Docker)

## Environment Summary

| Item | Value |
|------|-------|
| Base image | `dustynv/ros:humble-desktop-l4t-r36.4.0` |
| Container name | `ugv_jetson_ros_humble` |
| Workspace (host) | `/home/jetson/ugv_ws` |
| Workspace (container) | `/home/ws/ugv_ws` |
| SSH port | 23 (root / jetson) |
| UGV_MODEL | `ugv_beast` |
| LDLIDAR_MODEL | `ld19` |
| Serial port (ESP32) | `/dev/ttyTHS1` @ 115200 |
| LiDAR port | `/dev/ttyACM0` |
| OAK-D Lite | USB (Movidius MyriadX) |
| Vizanti web UI | `http://192.168.1.155:5100` |
