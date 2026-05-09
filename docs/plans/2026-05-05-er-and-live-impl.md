# ER Spatial Grounding + Gemini Live Arrival Narration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate ER's failure mode where it plots nav points "all over the place" by giving it a deterministic pixel→map projection tool, and add Gemini Live arrival narration so the supervisor describes what it sees on each goal completion.

**Architecture:**
1. **Issue 2 (ER projection)**: New `project_pixel_to_map(pixel_u, pixel_v)` tool runs inside `ugv_tools_api`, subscribes to OAK-D `camera_info`, latest depth frame, and the TF tree. It back-projects pixel → camera-frame 3D → map-frame 3D using stable computer-vision math, returning (x, y, z) plus costmap diagnostics. ER's system prompt is rewritten to use this tool instead of mental geometric reasoning.
2. **Issue 3 (Live arrival)**: `mission_poller.py` already detects terminal-status transitions. We add a hook that, on `succeeded`, fetches the latest OAK-D RGB snapshot via the existing `camera_snapshot` HTTP path and injects it into the Gemini Live session as a multimodal turn with a "describe your surroundings" prompt. Live's existing TTS pipeline narrates the response.

**Tech Stack:** Python 3.10, ROS2 Humble (rclpy, tf2_ros), FastAPI, httpx (async), Gemini ER 1.6 (google-genai SDK), Gemini Live (multimodal session), pytest.

**Reference snapshots for context:** SNAP-20260505-6453 (post-BNO085 Nav2 tuning state), SNAP-20260504-6432 (BNO085 integration), SNAP-20260504-6451 (Nav2 baseline).

**Repo layout reminder:**
- Local dev (this machine, NOT a git repo): `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/`
  - Plans: `docs/plans/`
  - Local edit staging: `deployments/`
- Robot (Jetson, IS a git repo): `/home/jetson/ugv_ws_waveshare/`
  - ER code: `ugv_tools_api/ugv_tools_api/er/`
  - Supervisor code: `ugv_tools_api/ugv_tools_api/supervisor/`
  - Inside container: same paths under `/home/ws/ugv_ws/`
- Services: `ugv-waveshare.service` (ROS2 stack), `ugv-tools-api.service` (FastAPI tool API), `ugv-er.service` (ER agent), `ugv-supervisor.service` (Gemini Live)

**Deploy pattern:** edit locally → `scp` to `/tmp/` on Jetson → `sudo cp` to source path → restart relevant service.

---

## Phase 0: Reconnaissance (must complete before any code)

### Task 0.1: Find the tool registry

**Why:** `tools_decl.py` references `registry.descriptors()` but we haven't seen where the registry is built. Adding a new tool requires registering it.

**Action:** SSH in and locate it.

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'grep -rn "class.*Registry\|registry =\|register_tool\|TOOL_REGISTRY" /home/jetson/ugv_ws_waveshare/ugv_tools_api/ 2>/dev/null | head -20'
```

**Deliverable:** Note the file path and registry-add API. Likely candidates:
- `ugv_tools_api/ugv_tools_api/tools/__init__.py`
- `ugv_tools_api/ugv_tools_api/registry.py`

**Commit:** none — this is reconnaissance.

### Task 0.2: Find Live session class

**Why:** Image injection requires calling a method on the running Gemini Live session. We need to know what's available.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'grep -nE "class.*Session\|live\.send\|send_realtime_input\|client_content\|inject" /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/supervisor/*.py | head -30'
```

**Deliverable:** Identify the session class and what API the `genai.live` client exposes for image injection. Confirm whether `send_realtime_input` accepts an image payload directly.

**Commit:** none.

### Task 0.3: Confirm OAK-D depth topic name & camera_info topic

**Why:** Plan assumes `/oak/depth/image_raw` and `/oak/rgb/camera_info` but we need to verify in the running system.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && ros2 topic list | grep -E \"oak|depth|camera_info\""'
```

**Deliverable:** Exact topic names recorded. Verify `/oak/stereo/depth` (or whatever's published) and `/oak/rgb/camera_info`.

**Commit:** none.

---

## Phase A: Projection math, pure unit tests (no ROS dep)

### Task A.1: Create staging directory + write failing math test

**Files:**
- Create: `deployments/er_projection/test_projection_math.py`

```python
"""Unit tests for pixel→camera-frame back-projection (no ROS dependencies).

The tool's math layer is pure and testable in isolation: given camera intrinsics
(fx, fy, cx, cy) and a depth value, back-project pixel (u, v) to a 3D point in
the camera optical frame using the standard pinhole model:
    X = (u - cx) * Z / fx
    Y = (v - cy) * Z / fy
    Z = depth
"""
import math

