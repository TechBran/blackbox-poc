#!/bin/bash
# start_tools_api.sh (runs inside ugv_waveshare container)
# Launches camera publishers + FastAPI tool schema server
set -eo pipefail

# ROS setup scripts reference unbound vars (e.g. AMENT_TRACE_SETUP_FILES).
# Disable nounset only while sourcing them, then restore.
set +u
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
set -u

export UGV_MODEL=ugv_beast
export UGV_TOOLS_HOST=0.0.0.0
export UGV_TOOLS_PORT=8080
# Project root on PYTHONPATH so `python3 -m ugv_tools_api[.nodes.*]` resolves
export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:${PYTHONPATH:-}

# Propagate SIGTERM/SIGINT to background camera nodes so systemd stop is clean
trap 'jobs -p | xargs -r kill 2>/dev/null; exit 0' TERM INT

# Wait up to 30s for Nav2 to be up
for i in $(seq 1 30); do
  if ros2 node list 2>/dev/null | grep -q bt_navigator; then
    break
  fi
  sleep 1
done

# Small settle delay so OAK-D / pantilt USB enumeration is ready post-boot
sleep 2

# Launch camera publishers in background (use -m form, cleaner than path-based)
python3 -m ugv_tools_api.nodes.pantilt_camera &
python3 -m ugv_tools_api.nodes.oakd_camera &
python3 -m ugv_tools_api.nodes.imu_zupt_node &
python3 -m ugv_tools_api.nodes.waypoints_bridge &

# Static TF for OAK-D IMU. The BMI270 reports with y-axis pointing UP
# (verified empirically: accel.y = +9.8 on a level chassis). One 90-degree
# roll around x aligns IMU-y to base z, which is what robot_localization
# needs to extract Vyaw from angular_velocity.y. Parent is 3d_camera_link
# so the IMU follows any future changes to the camera mount TF.
ros2 run tf2_ros static_transform_publisher \
  --x 0 --y 0 --z 0 \
  --roll 1.5708 --pitch 0 --yaw 0 \
  --frame-id 3d_camera_link --child-frame-id oak_imu_frame &

# Main server (foreground - systemd tracks this PID)
exec python3 -m ugv_tools_api
