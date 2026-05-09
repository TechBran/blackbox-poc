#!/bin/bash
# UGV Beast ROS2 Full Startup Script
# All modules + startup light confirmation sequence
#
# MAPPING_MODE controls which SLAM mode to use:
#   slam_2d  (default) - GMapping 2D SLAM
#   slam_3d            - RTAB-Map 3D SLAM (uses OAK-D + LiDAR)
#   nav_2d             - Nav2 navigation with saved 2D map

source /opt/ros/humble/setup.bash
source /opt/ros/humble/install/setup.bash
source /home/ws/ugv_ws/install/setup.bash 2>/dev/null
source /home/ws/depthai_ws/install/setup.bash 2>/dev/null
export UGV_MODEL=ugv_beast
export LDLIDAR_MODEL=ld19

# Mapping mode (override via env: MAPPING_MODE=slam_2d to switch back)
MAPPING_MODE=${MAPPING_MODE:-slam_3d}

# Clear stale OAK-D locks
rm -rf /root/.cache/depthai/crashdumps/ 2>/dev/null
rm -rf /home/ws/ugv_ws/.cache/depthai/crashdumps/ 2>/dev/null

# Configure SSH (port 23, root login) — config is ephemeral, must set on each boot
echo "root:jetson" | chpasswd 2>/dev/null
sed -i -E "s/^#?Port.*/Port 23/" /etc/ssh/sshd_config
sed -i -E "s/^#?PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config
sed -i -E "s/^#?PasswordAuthentication.*/PasswordAuthentication yes/" /etc/ssh/sshd_config

# Start SSH
service ssh start
# Ensure Flask is installed (Vizanti web server needs it)
PIP_INDEX_URL=https://pypi.org/simple/ pip3 install -q flask 2>/dev/null

# 1. SLAM / Navigation (mode-dependent)
echo "=== Starting mapping mode: $MAPPING_MODE ==="
case $MAPPING_MODE in
    slam_2d)
        ros2 launch ugv_slam gmapping.launch.py use_rviz:=false &
        ;;
slam_3d)
        # Bringup (LiDAR + motors + TF) — disable base_node odom TF
        ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=false pub_odom_tf:=false &
        sleep 10
        # EKF sensor fusion: encoders (rotation) + rf2o (translation) -> /odom + TF
        ros2 launch /home/ws/ugv_ws/ekf_odom.launch.py &
        sleep 5  # EKF needs time to receive first messages from both sensors
        # RTAB-Map 3D SLAM (proven working with this container)
        ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &
        ;;
    nav_2d)
        ros2 launch ugv_nav nav.launch.py use_localization:=amcl use_rviz:=false &
        ;;
    *)
        echo "Unknown MAPPING_MODE: $MAPPING_MODE (using slam_2d)"
        ros2 launch ugv_slam gmapping.launch.py use_rviz:=false &
        ;;
esac
sleep 12

# 2. Camera (pan-tilt 15fps color)
echo "=== Starting camera ==="
python3 /home/ws/ugv_ws/ugv_camera_node.py &
sleep 3

# 3. Gimbal control
echo "=== Starting gimbal ==="
python3 /home/ws/ugv_ws/ugv_gimbal_node.py &
sleep 2

# 4. Lights control
echo "=== Starting lights ==="
python3 /home/ws/ugv_ws/ugv_lights_node.py &
sleep 2

# 5. System node (OLED, e-stop, servo calibration)
echo "=== Starting system node ==="
python3 /home/ws/ugv_ws/ugv_system_node.py &
sleep 2

# 6. Map manager (save/list maps)
echo "=== Starting map manager ==="
python3 /home/ws/ugv_ws/ugv_mapping_node.py &
sleep 1

# 7. Vizanti web UI
echo "=== Starting Vizanti ==="
ros2 launch ugv_web_app bringup.launch.py host:=192.168.1.155 &
sleep 3

# 8. Foxglove Bridge (3D visualization via browser at ws://192.168.1.155:8765)
echo "=== Starting Foxglove Bridge ==="
ros2 launch foxglove_bridge foxglove_bridge_launch.xml  \
    port:=8765 num_threads:=2 send_buffer_limit:=50000000 &
sleep 2

# 9. OAK-D depth sensor
echo "=== Starting OAK-D depth ==="
python3 /home/ws/ugv_ws/ugv_depth_node.py &
sleep 5

# 9b. OAK-D camera static TF (mount position on robot)
echo "=== Publishing OAK-D static TF ==="
ros2 run tf2_ros static_transform_publisher \
    0.15 0.0 0.12 0.0 0.0 0.0 \
    base_footprint oak_camera_link &
sleep 1

# 9c. nvblox 3D depth reconstruction (OAK-D depth -> TSDF -> Nav2 costmap)
echo "=== Starting nvblox ==="
ros2 launch /home/ws/ugv_ws/nvblox.launch.py &
sleep 5

# 10. YOLO object detection (both cameras, OpenCV DNN + CUDA FP16)
echo "=== Starting YOLO detection ==="
python3 /home/ws/ugv_ws/ugv_yolo_node.py &
sleep 5

# 11. Pan-tilt object tracker
echo "=== Starting object tracker ==="
python3 /home/ws/ugv_ws/ugv_tracker_node.py &
sleep 2

# 12. Camera HTTP server (port 8090 — BlackBox portal connects here)
echo "=== Starting camera server ==="
python3 /home/ws/ugv_ws/ugv_camera_server.py &
sleep 3

# 13. Nav2 navigation stack (must start AFTER SLAM + LiDAR are publishing /map and /scan)
echo "=== Starting Nav2 ==="
ros2 launch /home/ws/ugv_ws/nav2_explore.launch.py &
NAV2_PID=$!
sleep 20

# Verify Nav2 is up by checking for the navigate_to_pose action server
echo "=== Waiting for Nav2 action server ==="
RETRIES=0
while [ $RETRIES -lt 10 ]; do
    if ros2 action list 2>/dev/null | grep -q 'navigate_to_pose'; then
        echo "=== Nav2 ready (navigate_to_pose action available) ==="
        break
    fi
    RETRIES=$((RETRIES + 1))
    sleep 3
done
if [ $RETRIES -ge 10 ]; then
    echo "=== WARNING: Nav2 action server not detected after 50s ==="
fi

# 14. Explore orchestrator (IDLE until /explore/start called)
echo "=== Starting explore orchestrator ==="
python3 /home/ws/ugv_ws/ugv_explore_node.py &
sleep 1

# ── Startup Confirmation: Flash then leave lights ON ──
echo "=== Startup confirmation + lights ON ==="
python3 -c "
import serial, json, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=0.01)
# Flash off briefly to signal boot complete
s.write((json.dumps({'T':132,'IO4':0,'IO5':0}) + '\n').encode())
time.sleep(0.5)
# Base lights bright (OAK-D needs it), head light subtle (pan-tilt excellent in low light)
s.write((json.dumps({'T':132,'IO4':200,'IO5':25}) + '\n').encode())
s.close()
print('Startup confirmation: lights ON for depth camera')
"

echo "=== ALL MODULES LAUNCHED (mode: $MAPPING_MODE) ==="
echo "=== Lights: BASE=ON, HEAD=ON (toggle via /lights/* services) ==="
wait