import pytest

from projection_math import pixel_to_camera_frame


def test_principal_point_back_projects_to_optical_axis():
    # Pixel exactly at principal point + depth d → camera point (0, 0, d).
    fx, fy, cx, cy = 600.0, 600.0, 320.0, 240.0
    x, y, z = pixel_to_camera_frame(u=320, v=240, depth=2.0,
                                    fx=fx, fy=fy, cx=cx, cy=cy)
    assert abs(x) < 1e-9
    assert abs(y) < 1e-9
    assert z == pytest.approx(2.0)


def test_pixel_offset_produces_lateral_offset():
    # Pixel shifted right of principal by 60px @ depth 2m, fx 600 → x = 60*2/600 = 0.20m
    fx, fy, cx, cy = 600.0, 600.0, 320.0, 240.0
    x, y, z = pixel_to_camera_frame(u=380, v=240, depth=2.0,
                                    fx=fx, fy=fy, cx=cx, cy=cy)
    assert x == pytest.approx(0.20)
    assert abs(y) < 1e-9
    assert z == pytest.approx(2.0)


def test_pixel_above_principal_produces_negative_y():
    # Image-y axis points down, optical-frame Y points down → pixel above principal
    # (smaller v) yields negative Y in camera frame.
    fx, fy, cx, cy = 600.0, 600.0, 320.0, 240.0
    _, y, _ = pixel_to_camera_frame(u=320, v=180, depth=2.0,
                                    fx=fx, fy=fy, cx=cx, cy=cy)
    assert y == pytest.approx((180 - 240) * 2.0 / 600.0)
    assert y < 0


def test_zero_depth_returns_origin():
    # Zero depth means no return (depth sensor missed) — tool should handle this
    # gracefully. Math layer just returns origin; tool layer rejects.
    x, y, z = pixel_to_camera_frame(u=320, v=240, depth=0.0,
                                    fx=600.0, fy=600.0, cx=320.0, cy=240.0)
    assert (x, y, z) == (0.0, 0.0, 0.0)
```

**Step 1: Run test to verify it fails**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/er_projection/
python3 -m pytest test_projection_math.py -v
```

Expected: `ModuleNotFoundError: No module named 'projection_math'` (4 errors).

### Task A.2: Implement minimal `projection_math.py`

**Files:**
- Create: `deployments/er_projection/projection_math.py`

```python
"""Pure-math layer for OAK-D pixel→camera-frame back-projection.

No ROS dependencies. All inputs are plain numbers; all outputs are plain
numbers. This is the layer that has unit tests; the surrounding tool wires
this to depth lookups and TF transforms.
"""
from __future__ import annotations


def pixel_to_camera_frame(
    u: float, v: float, depth: float,
    fx: float, fy: float, cx: float, cy: float,
) -> tuple[float, float, float]:
    """Back-project a pixel coordinate + depth to a 3D point in the camera
    optical frame using the standard pinhole model.

    The optical frame convention (ROS REP-103): +X right, +Y down, +Z forward.
    A pixel at (cx, cy) with depth d back-projects to (0, 0, d).

    Returns (X, Y, Z) in meters. If depth == 0 (no depth return), returns
    the origin — caller should reject this case.
    """
    if depth <= 0.0:
        return (0.0, 0.0, 0.0)
    z = float(depth)
    x = (float(u) - cx) * z / fx
    y = (float(v) - cy) * z / fy
    return (x, y, z)
```

**Step 2: Run tests to verify all pass**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/er_projection/
python3 -m pytest test_projection_math.py -v
```

Expected: 4 PASSED.

**Step 3: Commit on Jetson side** (this code lives on Jetson eventually, deploying happens later — for now just keep the staging files locally)

No commit yet, this is staging.

### Task A.3: Add depth-lookup helper with tests

**Why:** The tool reads the depth value at a specific pixel from a depth image (a 2D numpy array of mm or m). Need to extract the value safely with bounds checking.

**Files:**
- Modify: `deployments/er_projection/test_projection_math.py` (append)

```python
import numpy as np

from projection_math import sample_depth_at_pixel


def test_sample_depth_returns_value_in_meters():
    # 2x2 depth image in millimeters: [[1000, 2000], [3000, 4000]]
    depth_mm = np.array([[1000, 2000], [3000, 4000]], dtype=np.uint16)
    val = sample_depth_at_pixel(depth_mm, u=1, v=0, encoding="16UC1")
    assert val == pytest.approx(2.0)  # 2000mm = 2.0m


def test_sample_depth_meters_encoding():
    depth_m = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    val = sample_depth_at_pixel(depth_m, u=1, v=1, encoding="32FC1")
    assert val == pytest.approx(4.0)


