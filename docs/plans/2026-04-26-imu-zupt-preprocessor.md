# IMU ZUPT Preprocessor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Zero Velocity Update (ZUPT) preprocessor node between the OAK-D IMU publisher and the `robot_localization` EKF on the UGV Beast. When wheel encoders + commanded velocity both report stationary, override the IMU's reported angular velocity with zero (EKF locks yaw, no integration drift) AND continuously update a running-mean gyro bias estimate (EMA) so the bias subtraction tracks thermal drift over a long mission instead of being frozen at boot.

**Architecture:** A new ROS 2 Python node `imu_zupt_node` subscribes to `/oak/imu` (already bias-corrected by `oakd_camera.py`'s boot calibration — 2026-04-26 fix `6cf37a7`), `/odom_wheel` (wheel encoder twist), and `/cmd_vel` (commanded velocity). The node publishes `/oak/imu_zupt` at the same rate as the input. A debounced stationary detector requires ALL of `|cmd_vel.linear.x|`, `|cmd_vel.angular.z|`, `|odom_wheel.twist.linear.x|`, `|odom_wheel.twist.angular.z|` to be < 0.02 (m/s or rad/s) for ≥ 5 consecutive frames before declaring stationary. Once stationary: gyro samples flow into a slow EMA (alpha=0.01) updating the bias estimate live; output is republished with `angular_velocity = (0,0,0)` and tight covariance (1e-6) so the EKF locks. Once moving: output is raw input minus current EMA-bias with input's covariance preserved. The `ekf_filter_node` is re-pointed from `/oak/imu` → `/oak/imu_zupt` via a one-line edit to `ekf_oak.yaml`. `oakd_camera.py`'s existing boot calibration is preserved unchanged — it provides the EMA's seed value and handles the cold-boot bias before ZUPT has enough samples to converge.

**Tech Stack:**
- ROS 2 Humble (rclpy) — Python node
- `sensor_msgs/Imu`, `nav_msgs/Odometry`, `geometry_msgs/Twist` — message types
- `robot_localization` `ekf_filter_node` — downstream consumer (single-line topic remap)
- pytest for unit tests on the pure-Python detector + bias estimator (no ROS dependencies)
- Existing `oakd_camera.py` boot calibration (commit `6cf37a7`) — kept as bias seed

**Out of scope (deferred):**
- Accelerometer bias (we're 2D mode; EKF only consumes gyro yaw, accels not used)
- 3D yaw correction (gyro x/y biases captured but only z is gated; full 3D ZUPT would also pin x/y)
- Online thermal model (just EMA; could later add temperature-indexed bias table)
- Replacing `robot_localization` with `fusioncore` UKF (much bigger architectural swap)
- Diagnostics dashboard for bias estimate over time (could be added later)

---

## Empirical Knowledge Carried Forward

These regression guards from prior work MUST NOT be reintroduced:

| # | Lesson | Touched by |
|---|---|---|
| 1 | `oakd_camera.py` already does 2-second boot bias calibration (commit `6cf37a7`). DO NOT remove or duplicate this — ZUPT node is downstream of it | Task 6 (subscribe to already-corrected `/oak/imu`) |
| 2 | One change at a time per commit (memory `feedback_split_changes.md`). Land the new node FIRST (verify in isolation), then re-point EKF SECOND (verify integrated SLAM) | Tasks 6, 8 split |
| 3 | `/oak/imu` publishes BEST_EFFORT QoS. Subscribers MUST match or get nothing | Task 6 (subscriber QoS profile) |
| 4 | EKF reads `imu0_config index 11` (vyaw only). Static TF `3d_camera_link → oak_imu_frame` rotates IMU's y-axis into base z-axis. ZUPT node operates BEFORE this static TF — works on `/oak/imu` raw 3-axis values | Task 5 (zero all 3 axes when stationary, not just z) |
| 5 | Container has no `pyaml`-via-rosdep on first boot — only stock Python deps + bootstrap pip installs. ZUPT node uses ONLY stdlib + rclpy + standard `sensor_msgs/nav_msgs` (already present) | Task 6 (no new deps) |
| 6 | Plans targeting `slam_nav.yaml` must edit BOTH src AND install copies. `colcon build` is NOT routinely run in this workspace, so install is the canonical loaded path | Task 8 (sync src + install) |
| 7 | `ekf_oak.yaml` is at `/home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/ekf_oak.yaml` (loaded directly by slam_nav.launch.py at line 119) AND mirrored in src — both must match | Task 8 |
| 8 | `docker restart ugv_waveshare` is the safe restart (no USB cascade trigger). Service `systemctl restart` only restarts a single docker exec, not the container itself | Task 9 |

---

## Pre-flight: Verify current state (READ-ONLY)

**Step P-1: Confirm boot bias calibration is working**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'journalctl -u ugv-tools-api.service --since "5 minutes ago" --no-pager | grep -aE "OAK-D IMU"'
```

Expected: lines like `OAK-D IMU: calibrating gyro bias` and `OAK-D IMU gyro bias calibrated: x=... y=... z=...`. If not present, the boot calibration didn't run; investigate before proceeding.

**Step P-2: Confirm EKF yaw rate is near-zero at rest**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && python3 -c \"
import rclpy, time
from nav_msgs.msg import Odometry
rclpy.init(); n = rclpy.create_node(\\\"o\\\")
samples = []
n.create_subscription(Odometry, \\\"/odom\\\", lambda m: samples.append(m), 10)
deadline = time.time() + 5
while time.time() < deadline: rclpy.spin_once(n, timeout_sec=0.1)
yaws = [s.twist.twist.angular.z for s in samples]
avg = sum(yaws)/len(yaws) if yaws else 0
print(f\\\"avg_yaw_rate={avg:+.5f} rad/s ({avg*57.3:+.3f} deg/s) over {len(samples)} samples\\\")
n.destroy_node(); rclpy.shutdown()
\""'
```

Expected: `<0.05 deg/s` magnitude. If much higher, the boot calibration didn't take effect; investigate.

**Step P-3: Confirm `/cmd_vel` and `/odom_wheel` topics are publishing**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && \
     ros2 topic info /cmd_vel; \
     ros2 topic info /odom_wheel"'
```

Both must show `Publisher count: 1`. If not, the pipeline upstream is broken — fix before continuing (ZUPT depends on these signals).

---

## Target Repository Layout (after this plan)

```
docs/ugv-beast/setup/ugv_tools_api/
├── ugv_tools_api/
│   └── nodes/
│       ├── oakd_camera.py             # UNCHANGED (already has boot bias-cal)
│       └── imu_zupt_node.py           # NEW — ZUPT preprocessor
├── tests/
│   └── nodes/
│       ├── test_stationary_detector.py  # NEW — pure-logic tests
│       └── test_bias_ema.py             # NEW — pure-logic tests
└── deploy/
    └── (no changes to .env / .service files)

# Workspace files (Jetson-side, src + install):
ugv_main/ugv_tools_api/.../launch/<wherever_oakd_runs>.launch.py  # MODIFY: add imu_zupt_node entry
ugv_main/ugv_nav/param/ekf_oak.yaml                                # MODIFY: imu0: /oak/imu → /oak/imu_zupt
```

`docs/plans/notes/2026-04-26-imu-zupt-bench.md` — Task 10 bench record (after operator validation).

---

## Implementation Tasks

### Task 1: Pure-logic stationary detector — failing tests first (TDD)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/nodes/test_stationary_detector.py`

**Step 1: Write the failing tests**

```python
"""Tests for StationaryDetector — debounced motion detector for ZUPT.

Pure-logic class with no ROS dependencies. Caller feeds it (lin, ang) tuples
representing combined cmd_vel + odom_wheel signals; detector returns whether
the robot is currently considered stationary (debounced) or moving.
"""
import pytest
from ugv_tools_api.nodes.imu_zupt_node import StationaryDetector


def test_detector_starts_in_moving_state():
    """Initial state is 'moving' until proven stationary (fail-safe — IMU
    contributes its full input until the detector has enough evidence)."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    assert d.is_stationary() is False


def test_single_zero_frame_does_not_trigger_stationary():
    """Stationary requires DEBOUNCE consecutive frames below threshold."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is False


def test_debounced_zero_frames_trigger_stationary():
    """After exactly debounce_frames at zero, stationary becomes True."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is True


def test_motion_immediately_clears_stationary():
    """Any frame above threshold flips back to moving with no debounce."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is True
    d.update(linear=0.5, angular=0.0)
    assert d.is_stationary() is False


def test_threshold_includes_signs():
    """Threshold is on absolute value — negative motion also triggers moving."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.0, angular=0.0)
    d.update(linear=-0.5, angular=0.0)
    assert d.is_stationary() is False


def test_below_threshold_counts_as_zero():
    """Sub-threshold values (sensor noise) shouldn't break the debounce."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.001, angular=0.005)  # below threshold
    assert d.is_stationary() is True


def test_intermittent_motion_resets_debounce():
    """A single motion frame resets the counter; debounce restarts."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(4):
        d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is False  # 4 frames not enough yet
    d.update(linear=0.5, angular=0.0)
    d.update(linear=0.0, angular=0.0)  # only 1 zero frame again
    assert d.is_stationary() is False
```

**Step 2: Run, expect FAIL (module doesn't exist yet)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && \
     python3 -m pytest tests/nodes/test_stationary_detector.py -v 2>&1 | tail -10"'
```

Expected: `ModuleNotFoundError: No module named 'ugv_tools_api.nodes.imu_zupt_node'`.

**Step 3: Commit (red state)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   git add ugv_tools_api/tests/nodes/test_stationary_detector.py && \
   git -c "user.email=robot@blackbox" -c "user.name=robot" \
       commit -m "test(zupt): failing tests for StationaryDetector"'
```

---

### Task 2: Implement StationaryDetector — make tests pass

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py` (new file with detector class only — node code added in later tasks)

**Step 1: Write minimal implementation**

```python
"""IMU ZUPT preprocessor node.

Implements Zero Velocity Update for the OAK-D IMU stream. Subscribes to
/oak/imu (already bias-corrected by oakd_camera.py boot calibration),
/odom_wheel (EKF wheel input pre-fusion), and /cmd_vel (commanded velocity).
Detects when robot is stationary; when so, republishes IMU with
angular_velocity zeroed and tight covariance, AND updates a slow EMA of
gyro bias to track thermal drift. Otherwise passes through with current
EMA-bias subtracted.

EKF subscribes to /oak/imu_zupt instead of /oak/imu after Task 8.
"""
from __future__ import annotations
from collections import deque


class StationaryDetector:
    """Debounced stationary-vs-moving detector.

    Caller feeds (linear, angular) tuples (from cmd_vel + odom_wheel
    combined). After `debounce_frames` consecutive frames below `threshold`,
    is_stationary() returns True. Any single frame above threshold flips
    back to moving immediately (no debounce on the leading edge — protects
    against incorporating motion into the bias estimate).
    """

    def __init__(self, *, threshold: float = 0.02, debounce_frames: int = 5) -> None:
        self._threshold = threshold
        self._debounce_frames = debounce_frames
        self._zero_count = 0  # consecutive below-threshold frames
        self._stationary = False

    def update(self, *, linear: float, angular: float) -> None:
        """Feed one observation. Linear/angular can be cmd_vel.linear.x + 
        odom.twist.linear.x (already-fused) or independent — caller's choice."""
        if abs(linear) > self._threshold or abs(angular) > self._threshold:
            self._zero_count = 0
            self._stationary = False
        else:
            self._zero_count += 1
            if self._zero_count >= self._debounce_frames:
                self._stationary = True

    def is_stationary(self) -> bool:
        return self._stationary
```

**Step 2: Run tests, expect PASS**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && \
     python3 -m pytest tests/nodes/test_stationary_detector.py -v 2>&1 | tail -10"'
```

Expected: 7 PASS.

**Step 3: Commit**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   git add ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py && \
   git -c "user.email=robot@blackbox" -c "user.name=robot" \
       commit -m "feat(zupt): StationaryDetector — debounced motion gate"'
```

---

### Task 3: Pure-logic bias EMA estimator — failing tests first (TDD)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/nodes/test_bias_ema.py`

**Step 1: Write the failing tests**

```python
"""Tests for BiasEma — exponential-moving-average gyro bias estimator.

Updates only when stationary. Tracks 3-axis bias slowly. Caller passes
the raw (already-bias-corrected by boot cal) gyro tuple; estimator's
current() returns the residual bias to be subtracted from outgoing samples.
"""
import pytest
from ugv_tools_api.nodes.imu_zupt_node import BiasEma


def test_initial_bias_is_zero():
    """Initial EMA starts at (0,0,0) — boot cal already absorbed the gross bias."""
    ema = BiasEma(alpha=0.01)
    assert ema.current() == (0.0, 0.0, 0.0)


def test_seed_initializes_state():
    """Constructor accepts a seed value (e.g., from boot cal output)."""
    ema = BiasEma(alpha=0.01, seed=(0.001, 0.002, -0.003))
    assert ema.current() == pytest.approx((0.001, 0.002, -0.003))


def test_update_moves_toward_target():
    """One update with alpha=0.5 moves halfway from current to target."""
    ema = BiasEma(alpha=0.5)
    ema.update((0.1, 0.2, -0.3))
    assert ema.current() == pytest.approx((0.05, 0.1, -0.15))


def test_repeated_updates_converge():
    """100 updates with alpha=0.1 to constant target converges to ~target."""
    ema = BiasEma(alpha=0.1)
    target = (0.05, -0.02, 0.01)
    for _ in range(200):
        ema.update(target)
    cur = ema.current()
    for c, t in zip(cur, target):
        assert abs(c - t) < 1e-4, f"got {cur}, want {target}"


def test_alpha_zero_freezes_state():
    """alpha=0.0 means no update ever applied (test edge case)."""
    ema = BiasEma(alpha=0.0, seed=(1.0, 2.0, 3.0))
    ema.update((10.0, 10.0, 10.0))
    assert ema.current() == pytest.approx((1.0, 2.0, 3.0))


def test_alpha_one_replaces_state():
    """alpha=1.0 means each update fully replaces the state."""
    ema = BiasEma(alpha=1.0)
    ema.update((0.5, 0.5, 0.5))
    assert ema.current() == pytest.approx((0.5, 0.5, 0.5))
    ema.update((0.1, 0.1, 0.1))
    assert ema.current() == pytest.approx((0.1, 0.1, 0.1))
```

**Step 2: Run tests, expect FAIL (BiasEma doesn't exist yet)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && \
     python3 -m pytest tests/nodes/test_bias_ema.py -v 2>&1 | tail -10"'
```

Expected: `ImportError: cannot import name 'BiasEma'`.

**Step 3: Commit (red state)**

```bash
git add ugv_tools_api/tests/nodes/test_bias_ema.py
git commit -m "test(zupt): failing tests for BiasEma estimator"
```

---

### Task 4: Implement BiasEma — make tests pass

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py` (append BiasEma class)

**Step 1: Add the class to the existing file**

```python
class BiasEma:
    """Exponential-moving-average bias estimator.

    Updates only when caller decides — not coupled to stationary detector
    (caller composes them). new = alpha * sample + (1 - alpha) * current.
    Lower alpha = slower tracking = more noise immunity. alpha=0.01 at
    100 Hz means time constant ~1 second; over a 30-minute mission, the
    estimate adapts smoothly to thermal drift without absorbing transient
    motion noise.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.01,
        seed: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {alpha}")
        self._alpha = alpha
        self._x, self._y, self._z = seed

    def update(self, sample: tuple[float, float, float]) -> None:
        """Pull one (gx, gy, gz) toward sample by alpha."""
        sx, sy, sz = sample
        a = self._alpha
        self._x = a * sx + (1.0 - a) * self._x
        self._y = a * sy + (1.0 - a) * self._y
        self._z = a * sz + (1.0 - a) * self._z

    def current(self) -> tuple[float, float, float]:
        return (self._x, self._y, self._z)
```

**Step 2: Run tests, expect PASS**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && \
     python3 -m pytest tests/nodes/ -v 2>&1 | tail -15"'
```

Expected: 7 + 6 = 13 PASS (both detector and EMA tests).

**Step 3: Commit**

```bash
git add ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py
git commit -m "feat(zupt): BiasEma — exponential-moving-average bias tracker"
```

---

### Task 5: Combined preprocessor logic test (failing) — IMU output behavior

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/nodes/test_imu_preprocessor.py`

**Step 1: Write the failing tests**

```python
"""Tests for ImuPreprocessor — composes detector + EMA + IMU pass-through.

Pure-logic class. Takes raw gyro tuple + motion signals; returns the
gyro that should be published downstream and a flag indicating whether
to publish with 'locked' (tight) covariance.
"""
import pytest
from ugv_tools_api.nodes.imu_zupt_node import ImuPreprocessor


def test_moving_state_subtracts_current_bias():
    """When moving, output = input - current_bias. Bias is unchanged."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=2,
                        alpha=0.01, seed_bias=(0.001, 0.002, 0.003))
    # Force moving state
    p.observe_motion(linear=1.0, angular=0.0)
    out, locked = p.process_gyro((0.5, 0.6, 0.7))
    assert out == pytest.approx((0.5 - 0.001, 0.6 - 0.002, 0.7 - 0.003))
    assert locked is False


def test_stationary_state_outputs_zero_and_locks():
    """When stationary, output = (0,0,0), locked=True."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=2, alpha=0.01)
    p.observe_motion(linear=0.0, angular=0.0)
    p.observe_motion(linear=0.0, angular=0.0)  # debounce satisfied
    out, locked = p.process_gyro((0.05, -0.03, 0.01))
    assert out == (0.0, 0.0, 0.0)
    assert locked is True


def test_stationary_state_updates_bias_ema():
    """When stationary, the bias EMA tracks the input gyro stream."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=1, alpha=1.0,
                        seed_bias=(0.0, 0.0, 0.0))
    p.observe_motion(linear=0.0, angular=0.0)
    p.process_gyro((0.1, 0.2, 0.3))  # alpha=1 means full replace
    p.observe_motion(linear=0.0, angular=0.0)
    p.process_gyro((0.5, 0.5, 0.5))
    # After two stationary updates with alpha=1.0, bias = last sample
    assert p.current_bias() == pytest.approx((0.5, 0.5, 0.5))


def test_moving_state_does_not_update_bias():
    """When moving, bias stays put — important so motion isn't absorbed."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=1, alpha=1.0,
                        seed_bias=(0.001, 0.002, 0.003))
    p.observe_motion(linear=1.0, angular=0.0)  # moving
    p.process_gyro((1.0, 1.0, 1.0))
    assert p.current_bias() == pytest.approx((0.001, 0.002, 0.003))
```

**Step 2: Run tests, expect FAIL**

Expected: `ImportError: cannot import name 'ImuPreprocessor'`.

**Step 3: Commit (red state)**

```bash
git add ugv_tools_api/tests/nodes/test_imu_preprocessor.py
git commit -m "test(zupt): failing tests for ImuPreprocessor (composed)"
```

---

### Task 6: Implement ImuPreprocessor — make tests pass

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py` (append class)

**Step 1: Add the composed class**

```python
class ImuPreprocessor:
    """Composes StationaryDetector + BiasEma + IMU pass-through logic.

    Pure logic, no ROS. Caller drives it via observe_motion() (combined
    cmd_vel + odom_wheel signal) and process_gyro() (raw gyro tuple).
    Returns (gyro_output, locked) — gyro_output is what the node should
    publish; locked is True iff stationary (caller sets tight covariance).
    """

    def __init__(
        self,
        *,
        threshold: float = 0.02,
        debounce_frames: int = 5,
        alpha: float = 0.01,
        seed_bias: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        self._detector = StationaryDetector(
            threshold=threshold, debounce_frames=debounce_frames,
        )
        self._bias = BiasEma(alpha=alpha, seed=seed_bias)

    def observe_motion(self, *, linear: float, angular: float) -> None:
        """Caller passes combined motion signal."""
        self._detector.update(linear=linear, angular=angular)

    def process_gyro(
        self, sample: tuple[float, float, float]
    ) -> tuple[tuple[float, float, float], bool]:
        """Returns ((gx, gy, gz), locked). locked=True means downstream
        consumer should use tight covariance. When stationary, also updates
        bias EMA from this sample."""
        if self._detector.is_stationary():
            self._bias.update(sample)
            return ((0.0, 0.0, 0.0), True)
        bx, by, bz = self._bias.current()
        sx, sy, sz = sample
        return ((sx - bx, sy - by, sz - bz), False)

    def current_bias(self) -> tuple[float, float, float]:
        return self._bias.current()
```

**Step 2: Run all tests, expect PASS**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && \
     python3 -m pytest tests/nodes/ -v 2>&1 | tail -20"'
```

Expected: 7 + 6 + 4 = 17 PASS.

**Step 3: Commit**

```bash
git add ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py
git commit -m "feat(zupt): ImuPreprocessor composing detector + EMA + gating"
```

---

### Task 7: Wire up the ROS node — subscriptions, publisher, run loop

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py` (append node + main)

**Step 1: Add the ROS-aware Node class + main() entrypoint**

```python
import math
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist


# Tight covariance to advertise when locked (stationary). 1e-6 rad²/s² is
# below sensor floor; EKF will trust the (0,0,0) yaw rate fully and not
# integrate any bias residual. When moving, the input's covariance is
# preserved verbatim.
_LOCKED_COV_DIAG = 1e-6


class ImuZuptNode(Node):
    def __init__(self) -> None:
        super().__init__("imu_zupt_node")
        # Parameters with defaults tuned for UGV Beast (see plan notes).
        self.declare_parameter("threshold", 0.02)
        self.declare_parameter("debounce_frames", 5)
        self.declare_parameter("alpha", 0.01)
        self.declare_parameter("imu_in_topic", "/oak/imu")
        self.declare_parameter("imu_out_topic", "/oak/imu_zupt")
        self.declare_parameter("odom_topic", "/odom_wheel")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        threshold = self.get_parameter("threshold").value
        debounce = self.get_parameter("debounce_frames").value
        alpha = self.get_parameter("alpha").value
        in_topic = self.get_parameter("imu_in_topic").value
        out_topic = self.get_parameter("imu_out_topic").value
        odom_topic = self.get_parameter("odom_topic").value
        cmd_vel_topic = self.get_parameter("cmd_vel_topic").value

        self._pre = ImuPreprocessor(
            threshold=threshold, debounce_frames=debounce, alpha=alpha,
        )
        # Cache last-seen motion signals; aggregate into one observe_motion
        # per IMU sample so detector cadence matches IMU rate.
        self._last_cmd_vel = (0.0, 0.0)   # (linear, angular)
        self._last_wheel = (0.0, 0.0)

        # /oak/imu uses BEST_EFFORT QoS — must match.
        sensor_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        # /odom_wheel and /cmd_vel are RELIABLE/VOLATILE (default).
        default_qos = 10

        self._pub = self.create_publisher(Imu, out_topic, sensor_qos)
        self.create_subscription(Imu, in_topic, self._on_imu, sensor_qos)
        self.create_subscription(
            Odometry, odom_topic, self._on_odom, default_qos,
        )
        self.create_subscription(
            Twist, cmd_vel_topic, self._on_cmd_vel, default_qos,
        )

        # Diagnostic ticker — log bias estimate every 10 seconds.
        self._diag_timer = self.create_timer(10.0, self._log_diag)
        self._stationary_since = None

        self.get_logger().info(
            f"imu_zupt_node started: in={in_topic} out={out_topic} "
            f"threshold={threshold} debounce={debounce} alpha={alpha}"
        )

    def _on_cmd_vel(self, msg: Twist) -> None:
        self._last_cmd_vel = (msg.linear.x, msg.angular.z)

    def _on_odom(self, msg: Odometry) -> None:
        self._last_wheel = (msg.twist.twist.linear.x, msg.twist.twist.angular.z)

    def _on_imu(self, msg: Imu) -> None:
        # Aggregate motion: max-magnitude across cmd_vel + wheel for each axis.
        linear = max(abs(self._last_cmd_vel[0]), abs(self._last_wheel[0]))
        angular = max(abs(self._last_cmd_vel[1]), abs(self._last_wheel[1]))
        self._pre.observe_motion(linear=linear, angular=angular)

        sample = (
            msg.angular_velocity.x,
            msg.angular_velocity.y,
            msg.angular_velocity.z,
        )
        (gx, gy, gz), locked = self._pre.process_gyro(sample)

        out = Imu()
        out.header = msg.header
        out.orientation = msg.orientation
        out.orientation_covariance = msg.orientation_covariance
        out.angular_velocity.x = gx
        out.angular_velocity.y = gy
        out.angular_velocity.z = gz
        if locked:
            out.angular_velocity_covariance = [
                _LOCKED_COV_DIAG, 0.0, 0.0,
                0.0, _LOCKED_COV_DIAG, 0.0,
                0.0, 0.0, _LOCKED_COV_DIAG,
            ]
        else:
            out.angular_velocity_covariance = msg.angular_velocity_covariance
        out.linear_acceleration = msg.linear_acceleration
        out.linear_acceleration_covariance = msg.linear_acceleration_covariance
        self._pub.publish(out)

    def _log_diag(self) -> None:
        bx, by, bz = self._pre.current_bias()
        stat = self._pre._detector.is_stationary()
        self.get_logger().info(
            f"[zupt] state={'STATIONARY' if stat else 'MOVING'} "
            f"bias=({bx:+.5f}, {by:+.5f}, {bz:+.5f}) rad/s "
            f"yaw_axis={by * 57.3:+.3f} deg/s"
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ImuZuptNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
```

**Step 2: Sync to Jetson + smoke-test the node directly (NOT via launch yet)**

```bash
sshpass -p 'jetson' rsync -e 'ssh -o StrictHostKeyChecking=no' -av \
  /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py \
  jetson@192.168.1.155:/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py
```

Run the node manually (foreground, kill with Ctrl-C after seeing logs):

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && \
     timeout 15 python3 -m ugv_tools_api.nodes.imu_zupt_node 2>&1 | tail -20"'
```

Expected: log line `imu_zupt_node started: ...`, then within 10 seconds a `[zupt] state=STATIONARY bias=(...)` log line. Both `/oak/imu_zupt` topic should exist while the node is running.

**Step 3: Verify topic + sample**

In a second SSH while the node is still running (or re-run with longer timeout):

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && \
     ros2 topic info /oak/imu_zupt && \
     timeout 3 ros2 topic hz /oak/imu_zupt --use-wall-time 2>&1 | tail -3"'
```

Expected: Publisher count: 1, ~95-100 Hz publish rate.

**Step 4: Commit**

```bash
git add ugv_tools_api/ugv_tools_api/nodes/imu_zupt_node.py
git commit -m "feat(zupt): ImuZuptNode — ROS 2 wrapper subscribing to imu+odom+cmd_vel"
```

---

### Task 8: Add launch entry + re-point EKF (CAREFUL: two changes — split commits)

**Files:**
- Modify: `<wherever ugv_oakd_camera is launched>` — find via `grep -rn "ugv_tools_api.nodes.oakd_camera" /home/ws/ugv_ws/src/`. Add a new Node entry for `imu_zupt_node` alongside it.
- Modify: `ugv_main/ugv_nav/param/ekf_oak.yaml` (BOTH src and install): change `imu0: /oak/imu` → `imu0: /oak/imu_zupt`

**Step 1: Add launch entry FIRST (without re-pointing EKF — node is harmless if EKF doesn't subscribe)**

Find the launch file:

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare grep -rn "ugv_tools_api.nodes.oakd_camera" /home/ws/ugv_ws/src/ 2>/dev/null | head -3'
```

Edit the discovered launch file. Add (Python launch description):

```python
imu_zupt_node_action = Node(
    package='ugv_tools_api',
    executable='imu_zupt_node',  # NOTE: requires entry_point in setup.py
    name='imu_zupt_node',
    output='screen',
)
```

If `ugv_tools_api` package's `setup.py` doesn't list `imu_zupt_node` as a console_script entry_point, the executable won't be found. Either:
(a) Use `Node(package='ugv_tools_api', executable='python3', arguments=['-m', 'ugv_tools_api.nodes.imu_zupt_node'])` — works without setup.py change
(b) Update `setup.py` `entry_points={'console_scripts': [...]}` to register `imu_zupt_node = ugv_tools_api.nodes.imu_zupt_node:main`

Pick (a) for this plan to keep change scope tight.

Sync, restart container or just the relevant systemd unit, verify the node spins up alongside oakd_camera.

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S systemctl restart ugv-tools-api.service && sleep 10 && \
   docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && \
     ros2 node list | grep -aE \"imu_zupt|oakd\""'
```

Expected: both `/imu_zupt_node` and `/ugv_oakd_camera` listed.

**Step 2: Commit launch entry only**

```bash
git add <launch file>
git commit -m "feat(zupt): launch imu_zupt_node alongside oakd_camera"
```

**Step 3: Re-point EKF imu0 topic**

Edit `ekf_oak.yaml` (BOTH src and install):

```yaml
# Before:
imu0: /oak/imu

# After:
imu0: /oak/imu_zupt
```

Sync src + install, restart `ugv-tools-api` (or just restart the slam_nav launch — actually a `docker restart ugv_waveshare` is cleanest since it touches multiple services).

**Step 4: Verify EKF is now receiving from /oak/imu_zupt**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && \
     ros2 topic info /oak/imu_zupt --verbose | grep -aE \"Subscription count|ekf_filter_node\""'
```

Expected: Subscription count ≥ 1, `ekf_filter_node` listed as subscriber.

**Step 5: Commit EKF re-point**

```bash
git add <ekf_oak.yaml in src>
git commit -m "feat(zupt): point ekf_filter_node imu0 at /oak/imu_zupt"
```

---

### Task 9: Live verification — bias estimate + EKF yaw at rest

**Files:**
- (no file changes — pure verification)

**Step 1: Sample EKF /odom yaw rate at rest (5s)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "source /opt/ros/humble/setup.bash && python3 -c \"
import rclpy, time
from nav_msgs.msg import Odometry
rclpy.init(); n = rclpy.create_node(\\\"o\\\")
samples = []
n.create_subscription(Odometry, \\\"/odom\\\", lambda m: samples.append(m), 10)
deadline = time.time() + 5
while time.time() < deadline: rclpy.spin_once(n, timeout_sec=0.1)
yaws = [s.twist.twist.angular.z for s in samples]
avg = sum(yaws)/len(yaws) if yaws else 0
print(f\\\"avg_yaw_rate={avg:+.5f} rad/s ({avg*57.3:+.3f} deg/s) over {len(samples)} samples\\\")
n.destroy_node(); rclpy.shutdown()
\""'
```

Expected: `< 0.005 deg/s` magnitude (an order of magnitude better than the 0.025 deg/s we got from boot calibration alone).

**Step 2: Watch the diagnostic log for bias EMA tracking**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'journalctl -u ugv-tools-api.service --since "30 seconds ago" --no-pager | grep -aE "\\[zupt\\]" | tail -5'
```

Expected: log lines showing `state=STATIONARY` after a few seconds idle, with the bias slowly converging to a stable triple. Drive briefly, then stop — should see `state=MOVING` then back to `STATIONARY`.

---

### Task 10: Operator-led drive bench (Brandon in the loop)

**Files:**
- Create: `docs/plans/notes/2026-04-26-imu-zupt-bench.md`

**Verification matrix:**

| # | Criterion | How to verify |
|---|---|---|
| Z1 | EKF yaw rate stays at ~0 when robot is parked | journalctl `\[zupt\] state=STATIONARY`, /odom yaw rate < 0.005 deg/s sustained |
| Z2 | EKF yaw rate matches actual rotation when robot moves | /odom yaw rate matches commanded angular.z within 5% during turn-in-place |
| Z3 | Bias estimate stable after warm-up | `[zupt]` log lines show bias triple changing < 0.0001 rad/s between successive 10s samples |
| Z4 | SLAM map drift across 5 minutes of operation | drive a closed loop, return to start; /map's reported pose at start = pose at end within 10cm + 5° |
| Z5 | Global costmap stays clean | no phantom "red bubbles" appearing in vizanti during multi-minute idle periods |
| Z6 | No regressions in test suite | `pytest tests/` count = pre-plan + 17 new = (whatever) PASS |
| Z7 | Container CPU usage acceptable | `top` during operation: imu_zupt_node < 5% CPU |

**Bench protocol (Brandon-led):**
1. Power-cycle Jetson + container restart for clean state
2. Wait 30 seconds idle, observe Z1 + Z3 in journal + /odom
3. Drive forward 1m, stop, idle 10s — observe Z2 transitions
4. Rotate in place 360° in 10 seconds, stop, idle 10s — observe Z2 yaw rate matches
5. Drive a closed loop ~5m × 5m square, return to start — observe Z4
6. Park 5 minutes, watch global costmap — observe Z5
7. Run `pytest tests/` end-of-bench — observe Z6
8. Snapshot `top` output during driving — observe Z7

Save results to `docs/plans/notes/2026-04-26-imu-zupt-bench.md`.

**Commit + close:**

```bash
git add docs/plans/notes/2026-04-26-imu-zupt-bench.md
git commit -m "docs(zupt): operator bench record"
```

---

### Task 11: Memory + snapshot

**Step 1: Update `~/.claude/projects/.../memory/ugv_supervisor_2_5_async.md`**

Append a new section:

```markdown
## IMU ZUPT preprocessor (2026-04-26 — thermal-stable yaw estimate)

**Plan:** `docs/plans/2026-04-26-imu-zupt-preprocessor.md`. **Bench:** `docs/plans/notes/2026-04-26-imu-zupt-bench.md`.

**Problem solved:** boot-time gyro bias calibration in `oakd_camera.py` (commit `6cf37a7`) absorbed the ~4 deg/s zero-rate offset at startup but couldn't track thermal drift over a long mission. After 30 minutes of operation, residual bias accumulates and SLAM starts to drift again.

**Architecture:** New ROS 2 node `imu_zupt_node` (`ugv_tools_api/nodes/imu_zupt_node.py`) subscribes to `/oak/imu` (already bias-corrected) + `/odom_wheel` + `/cmd_vel`, gates the IMU stream based on a debounced stationary detector. When stationary: zeroes angular_velocity, tightens covariance to 1e-6 (EKF locks yaw), AND updates a slow EMA (alpha=0.01) of residual bias. When moving: passes through with current EMA-bias subtracted. Output topic `/oak/imu_zupt` consumed by `ekf_filter_node` (replacing direct `/oak/imu` subscription).

**Why it works:** combines the boot calibration (handles initial gross bias) with continuous online refinement (handles thermal drift). The "lock yaw at rest" behavior also prevents ANY residual error from accumulating during idle periods, so a parked robot has near-perfectly-zero integrated yaw drift.

**Architectural pattern:** This is the canonical mobile-robotics ZUPT (Zero Velocity Update) approach. Industry references — `manankharwar/fusioncore` (full UKF replacement, has native ZUPT), `Fixit-Davide/imu_zupt` (standalone preprocessor for foot-mounted IMU, similar logic). The mainstream stacks (Hiwonder, Clearpath, TurtleBot) DO NOT implement ZUPT — they rely on `dpkoch/imu_calib` boot-cal only, same pattern as our pre-2026-04-26-evening fix. Our ZUPT implementation puts us ahead of stock.

**Tunable parameters (declared on ImuZuptNode):**
- `threshold` (0.02) — m/s and rad/s magnitude below which a signal counts as zero
- `debounce_frames` (5) — ~50ms at 100Hz IMU rate; protects against fast stop transients
- `alpha` (0.01) — EMA smoothing factor; lower = slower bias tracking, more noise immunity
```

**Step 2: Mint snapshot via `/snapshot-dev`** capturing the implementation + bench results.

---

## Post-Implementation Verification

Before considering this plan done:

- [ ] Pre-flight P-1, P-2, P-3 confirmed before any code change
- [ ] All Tasks 1-11 committed on Jetson `ros2-humble-develop`
- [ ] 17 new pytest tests PASS (7 detector + 6 EMA + 4 preprocessor)
- [ ] Bench Z1-Z7 results documented and HEALTHY
- [ ] Memory updated; snapshot minted

## Rollback Path

If ZUPT causes EKF instability:

```bash
# Revert EKF subscription topic only (keeps imu_zupt_node alive but unused):
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   sed -i "s|imu0: /oak/imu_zupt|imu0: /oak/imu|" \
     src/ugv_main/ugv_nav/param/ekf_oak.yaml \
     /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/ekf_oak.yaml && \
   echo jetson | sudo -S docker restart ugv_waveshare'
```

5-minute revert. The imu_zupt_node keeps running but with no subscribers, harmless.

To fully remove: also delete the launch entry and `pkill -f imu_zupt_node`.

## Out-of-Plan Follow-Ups

After this lands:

1. **Diagnostic dashboard** — publish bias estimate as a `diagnostic_msgs/DiagnosticArray` on `/diagnostics`, viewable in vizanti or rqt_robot_monitor
2. **Bias seed from `oakd_camera.py`** — pipe the boot calibration result into ZUPT's `seed_bias` so the EMA starts from a good initial estimate (right now ZUPT seeds at 0)
3. **Differential-mode switch** — if EKF yaw still drifts during long sustained motion (where ZUPT can't intervene), try `imu0_differential: true` so EKF integrates yaw deltas instead of absolutes
4. **Online stationary calibration** — when stationary for >30 seconds, recompute a snapshot bias and re-seed the EMA. Catches thermal drift faster than alpha=0.01 EMA can
5. **Replace robot_localization with fusioncore** — if biases on accelerometer also start to matter (wouldn't surface in 2D mode), the full UKF with proper bias state vector is the destination
