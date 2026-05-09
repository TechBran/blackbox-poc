# OAK-D BasaltVIO — Single-Device Visual-Inertial Odometry

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the rf2o + EKF + Madgwick multi-sensor odometry stack with BasaltVIO running on the OAK-D Lite, providing hardware-synced position + orientation from a single device at 30Hz.

**Architecture:** BasaltVIO (a DepthAI v3.5 host node) fuses the OAK-D Lite's stereo camera pair (CAM_B + CAM_C) with its BMI270 IMU on-device. It outputs a 6DOF pose (position + quaternion) which we publish as `/odom` and broadcast as the `odom→base_footprint` TF. This completely replaces: rf2o_laser_odometry, robot_localization EKF, and imu_filter_madgwick. The LiDAR feeds only into slam_toolbox for mapping. Nav2 consumes the VIO odom for navigation.

**Tech Stack:** DepthAI v3.5 (`dai.node.BasaltVIO`), Python3, ROS2 Humble, tf2_ros, slam_toolbox, Nav2

**SSH:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23` (container)
**Host:** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no jetson@192.168.1.155 -p 22` (docker host)

---

## Why This Is the Right Architecture

### Current stack (broken):
```
D500 LiDAR → rf2o → position (WEAK — barely tracks forward motion)
OAK-D IMU → Madgwick → EKF → heading (gyro bias drifts without gravity correction)
EKF fuses two unsynchronized sources → jittery, drifting /odom
slam_toolbox tries to correct → lag → Nav2 overcorrects → sloppy navigation
```

### New stack (BasaltVIO):
```
OAK-D stereo cameras + OAK-D IMU → BasaltVIO (hardware-synced, on-device fusion)
→ Single /odom output: position + orientation at 30Hz
→ No EKF needed, no rf2o needed, no Madgwick needed
→ D500 LiDAR → slam_toolbox (mapping only)
→ Nav2 gets clean, consistent odom → precise navigation
```

