# UGV Beast Tool Schema Endpoint — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Build a self-contained Python FastAPI service running on the UGV Beast (Jetson Orin Nano, inside the `ugv_waveshare` Docker container) that exposes robot capabilities as LLM-callable tools — one consolidated tool registry served in Anthropic, OpenAI, and Gemini formats over a REST endpoint reachable via Tailscale.

**Architecture:** Three concurrent subsystems inside the container: (1) camera publisher nodes (pan-tilt USB + OAK-D Lite) that fill in the missing `/camera/*` and `/oak/*` topics, (2) a gimbal bridge that patches ugv_driver to subscribe to `/gimbal/absolute` so we don't contend on `/dev/ttyTHS1`, and (3) a FastAPI server (`ugv_tools_api`) that spins a dedicated `rclpy` executor thread, dispatches tool calls into ROS2, and returns JSON results. BlackBox reaches it over Tailscale at `http://<ugv-tailnet-hostname>:8080`.

**Tech Stack:** Python 3.10, FastAPI, Uvicorn, rclpy (ROS2 Humble), OpenCV/v4l2 (pan-tilt), depthai-python (OAK-D), Pydantic v2, pytest, systemd (Jetson host), Tailscale.

**Ground Truth (probed 2026-04-16):**
- Live nodes: `/ugv_bringup`, `/ugv_driver`, `/base_node`, `/LD19`, `/rf2o_laser_odometry`, `/slam_toolbox`, full Nav2 stack, `/vizanti_flask_node`. **No camera, gimbal, lights, or explore nodes.**
- Live topics: `/cmd_vel`, `/cmd_vel_nav`, `/cmd_vel_teleop`, `/odom`, `/scan`, `/imu/data`, `/map`, `/pose`, `/robot_pose`, `/ugv/led_ctrl`, `/tf`. **No camera or gimbal topics.**
- `/dev/video0` and `/dev/video1` exist (pan-tilt USB cam + potential OAK webcam mode).
- ESP32 on `/dev/ttyTHS1` returns JSON with `pan`, `tilt`, IMU, `odl`, `odr`, `v` (battery) — gimbal feedback is free via existing driver telemetry.
- `depthai` Python module is NOT installed — must be added in prep.

**Container/Host Paths:**
- Jetson host: `/home/jetson/ugv_ws/` (bind-mounted into container as `/home/ws/ugv_ws/`)
- New code lives in: `/home/jetson/ugv_ws/ugv_tools_api/` (host side, visible in container at `/home/ws/ugv_ws/ugv_tools_api/`)
- Local dev repo: `docs/ugv-beast/setup/ugv_tools_api/` — rsync to Jetson host via `scripts/sync-ugv-tools.sh`.
- SSH into container: `sshpass -p 'jetson' ssh root@192.168.1.155 -p 23`
- SSH into host: `sshpass -p 'jetson' ssh jetson@192.168.1.155`

**Design Decision — One Tool Schema or Many?**
Use **ONE consolidated registry** (`GET /tools`) that returns all ~30 tools. Organized by namespace prefix (`motion_*`, `gimbal_*`, `camera_*`, `nav_*`, `status_*`, `lights_*`, `system_*`). Rationale: (a) single LLM cache block = cheaper prompt caching, (b) models reason better with cohesive capability list than with multiple discovery hops, (c) mirrors how BlackBox's existing tool vault works (`toolvault_architecture.md`). Format converters render the same registry as Anthropic `input_schema`, OpenAI `function.parameters`, or Gemini `parameters`.

---

## Phase 0 — Prep & Scaffolding

### Task 0.1: Create local project skeleton

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/pyproject.toml`
- Create: `docs/ugv-beast/setup/ugv_tools_api/README.md`
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/__init__.py`
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/__init__.py`
- Create: `scripts/sync-ugv-tools.sh`

**Step 1: Scaffold pyproject.toml**

```toml
[project]
name = "ugv_tools_api"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "fastapi>=0.111",
  "uvicorn[standard]>=0.29",
  "pydantic>=2.6",
  "opencv-python-headless>=4.9",
  "numpy>=1.24",
  "depthai>=2.24",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27", "ruff>=0.4"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Scaffold sync script**

```bash
#!/bin/bash
# scripts/sync-ugv-tools.sh - rsync local code to Jetson host
set -euo pipefail
JETSON_IP="192.168.1.155"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)/docs/ugv-beast/setup/ugv_tools_api/"
REMOTE_DIR="/home/jetson/ugv_ws/ugv_tools_api/"
sshpass -p 'jetson' rsync -avz --delete \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.egg-info' \
  "$LOCAL_DIR" "jetson@${JETSON_IP}:${REMOTE_DIR}"
echo "Synced to ${JETSON_IP}:${REMOTE_DIR}"
```

**Step 3: chmod +x and test**

```bash
chmod +x scripts/sync-ugv-tools.sh
./scripts/sync-ugv-tools.sh
```
Expected: rsync copies 3 files; no errors.

**Step 4: Commit**

```bash
git add docs/ugv-beast/setup/ugv_tools_api scripts/sync-ugv-tools.sh
git commit -m "feat(ugv): scaffold ugv_tools_api project skeleton"
```

---

### Task 0.2: Install Python deps inside container

**Files:** (runtime action, no repo change)

**Step 1: SSH into container and verify Python version**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "python3 --version && which pip3"
```
Expected: Python 3.10.x, pip available.

**Step 2: Install deps (system-wide since container is ephemeral-feeling but bind-mounted persistent packages live here)**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "pip3 install 'fastapi>=0.111' 'uvicorn[standard]>=0.29' 'pydantic>=2.6' \
   'opencv-python-headless>=4.9' depthai pytest httpx"
```

**Step 3: Verify depthai + OAK-D visibility**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "python3 -c 'import depthai as dai; print([d.getMxId() for d in dai.Device.getAllAvailableDevices()])'"
```
Expected: list with at least one MxId string (the OAK-D Lite). If empty, check USB cable + `sudo usermod -aG dialout root` (unlikely — container runs as root).

**Step 4: Commit a note-only file recording install**

```bash
# In local repo:
cat >> docs/ugv-beast/setup/ugv_tools_api/README.md <<'EOF'
## Container prerequisites (run once)
Installed 2026-04-16 via:
    pip3 install fastapi uvicorn[standard] pydantic opencv-python-headless depthai pytest httpx
EOF
git add docs/ugv-beast/setup/ugv_tools_api/README.md
git commit -m "docs(ugv): record container pip install for ugv_tools_api"
```

---

## Phase 1 — Missing Camera Publisher Nodes

These are standalone ROS2 nodes that fill in topics the tool server relies on. Test against ROS graph.

### Task 1.1: Pan-tilt camera publisher (V4L2 MJPEG)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py`
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/test_pantilt_camera.py`

**Step 1: Write the failing test**

```python
# tests/test_pantilt_camera.py
import pytest
from ugv_tools_api.nodes.pantilt_camera import PantiltCameraNode, resolve_v4l2_device

def test_resolve_v4l2_prefers_video0(monkeypatch, tmp_path):
    (tmp_path / "video0").touch()
    (tmp_path / "video1").touch()
    assert resolve_v4l2_device(search_root=str(tmp_path)) == str(tmp_path / "video0")

