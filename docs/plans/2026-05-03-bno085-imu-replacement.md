# BNO085 IMU Replacement — UGV Beast PT

**Date:** 2026-05-03
**Operator:** Brandon
**Status:** In progress — implementing immediately after plan approval
**Target:** Replace OAK-D BMI270 + Madgwick fusion with BNO085 in-chip fusion as the sole orientation source for the UGV. Encoders remain the translation source.

---

## Locked Decisions

| Decision | Value |
|---|---|
| **IMU mount X axis** | Robot forward (matched, no rotation needed) |
| **IMU mount offset** | 1.5" (0.0381 m) to the **right** of base_link → `y = -0.0381 m` in REP-103 |
| **IMU mount Z** | TBD during URDF edit (measure during install) |
| **Fusion report** | **Game Rotation Vector** (`0x08`) — gyro+accel only, no magnetometer |
| **Secondary reports** | Calibrated Gyroscope (`0x02`), Linear Acceleration (`0x04`) — published but **not fused into EKF** |
| **Topic name** | `/imu/bno085/data` during A/B; remap to `/imu/data` after Phase 6 |
| **Driver location** | ROS Docker container, with `/dev/i2c-1` passed in via `--device` |
| **Driver implementation** | Python rclpy node wrapping `adafruit-circuitpython-bno08x` |
| **EKF inputs from BNO085** | Yaw (orientation Z), vyaw (angular velocity Z) only. **Linear accel not fused** (off-center mount makes accel ≠ base_link accel during turns; encoders own translation). |
| **Magnetometer** | Disabled (Game RV does not use it) |
| **Madgwick / complementary filter** | Removed from launch graph (BNO085 fuses on-chip) |
| **OAK-D IMU stream** | Disabled in OAK-D launch params |

## Why BNO085 Wins

Current pipeline: OAK-D BMI270 (raw 6-DoF) → ROS Madgwick filter (host CPU) → robot_localization EKF.

The BMI270 has no fusion processor — the Madgwick layer is mandatory and fragile (the `frame_id='odom'` Madgwick bug from project memory cost a debugging session). The BNO085 contains a CEVA/Hillcrest BNO080 fusion stack on a Cortex-M0+ — the same fusion code that ships in AirPods, Oculus controllers, and Logitech mice — running 1 kHz on dedicated silicon. We get already-stabilized quaternions, gyro-bias-corrected angular velocity, and dynamically calibrated accel, with one ROS layer deleted.

## Mounting Offset Analysis

For a rigid body, **angular velocity is identical at every point**. Gyroscope readings (and yaw integration) are unaffected by the 1.5" offset. Linear acceleration *does* differ off-axis (centripetal `ω²r` + tangential `αr`), but we are explicitly **not fusing linear accel into position** — encoders provide translation. The offset only enters as a static TF transform `base_link → imu_link` for visualization and any future depth-cam-to-IMU extrinsics.

## Reference Documentation

