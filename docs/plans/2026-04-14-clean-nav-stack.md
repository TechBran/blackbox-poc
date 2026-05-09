# Clean Navigation Stack — Encoder + EKF + slam_toolbox

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Strip the navigation stack down to the proven architecture: encoder odometry → EKF → slam_toolbox scan correction. Remove BasaltVIO (experimental, drifts, crashes slam_toolbox). Keep all motor, inflation, and recovery fixes from today's session.

**Architecture:** Encoder L/R velocities feed the EKF for vx + wz. The EKF publishes odom→base_footprint TF. slam_toolbox matches LiDAR scans against the map and publishes map→odom TF to correct drift. No VIO, no Madgwick, no rf2o in the EKF. OAK-D provides depth for future obstacle avoidance and IMU for diagnostics only.

**Tech Stack:** ROS2 Humble, robot_localization EKF, slam_toolbox, Nav2 (SmacPlanner2D + MPPI), DepthAI v3.5 (depth only), ESP32 T=11 direct PWM

**SSH:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23` (container)
**Host:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no jetson@192.168.1.155 -p 22` (Jetson)

---

## What This Plan Changes

| File | Action | Why |
|------|--------|-----|
| `ugv_depth_node.py` | **Rewrite** — restore depth+IMU only, remove VIO | VIO drifts, crashes slam_toolbox |
| `ekf_params.yaml` | **Fix** — publish_tf: true, encoder vx+wz | EKF must own the odom→base_footprint TF |
| `start_ros2.sh` | **Clean** — disable Madgwick, YOLO; keep EKF | Remove dead/wasteful nodes |
| `nav2_explore_params.yaml` | **Already fixed** — inflation 0.40/2.5, smoother 1.5 rad/s | Keep today's fixes |
| `nav2_bt.xml` | **Already fixed** — Spin+Backup recovery | Keep today's fixes |
| `ugv_serial_node.py` | **Already fixed** — T=11 PWM, proper covariance | Keep today's fixes |

## What Stays Unchanged

| Component | Why |
|-----------|-----|
| T=11 direct PWM motor control | Both tracks reverse correctly |
| MIN_PWM=65, PWM_SCALE=500 | Overcomes static friction |
| Twist covariance wz=0.05 (moving) | EKF trusts encoder angular velocity |
| inflation_radius: 0.40, cost_scaling: 2.5 | Visible gradient for corridor centering |
| cost_travel_multiplier: 3.0 | Planner prefers corridor centers |
| Spin+Backup recovery in BT | Robot can escape when stuck |
| velocity_smoother: max angular 1.5 | Allows fast rotation from Nav2 |
| Encoder wz formula: (L-R)/TRACK_WIDTH | Correct sign convention |

---

### Task 1: Restore ugv_depth_node.py — depth + IMU only, no VIO

**Files:**
- Rewrite: `/home/ws/ugv_ws/ugv_depth_node.py` (currently 199 lines VIO-only → restore ~300 line depth+IMU version)

**Step 1: Write the restored depth node**

SCP to robot: `sshpass -p 'jetson' scp -P 23 /tmp/ugv_depth_node_clean.py root@192.168.1.155:/home/ws/ugv_ws/ugv_depth_node.py`