def test_resolve_v4l2_raises_if_none(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_v4l2_device(search_root=str(tmp_path))
```

**Step 2: Run test to verify it fails**

```bash
cd docs/ugv-beast/setup/ugv_tools_api && pytest tests/test_pantilt_camera.py -v
```
Expected: FAIL — `ModuleNotFoundError: ugv_tools_api.nodes.pantilt_camera`.

**Step 3: Minimal implementation**

```python
# ugv_tools_api/nodes/pantilt_camera.py
"""Publishes /dev/videoN frames as sensor_msgs/CompressedImage on /camera/image/compressed."""
from pathlib import Path
import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage

def resolve_v4l2_device(search_root: str = "/dev") -> str:
    candidates = sorted(Path(search_root).glob("video*"))
    if not candidates:
        raise FileNotFoundError(f"No /dev/video* devices under {search_root}")
    return str(candidates[0])

class PantiltCameraNode(Node):
    def __init__(self, device: str | None = None, width: int = 640, height: int = 480, fps: int = 15):
        super().__init__("ugv_pantilt_camera")
        self.device = device or resolve_v4l2_device()
        self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.pub = self.create_publisher(CompressedImage, "/camera/image/compressed", 10)
        self.timer = self.create_timer(1.0 / fps, self._tick)
        self.get_logger().info(f"pan-tilt camera: {self.device} {width}x{height}@{fps}")

    def _tick(self):
        ok, frame = self.cap.read()
        if not ok:
            return
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "pantilt_camera"
        msg.format = "jpeg"
        msg.data = buf.tobytes()
        self.pub.publish(msg)

def main():
    rclpy.init()
    node = PantiltCameraNode()
    try:
        rclpy.spin(node)
    finally:
        node.cap.release()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_pantilt_camera.py -v
```
Expected: both tests PASS.

**Step 5: Integration test on robot**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "source /opt/ros/humble/setup.bash && python3 /home/ws/ugv_ws/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py &
   sleep 3 && ros2 topic hz /camera/image/compressed --window 10"
```
Expected: ~15Hz on `/camera/image/compressed`.

**Step 6: Commit**

```bash
git add docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py \
        docs/ugv-beast/setup/ugv_tools_api/tests/test_pantilt_camera.py
git commit -m "feat(ugv): pan-tilt USB camera publisher node"
```

---

### Task 1.2: OAK-D Lite camera publisher (depthai standalone)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/oakd_camera.py`
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/test_oakd_camera.py`

**Step 1: Write the failing test**

```python
# tests/test_oakd_camera.py
from ugv_tools_api.nodes.oakd_camera import build_pipeline

def test_build_pipeline_has_rgb_output():
    p = build_pipeline(rgb_width=640, rgb_height=480, fps=15)
    outputs = [n.getName() for n in p.getAllNodes() if hasattr(n, "getName")]
    # The XLinkOut we wire up is named "rgb"
    assert any("rgb" in p.serializeToJson().lower() for _ in [1])
```

**Step 2: Verify fail**

```bash
pytest tests/test_oakd_camera.py -v
```
Expected: FAIL (module missing).

**Step 3: Implementation**

```python
# ugv_tools_api/nodes/oakd_camera.py
"""Publishes OAK-D Lite RGB frames on /oak/rgb/image_rect."""
import depthai as dai
import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CompressedImage

def build_pipeline(rgb_width=640, rgb_height=480, fps=15):
    p = dai.Pipeline()
    cam = p.create(dai.node.ColorCamera)
    cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
    cam.setPreviewSize(rgb_width, rgb_height)
    cam.setInterleaved(False)
    cam.setFps(fps)
    cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
    xout = p.create(dai.node.XLinkOut)
    xout.setStreamName("rgb")
    cam.preview.link(xout.input)
    return p

class OakdCameraNode(Node):
    def __init__(self, width=640, height=480, fps=15):
        super().__init__("ugv_oakd_camera")
        self.device = dai.Device(build_pipeline(width, height, fps))
        self.q = self.device.getOutputQueue(name="rgb", maxSize=4, blocking=False)
        self.pub_raw = self.create_publisher(Image, "/oak/rgb/image_rect", 10)
        self.pub_jpeg = self.create_publisher(CompressedImage, "/oak/rgb/image_rect/compressed", 10)
        self.timer = self.create_timer(1.0 / fps, self._tick)
        self.get_logger().info(f"OAK-D camera {width}x{height}@{fps}")

    def _tick(self):
        pkt = self.q.tryGet()
        if pkt is None:
            return
        frame = pkt.getCvFrame()
        h, w = frame.shape[:2]
        stamp = self.get_clock().now().to_msg()

        raw = Image()
        raw.header.stamp = stamp
        raw.header.frame_id = "oak_rgb_camera_optical_frame"
        raw.height, raw.width = h, w
        raw.encoding = "bgr8"
        raw.is_bigendian = 0
        raw.step = w * 3
        raw.data = frame.tobytes()
        self.pub_raw.publish(raw)

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            cimg = CompressedImage()
            cimg.header = raw.header
            cimg.format = "jpeg"
            cimg.data = buf.tobytes()
            self.pub_jpeg.publish(cimg)

def main():
    rclpy.init()
    node = OakdCameraNode()
    try:
        rclpy.spin(node)
    finally:
        node.device.close()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
```

**Step 4: Run pipeline build test**

```bash
pytest tests/test_oakd_camera.py -v
```
Expected: PASS (tests run without needing a real OAK).

**Step 5: On-robot integration test**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "source /opt/ros/humble/setup.bash && python3 /home/ws/ugv_ws/ugv_tools_api/ugv_tools_api/nodes/oakd_camera.py &
   sleep 5 && ros2 topic hz /oak/rgb/image_rect --window 10"
```
Expected: ~15Hz. If OAK-D device not found, fail loudly — do not fallback to fake data.

**Step 6: Commit**

```bash
git add docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/oakd_camera.py \
        docs/ugv-beast/setup/ugv_tools_api/tests/test_oakd_camera.py
git commit -m "feat(ugv): OAK-D Lite RGB publisher via depthai"
```

---

## Phase 2 — Gimbal Bridge

Serial `/dev/ttyTHS1` is owned by `ugv_driver`. We add a thin subscribe-and-forward patch inside the existing driver so only one process owns the port.

### Task 2.1: Patch ugv_driver to accept /gimbal/absolute

**Files:**
- Modify: `/home/ws/ugv_ws/src/ugv_main/ugv_bringup/ugv_bringup/ugv_driver.py` (on the Jetson — we'll copy patched version into local repo as well for source-of-truth)
- Create in repo: `docs/ugv-beast/setup/ugv_driver_patches/add_gimbal_topic.patch`

**Step 1: Locate ugv_driver.py on robot**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "find /home/ws/ugv_ws/src -name 'ugv_driver.py' | head -3"
```
Capture path; copy locally:
```bash
sshpass -p 'jetson' scp root@192.168.1.155:/home/ws/ugv_ws/src/ugv_main/ugv_bringup/ugv_bringup/ugv_driver.py \
  /tmp/ugv_driver.py.orig
```

**Step 2: Design the patch — add to __init__:**

```python
# Inside UGVDriver.__init__, after existing subscribers:
from geometry_msgs.msg import Point
self.create_subscription(Point, "/gimbal/absolute", self._on_gimbal_absolute, 10)

# New method:
def _on_gimbal_absolute(self, msg: Point):
    # Waveshare ESP32 firmware cmd T=132 -> set gimbal position.
    # x=pan (deg, -180..180), y=tilt (deg, -45..90), z=speed (1..300, 0 uses default 100)
    cmd = {"T": 132, "X": float(msg.x), "Y": float(msg.y), "SPD": int(msg.z) if msg.z else 100, "ACC": 0}
    self._write_json(cmd)
```

If `_write_json` isn't already the internal serial write helper, identify the actual serial write method name (typically `self.serial_ctrl.write(...)` or `self.base_ctrl.send_command(...)`) and adapt.

**Step 3: Write the patch**

Save as `docs/ugv-beast/setup/ugv_driver_patches/add_gimbal_topic.patch` (unified diff format) so we can re-apply after upstream pulls.

**Step 4: Apply on robot**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "cd /home/ws/ugv_ws && patch -p0 < /home/ws/ugv_ws/ugv_tools_api/patches/add_gimbal_topic.patch && \
   cd /home/ws/ugv_ws && colcon build --packages-select ugv_bringup --symlink-install"
```

**Step 5: Verify topic appears + gimbal moves**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "source /home/ws/ugv_ws/install/setup.bash && ros2 topic list | grep gimbal; \
   ros2 topic pub /gimbal/absolute geometry_msgs/msg/Point '{x: 30.0, y: 0.0, z: 100.0}' --once; sleep 2; \
   ros2 topic pub /gimbal/absolute geometry_msgs/msg/Point '{x: 0.0, y: 0.0, z: 100.0}' --once"
```
Expected: `/gimbal/absolute` listed, gimbal physically moves right then back to center.

**Step 6: Commit the patch**

```bash
git add docs/ugv-beast/setup/ugv_driver_patches/add_gimbal_topic.patch
git commit -m "feat(ugv): patch ugv_driver to accept /gimbal/absolute topic"
```

---

### Task 2.2: Gimbal state publisher (extract pan/tilt from ESP32 telemetry)

The ESP32 already emits `{"pan":..., "tilt":...}` in its T=1001 telemetry. `ugv_driver` parses this for IMU/odom — extend it to also publish a gimbal state message.

**Files:**
- Modify: ugv_driver.py (same patch file, add stanza)
- Test: shell verification

**Step 1: Patch stanza**

```python
# In ugv_driver's telemetry parser (where ax/ay/az are extracted), add:
from geometry_msgs.msg import PointStamped
# in __init__:
self.gimbal_state_pub = self.create_publisher(PointStamped, "/gimbal/state", 10)
# in telemetry callback, after parsing `data`:
if "pan" in data and "tilt" in data:
    gs = PointStamped()
    gs.header.stamp = self.get_clock().now().to_msg()
    gs.header.frame_id = "pantilt_base"
    gs.point.x = float(data["pan"])
    gs.point.y = float(data["tilt"])
    gs.point.z = 0.0
    self.gimbal_state_pub.publish(gs)
```

**Step 2: Apply, rebuild, verify**

```bash
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "source /home/ws/ugv_ws/install/setup.bash && ros2 topic echo /gimbal/state --once"
```
Expected: one message with `x` ≈ current pan, `y` ≈ current tilt.

**Step 3: Commit**

```bash
git add docs/ugv-beast/setup/ugv_driver_patches/add_gimbal_topic.patch
git commit -m "feat(ugv): publish /gimbal/state from ESP32 telemetry"
```

---

## Phase 3 — ROS2 Bridge Library

The FastAPI handlers are async; `rclpy` is sync. Bridge runs the ROS executor in a dedicated thread and exposes thread-safe helpers.

### Task 3.1: ROS2 bridge with lifecycle + thread-safe getters

**Files:**
- Create: `ugv_tools_api/ros_bridge.py`
- Test: `tests/test_ros_bridge.py`

**Step 1: Failing test**

```python
# tests/test_ros_bridge.py
import time
from ugv_tools_api.ros_bridge import RosBridge

def test_bridge_starts_and_shuts_down_cleanly():
    b = RosBridge()
    b.start()
    time.sleep(0.5)
    assert b.is_running()
    b.stop()
    assert not b.is_running()
```

**Step 2: Verify fail** — `pytest tests/test_ros_bridge.py -v` → ModuleNotFoundError.

**Step 3: Implement**

```python
# ugv_tools_api/ros_bridge.py
"""Singleton bridge: owns rclpy context, spins executor on a daemon thread,
exposes typed publish/subscribe/call helpers to the FastAPI layer."""
from __future__ import annotations
import threading
import time
from typing import Any, Callable, Optional
import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

class _BridgeNode(Node):
    def __init__(self):
        super().__init__("ugv_tools_api_bridge")
        self._latest: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._publishers: dict[str, Any] = {}

    def cache_topic(self, topic: str, msg_type, qos: int = 10):
        def cb(msg):
            with self._lock:
                self._latest[topic] = (time.time(), msg)
        self.create_subscription(msg_type, topic, cb, qos)

    def get_latest(self, topic: str):
        with self._lock:
            return self._latest.get(topic)

    def publisher(self, topic: str, msg_type, qos: int = 10):
        if topic not in self._publishers:
            self._publishers[topic] = self.create_publisher(msg_type, topic, qos)
        return self._publishers[topic]

class RosBridge:
    _instance: Optional["RosBridge"] = None

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._executor: Optional[MultiThreadedExecutor] = None
        self._node: Optional[_BridgeNode] = None
        self._running = threading.Event()

    @classmethod
    def instance(cls) -> "RosBridge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self):
        if self._running.is_set():
            return
        if not rclpy.ok():
            rclpy.init()
        self._node = _BridgeNode()
        self._executor = MultiThreadedExecutor(num_threads=4)
        self._executor.add_node(self._node)
        self._running.set()
        self._thread = threading.Thread(target=self._spin, daemon=True, name="ros_bridge")
        self._thread.start()

    def _spin(self):
        try:
            while self._running.is_set() and rclpy.ok():
                self._executor.spin_once(timeout_sec=0.1)
        finally:
            pass

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._executor:
            self._executor.shutdown()
        if self._node:
            self._node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    def is_running(self) -> bool:
        return self._running.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def node(self) -> _BridgeNode:
        if self._node is None:
            raise RuntimeError("Bridge not started")
        return self._node
```

**Step 4: Verify pass** — `pytest tests/test_ros_bridge.py -v`. Note: requires ROS2 sourced — CI should source `/opt/ros/humble/setup.bash` before pytest.

**Step 5: Commit**

```bash
git add ugv_tools_api/ros_bridge.py tests/test_ros_bridge.py
git commit -m "feat(ugv): ROS2 bridge with thread-safe executor"
```

---

### Task 3.2: Register canonical subscriptions in bridge

**Files:** Modify `ros_bridge.py`; add `tests/test_bridge_subscriptions.py`.

**Step 1: Test that, once started, bridge caches the usual topics**

```python
import time
from ugv_tools_api.ros_bridge import RosBridge
def test_bridge_caches_expected_topics():
    b = RosBridge.instance(); b.start(); time.sleep(3.0)
    # With robot running, these should be populated
    for t in ["/odom", "/scan", "/robot_pose", "/map"]:
        assert b.node.get_latest(t) is not None, f"no msg on {t}"
    b.stop()
```

Mark this test `@pytest.mark.integration` so it skips in CI.

**Step 2: Extend `RosBridge.start()`**

```python
# After self._thread.start():
from nav_msgs.msg import Odometry, OccupancyGrid
from sensor_msgs.msg import LaserScan, CompressedImage, Image
from geometry_msgs.msg import PoseStamped, PointStamped, Twist, Point
self._node.cache_topic("/odom", Odometry)
self._node.cache_topic("/scan", LaserScan)
self._node.cache_topic("/robot_pose", PoseStamped)
self._node.cache_topic("/gimbal/state", PointStamped)
from rclpy.qos import QoSProfile, DurabilityPolicy
map_qos = QoSProfile(depth=1); map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
self._node.create_subscription(OccupancyGrid, "/map",
    lambda m: self._node._latest.__setitem__("/map", (time.time(), m)), map_qos)
self._node.cache_topic("/camera/image/compressed", CompressedImage)
self._node.cache_topic("/oak/rgb/image_rect/compressed", CompressedImage)
# Pre-create publishers for cmd_vel and /gimbal/absolute
self._node.publisher("/cmd_vel", Twist)
self._node.publisher("/gimbal/absolute", Point)
```

**Step 3: Run integration test with robot up**

```bash
pytest tests/test_bridge_subscriptions.py -v -m integration
```
Expected PASS on live robot; SKIP otherwise.

**Step 4: Commit**

```bash
git add ugv_tools_api/ros_bridge.py tests/test_bridge_subscriptions.py
git commit -m "feat(ugv): bridge registers canonical subscriptions/publishers"
```

---

## Phase 4 — Tool Schema Model

One Pydantic-backed registry. Dispatcher maps tool name → async handler.

### Task 4.1: Tool descriptor model + format converters

**Files:**
- Create: `ugv_tools_api/schema.py`
- Test: `tests/test_schema.py`

**Step 1: Failing test**

```python
# tests/test_schema.py
from ugv_tools_api.schema import ToolDescriptor, ParamSchema, render_anthropic, render_openai, render_gemini

EX = ToolDescriptor(
    name="motion_move_forward",
    description="Drive straight forward for a set distance.",
    parameters={
        "distance_m": ParamSchema(type="number", minimum=0.01, maximum=2.0,
                                  description="Meters to travel forward."),
        "speed_m_s": ParamSchema(type="number", minimum=0.02, maximum=0.15, default=0.1,
                                 description="Linear velocity."),
    },
    required=["distance_m"],
)

def test_anthropic_has_input_schema():
    out = render_anthropic([EX])[0]
    assert out["name"] == "motion_move_forward"
    assert out["input_schema"]["type"] == "object"
    assert "distance_m" in out["input_schema"]["properties"]
    assert out["input_schema"]["required"] == ["distance_m"]

def test_openai_has_function_envelope():
    out = render_openai([EX])[0]
    assert out["type"] == "function"
    assert out["function"]["name"] == "motion_move_forward"
    assert "parameters" in out["function"]

def test_gemini_flat_parameters():
    out = render_gemini([EX])[0]
    assert out["name"] == "motion_move_forward"
    assert out["parameters"]["type"] == "object"
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/schema.py
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

class ParamSchema(BaseModel):
    type: Literal["string", "number", "integer", "boolean", "array", "object"]
    description: str
    default: Any | None = None
    minimum: float | None = None
    maximum: float | None = None
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None  # for type=array

class ToolDescriptor(BaseModel):
    name: str
    description: str
    parameters: dict[str, ParamSchema] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)

def _props_block(tool: ToolDescriptor) -> dict[str, Any]:
    props = {}
    for pname, ps in tool.parameters.items():
        entry: dict[str, Any] = {"type": ps.type, "description": ps.description}
        for opt in ("default", "minimum", "maximum", "enum", "items"):
            v = getattr(ps, opt)
            if v is not None:
                entry[opt] = v
        props[pname] = entry
    return {"type": "object", "properties": props, "required": tool.required}

def render_anthropic(tools: list[ToolDescriptor]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "input_schema": _props_block(t)} for t in tools]