- [SH-2 Reference Manual (CEVA)](https://www.ceva-ip.com/wp-content/uploads/SH-2-Reference-Manual.pdf) — SHTP packet format, sensor reports, command opcodes
- [BNO080/BNO085 Datasheet](https://www.ceva-ip.com/wp-content/uploads/BNO080_085-Datasheet.pdf) — Electrical, timing, axis convention
- [Adafruit BNO085 Learn Guide](https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085) — Breakout pinout, library
- [Adafruit_CircuitPython_BNO08x](https://github.com/adafruit/Adafruit_CircuitPython_BNO08x) — Driver we're wrapping
- ROS REP-103, REP-105, REP-145 — IMU/frame conventions

## Wiring (Confirmed Live 2026-05-03)

| BNO085 pin | Wire | Jetson 40-pin | Signal |
|---|---|---|---|
| VIN | Red | **17** | 3.3V |
| GND | Black | **20** | GND |
| SDA | Blue | **27** | I2C0_SDA → `/dev/i2c-1` |
| SCL | Yellow | **28** | I2C0_SCL → `/dev/i2c-1` |

Verified `i2cdetect -y -r 1` shows `0x4a`. Verified SHTP advertisement decodes cleanly with firmware version string "1.0.0" and channel descriptors "SHTP", "control".

---

## Phase 1 — Driver Standup

**Goal:** `bno085_imu_node` running in the ROS Docker container, publishing `sensor_msgs/Imu` at 100 Hz on `/imu/bno085/data` (parallel to existing `/imu/data` for A/B).

**New files:**
- `ugv_ws/src/ugv_drivers/ugv_bno085/ugv_bno085/imu_node.py`
- `ugv_ws/src/ugv_drivers/ugv_bno085/ugv_bno085/__init__.py`
- `ugv_ws/src/ugv_drivers/ugv_bno085/setup.py`
- `ugv_ws/src/ugv_drivers/ugv_bno085/setup.cfg`
- `ugv_ws/src/ugv_drivers/ugv_bno085/package.xml`
- `ugv_ws/src/ugv_drivers/ugv_bno085/resource/ugv_bno085`
- `ugv_ws/src/ugv_drivers/ugv_bno085/launch/bno085.launch.py`

**Container changes:**
- `pip install adafruit-circuitpython-bno08x adafruit-blinka` (inside container)
- Pass `--device /dev/i2c-1:/dev/i2c-1` to `ugv_ros2` container (edit Docker compose / systemd unit)
- Container user must be in `i2c` group, or run node as root

**Reports enabled:**
- `BNO_REPORT_GAME_ROTATION_VECTOR` (0x08) @ 100 Hz → quaternion (no mag)
- `BNO_REPORT_GYROSCOPE` (0x02) @ 100 Hz → calibrated rad/s
- `BNO_REPORT_LINEAR_ACCELERATION` (0x04) @ 100 Hz → m/s² (gravity removed)

**`sensor_msgs/Imu` covariances (per BNO085 datasheet):**
- Orientation σ ≈ 0.5° → covariance ≈ 7.6e-5 rad²
- Angular velocity σ ≈ 0.014°/s → covariance ≈ 6e-8 (rad/s)²
- Linear accel σ ≈ 0.05 m/s² → covariance ≈ 2.5e-3 (m/s²)²

**`frame_id`:** `imu_link`

**Verification:**
- `ros2 topic hz /imu/bno085/data` → 95–100 Hz steady
- `ros2 topic echo /imu/bno085/data --field orientation` → quaternion changes when chassis tilts
- `rqt_plot /imu/bno085/data/angular_velocity/z` → spike during manual rotation

## Phase 2 — URDF + TF

**Goal:** Static `base_link → imu_link` transform reflecting actual mount.

**File:** `ugv_ws/src/ugv_main/ugv_description/urdf/ugv.urdf.xacro` (or whichever URDF the active stack loads)

**Add:**
```xml
<link name="imu_link"/>
<joint name="imu_joint" type="fixed">
  <parent link="base_link"/>
  <child link="imu_link"/>
  <origin xyz="0.0 -0.0381 [Z_TBD]" rpy="0 0 0"/>
</joint>
```

`y = -0.0381` because 1.5" right = negative Y in REP-103. `Z_TBD` is the height above base_link — measure during install.

**Verification:**
- `ros2 run tf2_ros tf2_echo base_link imu_link` → translation matches reality
- RViz: chassis + IMU axes triad shows IMU at correct spot

## Phase 3 — EKF Reconfiguration

**Goal:** EKF consumes BNO085 yaw + vyaw instead of Madgwick output. Linear accel not fused.

**File:** `ugv_ws/src/ugv_main/ugv_bringup/param/ekf.yaml`

**Replace `imu0_*` block:**
```yaml
imu0: /imu/data
imu0_config: [false, false, false,    # x, y, z position
              false, false, true,     # roll, pitch, yaw — yaw only
              false, false, false,    # vx, vy, vz
              false, false, true,     # vroll, vpitch, vyaw
              false, false, false]    # ax, ay, az — accel NOT fused
imu0_differential: false
imu0_relative: true        # zero yaw at startup so map frame doesn't snap
imu0_remove_gravitational_acceleration: false
imu0_queue_size: 10
imu0_nodelay: true
```

**Lower IMU yaw process noise** (currently `[5,5] = 0.06`) → `0.01` to let EKF trust BNO085 more.

**Launch file changes:** `ugv_ws/src/ugv_main/ugv_bringup/launch/bringup_imu_ekf.launch.py`
- Comment out `imu_filter_madgwick_node`
- Comment out `imu_complementary_filter_node`
- Add include of `bno085.launch.py`

**Topic remap:** Either rename driver output to `/imu/data` directly, or add a remap in launch. Prefer rename for fewer chains.

**Verification:**
- `ros2 topic echo /odometry/filtered --field pose.pose.orientation` while rotating robot 90° by hand → yaw advances ~π/2 rad smoothly, returns to 0 on reverse
- 360° hand-rotation closes within 1°

## Phase 4 — ZUPT Preprocessor Migration

**Goal:** Retarget the ZUPT (Zero-velocity UPdaTe) preprocessor from `/oak/imu` to BNO085. With BNO085's better gyro bias estimation, ZUPT may become redundant — keep it as safety net.

**File:** ZUPT preprocessor node (path to be confirmed during implementation, per snapshot SNAP-20260427-6316)

**Change:** Subscribe topic from `/oak/imu` → `/imu/data` (or `/imu/bno085/data` during A/B)

**Re-tune threshold** if BNO085 noise floor differs from BMI270.

**Verification:** Robot stationary → `/imu/data/angular_velocity` already ≈ 0 from BNO085 alone. ZUPT remains redundant safety.

## Phase 5 — Disable OAK-D IMU Stream

**Goal:** Stop OAK-D from publishing IMU. Saves USB bandwidth, removes ambiguity.

**File:** OAK-D launch / config (depthai_ros_driver bringup)

**Change:** `imu.enable: false` (or equivalent param)

**Verification:**
- `ros2 topic list | grep oak` → no `/oak/imu`
- `ros2 topic hz /oak/rgb/image_rect` → still 15 fps (not regressed)

## Phase 6 — Bench Validation

| Test | Pass criteria |
|---|---|
| Static yaw drift | Stationary 5 min → drift < 0.5°/min |
| 90° rotation accuracy | Manual 90° turn → reads 89–91° |
| 360° loop closure | Manual 360° turn → returns within 1° of start |
| Magnetometer immunity | Wave magnet near IMU → Game RV yaw unchanged (proves we're really on Game RV, not RV) |
| Encoder + IMU agreement | Drive 1 m straight → odometry/filtered shows 1.0 ± 0.05 m, yaw drift < 0.5° |
| Spin-in-place | `cmd_vel` ω=0.5 rad/s × 10 s → robot rotates ~286°; yaw matches ±2° |
| Off-center artifact | Spin-in-place at high rate → confirm no spurious linear-velocity drift in `/odometry/filtered` (proves we correctly excluded linear accel) |

## Phase 7 — Mission Validation + Snapshot

- Run a 200 sqft autonomous exploration (mirror of `SNAP-20260428-6336`)
- Compare loop-closure quality in saved 2D map vs prior runs
- Mint snapshot under `Brandon` operator documenting migration

## Phase 8 — Rollback Plan

Single-commit revert path. Keep reversible:

1. URDF `imu_link` block — additive, harmless if left in
2. `ugv_bno085` package — separate package, can be excluded from `colcon build --packages-select`
3. EKF config — `git revert` brings Madgwick chain back
4. OAK-D `imu.enable` — flip back to `true`

Add launch arg `use_bno085:=true|false` so a single command swaps back to the OAK-D + Madgwick pipeline mid-mission if needed.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Adafruit Python lib too slow for 100 Hz in container | Measure during Phase 1; fallback to UART-RVC mode (would need re-wire to UART pins) or C++ port if Python can't keep up |
| Game RV gyro yaw drifts over long missions | BNO085 gyro bias correction is excellent; ZUPT (Phase 4) provides safety net at standstill |
| URDF `Z_TBD` height wrong → small pitch/roll bias | Measure carefully; URDF can be tuned post-flight without rebuild |
| Container can't access `/dev/i2c-1` after compose change | Verify `--device` syntax for the actual container (Docker vs systemd); fall back to host driver + DDS multicast |
| Adafruit-blinka not happy on Jetson Orin | Library does support Jetson; if board detection fails, manually instantiate `busio.I2C(board.SCL, board.SDA)` or use `Adafruit_PureIO` directly |

---

## Out of Scope (Explicitly)

- BNO085 magnetometer use (Game RV only, no mag)
- Linear accel fusion into position (encoders own translation)
- BNO085 axis remap via on-chip command 0xFC (handled in URDF instead)
- Switching from I2C to SPI or UART-RVC mode (I2C is wired and working)
- DCD calibration save to flash (not needed for Game RV which auto-calibrates gyro at standstill)

---

## Implementation Log

### 2026-05-03 — Phases 1-5 landed (snapshot SNAP-20260504-6432)
- Phase 1: BNO085 driver running at 95 Hz on `/imu/bno085/data`
- Phase 2: URDF imu_joint placed at `xyz="0 -0.0381 0"` (1.5" right of center)
- Phase 3: EKF config updated to fuse yaw + vyaw from BNO085
- Phase 4: ZUPT preprocessor disabled
- Phase 5: OAK-D IMU stream disabled
- Auto-respawn loop in start_waveshare.sh handles driver crashes
- SHTP preflight in driver handles boot-time stuck state
- Total CPU/USB savings from removing OAK-D IMU pipeline: rate jumped 25→30Hz on /odom

### 2026-05-04 — URDF rpy correction (snapshot SNAP-20260504-6433)
**The hidden bug.** `imu_joint rpy="0 0 0"` assumed the chip was level, but BNO085 was physically pitched -20° in its bracket. `robot_localization` applied the chip's tilted quaternion as if already in `base_link` frame, causing yaw to underestimate true rotation by cos(20°)=6%. SLAM Toolbox compensated via `map → odom` jumps (24cm + 5° in 25s drive measured from rosbag), which Nav2 interpreted as "still not facing target" and rotated forever in place.

**Fix:** Single URDF edit. `rpy="0 0 0"` → `rpy="0 -0.349 0"`.

**Verification:** TF tree confirmed shows `base_link → base_imu_link` rotation = -19.996°. User confirmed extended driving session with steady IMU and a perfect SLAM map.

**Hard data that proved the diagnosis:**
- 1728 stationary IMU samples: mean pitch = -19.64° (chip was always tilted)
- During 50° physical rotation: phantom roll appeared (-0.6° → -12.1°), the unmistakable signature of an uncompensated tilted gyro
- 0/2517 IMU msgs had timing skew or large yaw jumps — chip itself is fine
- SLAM `map → odom` corrections during the 25s drive quantified the EKF yaw error

**Lesson:** `two_d_mode: true` in robot_localization does NOT zero out IMU pitch/roll. It skips fusing them into state, but quaternion math still uses them. Always declare sensor mounting orientation honestly in URDF.

### Recurring known issue: boot-time stuck state
Chip occasionally remains locked across power cycles (3.3V capacitors hold charge briefly). Operating workflow: reboot → check Vizanti for IMU rotation on first move; if works, drive; if not, reboot once more; rare cases need physical VIN disconnect. **Permanent fix pending: wire RESET pin to Jetson GPIO12 (pin 32)** — driver already supports `reset_gpio` parameter for this.

### Status
- ✅ Phases 1-5 complete and validated
- ✅ URDF tilt correction deployed and verified with extended driving
- ✅ SLAM map tracking confirmed clean
- 🟡 Boot-time stuck state has a manual workaround (reboot)
- ⏳ Future: RESET pin wiring or UART-RVC mode for permanent lockup elimination