### What BasaltVIO provides:
- **Translation** from stereo visual feature tracking (what rf2o couldn't do)
- **Rotation** from IMU gyro + gravity correction (what the EKF struggled with)
- **Hardware sync** — camera + IMU on same device, same clock (no timestamp mismatch)
- **30Hz output** — 3× faster than rf2o (10Hz), smoother than EKF (12Hz jittery)

---

## Hardware Reference

- **OAK-D Lite**: CAM_A (RGB), CAM_B (left mono OV7251), CAM_C (right mono OV7251), BMI270 IMU
- **USB**: OAK-D Lite has USB 2.0 — bandwidth limited, plan for 30fps VIO (not 60)
- **DepthAI v3.5.0**: installed, `dai.node.BasaltVIO` confirmed available
- **BasaltVIO status**: "early access preview" — may have bugs, API may change

---

### Task 1: Test BasaltVIO standalone — verify it works on our OAK-D Lite

**Files:**
- Create: `/home/ws/ugv_ws/test_basalt_vio.py` (temporary test script)

This script tests BasaltVIO independent of the depth node. It creates a minimal pipeline with just stereo + IMU + VIO, prints pose output for 10 seconds.

```python
#!/usr/bin/env python3
"""Test BasaltVIO on OAK-D Lite — verify pose output."""
import depthai as dai
import time
import subprocess
import os
import math

# Kill depth node to free OAK-D
subprocess.run(['pkill', '-f', 'ugv_depth_node'], capture_output=True)
time.sleep(3)
os.system("rm -rf /root/.cache/depthai/crashdumps/ 2>/dev/null")

pipeline = dai.Pipeline()

# Camera nodes (stereo pair for VIO)
fps = 30  # Conservative for USB 2.0
width, height = 640, 400

left = pipeline.create(dai.node.Camera).build(
    dai.CameraBoardSocket.CAM_B, sensorFps=fps)
right = pipeline.create(dai.node.Camera).build(
    dai.CameraBoardSocket.CAM_C, sensorFps=fps)

# IMU
imu = pipeline.create(dai.node.IMU)
imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 200)
imu.setBatchReportThreshold(1)
imu.setMaxBatchReports(10)

# BasaltVIO
vio = pipeline.create(dai.node.BasaltVIO)
left.requestOutput((width, height)).link(vio.left)
right.requestOutput((width, height)).link(vio.right)
imu.out.link(vio.imu)

# Output queue
transform_q = vio.transform.createOutputQueue(maxSize=10, blocking=False)

print("Starting BasaltVIO test (30fps, 640x400)...")
try:
    pipeline.start()
    print("Pipeline started! Waiting for VIO convergence (keep robot still)...")
    time.sleep(3)

    print("")
    print("Reading VIO poses for 10 seconds...")
    print("{:<6} {:<10} {:<10} {:<10} {:<10}".format("t(s)", "x(m)", "y(m)", "z(m)", "yaw(deg)"))

    t0 = time.time()
    count = 0
    while time.time() - t0 < 10:
        td = transform_q.tryGet()
        if td is not None:
            # Introspect the TransformData object on first sample
            if count == 0:
                print("")
                print("TransformData attributes: {}".format(
                    [a for a in dir(td) if not a.startswith('_')]))
                print("")

            # Try different attribute access patterns
            try:
                # Pattern 1: direct attributes
                x, y, z = td.x, td.y, td.z
                qx, qy, qz, qw = td.qx, td.qy, td.qz, td.qw
            except AttributeError:
                try:
                    # Pattern 2: getTranslation/getRotation methods
                    trans = td.getTranslation()
                    rot = td.getRotation()
                    x, y, z = trans.x, trans.y, trans.z
                    qx, qy, qz, qw = rot.x, rot.y, rot.z, rot.w
                except AttributeError:
                    try:
                        # Pattern 3: list/tuple access
                        x, y, z = td[0], td[1], td[2]
                        qx, qy, qz, qw = td[3], td[4], td[5], td[6]
                    except (TypeError, IndexError):
                        print("Cannot access TransformData — check dir() output above")
                        break

            yaw = math.degrees(2 * math.atan2(qz, qw))
            elapsed = time.time() - t0

            if count % 10 == 0:  # Print every 10th sample
                print("{:<6.1f} {:<10.4f} {:<10.4f} {:<10.4f} {:<10.1f}".format(
                    elapsed, x, y, z, yaw))

            count += 1

        time.sleep(0.01)

    print("")
    print("Total VIO samples: {} ({:.1f} Hz)".format(count, count / 10.0))
    if count > 0:
        print(">>> BasaltVIO is WORKING on OAK-D Lite! <<<")
    else:
        print(">>> No VIO output received — check configuration <<<")

    pipeline.stop()

except Exception as e:
    print("BasaltVIO test FAILED: {}".format(e))
    import traceback
    traceback.print_exc()
    try:
        pipeline.stop()
    except:
        pass
```

**Step 1: Deploy and run test**
```bash
sshpass -p 'jetson' scp -P 23 /tmp/test_basalt_vio.py root@192.168.1.155:/home/ws/ugv_ws/
# Kill depth node first, then run
docker exec ugv_jetson_ros_humble python3 /home/ws/ugv_ws/test_basalt_vio.py
```

**Verification:**
- VIO samples received at 20-30Hz
- Position (x, y, z) starts near (0, 0, 0) and stays there if robot is still
- TransformData attribute names identified (needed for Task 3)
- No crashes or errors

**If BasaltVIO crashes:** Check depthai version (`python3 -c "import depthai; print(depthai.__version__)"`). May need `pip3 install depthai --upgrade`. Known issues: race condition bug (#1510), may need develop branch.

**Critical:** Record the exact TransformData attribute names from the `dir()` output. We need these for Task 3.

---

### Task 2: Test BasaltVIO while driving — verify it tracks motion

**Files:**
- Modify: `/home/ws/ugv_ws/test_basalt_vio.py` (add driving test)

After Task 1 confirms VIO outputs poses, modify the script to:
1. Read VIO while stationary (5 seconds)
2. Drive forward 0.2 m/s for 3 seconds via serial cmd_vel
3. Read VIO during/after driving
4. Verify position changes by ~0.6m

**Step 1: Run the driving test**
```bash
docker exec ugv_jetson_ros_humble python3 /home/ws/ugv_ws/test_basalt_vio.py
```

**Verification:**
- Stationary: position stays near (0, 0, 0), drift < 5mm over 5 seconds
- After driving forward: position changes by approximately 0.5-0.7m in one axis
- Rotation: if robot turns, yaw changes proportionally

**IMPORTANT — axis convention:** BasaltVIO outputs in camera coordinate frame:
- Camera Z = forward (into scene)
- Camera X = right
- Camera Y = down

This needs remapping to ROS convention (same as we did for the IMU):
- robot_x = camera_z (forward)
- robot_y = -camera_x (left)
- robot_z = -camera_y (up)

Verify which axis changes when driving forward. This determines the remapping.

---

### Task 3: Integrate BasaltVIO into ugv_depth_node.py

**Files:**
- Modify: `/home/ws/ugv_ws/ugv_depth_node.py`

The depth node already creates the stereo cameras and runs a pipeline. We add BasaltVIO to the same pipeline and publish its output as `/odom` + TF.

**Key changes:**

1. Add imports: `tf2_ros`, `nav_msgs.msg.Odometry`, `geometry_msgs.msg.TransformStamped`
2. Add publishers: `/odom` (Odometry), TF broadcaster (odom→base_footprint)
3. Add BasaltVIO node to pipeline, link stereo + IMU
4. In the capture loop, read VIO output and publish
5. Apply axis remapping (camera→robot convention)

**Step 1: Add VIO to pipeline (in `_capture_loop()` before `pipeline.start()`)**

After the existing stereo depth setup, add:
```python
            # BasaltVIO — visual-inertial odometry from stereo + IMU
            vio = pipeline.create(dai.node.BasaltVIO)
            left_vio = mono_l.requestOutput((640, 400))
            right_vio = mono_r.requestOutput((640, 400))
            left_vio.link(vio.left)
            right_vio.link(vio.right)
            imu_node.out.link(vio.imu)  # Share IMU node with IMU publisher
            vio_q = vio.transform.createOutputQueue(maxSize=10, blocking=False)
```

NOTE: `mono_l` and `mono_r` already exist in the depth node for stereo depth. We request a SECOND output at 640x400 for VIO. The camera node handles multiple outputs. If this causes bandwidth issues, reduce the RGB/depth rate.

NOTE: `imu_node` also already exists (we added it for IMU publishing). BasaltVIO shares it.

**Step 2: In the capture loop, read VIO and publish**

After the existing depth/RGB/IMU processing:
```python
                # ── VIO Odometry ──
                vio_data = vio_q.tryGet()
                if vio_data is not None:
                    odom_msg = Odometry()
                    odom_msg.header.stamp = stamp
                    odom_msg.header.frame_id = 'odom'
                    odom_msg.child_frame_id = 'base_footprint'

                    # Get pose from VIO (use attribute names from Task 1)
                    # Apply camera→robot axis remapping:
                    #   robot_x = cam_z, robot_y = -cam_x, robot_z = -cam_y
                    # ADJUST THESE BASED ON TASK 1 FINDINGS:
                    odom_msg.pose.pose.position.x = VIO_Z    # camera forward → robot forward
                    odom_msg.pose.pose.position.y = -VIO_X   # camera right → robot left
                    odom_msg.pose.pose.position.z = 0.0       # ground robot, always 0

                    # Quaternion remapping (same axis swap)
                    # ADJUST BASED ON TASK 2 AXIS VERIFICATION:
                    odom_msg.pose.pose.orientation = REMAPPED_QUATERNION

                    self.odom_pub.publish(odom_msg)

                    # Broadcast TF: odom → base_footprint
                    t = TransformStamped()
                    t.header.stamp = stamp
                    t.header.frame_id = 'odom'
                    t.child_frame_id = 'base_footprint'
                    t.transform.translation = odom_msg.pose.pose.position
                    t.transform.rotation = odom_msg.pose.pose.orientation
                    self.tf_broadcaster.sendTransform(t)
```

**Step 3: Add publishers to `__init__()`**
```python
        # VIO odometry publisher
        self.odom_pub = self.create_publisher(Odometry, '/odom', 50)
        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self)
```

**Step 4: Remove the standalone IMU publisher** (optional)
Since BasaltVIO handles IMU internally, the separate `/oak/imu` publisher is no longer needed for odometry. Keep it for diagnostics if desired.

**Verification:**
```bash
ros2 topic hz /odom          # Expected: 20-30Hz
ros2 topic echo /odom --once  # Expected: position near (0,0,0) if stationary
ros2 run tf2_ros tf2_echo odom base_footprint  # Expected: valid transform
```

---

### Task 4: Update start_ros2.sh — remove EKF/rf2o/Madgwick stack

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

In the `slam_3d` block, REMOVE these lines:
```bash
        # IMU Madgwick filter — NO LONGER NEEDED (BasaltVIO does IMU fusion internally)
        # ros2 launch /home/ws/ugv_ws/imu_filter.launch.py &
        # sleep 2

        # EKF sensor fusion — NO LONGER NEEDED (BasaltVIO provides /odom directly)
        # ros2 launch /home/ws/ugv_ws/ekf_odom.launch.py &
        # sleep 5
```

Also REMOVE the OAK-D IMU static TF (VIO handles its own frame):
```bash
        # OAK-D IMU static TF — NO LONGER NEEDED
        # ros2 run tf2_ros static_transform_publisher ...
```

The rf2o launch is part of `bringup_lidar.launch.py`. It can stay running (doesn't hurt), but its output is no longer consumed by anything. If CPU is a concern, disable it by commenting out the rf2o IncludeLaunchDescription in the launch file.

**The boot sequence becomes:**
```
1. Bringup (LiDAR + TF)
2. Serial node (ESP32 cmd_vel)
3. OAK-D depth node (RGB + depth + VIO → /odom + TF)     ← replaces EKF+rf2o+Madgwick
4. slam_toolbox (mapping from /scan + odom TF)
5. Nav2 (navigation)
6. Everything else (camera server, explore, YOLO, etc.)
```

**Verification:**
```bash
# After boot, verify NO EKF or Madgwick running
ps aux | grep -E 'ekf_node|madgwick|rf2o' | grep -v grep
# Expected: no output (or only rf2o if kept running)

# Verify VIO providing odom
ros2 topic hz /odom
# Expected: 20-30Hz from depth node's VIO

# Verify TF chain
ros2 run tf2_ros tf2_echo map base_footprint
# Expected: valid transform (slam_toolbox: map→odom, VIO: odom→base_footprint)
```

---

### Task 5: Update Nav2 params — remove EKF references

**Files:**
- Modify: `/home/ws/ugv_ws/nav2_explore_params.yaml`

No changes needed to Nav2 params — it consumes `/odom` topic and `odom→base_footprint` TF regardless of source. Just verify `odom_topic: /odom` is set in the BT navigator config (it already is).

**Verification:**
```bash
# Set a close waypoint in Vizanti — should navigate without EKF
# The odom comes directly from BasaltVIO now
```

---

### Task 6: Stationary stability test

**Step 1: Robot stationary for 60 seconds**
```bash
python3 /home/ws/ugv_ws/drift_test.py  # (modified for 60s instead of 10s)
```

**Expected:**
- Position drift: < 10mm over 60 seconds (BasaltVIO has gravity correction)
- Yaw drift: < 0.5° over 60 seconds (stereo features anchor heading)
- Jitter: < 2mm position, < 0.5° yaw

**Compare with current rf2o+IMU stack:**
- Current position jitter: ±3.5mm (acceptable)
- Current yaw jitter: ±1.0° (causes costmap artifacts)
- Current yaw drift: 0.008°/s (6°/min without gravity correction)

BasaltVIO should significantly beat all three metrics.

---

### Task 7: Driving navigation test

**Step 1: Drive forward 1 meter**
- Use Vizanti joystick, drive straight forward
- VIO should track position accurately (within 5cm of actual)
- No overshoot, no sudden jumps

**Step 2: Rotate 360°**
- Spin in place
- Yaw should return to within 5° of start
- Walls should NOT thicken in the map

**Step 3: Navigate to a waypoint**
- Set waypoint 2 meters away in mapped area
- Robot should navigate smoothly without sloppy turns
- No overcorrection oscillations

**Step 4: Navigate around a corner**
- Set waypoint in another room
- Robot should follow the planned path around the corner
- No wall-running, no confusion at the turn

---

### Task 8: Performance tuning (if needed)

**USB bandwidth issues:**
If the depth node drops frames or VIO rate is too low, reduce RGB frame rate:
```python
cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A, sensorFps=10)
```
This gives VIO more USB bandwidth (mono cameras dominate for VIO).

**VIO output too slow:**
Try increasing mono camera FPS:
```python
left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B, sensorFps=60)
```
But watch for USB saturation.

**VIO tracking failures:**
In texture-less environments (white walls, empty rooms), BasaltVIO may lose tracking. If this happens:
- Keep the serial node's `/encoder/odom` as a fallback
- Add an EKF that fuses VIO + encoders (only when VIO is lost)
- This is Approach B from the research — save for later if needed

---

## What Gets Removed

| Component | Status | Reason |
|-----------|--------|--------|
| `robot_localization` EKF | **REMOVED** | BasaltVIO fuses camera + IMU internally |
| `imu_filter_madgwick` | **REMOVED** | BasaltVIO handles IMU fusion |
| `rf2o_laser_odometry` | **OPTIONAL REMOVE** | VIO replaces laser scan matching for odom |
| `ekf_params.yaml` | **UNUSED** | No EKF to configure |
| `ekf_odom.launch.py` | **UNUSED** | No EKF to launch |
| `imu_filter.launch.py` | **UNUSED** | No Madgwick to launch |

## What Stays

| Component | Role |
|-----------|------|
| `ugv_depth_node.py` | RGB + depth + **VIO odometry** (expanded) |
| `ugv_serial_node.py` | ESP32 cmd_vel + encoder data (diagnostic/fallback) |
| `slam_toolbox` | 2D SLAM from LiDAR /scan + VIO odom TF |
| `Nav2` | Navigation with VIO-provided /odom |
| `D500 LiDAR` | Mapping only (no longer used for odometry) |

---

## Rollback

If BasaltVIO doesn't work on OAK-D Lite (USB bandwidth, tracking failures, API bugs):

1. Restore EKF + Madgwick + rf2o in `start_ros2.sh` (uncomment the lines)
2. Remove VIO code from `ugv_depth_node.py` (revert to pre-VIO version)
3. The rf2o + IMU + slam_toolbox stack is preserved and unchanged

All removed components are just commented out, not deleted. Quick revert.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| BasaltVIO not available on OAK-D Lite | Low | High | Task 1 tests this first |
| USB 2.0 bandwidth insufficient | Medium | Medium | Reduce RGB rate, VIO at 30fps not 60 |
| VIO tracking lost in texture-less rooms | Medium | Medium | Encoder fallback, keep serial node |
| TransformData API differs from docs | Medium | Low | Task 1 introspects attributes |
| BasaltVIO "early access" bugs | Medium | Medium | Known issues documented, develop branch available |
| Axis remapping wrong | Low | High | Task 2 verifies empirically |