def render_openai(tools: list[ToolDescriptor]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description, "parameters": _props_block(t)
    }} for t in tools]

def render_gemini(tools: list[ToolDescriptor]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "parameters": _props_block(t)} for t in tools]
```

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git add ugv_tools_api/schema.py tests/test_schema.py
git commit -m "feat(ugv): tool descriptor model + anthropic/openai/gemini renderers"
```

---

### Task 4.2: Tool registry + dispatcher

**Files:**
- Create: `ugv_tools_api/registry.py`
- Test: `tests/test_registry.py`

**Step 1: Failing test**

```python
# tests/test_registry.py
import asyncio, pytest
from ugv_tools_api.registry import ToolRegistry, tool
from ugv_tools_api.schema import ParamSchema

reg = ToolRegistry()

@reg.register(
    name="math_add",
    description="Add two numbers.",
    parameters={"a": ParamSchema(type="number", description="a"),
                "b": ParamSchema(type="number", description="b")},
    required=["a", "b"],
)
async def _add(a: float, b: float) -> dict:
    return {"sum": a + b}

def test_registry_lists_tool():
    assert "math_add" in reg.names()

def test_registry_dispatches():
    result = asyncio.run(reg.dispatch("math_add", {"a": 2, "b": 3}))
    assert result == {"sum": 5}

def test_registry_rejects_missing_required():
    with pytest.raises(ValueError):
        asyncio.run(reg.dispatch("math_add", {"a": 2}))
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/registry.py
from __future__ import annotations
import inspect
from typing import Any, Awaitable, Callable
from .schema import ToolDescriptor, ParamSchema, render_anthropic, render_openai, render_gemini

ToolHandler = Callable[..., Awaitable[Any]]

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, tuple[ToolDescriptor, ToolHandler]] = {}

    def register(self, *, name: str, description: str,
                 parameters: dict[str, ParamSchema] | None = None,
                 required: list[str] | None = None):
        td = ToolDescriptor(name=name, description=description,
                            parameters=parameters or {}, required=required or [])
        def deco(fn: ToolHandler):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Tool {name} handler must be async")
            self._tools[name] = (td, fn)
            return fn
        return deco

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def descriptors(self) -> list[ToolDescriptor]:
        return [td for td, _ in self._tools.values()]

    async def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        td, handler = self._tools[name]
        missing = [r for r in td.required if r not in args]
        if missing:
            raise ValueError(f"Missing required params for {name}: {missing}")
        return await handler(**args)

    # Convenience renderers
    def as_anthropic(self): return render_anthropic(self.descriptors())
    def as_openai(self): return render_openai(self.descriptors())
    def as_gemini(self): return render_gemini(self.descriptors())

# Module-level singleton used by tool modules
registry = ToolRegistry()
tool = registry.register  # shorthand: @tool(...)
```

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git add ugv_tools_api/registry.py tests/test_registry.py
git commit -m "feat(ugv): async tool registry with multi-format rendering"
```

---

## Phase 5 — Tool Implementations

Each file registers a domain's tools using `@tool(...)`. Handlers import `RosBridge.instance()` when they need to talk to ROS.

### Task 5.1: Motion tools

**Files:**
- Create: `ugv_tools_api/tools/motion.py`
- Test: `tests/test_tools_motion.py`

**Step 1: Failing test**

```python
# tests/test_tools_motion.py
import asyncio
from unittest.mock import MagicMock, patch
from ugv_tools_api import tools_motion  # triggers registration
from ugv_tools_api.registry import registry

