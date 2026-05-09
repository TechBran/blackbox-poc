#!/bin/bash
# start_isaac.sh — entrypoint inside ugv_jetson_nvblox:vpi-fixed container.
# Sources Isaac ROS env, then launches nvblox via ros2 run with our params.
#
# v3 corrections (2026-05-08, after live debug):
#   - Add `-r pointcloud:=/scan_filtered_pointcloud` so nvblox actually
#     subscribes to our LiDAR PointCloud2 source. The YAML lidar_topic
#     parameter is silently ignored — nvblox uses fixed topic names that
#     must be remapped at launch time. (Verified via `ros2 node info`:
#     subscribers list is /pointcloud, /camera_0/color/image,
#     /camera_0/color/camera_info, /pose, /transform.)
#   - For OAK-D depth, also remap camera_0/depth/image and
#     camera_0/depth/camera_info once use_depth:=true is set.
#   - Cross-container data delivery requires --ipc=container:ugv_waveshare
#     in the docker-run line (otherwise FastRTPS shared-memory transport
#     looks for segments in the wrong namespace and discovery succeeds
#     while data flow is silent — symptom: subscriber count > 0 but
#     /tf, /tf_static, /scan_filtered_pointcloud have 0 message rate).
#
# v2 corrections (2026-05-07):
#   - Source /opt/ros/humble/setup.bash (NOT install/setup.bash)
#   - Use `ros2 run` not `ros2 launch nvblox.launch.py` (no such launch file)
set -eo pipefail

# ROS setup scripts reference unbound vars — disable nounset while sourcing
set +u
source /opt/ros/humble/setup.bash
[ -f /opt/ros/humble/install/setup.bash ] && source /opt/ros/humble/install/setup.bash
set -u

trap 'jobs -p | xargs -r kill 2>/dev/null; exit 0' TERM INT

# Wait for the converted LiDAR pointcloud to appear (depends on
# ugv_waveshare's laserscan_to_pointcloud_node, which depends on /scan_filtered)
echo "[start_isaac] waiting for /scan_filtered_pointcloud topic..."
for i in $(seq 1 30); do
  if ros2 topic list 2>/dev/null | grep -q "/scan_filtered_pointcloud"; then
    echo "[start_isaac] /scan_filtered_pointcloud detected; launching nvblox_node"
    break
  fi
  sleep 1
done

# Background the costmap relay BEFORE exec'ing nvblox_node. The relay
# subscribes to /nvblox_node/static_map_slice (which doesn't exist yet,
# but rclpy will discover it once nvblox starts publishing). Republishes
# as nav_msgs/OccupancyGrid on /nvblox_local_costmap with TRANSIENT_LOCAL
# QoS so Nav2 static_layer can late-subscribe.
echo "[start_isaac] backgrounding nvblox_costmap_relay.py"
python3 /isaac_params/nvblox_costmap_relay.py &

# Launch nvblox directly via ros2 run (no launch file in image).
# Topic remaps drive what nvblox actually subscribes to (YAML lidar_topic
# is silently ignored — see node_info Subscribers list for fixed names).
#
# Subscribers nvblox creates (verified 2026-05-08 against vpi-fixed image):
#   /pointcloud                    LiDAR PointCloud2
#   /camera_0/depth/image          OAK-D depth Image
#   /camera_0/depth/camera_info    OAK-D depth CameraInfo
#   /camera_0/color/image          OAK-D color (unused; left dangling)
#   /camera_0/color/camera_info    OAK-D color info (unused; left dangling)
#   /pose, /transform              optional pose source (we use TF instead)
exec ros2 run nvblox_ros nvblox_node \
  --ros-args \
  --params-file /isaac_params/nvblox_config.yaml \
  -r __node:=nvblox_node \
  -r pointcloud:=/scan_filtered_pointcloud \
  -r camera_0/depth/image:=/oak/stereo/depth \
  -r camera_0/depth/camera_info:=/oak/stereo/camera_info
