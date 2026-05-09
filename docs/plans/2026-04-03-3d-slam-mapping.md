# 3D SLAM Mapping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the OAK-D Lite depth node to publish raw Image + CameraInfo topics compatible with RTAB-Map, enabling full 3D SLAM mapping alongside the existing 2D GMapping.

**Architecture:** The upgraded `ugv_depth_node.py` publishes both compressed (for Vizanti viewing) AND raw (for RTAB-Map) topics with proper CameraInfo calibration. A new `ugv_mapping_node.py` manages switching between 2D and 3D SLAM modes, map saving, and startup map loading. RTAB-Map consumes LiDAR `/scan` + OAK-D RGB + OAK-D depth for 3D point cloud generation.

**Tech Stack:** RTAB-Map, depthai 3.5, OpenCV, ROS2 Humble, Nav2

**Target Machine:** Jetson Orin Nano 8GB @ `192.168.1.155` (SSH port 23 into container)

---

### Task 1: Upgrade ugv_depth_node.py for RTAB-Map Compatibility

**Why:** RTAB-Map requires raw `sensor_msgs/Image` + `CameraInfo` topics. Our current node only publishes CompressedImage.

**Files:**
- Modify: `/home/jetson/ugv_ws/ugv_depth_node.py`

**What to add (keep ALL existing compressed topics):**

New publishers:
```python
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge

# Raw image publishers (for RTAB-Map)
self.rgb_raw_pub = self.create_publisher(Image, '/oak/rgb/image_rect', 10)
self.depth_raw_pub = self.create_publisher(Image, '/oak/stereo/image_raw', 10)
self.camera_info_pub = self.create_publisher(CameraInfo, '/oak/rgb/camera_info', 10)
self.bridge = CvBridge()
```

In the capture loop, add raw publishing alongside compressed:
```python
# Publish raw RGB for RTAB-Map
raw_msg = self.bridge.cv2_to_imgmsg(rgb_frame, encoding='bgr8')
raw_msg.header.stamp = stamp
raw_msg.header.frame_id = '3d_camera_link'
self.rgb_raw_pub.publish(raw_msg)

# Publish raw depth for RTAB-Map (16UC1 = millimeters)
depth_msg = self.bridge.cv2_to_imgmsg(depth_frame, encoding='16UC1')
depth_msg.header.stamp = stamp
depth_msg.header.frame_id = '3d_camera_link'
self.depth_raw_pub.publish(depth_msg)

# Publish CameraInfo (calibration data from OAK-D)
info_msg = CameraInfo()
info_msg.header = raw_msg.header
info_msg.width = rgb_frame.shape[1]
info_msg.height = rgb_frame.shape[0]
# Get calibration from device
info_msg.k = calibration_matrix.flatten().tolist()
self.camera_info_pub.publish(info_msg)
```

**Calibration:** Get from `device.readCalibration()` during init.

**Test:** After upgrade, verify these topics exist:
```bash
ros2 topic list | grep oak
# Should show:
# /oak/rgb/image_rect
# /oak/rgb/camera_info
# /oak/stereo/image_raw
# /depth/image/compressed (existing)
# /depth/rgb/compressed (existing)
```

**Test data flow:**
```bash
timeout 5 ros2 topic hz /oak/rgb/image_rect
timeout 5 ros2 topic hz /oak/stereo/image_raw
timeout 5 ros2 topic hz /oak/rgb/camera_info
```

---

### Task 2: Install cv_bridge in Container

**Why:** cv_bridge converts between OpenCV frames and ROS2 Image messages. Required for raw Image publishing.

```bash
# Inside container
apt-get install -y ros-humble-cv-bridge
# OR if already available:
pip3 install cv-bridge
```

**Test:**
```python
from cv_bridge import CvBridge
bridge = CvBridge()
print("cv_bridge OK")
```

---

### Task 3: Create Custom RTAB-Map Launch

**Why:** The Waveshare `rtabmap_rgbd.launch.py` includes its own bringup + depthai_ros_driver (which crashes). We need a version that works with our architecture (our bringup already running + our depth node).

**Files:**
- Create: `/home/jetson/ugv_ws/rtabmap_custom.launch.py`

**The launch should:**
1. NOT include bringup_lidar (already running from start_ros2.sh)
2. NOT include oak_d_lite launch (our depth node already running)
3. Start only the RTAB-Map SLAM node with correct remappings
4. Start robot_pose_publisher