The node must publish:
- `/depth/image/compressed` — colorized depth (Vizanti)
- `/depth/rgb/compressed` — RGB color (Vizanti)
- `/oak/rgb/image_rect` — raw RGB 640x480
- `/oak/stereo/image_raw` — raw 16UC1 depth 640x480
- `/oak/rgb/camera_info` — intrinsics from EEPROM
- `/oak/imu` — BMI270 accel + gyro at 100Hz (diagnostic only, EKF doesn't use it)

It must NOT publish:
- `/odom` — EKF owns this
- TF `odom→base_footprint` — EKF owns this

Use the original depth node code from before the VIO migration (the version that was read at the start of this session with IMU publishing). Strip all VIO, tf2_ros, and Odometry imports/code.

**Step 2: Verify syntax**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "python3 -c 'import ast; ast.parse(open(\"/home/ws/ugv_ws/ugv_depth_node.py\").read()); print(\"OK\")'"
```

Expected: `OK`

---

### Task 2: Fix ekf_params.yaml — EKF owns the TF again

**Files:**
- Modify: `/home/ws/ugv_ws/ekf_params.yaml`

**Step 1: Set publish_tf back to true**

```bash
sed -i 's/publish_tf: false/publish_tf: true/' /home/ws/ugv_ws/ekf_params.yaml
```

**Step 2: Verify the full EKF config is correct**

The config should have:
- `publish_tf: true` — EKF is the SOLE odom→base_footprint publisher
- `odom0: /encoder/odom` with vx (index 6) and wz (index 11) enabled
- IMU disabled (commented out)
- `odom0_twist_rejection_threshold: 3.0`
- `initial_estimate_covariance[11]: 0.1` (yaw_rate starts uncertain)

```bash
grep 'publish_tf' /home/ws/ugv_ws/ekf_params.yaml
# Expected: publish_tf: true
```

---

### Task 3: Clean start_ros2.sh — remove dead nodes, disable YOLO

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

**Step 1: Disable Madgwick filter (not needed — EKF only uses encoder)**

Find and comment out the Madgwick section:
```bash
# IMU Madgwick filter — DISABLED (EKF uses encoder wz, not IMU)
# ros2 launch /home/ws/ugv_ws/imu_filter.launch.py &
# sleep 2
```

**Step 2: Ensure EKF is still launched**

The EKF launch line must be UNCOMMENTED and active:
```bash
echo "=== Starting EKF sensor fusion ==="
ros2 launch /home/ws/ugv_ws/ekf_odom.launch.py &
sleep 5
```

**Step 3: Disable YOLO (73% CPU, not needed for navigation)**

Comment out:
```bash
# YOLO DISABLED — saves 73% CPU for navigation
# echo "=== Starting YOLO detection ==="
# python3 /home/ws/ugv_ws/ugv_yolo_node.py &
# sleep 5
```

Also comment out the tracker (depends on YOLO):
```bash
# Tracker DISABLED (depends on YOLO)
# echo "=== Starting object tracker ==="
# python3 /home/ws/ugv_ws/ugv_tracker_node.py &
# sleep 2
```

**Step 4: Verify start_ros2.sh syntax**

```bash
bash -n /home/ws/ugv_ws/start_ros2.sh && echo "SYNTAX OK"
```

---

### Task 4: Reboot and verify the full stack

**Step 1: Reboot the robot**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 -p 22 "echo 'jetson' | sudo -S reboot"
```

Wait 120 seconds for full boot.

**Step 2: Verify all critical nodes**

```bash
ros2 node list | grep -E '(depth|serial|ekf|slam|controller|planner|bt_nav)'
```

Expected nodes:
- `/ugv_depth` — OAK-D depth + IMU (NO VIO)
- `/ugv_serial` — encoder odom + motor control
- `/ekf_filter_node` — sensor fusion (SOLE odom→base_footprint TF publisher)
- `/slam_toolbox` — SLAM mapping (map→odom TF)
- `/controller_server` — MPPI path following
- `/planner_server` — SmacPlanner2D
- `/bt_navigator` — behavior tree

NOT expected:
- No `imu_filter` (Madgwick disabled)
- No VIO-related messages

**Step 3: Verify single /odom publisher**

```bash
ros2 topic info /odom
```

Expected: `Publisher count: 1` (EKF only)

**Step 4: Verify TF chain**

```bash
ros2 run tf2_ros tf2_echo map base_footprint
```

Expected: Valid transform (map→odom from slam_toolbox, odom→base_footprint from EKF)

**Step 5: Verify costmaps are alive**

```bash
ros2 topic hz /local_costmap/costmap --window 5
```

Expected: 5-10 Hz

---

### Task 5: Test stationary stability

**Step 1: Monitor EKF heading + position for 30 seconds (robot still)**

```python
# Run on robot via SSH
import rclpy, time, math
from nav_msgs.msg import Odometry

rclpy.init()
n = rclpy.create_node('stability_test')
poses = []

def cb(msg):
    p = msg.pose.pose.position
    q = msg.pose.pose.orientation
    yaw = math.degrees(2 * math.atan2(q.z, q.w))
    poses.append((time.time(), p.x, p.y, yaw))

n.create_subscription(Odometry, '/odom', cb, 10)
t0 = time.time()
while time.time() - t0 < 30:
    rclpy.spin_once(n, timeout_sec=0.05)

if poses:
    x0, y0, yaw0 = poses[0][1], poses[0][2], poses[0][3]
    x1, y1, yaw1 = poses[-1][1], poses[-1][2], poses[-1][3]
    drift = math.sqrt((x1-x0)**2 + (y1-y0)**2)
    print(f"30s stationary: pos drift={drift*1000:.1f}mm, yaw drift={yaw1-yaw0:+.2f}deg")
    print(f"Rate: {len(poses)/30:.1f} Hz")
    if drift < 0.05 and abs(yaw1-yaw0) < 2:
        print(">>> STABLE — good for navigation")

n.destroy_node()
rclpy.shutdown()
```

Expected:
- Position drift: < 50mm over 30s (encoder drift is slow)
- Yaw drift: < 2° over 30s (slam_toolbox corrects via scan matching)
- Rate: ~5 Hz (encoder data rate)
- NO random jumps (VIO glitches are gone)

---

### Task 6: Test navigation — set a waypoint

**Step 1: In Vizanti, verify the map is clean**
- LiDAR scans should show walls clearly
- Robot avatar should be stable (no jumping)
- Gray inflation gradient visible around walls

**Step 2: Set a close waypoint (1-2m away, same corridor)**
- Robot should plan path through corridor center
- Robot should rotate to face the waypoint
- Robot should drive smoothly to the waypoint
- No "failed to make progress" errors

**Step 3: Set a waypoint around a corner**
- Robot should plan path around the corner
- Robot should follow the path (not cut toward the wall)
- If stuck, Spin recovery should activate (rotate 90° to escape)

**Step 4: If navigation fails, check these:**
1. `ros2 topic echo /cmd_vel --once` — is the controller sending commands?
2. `ros2 topic info /odom` — still 1 publisher?
3. `ros2 run tf2_ros tf2_echo map base_footprint` — TF chain intact?
4. Costmap alive? `ros2 topic hz /local_costmap/costmap`

---

## Architecture Diagram (Final State)

```
┌──────────────────────────────────────────────────────────────────┐
│                        UGV Beast Navigation Stack                 │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ESP32 Serial (/dev/ttyTHS1)                                      │
│  ├─ L/R encoder velocity → ugv_serial_node.py                    │
│  │   ├─ /encoder/odom (vx + wz at ~5Hz)                          │
│  │   └─ cmd_vel_cb: T=11 direct PWM (MIN_PWM=65)                │
│  │                                                                │
│  D500 LiDAR (/dev/ttyACM0)                                       │
│  └─ /scan (10Hz, 360°)                                           │
│                                                                    │
│  OAK-D Lite (USB)                                                 │
│  └─ ugv_depth_node.py                                             │
│      ├─ /oak/imu (diagnostic only, 100Hz)                         │
│      ├─ /depth/image/compressed (Vizanti)                         │
│      └─ /oak/stereo/image_raw (future obstacle avoidance)         │
│                                                                    │
├──────────────────────────────────────────────────────────────────┤
│  LOCALIZATION                                                      │
│                                                                    │
│  /encoder/odom ──→ robot_localization EKF ──→ /odom              │
│                     (vx + wz, publish_tf: true)                   │
│                     └─ TF: odom → base_footprint                  │
│                                                                    │
│  /scan ──→ slam_toolbox (Karto scan matcher)                      │
│             └─ TF: map → odom (corrects encoder drift)            │
│             └─ /map (occupancy grid)                              │
│                                                                    │
├──────────────────────────────────────────────────────────────────┤
│  NAVIGATION                                                        │
│                                                                    │
│  Nav2: SmacPlanner2D → MPPI DiffDrive controller                  │
│  ├─ Inflation: 0.40m radius, 2.5 cost_scaling (gray gradient)    │
│  ├─ Velocity smoother: max angular 1.5 rad/s                     │
│  ├─ Recovery: ClearCostmaps → Spin 90° → Wait → Spin -90° → Back│
│  └─ /cmd_vel → ugv_serial → T=11 PWM → motors                   │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘

TF Chain: map → odom → base_footprint → base_link → base_lidar_link
          (slam)  (EKF)   (static)       (static)     (static)
```

---

## Rollback

If the encoder+EKF approach still doesn't work:

1. The VIO depth node is saved at `/tmp/ugv_depth_node_vio.py` on the BlackBox server
2. The BasaltVIO plan is at `docs/plans/2026-04-12-oakd-basalt-vio.md`
3. All config files are backed up in git history (the robot doesn't have git, but copies exist on BlackBox at `/tmp/`)

---

## Lessons Learned (from this session)

| Lesson | Detail |
|--------|--------|
| BasaltVIO is not production-ready | "Early access preview", known drift, known race condition on Jetson |
| Madgwick publishes wrong frame_id | `frame_id='odom'` instead of sensor frame → EKF silently drops data |
| ESP32 T=13 PID is broken for reverse | Use T=11 direct PWM instead |
| robot_localization ignores high-covariance data | Twist covariance 1000 = EKF gives zero weight |
| slam_toolbox scan matcher is the real hero | Encoder odom just needs to be "good enough" |
| Multiple /odom publishers crash slam_toolbox | MUST have exactly ONE /odom + ONE odom TF publisher |
| VIO at 30Hz + encoder at 5Hz on same topic = chaos | Alternating poses from different sources confuses everything |