def test_motion_tools_registered():
    names = registry.names()
    for t in ["motion_move_forward", "motion_move_backward",
              "motion_rotate_left", "motion_rotate_right", "motion_stop"]:
        assert t in names

def test_motion_stop_publishes_zero_twist():
    fake_pub = MagicMock()
    with patch("ugv_tools_api.tools.motion._cmd_vel_pub", return_value=fake_pub):
        asyncio.run(registry.dispatch("motion_stop", {}))
    assert fake_pub.publish.called
    twist = fake_pub.publish.call_args.args[0]
    assert twist.linear.x == 0 and twist.angular.z == 0
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/tools/motion.py
"""Motion tools: publishes to /cmd_vel via the ROS bridge."""
import asyncio
from geometry_msgs.msg import Twist
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

# Safety clamps (match velocity_smoother limits)
MAX_LIN = 0.15
MAX_ANG = 0.8

def _cmd_vel_pub():
    return RosBridge.instance().node.publisher("/cmd_vel", Twist)

async def _drive(linear: float, angular: float, duration: float):
    pub = _cmd_vel_pub()
    t0 = asyncio.get_event_loop().time()
    twist = Twist(); twist.linear.x = linear; twist.angular.z = angular
    while asyncio.get_event_loop().time() - t0 < duration:
        pub.publish(twist)
        await asyncio.sleep(0.1)
    stop = Twist()
    pub.publish(stop); pub.publish(stop)  # redundant stop for reliability
    return {"executed": {"linear": linear, "angular": angular, "duration_s": duration}}