```python
# RTAB-Map node only - no bringup, no depthai driver
rtabmap_node = Node(
    package='rtabmap_slam',
    executable='rtabmap',
    output='screen',
    parameters=[{
        'frame_id': 'base_footprint',
        'subscribe_rgb': True,
        'subscribe_depth': True,
        'subscribe_scan': True,
        'approx_sync': True,
        'Rtabmap/DetectionRate': '3.5',
        'queue_size': 20,
    }],
    remappings=[
        ('rgb/image', '/oak/rgb/image_rect'),
        ('rgb/camera_info', '/oak/rgb/camera_info'),
        ('depth/image', '/oak/stereo/image_raw'),
    ],
    arguments=['-d']  # Delete previous database
)
```

**Test:**
```bash
ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py
# Should start without errors, begin building 3D map
```

---

### Task 4: Create ugv_mapping_node.py (Map Manager)

**Why:** Need a single node to handle map saving, loading, and switching between 2D/3D modes.

**Files:**
- Create: `/home/jetson/ugv_ws/ugv_mapping_node.py`

**Services:**
```
/mapping/save_2d    (Trigger) - Save current GMapping map
/mapping/save_3d    (Trigger) - Save current RTAB-Map database
/mapping/list       (Trigger) - List all saved maps
```

**Map storage:**
```
/home/ws/ugv_ws/maps/
  2d/
    map_YYYYMMDD_HHMMSS.pgm
    map_YYYYMMDD_HHMMSS.yaml
  3d/
    rtabmap_YYYYMMDD_HHMMSS.db
```

**2D map save (uses nav2_map_server):**
```bash
ros2 run nav2_map_server map_saver_cli -f /home/ws/ugv_ws/maps/2d/map_$(date +%Y%m%d_%H%M%S)
```

**3D map save (copy RTAB-Map database):**
```bash
cp ~/.ros/rtabmap.db /home/ws/ugv_ws/maps/3d/rtabmap_$(date +%Y%m%d_%H%M%S).db
```

---

### Task 5: Fix 2D Map Override Issue

**Why:** Brandon reports old maps loading instead of new ones. The nav launch hardcodes `map.yaml` filename.

**Fix:** The save script always overwrites `map.pgm`/`map.yaml` in the maps directory. New maps replace old. But if GMapping isn't updating (robot not moving enough), the old map persists.

**Check:** Verify the GMapping `linearUpdate` and `angularUpdate` params:
```bash
grep -r "linearUpdate\|angularUpdate" /home/ws/ugv_ws/src/ugv_else/gmapping/
```

Default: `linearUpdate=1.0` (must move 1 meter before map updates), `angularUpdate=0.5` (must rotate 0.5 rad). These might be too high for a small room. Reduce to:
- `linearUpdate=0.2` (20cm)
- `angularUpdate=0.2` (~12 degrees)

---

### Task 6: Update start_ros2.sh for Mapping Mode Selection

**Why:** Need to choose between 2D SLAM, 3D SLAM, or navigation (using saved map) on startup.

**Approach:** Default to 2D SLAM (current). Add environment variable for mode:
```bash
# In start_ros2.sh:
MAPPING_MODE=${MAPPING_MODE:-slam_2d}  # Options: slam_2d, slam_3d, nav_2d

case $MAPPING_MODE in
    slam_2d)
        ros2 launch ugv_slam gmapping.launch.py use_rviz:=false &
        ;;
    slam_3d)
        ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py &
        ;;
    nav_2d)
        ros2 launch ugv_nav nav.launch.py use_localization:=amcl use_rviz:=false &
        ;;
esac
```

---

### Task 7: Test Full 3D SLAM Pipeline

**Steps:**
1. Stop current SLAM: `pkill -f slam_gmapping`
2. Ensure depth node running with raw topics
3. Launch RTAB-Map: `ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py`
4. Drive robot around room
5. Verify 3D map building: `ros2 topic echo /rtabmap/mapData --once`
6. Save: `cp ~/.ros/rtabmap.db ~/maps/3d/first_map.db`

---

## Dependency Chain

```
Task 2 (cv_bridge) → Task 1 (depth node upgrade) → Task 3 (RTAB-Map launch) → Task 7 (test)
Task 4 (map manager) - independent
Task 5 (2D fix) - independent
Task 6 (startup modes) - after Tasks 3+5
```

## Time Estimates

| Task | Time |
|------|------|
| 1. Depth node upgrade | 15 min |
| 2. cv_bridge install | 2 min |
| 3. RTAB-Map launch | 10 min |
| 4. Map manager node | 10 min |
| 5. Fix 2D map params | 5 min |
| 6. Startup modes | 5 min |
| 7. Test 3D SLAM | 15 min |
| **Total** | **~1 hour** |