def test_sample_depth_out_of_bounds_returns_zero():
    depth = np.zeros((2, 2), dtype=np.uint16)
    assert sample_depth_at_pixel(depth, u=5, v=5, encoding="16UC1") == 0.0
    assert sample_depth_at_pixel(depth, u=-1, v=0, encoding="16UC1") == 0.0


def test_sample_depth_handles_3x3_neighborhood_average():
    # A robust sample uses a small NxN median to handle depth noise. Verify
    # the helper supports this when window > 1.
    depth = np.array([[100, 200, 300],
                      [400, 0, 600],
                      [700, 800, 900]], dtype=np.uint16)
    # Window=3 around center, ignoring 0 (no-return), median of 100..900 = 500
    val = sample_depth_at_pixel(depth, u=1, v=1, encoding="16UC1", window=3)
    assert val == pytest.approx(0.500)  # 500mm
```

**Step 1: Run to verify failure**

```bash
python3 -m pytest test_projection_math.py -v
```

Expected: 4 new tests fail with `AttributeError: module 'projection_math' has no attribute 'sample_depth_at_pixel'`.

### Task A.4: Implement `sample_depth_at_pixel`

**Files:**
- Modify: `deployments/er_projection/projection_math.py` (append)

```python
import numpy as np


def sample_depth_at_pixel(
    depth_image: np.ndarray,
    u: int, v: int,
    encoding: str,
    window: int = 1,
) -> float:
    """Read depth at a pixel, returning meters.

    encoding:
      - "16UC1": uint16 millimeters (Luxonis OAK-D default)
      - "32FC1": float32 meters
    window:
      - 1: single-pixel read
      - >1: median over an NxN neighborhood, ignoring zero (no-return) pixels.
        Robust against per-pixel depth glitches.

    Out-of-bounds (u, v) → 0.0.
    """
    h, w = depth_image.shape[:2]
    if u < 0 or v < 0 or u >= w or v >= h:
        return 0.0

    half = window // 2
    u0 = max(0, u - half); u1 = min(w, u + half + 1)
    v0 = max(0, v - half); v1 = min(h, v + half + 1)
    patch = depth_image[v0:v1, u0:u1].astype(np.float64)

    if encoding == "16UC1":
        scale = 0.001  # mm → m
    elif encoding == "32FC1":
        scale = 1.0
    else:
        raise ValueError(f"Unknown depth encoding: {encoding}")

    if window == 1:
        return float(patch.flat[0]) * scale

    valid = patch[patch > 0]
    if valid.size == 0:
        return 0.0
    return float(np.median(valid)) * scale
```

**Step 2: Run tests to verify all pass**

```bash
python3 -m pytest test_projection_math.py -v
```

Expected: 8 PASSED.

### Task A.5: Add quaternion + TF helper for camera→map transform

**Why:** Once we have the 3D point in camera frame, we transform it through `oak_optical_frame → base_link → map`. This math is also testable in isolation if we provide the transforms as 4x4 matrices.

**Files:**
- Modify: `deployments/er_projection/test_projection_math.py` (append)

```python
from projection_math import transform_point_to_frame


def test_identity_transform_is_noop():
    # 4x4 identity + point (1,2,3) → (1,2,3)
    T = np.eye(4)
    out = transform_point_to_frame(point=(1.0, 2.0, 3.0), transform=T)
    assert out == pytest.approx((1.0, 2.0, 3.0))


def test_translation_only_adds_translation():
    T = np.eye(4)
    T[0:3, 3] = [10.0, 20.0, 30.0]
    out = transform_point_to_frame(point=(1.0, 2.0, 3.0), transform=T)
    assert out == pytest.approx((11.0, 22.0, 33.0))