@tool(
    name="motion_move_forward",
    description="Drive the robot straight forward for a duration.",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=10.0,
                                  description="Seconds to drive. Clamped at 10."),
        "speed_m_s": ParamSchema(type="number", minimum=0.02, maximum=MAX_LIN, default=0.1,
                                 description="Linear speed in m/s (max 0.15)."),
    },
    required=["duration_s"],
)
async def motion_move_forward(duration_s: float, speed_m_s: float = 0.1):
    speed = max(0.02, min(MAX_LIN, float(speed_m_s)))
    return await _drive(speed, 0.0, min(float(duration_s), 10.0))

@tool(
    name="motion_move_backward",
    description="Drive the robot straight backward for a duration.",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=10.0,
                                  description="Seconds to drive."),
        "speed_m_s": ParamSchema(type="number", minimum=0.02, maximum=MAX_LIN, default=0.08,
                                 description="Linear speed."),
    },
    required=["duration_s"],
)
async def motion_move_backward(duration_s: float, speed_m_s: float = 0.08):
    speed = max(0.02, min(MAX_LIN, float(speed_m_s)))
    return await _drive(-speed, 0.0, min(float(duration_s), 10.0))

@tool(
    name="motion_rotate_left",
    description="Rotate in place counter-clockwise (positive angular velocity).",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=5.0,
                                  description="Seconds to rotate."),
        "rate_rad_s": ParamSchema(type="number", minimum=0.1, maximum=MAX_ANG, default=0.5,
                                  description="Angular rate rad/s."),
    },
    required=["duration_s"],
)
async def motion_rotate_left(duration_s: float, rate_rad_s: float = 0.5):
    return await _drive(0.0, min(MAX_ANG, float(rate_rad_s)), min(float(duration_s), 5.0))

@tool(
    name="motion_rotate_right",
    description="Rotate in place clockwise (negative angular velocity).",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=5.0, description="Seconds."),
        "rate_rad_s": ParamSchema(type="number", minimum=0.1, maximum=MAX_ANG, default=0.5, description="rad/s."),
    },
    required=["duration_s"],
)
async def motion_rotate_right(duration_s: float, rate_rad_s: float = 0.5):
    return await _drive(0.0, -min(MAX_ANG, float(rate_rad_s)), min(float(duration_s), 5.0))

@tool(
    name="motion_stop",
    description="Immediately stop all motion (zero velocity).",
)
async def motion_stop():
    pub = _cmd_vel_pub()
    z = Twist()
    for _ in range(3):
        pub.publish(z)
        await asyncio.sleep(0.05)
    return {"stopped": True}
```

Add `tools_motion.py` shim at package root importing from `tools.motion` if needed — or update test import to `ugv_tools_api.tools import motion`.

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git add ugv_tools_api/tools/motion.py tests/test_tools_motion.py
git commit -m "feat(ugv): motion tools (forward/backward/rotate/stop)"
```

---

### Task 5.2: Gimbal tools

**Files:** `ugv_tools_api/tools/gimbal.py`, `tests/test_tools_gimbal.py`.

**Step 1: Failing test**

```python
def test_gimbal_look_at_clamps_ranges():
    from ugv_tools_api.tools.gimbal import _clamp
    assert _clamp(200, -180, 180) == 180
    assert _clamp(-200, -180, 180) == -180
    assert _clamp(95, -45, 90) == 90
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/tools/gimbal.py
from geometry_msgs.msg import Point, PointStamped
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

def _clamp(v, lo, hi): return max(lo, min(hi, v))

@tool(
    name="gimbal_look_at",
    description="Point the pan-tilt gimbal to an absolute pan/tilt angle in degrees.",
    parameters={
        "pan_deg": ParamSchema(type="number", minimum=-180, maximum=180,
                               description="Pan angle. Negative=right, positive=left. Zero=forward."),
        "tilt_deg": ParamSchema(type="number", minimum=-45, maximum=90,
                                description="Tilt angle. Negative=down, positive=up."),
        "speed": ParamSchema(type="integer", minimum=1, maximum=300, default=100,
                             description="Servo speed 1-300. 100 is a gentle default."),
    },
    required=["pan_deg", "tilt_deg"],
)
async def gimbal_look_at(pan_deg: float, tilt_deg: float, speed: int = 100):
    pub = RosBridge.instance().node.publisher("/gimbal/absolute", Point)
    m = Point()
    m.x = _clamp(float(pan_deg), -180, 180)
    m.y = _clamp(float(tilt_deg), -45, 90)
    m.z = float(_clamp(int(speed), 1, 300))
    pub.publish(m)
    return {"commanded": {"pan_deg": m.x, "tilt_deg": m.y, "speed": m.z}}

@tool(
    name="gimbal_reset",
    description="Return the gimbal to the forward-center home position (pan=0, tilt=0).",
)
async def gimbal_reset():
    return await gimbal_look_at(0.0, 0.0, 150)

@tool(
    name="gimbal_get_state",
    description="Get the current pan and tilt angles of the gimbal.",
)
async def gimbal_get_state():
    cached = RosBridge.instance().node.get_latest("/gimbal/state")
    if cached is None:
        return {"error": "no gimbal state received yet"}
    ts, msg = cached
    return {"pan_deg": msg.point.x, "tilt_deg": msg.point.y, "age_s": round(__import__('time').time() - ts, 3)}
```

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git commit -am "feat(ugv): gimbal tools (look_at, reset, get_state)"
```

---

### Task 5.3: Camera tools (snapshot + list)

**Files:** `ugv_tools_api/tools/camera.py`, `tests/test_tools_camera.py`.

**Step 1: Failing test**

```python
# test that camera_list returns expected camera names
import asyncio
from ugv_tools_api.tools import camera  # noqa: F401
from ugv_tools_api.registry import registry

def test_list_cameras():
    out = asyncio.run(registry.dispatch("camera_list", {}))
    assert set(out["cameras"]) >= {"pantilt", "oakd"}
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/tools/camera.py
import base64, time
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

CAMERA_TOPICS = {
    "pantilt": "/camera/image/compressed",
    "oakd":    "/oak/rgb/image_rect/compressed",
}

@tool(
    name="camera_list",
    description="List all cameras available on the robot and whether each is currently streaming.",
)
async def camera_list():
    node = RosBridge.instance().node
    now = time.time()
    cams = {}
    for name, topic in CAMERA_TOPICS.items():
        c = node.get_latest(topic)
        cams[name] = {
            "topic": topic,
            "streaming": c is not None and (now - c[0]) < 2.0,
            "last_frame_age_s": round(now - c[0], 2) if c else None,
        }
    return {"cameras": list(cams.keys()), "details": cams}

@tool(
    name="camera_snapshot",
    description="Capture the latest JPEG frame from a camera, return base64 or a URL path.",
    parameters={
        "camera": ParamSchema(type="string", enum=["pantilt", "oakd"],
                              description="Which camera to snapshot."),
        "as_url": ParamSchema(type="boolean", default=False,
                              description="If true, return a GET URL like /snapshot/pantilt instead of base64."),
    },
    required=["camera"],
)
async def camera_snapshot(camera: str, as_url: bool = False):
    topic = CAMERA_TOPICS.get(camera)
    if not topic:
        return {"error": f"unknown camera {camera}"}
    cached = RosBridge.instance().node.get_latest(topic)
    if cached is None:
        return {"error": f"no frames yet on {topic}"}
    ts, msg = cached
    if as_url:
        return {"camera": camera, "url": f"/snapshot/{camera}", "age_s": round(time.time() - ts, 3)}
    return {
        "camera": camera,
        "format": "jpeg",
        "age_s": round(time.time() - ts, 3),
        "image_b64": base64.b64encode(bytes(msg.data)).decode("ascii"),
        "size_bytes": len(msg.data),
    }
```

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git commit -am "feat(ugv): camera tools (list, snapshot base64+url)"
```

---

### Task 5.4: Status tools (pose, odom, LiDAR summary, nodes, topics, health)

**Files:** `ugv_tools_api/tools/status.py`, `tests/test_tools_status.py`.

**Step 1: Failing test**

```python
def test_status_tools_registered():
    from ugv_tools_api.tools import status  # noqa
    from ugv_tools_api.registry import registry
    for t in ["status_get_pose", "status_get_lidar_summary",
              "status_list_nodes", "status_list_topics", "status_health"]:
        assert t in registry.names()
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/tools/status.py
import math, subprocess, time
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

def _yaw_from_quat(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)

@tool(name="status_get_pose", description="Get the robot's current (x, y, yaw) pose in the map frame.")
async def status_get_pose():
    cached = RosBridge.instance().node.get_latest("/robot_pose")
    if cached is None:
        return {"error": "no pose yet"}
    _, msg = cached
    p = msg.pose.position; o = msg.pose.orientation
    yaw = _yaw_from_quat(o)
    return {"x": round(p.x, 3), "y": round(p.y, 3),
            "yaw_rad": round(yaw, 3), "yaw_deg": round(math.degrees(yaw), 1)}

@tool(name="status_get_odom", description="Get filtered odometry (position + linear/angular velocity).")
async def status_get_odom():
    cached = RosBridge.instance().node.get_latest("/odom")
    if cached is None:
        return {"error": "no odom yet"}
    _, msg = cached
    return {
        "x": round(msg.pose.pose.position.x, 3),
        "y": round(msg.pose.pose.position.y, 3),
        "yaw_deg": round(math.degrees(_yaw_from_quat(msg.pose.pose.orientation)), 1),
        "v_linear": round(msg.twist.twist.linear.x, 3),
        "v_angular": round(msg.twist.twist.angular.z, 3),
    }

@tool(name="status_get_lidar_summary",
      description="Summarize the LiDAR scan as 8 directional sectors with min distance per sector.")
async def status_get_lidar_summary():
    cached = RosBridge.instance().node.get_latest("/scan")
    if cached is None:
        return {"error": "no /scan yet"}
    _, scan = cached
    ranges = scan.ranges; n = len(ranges)
    if n == 0: return {"error": "empty scan"}
    names = ["front","front_left","left","back_left","back","back_right","right","front_right"]
    sector = n // 8
    out = {}
    for i, name in enumerate(names):
        chunk = [r for r in ranges[i*sector:(i+1)*sector] if scan.range_min < r < scan.range_max]
        out[name] = round(min(chunk), 3) if chunk else None
    valid = [r for r in ranges if scan.range_min < r < scan.range_max]
    return {"sectors_m": out, "overall_min_m": round(min(valid), 3) if valid else None}

@tool(name="status_list_nodes", description="List all running ROS2 nodes.")
async def status_list_nodes():
    proc = subprocess.run(["ros2", "node", "list"], capture_output=True, text=True, timeout=5)
    return {"nodes": sorted([n for n in proc.stdout.splitlines() if n.strip()])}

@tool(name="status_list_topics", description="List all active ROS2 topics with message type.")
async def status_list_topics():
    proc = subprocess.run(["ros2", "topic", "list", "-t"], capture_output=True, text=True, timeout=5)
    topics = []
    for line in proc.stdout.splitlines():
        if "[" in line and "]" in line:
            name, tp = line.rsplit("[", 1)
            topics.append({"topic": name.strip(), "type": tp.rstrip("]").strip()})
    return {"topics": sorted(topics, key=lambda x: x["topic"])}

@tool(name="status_health", description="Overall health: bridge running, topic freshness, subscribed counts.")
async def status_health():
    b = RosBridge.instance()
    now = time.time()
    topics_seen = {}
    for t in ["/odom","/scan","/robot_pose","/map",
              "/camera/image/compressed","/oak/rgb/image_rect/compressed","/gimbal/state"]:
        c = b.node.get_latest(t)
        topics_seen[t] = None if c is None else round(now - c[0], 2)
    return {
        "bridge_running": b.is_running(),
        "topic_freshness_s": topics_seen,
        "timestamp": now,
    }
```

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git commit -am "feat(ugv): status tools (pose/odom/lidar/nodes/topics/health)"
```

---

### Task 5.5: Navigation tools (Nav2 action client)

**Files:** `ugv_tools_api/tools/nav.py`, `tests/test_tools_nav.py`.

**Step 1: Failing test**

```python
def test_nav_tools_registered():
    from ugv_tools_api.tools import nav  # noqa
    from ugv_tools_api.registry import registry
    for t in ["nav_goto_point", "nav_cancel", "nav_status"]:
        assert t in registry.names()
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/tools/nav.py
import math, threading, time
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

_state = {"status": "idle", "distance_remaining": None, "handle": None, "client": None}
_lock = threading.Lock()

def _client() -> ActionClient:
    with _lock:
        if _state["client"] is None:
            _state["client"] = ActionClient(RosBridge.instance().node, NavigateToPose, "navigate_to_pose")
        return _state["client"]

@tool(
    name="nav_goto_point",
    description="Navigate to an (x, y, yaw) pose in the map frame using Nav2.",
    parameters={
        "x": ParamSchema(type="number", description="Map-frame x in meters."),
        "y": ParamSchema(type="number", description="Map-frame y in meters."),
        "yaw_deg": ParamSchema(type="number", default=0.0, minimum=-180, maximum=180,
                               description="Goal heading in degrees."),
    },
    required=["x", "y"],
)
async def nav_goto_point(x: float, y: float, yaw_deg: float = 0.0):
    ac = _client()
    if not ac.wait_for_server(timeout_sec=3.0):
        return {"error": "Nav2 action server not available"}
    g = NavigateToPose.Goal()
    g.pose.header.frame_id = "map"
    g.pose.header.stamp = RosBridge.instance().node.get_clock().now().to_msg()
    g.pose.pose.position.x = float(x); g.pose.pose.position.y = float(y)
    yaw = math.radians(float(yaw_deg))
    g.pose.pose.orientation.z = math.sin(yaw / 2); g.pose.pose.orientation.w = math.cos(yaw / 2)

    def _feedback(fb):
        with _lock: _state["distance_remaining"] = fb.feedback.distance_remaining

    send_fut = ac.send_goal_async(g, feedback_callback=_feedback)
    t0 = time.time()
    while not send_fut.done() and time.time() - t0 < 3.0:
        time.sleep(0.05)
    if not send_fut.done():
        return {"error": "Timed out sending goal"}
    handle = send_fut.result()
    if not (handle and handle.accepted):
        return {"error": "Nav2 rejected goal"}
    with _lock:
        _state["status"] = "navigating"; _state["handle"] = handle

    def _monitor(h):
        fut = h.get_result_async()
        while not fut.done(): time.sleep(0.2)
        r = fut.result()
        status_map = {4: "succeeded", 5: "canceled", 6: "aborted"}
        with _lock:
            _state["status"] = status_map.get(r.status, f"ended_{r.status}")
            _state["handle"] = None
    threading.Thread(target=_monitor, args=(handle,), daemon=True).start()
    return {"accepted": True, "goal": {"x": x, "y": y, "yaw_deg": yaw_deg}}

@tool(name="nav_cancel", description="Cancel the currently active Nav2 goal.")
async def nav_cancel():
    with _lock: h = _state["handle"]
    if h is None: return {"canceled": False, "reason": "no active goal"}
    h.cancel_goal_async()
    return {"canceled": True}