def test_90deg_yaw_rotates_point():
    # 90° yaw around Z: x,y → -y,x
    T = np.eye(4)
    T[0:3, 0:3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    out = transform_point_to_frame(point=(1.0, 0.0, 0.0), transform=T)
    assert out[0] == pytest.approx(0.0, abs=1e-9)
    assert out[1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(0.0)
```

**Step 1: Run to verify failure**

```bash
python3 -m pytest test_projection_math.py -v
```

Expected: 3 new tests fail.

### Task A.6: Implement `transform_point_to_frame`

**Files:**
- Modify: `deployments/er_projection/projection_math.py` (append)

```python
def transform_point_to_frame(
    point: tuple[float, float, float],
    transform: np.ndarray,
) -> tuple[float, float, float]:
    """Apply a 4x4 homogeneous transform to a 3D point.

    The transform is the pose of the SOURCE frame in the TARGET frame —
    i.e., to transform a point from camera to map, pass the transform from
    map to camera (the message TF returns from lookup_transform(target='map',
    source='oak_optical_frame')).
    """
    p = np.array([point[0], point[1], point[2], 1.0])
    out = transform @ p
    return (float(out[0]), float(out[1]), float(out[2]))
```

**Step 2: Verify all 11 math tests pass**

```bash
python3 -m pytest test_projection_math.py -v
```

Expected: 11 PASSED.

**Step 3: Commit-equivalent (file timestamp)**

Note in the plan execution log that math layer is complete. No git here (BlackBox project not a git repo).

---

## Phase B: ROS2 wiring (the tool body)

### Task B.1: Recon — find the tool descriptor pattern

**Why:** Phase 0 found the registry, but we need to see one example of an existing tool to match the pattern.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'find /home/jetson/ugv_ws_waveshare/ugv_tools_api -name "*.py" -path "*/tools/*" | head -10'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'find /home/jetson/ugv_ws_waveshare/ugv_tools_api -name "*.py" | xargs grep -l "ToolDescriptor\|@register_tool\|register_tool" 2>/dev/null | head -5'
```

Pick an existing simple tool (e.g., `status_get_pose`) and read it to mirror the pattern.

**Deliverable:** Note the descriptor schema and registration path.

### Task B.2: Create `project_pixel_to_map_node.py` skeleton

**Files:**
- Create: `deployments/er_projection/project_pixel_to_map_node.py`

```python
#!/usr/bin/env python3
"""project_pixel_to_map — a ROS2 component that exposes a pixel-to-map
projection service for the ER agent.

ER currently has to do mental geometry: pixel + depth + robot pose +
camera intrinsics + TF chain → world (x, y) coordinates. This is unreliable
in LLMs. This node deterministically performs the math and returns the
resulting map-frame point along with diagnostic flags about the cell
(in lethal? in inflation? distance to nearest wall?).

The node:
  - subscribes to /oak/rgb/camera_info (intrinsics)
  - subscribes to /oak/depth/image_raw (depth, aligned with RGB)
  - listens to TF (oak_optical_frame → base_link → odom → map)
  - subscribes to /global_costmap/costmap (for costmap diagnostics)
  - exposes service /project_pixel_to_map taking (u, v, frame, window)
    and returning (x, y, z, depth_m, near_wall_m, in_lethal, in_inflation)

Mounted in the ugv_tools_api package; the FastAPI tool layer wraps the
service so it shows up in the LLM's tool registry.
"""
from __future__ import annotations

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CameraInfo, Image
from nav_msgs.msg import OccupancyGrid
import tf2_ros
from tf2_ros import Buffer, TransformListener
from cv_bridge import CvBridge

# Re-import the pure-math helpers (will live in this same package on robot)
from .projection_math import (
    pixel_to_camera_frame,
    sample_depth_at_pixel,
    transform_point_to_frame,
)


class PixelToMapProjector(Node):
    def __init__(self) -> None:
        super().__init__("project_pixel_to_map")
        self.declare_parameter("camera_info_topic", "/oak/rgb/camera_info")
        self.declare_parameter("depth_topic", "/oak/depth/image_raw")
        self.declare_parameter("global_costmap_topic", "/global_costmap/costmap")
        self.declare_parameter("camera_optical_frame", "oak_optical_frame")
        self.declare_parameter("default_window", 5)

        self._intrinsics: CameraInfo | None = None
        self._latest_depth: Image | None = None
        self._latest_costmap: OccupancyGrid | None = None
        self._bridge = CvBridge()

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.create_subscription(
            CameraInfo,
            self.get_parameter("camera_info_topic").value,
            self._on_camera_info, sensor_qos)
        self.create_subscription(
            Image,
            self.get_parameter("depth_topic").value,
            self._on_depth, sensor_qos)
        self.create_subscription(
            OccupancyGrid,
            self.get_parameter("global_costmap_topic").value,
            self._on_costmap, 10)

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self.get_logger().info("project_pixel_to_map ready")

    def _on_camera_info(self, msg: CameraInfo) -> None:
        self._intrinsics = msg

    def _on_depth(self, msg: Image) -> None:
        self._latest_depth = msg

    def _on_costmap(self, msg: OccupancyGrid) -> None:
        self._latest_costmap = msg

    # API method exposed via FastAPI in the tool layer; pure-Python here for
    # ease of testing.
    def project(self, u: int, v: int, target_frame: str = "map",
                window: int = 0) -> dict:
        if self._intrinsics is None:
            return {"ok": False, "error": "no camera_info yet"}
        if self._latest_depth is None:
            return {"ok": False, "error": "no depth frame yet"}

        info = self._intrinsics
        fx, fy = info.k[0], info.k[4]
        cx, cy = info.k[2], info.k[5]

        if window <= 0:
            window = int(self.get_parameter("default_window").value)

        depth_image = self._bridge.imgmsg_to_cv2(self._latest_depth, desired_encoding="passthrough")
        depth_m = sample_depth_at_pixel(
            depth_image, u=u, v=v,
            encoding=self._latest_depth.encoding,
            window=window,
        )
        if depth_m <= 0.0 or depth_m > 10.0:
            return {"ok": False, "error": f"invalid depth at pixel: {depth_m:.3f}m"}

        x_c, y_c, z_c = pixel_to_camera_frame(
            u=u, v=v, depth=depth_m,
            fx=fx, fy=fy, cx=cx, cy=cy,
        )

        cam_frame = self.get_parameter("camera_optical_frame").value
        try:
            tx = self._tf_buffer.lookup_transform(
                target_frame, cam_frame, rclpy.time.Time())
        except (tf2_ros.LookupException, tf2_ros.ConnectivityException,
                tf2_ros.ExtrapolationException) as exc:
            return {"ok": False, "error": f"TF lookup failed: {exc}"}

        transform_mat = self._tf_to_matrix(tx)
        wx, wy, wz = transform_point_to_frame((x_c, y_c, z_c), transform_mat)

        diag = self._costmap_diagnostics(wx, wy)
        return {
            "ok": True,
            "frame": target_frame,
            "x": wx, "y": wy, "z": wz,
            "depth_m": depth_m,
            **diag,
        }

    def _tf_to_matrix(self, transform_stamped) -> np.ndarray:
        # Convert geometry_msgs/TransformStamped → 4x4 numpy
        t = transform_stamped.transform.translation
        q = transform_stamped.transform.rotation
        # Quaternion to rotation matrix
        x, y, z, w = q.x, q.y, q.z, q.w
        R = np.array([
            [1 - 2*(y*y + z*z), 2*(x*y - z*w),     2*(x*z + y*w)],
            [2*(x*y + z*w),     1 - 2*(x*x + z*z), 2*(y*z - x*w)],
            [2*(x*z - y*w),     2*(y*z + x*w),     1 - 2*(x*x + y*y)],
        ])
        T = np.eye(4)
        T[0:3, 0:3] = R
        T[0:3, 3] = [t.x, t.y, t.z]
        return T

    def _costmap_diagnostics(self, wx: float, wy: float) -> dict:
        if self._latest_costmap is None:
            return {"in_lethal": None, "in_inflation": None,
                    "near_wall_m": None}
        cm = self._latest_costmap
        ox, oy = cm.info.origin.position.x, cm.info.origin.position.y
        res = cm.info.resolution
        col = int((wx - ox) / res)
        row = int((wy - oy) / res)
        if col < 0 or col >= cm.info.width or row < 0 or row >= cm.info.height:
            return {"in_lethal": False, "in_inflation": False,
                    "near_wall_m": None, "outside_costmap": True}
        idx = row * cm.info.width + col
        cost = cm.data[idx]
        # -1 = unknown, 100 = lethal in standard occupancy grid
        return {
            "in_lethal": cost >= 99,
            "in_inflation": 50 <= cost < 99,
            "cost": int(cost),
            "outside_costmap": False,
        }


def main(args=None):
    rclpy.init(args=args)
    node = PixelToMapProjector()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
```

**Step 1: Validate Python syntax locally**

```bash
python3 -c "import ast; ast.parse(open('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/er_projection/project_pixel_to_map_node.py').read()); print('SYNTAX OK')"
```

Expected: `SYNTAX OK`.

### Task B.3: Deploy node to robot

**Action:**

```bash
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/er_projection/project_pixel_to_map_node.py jetson@192.168.1.155:/tmp/project_pixel_to_map_node.py
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/er_projection/projection_math.py jetson@192.168.1.155:/tmp/projection_math.py
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
TARGET=/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/projection
echo jetson | sudo -S mkdir -p $TARGET
echo jetson | sudo -S cp /tmp/project_pixel_to_map_node.py $TARGET/node.py
echo jetson | sudo -S cp /tmp/projection_math.py $TARGET/projection_math.py
echo jetson | sudo -S touch $TARGET/__init__.py
ls -la $TARGET/
'
```

Expected: 3 files in the projection package directory.

### Task B.4: Add node startup to `start_tools_api.sh`

**Files:**
- Modify: `/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh`

Locate the existing background-launch section (around line 41 where `waypoints_bridge` is launched) and add:

```bash
# Pixel→map projector for the ER agent's spatial grounding tool.
python3 -m ugv_tools_api.projection.node &
```

**Step 1: Apply via sed:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S sed -i "/python3 -m ugv_tools_api.nodes.waypoints_bridge &/a python3 -m ugv_tools_api.projection.node \&" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh
grep -A1 "waypoints_bridge" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh
'
```

Expected output: `python3 -m ugv_tools_api.nodes.waypoints_bridge &` followed by `python3 -m ugv_tools_api.projection.node &`.

### Task B.5: Restart and verify the node is alive

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S systemctl restart ugv-tools-api.service
sleep 15
echo jetson | sudo -S docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && ros2 node list | grep project_pixel_to_map"
'
```

Expected: `/project_pixel_to_map` listed.

**Rollback if it fails:** revert `start_tools_api.sh`, restart service. The new node starting wrong should NOT take down anything else.

### Task B.6: Smoke-test via direct Python

**Why:** Before wiring it to the LLM, exercise the projection method directly to confirm math is correct end-to-end with real OAK-D data.

**Action:** SSH into container, drop into Python REPL, instantiate (or attach to) the node, call `.project(320, 240)`, eyeball the result.

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
python3 -c \"
import rclpy
from ugv_tools_api.projection.node import PixelToMapProjector
rclpy.init()
n = PixelToMapProjector()
import time
end = time.time()+5
while time.time()<end:
    rclpy.spin_once(n, timeout_sec=0.1)
result = n.project(u=320, v=240, target_frame='map', window=5)
print('Center pixel projection:', result)
\"
"'
```

Expected: a result like `{'ok': True, 'frame': 'map', 'x': ..., 'y': ..., 'depth_m': ..., 'cost': ..., 'in_lethal': False}`. Sanity check: the (x, y) should plausibly be a few meters in front of the robot.

---

## Phase C: HTTP tool surface for the ER agent

### Task C.1: Add HTTP route + tool descriptor

**Why:** ER calls tools via the FastAPI surface. Need a new route that the ER's tool layer knows about.

**Files:**
- Modify: existing tools router (path determined in Task 0.1, e.g., `ugv_tools_api/ugv_tools_api/routes/tools.py`)
- Modify: `ugv_tools_api/ugv_tools_api/registry.py` (or wherever ToolDescriptor lives)

Add a Pydantic input model:

```python
class ProjectPixelArgs(BaseModel):
    pixel_u: int = Field(..., ge=0, description="Image column in OAK-D RGB (0..1280)")
    pixel_v: int = Field(..., ge=0, description="Image row in OAK-D RGB (0..720)")
    target_frame: str = Field("map", description="Output TF frame (default 'map')")
    window: int = Field(5, ge=1, le=11, description="Median-filter window in pixels for depth robustness")
```

Add the route:

```python
@router.post("/tool/project_pixel_to_map")
async def project_pixel_to_map(args: ProjectPixelArgs):
    """Back-project an OAK-D pixel to a map-frame coordinate.

    Use this BEFORE nav_goto_point when the model has identified an object
    in the OAK-D RGB image. Pass the pixel coordinates (u, v) of the
    target's center; the tool returns (x, y) in the map frame.
    """
    # The projector node lives in-process; expose via a singleton
    return _projector_singleton().project(
        u=args.pixel_u, v=args.pixel_v,
        target_frame=args.target_frame, window=args.window,
    )
```

Register in the tool descriptor list following the existing pattern (after recon Task 0.1 found it).

**Step 1:** edit locally, test syntax, deploy.

```bash
python3 -c "import ast; ast.parse(open('PATH').read()); print('SYNTAX OK')"
sshpass -p 'jetson' scp <local> jetson@192.168.1.155:/tmp/<file>
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /tmp/<file> /home/jetson/ugv_ws_waveshare/<dest>'
```

### Task C.2: Smoke-test the HTTP tool

```bash
curl -X POST http://192.168.1.155:8080/tool/project_pixel_to_map \
  -H "Content-Type: application/json" \
  -d '{"pixel_u": 320, "pixel_v": 240, "target_frame": "map", "window": 5}'
```

Expected: JSON with `{"ok": true, "frame": "map", "x": <number>, "y": <number>, ...}`. If `ok: false`, inspect the `error` field.

### Task C.3: Verify ER tool registry picks up the new tool

```bash
curl http://192.168.1.155:8080/tools?format=anthropic | python3 -m json.tool | grep -A2 project_pixel
```

Expected: `project_pixel_to_map` appears in the registry with its description and schema.

---

## Phase D: ER prompt update

### Task D.1: Read current ER system prompt section that mentions spatial reasoning

**File:** `/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/er/agent_loop.py`

Find the "## Spatial reasoning" section (around line 130 based on prior recon).

### Task D.2: Replace the mental-projection paragraph with tool-use instruction

**Files:**
- Modify: `/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/er/agent_loop.py`

**Old text (the part to replace):**

```
You are Gemini Robotics-ER 1.6 — you natively reason in 3D. Use that. When asked to
go to or look at something, locate it in the OAK-D RGB image, read its distance directly
from the OAK-D depth image at the same pixel coordinates, then cross-reference with the
local costmap (is the path clear at chassis level?) and the LiDAR bird's-eye (anything
beside or behind you the camera misses?).
```

**New text:**

```
You are Gemini Robotics-ER 1.6 — you natively reason in 3D, but you MUST use
the project_pixel_to_map tool for any nav target you derive from the OAK-D
view. Mental geometric reasoning from pixel + depth + pose is NOT reliable
across many calls; the projection tool is. Workflow:

  1. Identify the target object in the OAK-D RGB image
  2. Note its center pixel (u, v) — e.g., a chair centered at pixel (640, 380)
  3. Call project_pixel_to_map(pixel_u=640, pixel_v=380) — returns (x, y) in
     the map frame plus diagnostic flags (in_lethal, in_inflation, cost)
  4. If in_lethal=true, the projected target IS a wall — pick a different
     pixel slightly toward open floor in the same image, project again
  5. Pass the projected (x, y) directly to nav_goto_point(x, y)

Cross-check with the local costmap (is the path clear at chassis level?)
and the LiDAR bird's-eye (anything beside or behind you the camera misses?).
```

**Step 1:** edit via sed or via Python regex on the Jetson. Verify syntax with `python3 -c "import ast; ast.parse(...)"`.

### Task D.3: Restart ER service and confirm prompt update

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl restart ugv-er.service'
sleep 8
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S journalctl -u ugv-er.service --since "20 seconds ago" --no-pager | tail -10'
```

Expected: ER service restarts cleanly, no exceptions.

### Task D.4: End-to-end ER mission test

**Action:** Brandon issues a vision-grounded command via the supervisor, e.g., "Go to the chair." Watch the ER agent log:
- It should call `project_pixel_to_map` with a pixel
- The returned (x, y) should be near the actual chair location
- The subsequent `nav_goto_point(x, y)` should bring the robot to within ~0.5m of the target

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S journalctl -u ugv-er.service -f' &
# Wait for mission, then Ctrl-C
```

**Pass criteria:** ER calls the new tool. Robot drives close to the target.

**Rollback:** revert prompt change if ER misbehaves; the tool is harmless if unused.

---

## Phase E: Mission poller hook (Issue 3)

### Task E.1: Read current mission_poller.py

**File:** `/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/supervisor/mission_poller.py`

Identify the status-transition block (we know `self._last_status` exists from earlier recon — line ~109).

### Task E.2: Write a unit test for arrival callback

**Files:**
- Create: `deployments/er_projection/test_mission_poller_arrival.py`

```python
"""Test that the poller invokes an arrival callback exactly once on
navigating→succeeded transition, and never on other transitions."""

class FakeStatusFeed:
    def __init__(self, statuses): self._s = list(statuses); self._i = 0
    def next_status(self): s = self._s[self._i]; self._i += 1; return s


def test_arrival_callback_fires_once_on_succeeded():
    arrivals = []
    feed = FakeStatusFeed(["navigating", "navigating", "succeeded", "idle"])
    
    from mission_poller_test_shim import poll_with_callback
    poll_with_callback(feed, on_arrival=lambda: arrivals.append(True),
                       max_iters=4)
    
    assert len(arrivals) == 1


def test_no_arrival_on_aborted():
    arrivals = []
    feed = FakeStatusFeed(["navigating", "aborted", "idle"])
    from mission_poller_test_shim import poll_with_callback
    poll_with_callback(feed, on_arrival=lambda: arrivals.append(True), max_iters=3)
    assert arrivals == []


def test_double_succeeded_only_fires_once_per_mission():
    arrivals = []
    feed = FakeStatusFeed(["navigating", "succeeded", "succeeded"])
    from mission_poller_test_shim import poll_with_callback
    poll_with_callback(feed, on_arrival=lambda: arrivals.append(True), max_iters=3)
    assert len(arrivals) == 1
```

### Task E.3: Implement test shim that mirrors the poller logic

```python
# deployments/er_projection/mission_poller_test_shim.py
def poll_with_callback(feed, on_arrival, max_iters=10):
    last = None
    for _ in range(max_iters):
        cur = feed.next_status()
        if last == "navigating" and cur == "succeeded":
            on_arrival()
        last = cur
```

Run tests, verify pass.

### Task E.4: Apply equivalent logic to mission_poller.py

**File:** `/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/supervisor/mission_poller.py`

Add an `on_arrival` callback parameter and dispatch at the right transition. Save and restart `ugv-supervisor.service`.

---

## Phase F: Live session image injection

### Task F.1: Recon Live API

**Why:** Phase 0 noted the Live session class. Need to confirm the exact method to push a multimodal image-bearing user turn.

**Action:** Read the Gemini Live API docs (already in this conversation's memory) AND check `session.py` to see what's already wired.

### Task F.2: Add `inject_arrival_event` method

**File:** `/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/supervisor/session.py`

```python
async def inject_arrival_event(self, image_bytes: bytes, mime: str = "image/jpeg") -> None:
    """Push an OAK-D RGB snapshot into the Live session as a multimodal
    user turn, prompting Live to describe surroundings via TTS."""
    import google.genai.types as gtypes
    parts = [
        gtypes.Part.from_text(
            "Robot has reached its destination. Describe what you see in this "
            "image — your immediate surroundings — as if reporting to the "
            "operator. One sentence, plain spoken English."
        ),
        gtypes.Part.from_bytes(data=image_bytes, mime_type=mime),
    ]
    await self._live_session.send_client_content(
        turns=[gtypes.Content(role="user", parts=parts)]
    )
```

(Adjust to match the actual Live session SDK methods identified in Task F.1.)

### Task F.3: Wire poller to session

**File:** `mission_poller.py`

In the arrival callback created in Task E.4:

```python
async def _on_arrival(self) -> None:
    image_bytes = await self._fetch_oakd_snapshot()
    if image_bytes is None:
        return
    await self._session.inject_arrival_event(image_bytes)


async def _fetch_oakd_snapshot(self) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get("http://localhost:8080/snapshot/oakd")
            r.raise_for_status()
            return r.content
    except Exception:
        return None
```

### Task F.4: Smoke-test the injection path

Stub a successful nav (e.g., publish a manual `succeeded` to the relevant status topic) and verify the supervisor produces a TTS narration. If TTS speaks an image description, success.

Expected log line in `ugv-supervisor.service`: something like `arrival event sent to Live with N-byte image`.

---

## Phase G: End-to-end validation

### Task G.1: ER mission with project_pixel_to_map

Brandon issues "Go to the chair," watches:
- ER calls `project_pixel_to_map`
- (x, y) is plausible
- Robot drives to within tolerance
- On arrival, Live narrates surroundings

### Task G.2: Multi-waypoint mission

Brandon sends a 4-waypoint path through Vizanti, watches:
- Robot drives waypoint to waypoint without 360 spins (already verified in earlier session)
- At EACH arrival, Live narrates briefly ("I see a workbench / I see a hallway / ...")

### Task G.3: Edge-case sweep

Test:
- ER calls projection at a depth-zero pixel → tool returns `ok: false`, error described
- ER calls projection at a wall → tool returns `in_lethal: true`, ER picks a different pixel
- Mission completes from `aborted` status → no Live narration (only success triggers)

### Task G.4: Snapshot the final state

Use `/chat/save` to mint a snapshot capturing:
- Both features landed
- Final files modified
- Test results
- Search hints

---

## Rollback Plans

### Issue 2 (Projection tool)
- Disable: comment out `python3 -m ugv_tools_api.projection.node &` in `start_tools_api.sh`, restart `ugv-tools-api.service`
- Revert prompt: restore the original "Spatial reasoning" paragraph in `agent_loop.py`, restart `ugv-er.service`
- Tool unused = harmless, even if left registered

### Issue 3 (Live arrival narration)
- Disable: set a parameter `arrival_narration: false` on the supervisor, OR comment out the `_on_arrival` callback wiring
- Restart `ugv-supervisor.service`
- ER's own narration is unaffected

---

## Out of Scope (do not do here)

- New camera hardware or OAK-D firmware changes
- Migrating ER to a different model
- Custom panoramic stitching for arrival narration (Phase A only — single-frame description)
- Coordination with the docking project from SNAP-20260505-6454 (separate effort)
- AMCL toggle button (separate effort)

---

## Search Hints

- 'project_pixel_to_map tool implementation'
- 'ER spatial grounding deterministic projection'
- 'Gemini Live arrival narration mission_poller'
- 'OAK-D depth back-projection TF camera_optical_frame map'
- 'cv_bridge CameraInfo intrinsics fx fy cx cy ROS2'

---

## Implementation Log

(Filled in as phases land)