@tool(name="nav_status", description="Get the current Nav2 goal status and distance remaining.")
async def nav_status():
    with _lock:
        return {"status": _state["status"],
                "distance_remaining_m": _state["distance_remaining"]}
```

**Step 4: Verify pass.**

**Step 5: Commit**

```bash
git commit -am "feat(ugv): nav tools (goto_point / cancel / status)"
```

---

### Task 5.6: System tools (estop, oled, servo calibration)

**Files:** `ugv_tools_api/tools/system.py`, `tests/test_tools_system.py`.

ESP32 JSON commands used:
- `{"T": 0}` — emergency stop (all motors zero)
- `{"T": 134}` — servo center calibration
- `{"T": 135}` — servo release

Since we can't open /dev/ttyTHS1 directly, we route through `/ugv/cmd_raw` if ugv_driver exposes it, else add a tiny subscription in `ugv_driver.py` to `/ugv/json_cmd` (std_msgs/String that forwards JSON).

**Step 1: Patch ugv_driver to accept /ugv/json_cmd (add to same patch file)**

```python
from std_msgs.msg import String
self.create_subscription(String, "/ugv/json_cmd", self._on_json_cmd, 10)
def _on_json_cmd(self, msg: String):
    try:
        self._write_json(__import__("json").loads(msg.data))
    except Exception as e:
        self.get_logger().warn(f"/ugv/json_cmd parse error: {e}")
```

Apply patch; rebuild.

**Step 2: Implement tools**

```python
# ugv_tools_api/tools/system.py
import json
from std_msgs.msg import String
from geometry_msgs.msg import Twist
from ..ros_bridge import RosBridge
from ..registry import tool

def _json_cmd_pub():
    return RosBridge.instance().node.publisher("/ugv/json_cmd", String)

@tool(name="system_emergency_stop",
      description="Emergency stop: zero velocity plus ESP32 T=0 all-motor cutoff.")
async def system_emergency_stop():
    # Local zero + firmware-level stop
    RosBridge.instance().node.publisher("/cmd_vel", Twist).publish(Twist())
    _json_cmd_pub().publish(String(data=json.dumps({"T": 0})))
    return {"estopped": True}

@tool(name="system_servo_center",
      description="Center/calibrate all servos to mid-point. Use with robot at rest.")
async def system_servo_center():
    _json_cmd_pub().publish(String(data=json.dumps({"T": 134})))
    return {"centered": True}

@tool(name="system_servo_release",
      description="Release servo torque (limp mode) — robot will not resist movement.")
async def system_servo_release():
    _json_cmd_pub().publish(String(data=json.dumps({"T": 135})))
    return {"released": True}
```

**Step 3: Test**

```python
def test_system_tools_registered():
    from ugv_tools_api.tools import system  # noqa
    from ugv_tools_api.registry import registry
    for t in ["system_emergency_stop", "system_servo_center", "system_servo_release"]:
        assert t in registry.names()
```

**Step 4: Commit**

```bash
git commit -am "feat(ugv): system tools (estop/servo_center/servo_release)"
```

---

## Phase 6 — HTTP Server

### Task 6.1: FastAPI app + routes

**Files:**
- Create: `ugv_tools_api/server.py`
- Test: `tests/test_server.py`

**Step 1: Failing test**

```python
# tests/test_server.py
from fastapi.testclient import TestClient
from ugv_tools_api.server import app
c = TestClient(app)

def test_health(): assert c.get("/health").json()["ok"] is True

def test_tools_anthropic_format():
    r = c.get("/tools?format=anthropic").json()
    assert isinstance(r, list) and len(r) > 0
    assert all("input_schema" in t for t in r)

def test_tools_openai_format():
    r = c.get("/tools?format=openai").json()
    assert all(t["type"] == "function" for t in r)

def test_unknown_tool_404():
    r = c.post("/tool/nope_not_real", json={})
    assert r.status_code == 404
```

**Step 2: Verify fail.**

**Step 3: Implement**

```python
# ugv_tools_api/server.py
"""FastAPI HTTP surface for the UGV Beast tool schema."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from typing import Any
import base64, time

# Import tool modules to register handlers before app starts
from .registry import registry
from .ros_bridge import RosBridge
from .tools import motion, gimbal, camera, status, nav, system  # noqa: F401

@asynccontextmanager
async def lifespan(app: FastAPI):
    RosBridge.instance().start()
    yield
    RosBridge.instance().stop()

app = FastAPI(title="UGV Beast Tool Schema API", version="0.1.0", lifespan=lifespan)

@app.get("/health")
def health():
    return {"ok": True, "bridge": RosBridge.instance().is_running()}

@app.get("/tools")
def list_tools(format: str = Query("anthropic", pattern="^(anthropic|openai|gemini)$")):
    if format == "anthropic": return registry.as_anthropic()
    if format == "openai":    return registry.as_openai()
    return registry.as_gemini()

class ToolCall(BaseModel):
    class Config: extra = "allow"

@app.post("/tool/{name}")
async def call_tool(name: str, body: dict[str, Any] | None = None):
    body = body or {}
    if name not in registry.names():
        raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
    try:
        result = await registry.dispatch(name, body)
        return {"tool": name, "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return JSONResponse(status_code=500, content={"tool": name, "error": str(e)})

@app.get("/snapshot/{camera_name}")
def snapshot(camera_name: str):
    from .tools.camera import CAMERA_TOPICS
    topic = CAMERA_TOPICS.get(camera_name)
    if not topic:
        raise HTTPException(404, f"unknown camera {camera_name}")
    cached = RosBridge.instance().node.get_latest(topic)
    if cached is None:
        raise HTTPException(503, f"no frames on {topic} yet")
    _, msg = cached
    return Response(content=bytes(msg.data), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
```

**Step 4: Verify pass.** Run: `pytest tests/test_server.py -v`.

**Step 5: Commit**

```bash
git commit -am "feat(ugv): FastAPI HTTP surface (tools, tool dispatch, snapshot)"
```

---

### Task 6.2: Entrypoint + uvicorn runner

**Files:**
- Create: `ugv_tools_api/__main__.py`
- Modify: `pyproject.toml` (add script)

**Step 1: Implement**

```python
# ugv_tools_api/__main__.py
import os, uvicorn
from .server import app

def main():
    host = os.environ.get("UGV_TOOLS_HOST", "0.0.0.0")
    port = int(os.environ.get("UGV_TOOLS_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info", workers=1)

if __name__ == "__main__":
    main()
```

**Step 2: Smoke test**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "cd /home/ws/ugv_ws/ugv_tools_api && source /opt/ros/humble/setup.bash && \
   source /home/ws/ugv_ws/install/setup.bash && python3 -m ugv_tools_api &
   sleep 4 && curl -s http://localhost:8080/health"
```
Expected: `{"ok": true, "bridge": true}`.

**Step 3: Commit**

```bash
git commit -am "feat(ugv): __main__ entrypoint for uvicorn"
```

---

## Phase 7 — Systemd + Tailscale

### Task 7.1: Systemd service for auto-start

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-tools-api.service`
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/start_tools_api.sh`

**Step 1: Write the service unit**

```ini
# ugv-tools-api.service (install at /etc/systemd/system/ on Jetson host)
[Unit]
Description=UGV Beast Tool Schema API
Requires=ugv-waveshare.service docker.service
After=ugv-waveshare.service

[Service]
Type=simple
Restart=on-failure
RestartSec=5
ExecStart=/usr/bin/docker exec ugv_waveshare /home/ws/ugv_ws/ugv_tools_api/deploy/start_tools_api.sh
ExecStop=/usr/bin/docker exec ugv_waveshare pkill -f "python3 -m ugv_tools_api"
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
```

**Step 2: Write the start script**

```bash
#!/bin/bash
# start_tools_api.sh (runs inside ugv_waveshare container)
set -euo pipefail
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
export UGV_MODEL=ugv_beast
export UGV_TOOLS_HOST=0.0.0.0
export UGV_TOOLS_PORT=8080

# Wait up to 30s for Nav2 to be up
for i in $(seq 1 30); do
  if ros2 node list 2>/dev/null | grep -q bt_navigator; then break; fi
  sleep 1
done

# Also launch camera publishers in background
python3 /home/ws/ugv_ws/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py &
python3 /home/ws/ugv_ws/ugv_tools_api/ugv_tools_api/nodes/oakd_camera.py &

# Main server (foreground — systemd tracks this)
exec python3 -m ugv_tools_api
```

**Step 3: Install & enable**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  "sudo cp /home/jetson/ugv_ws/ugv_tools_api/deploy/ugv-tools-api.service /etc/systemd/system/ && \
   chmod +x /home/jetson/ugv_ws/ugv_tools_api/deploy/start_tools_api.sh && \
   sudo systemctl daemon-reload && sudo systemctl enable ugv-tools-api.service && \
   sudo systemctl start ugv-tools-api.service && \
   sleep 8 && sudo systemctl status ugv-tools-api.service --no-pager"
```
Expected: `active (running)`.

**Step 4: Commit**

```bash
git add docs/ugv-beast/setup/ugv_tools_api/deploy/
git commit -m "feat(ugv): systemd service + startup script for tools api"
```

---

### Task 7.2: Tailscale reachability from BlackBox

**Files:**
- Create: `scripts/test-ugv-tools-remote.sh`

**Step 1: Confirm UGV tailnet hostname**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "tailscale status | head -3"
```
Capture the tailnet name (e.g., `ugv-beast`).

**Step 2: Write reachability test script**

```bash
#!/bin/bash
# scripts/test-ugv-tools-remote.sh
set -euo pipefail
UGV_HOST="${UGV_HOST:-ugv-beast}"
curl -fsS "http://${UGV_HOST}:8080/health" | jq .
curl -fsS "http://${UGV_HOST}:8080/tools?format=anthropic" | jq 'length'
curl -fsS -X POST "http://${UGV_HOST}:8080/tool/status_health" -H 'Content-Type: application/json' -d '{}' | jq .
```

**Step 3: Run it from the BlackBox**

```bash
chmod +x scripts/test-ugv-tools-remote.sh && ./scripts/test-ugv-tools-remote.sh
```
Expected: `ok:true`, tool count >= 25, health payload with fresh topic timestamps.

**Step 4: Commit**

```bash
git add scripts/test-ugv-tools-remote.sh
git commit -m "feat(ugv): tailscale reachability test script"
```

---

## Phase 8 — End-to-End Validation

### Task 8.1: Drive the robot via Claude tool-use

**Files:**
- Create: `scripts/ugv-llm-demo.py`

**Step 1: Write a minimal demo**

```python
# scripts/ugv-llm-demo.py
"""Minimal smoke test: fetch tools from UGV, hand them to Claude, let it drive."""
import os, json, requests
from anthropic import Anthropic

UGV = os.environ.get("UGV_URL", "http://ugv-beast:8080")
tools = requests.get(f"{UGV}/tools?format=anthropic").json()
client = Anthropic()

msg = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="You control a UGV Beast robot. Use tools responsibly.",
    tools=tools,
    messages=[{"role": "user",
               "content": "Look forward with the gimbal, then rotate the robot left for 1 second, then stop."}],
)
for block in msg.content:
    if block.type == "tool_use":
        r = requests.post(f"{UGV}/tool/{block.name}", json=block.input)
        print(block.name, "→", r.json())
```

**Step 2: Run it**

```bash
ANTHROPIC_API_KEY=... python3 scripts/ugv-llm-demo.py
```
Expected:
- `gimbal_look_at` call executes; gimbal physically moves to center.
- `motion_rotate_left` call executes; robot rotates counter-clockwise ~1s.
- `motion_stop` call executes; robot stops.

**Step 3: Commit**

```bash
git add scripts/ugv-llm-demo.py
git commit -m "feat(ugv): end-to-end Claude tool-use smoke demo"
```

---

### Task 8.2: Document the capability surface

**Files:**
- Modify: `docs/ugv-beast/research/CHEATSHEET.md` — add a "Tool Schema API" section
- Create: `docs/ugv-beast/setup/ugv_tools_api/README.md` — flesh out

**Step 1: Append to CHEATSHEET.md**

```markdown
## Tool Schema API (BlackBox ↔ UGV)

**Endpoint:** `http://ugv-beast:8080` (Tailscale) / `http://192.168.1.155:8080` (LAN)

| Route | Purpose |
|-------|---------|
| `GET /health` | Liveness + bridge status |
| `GET /tools?format=anthropic\|openai\|gemini` | LLM-formatted tool schemas |
| `POST /tool/{name}` | Dispatch a tool call; body is JSON params |
| `GET /snapshot/{pantilt\|oakd}` | Latest JPEG frame |

Tool namespaces: `motion_*`, `gimbal_*`, `camera_*`, `nav_*`, `status_*`, `system_*`.

Restart: `sudo systemctl restart ugv-tools-api.service` (on Jetson host).
```

**Step 2: Update README.md** with quickstart + tool list.

**Step 3: Commit**

```bash
git commit -am "docs(ugv): document tool schema API surface"
```

---

### Task 8.3: Reboot resilience

**Step 1: Cold reboot**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "sudo reboot"
```

**Step 2: Wait ~90s (slam_toolbox index rebuild), re-run remote tests**

```bash
sleep 120 && ./scripts/test-ugv-tools-remote.sh
```
Expected: all three checks still pass — ugv-waveshare, ugv-tools-api, and camera nodes all up.

**Step 3: If anything fails**: diagnose with `sudo journalctl -u ugv-tools-api.service -f` on the Jetson host. Do not mask errors with shortcuts — fix the root cause.

---

## Out of Scope (Explicit Non-Goals for V1)

- **Streaming WebSocket** for continuous video — V1 is request/response snapshots only. Add in V2 if needed.
- **BlackBox ToolVault auto-injection** — separate next-phase plan per user's stated scope.
- **Authentication** — V1 relies on Tailscale for access control. Add API keys only if we later expose over public internet.
- **YOLO / tracking / lights / explore_lite tools** — CHEATSHEET mentions these, but the underlying nodes aren't in the stock rebuild. Add them when (and if) their upstream ROS nodes are restored.
- **AMCL + saved-map navigation** — `nav_goto_point` currently uses online SLAM; waypoints drift. See `ugv_stock_rebuild.md` memory for the saved-map follow-up.

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| ugv_driver patch collides with upstream pulls | Patch stored as unified diff; reapply via `patch -p0` after pulls |
| rclpy + asyncio threading deadlock | Use `MultiThreadedExecutor(num_threads=4)` + publish on a dedicated thread (as in Cron memory note) |
| OAK-D USB bandwidth saturation | Single 640×480 @ 15fps stream; if CPU hot, drop to 320×240 |
| Motion tools misused by hallucinating LLM | Hard server-side clamps (MAX_LIN=0.15, MAX_ANG=0.8, max duration 10s) and `status_get_lidar_summary` tool so LLM can self-check |
| Serial contention on /dev/ttyTHS1 | Only ugv_driver touches it; gimbal+system routed via /gimbal/absolute and /ugv/json_cmd topics |
| Tailscale hostname change | Use `tailscale status --json` in test script, fall back to magic DNS |

---

Plan complete and saved to `docs/plans/2026-04-16-ugv-tool-schema.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Parallel Session (separate)** — Open a new session with `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
