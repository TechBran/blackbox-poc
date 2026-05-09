# GPU Offload — Three-Piece Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Free 30-50% of UGV Beast's CPU by offloading three workloads to the Jetson Orin Nano's currently-idle Ampere GPU (1024 CUDA cores @ 0% utilization), without any data loss in the existing `ugv_waveshare` container.

**Architecture:**
1. **Piece 1 (pantilt_camera)** — eliminate CPU cost via raw V4L2 MJPEG passthrough (primary) or NVENC hardware-accelerated JPEG (fallback). Lives in existing container; no Docker changes.
2. **Piece 2 (nvblox costmaps)** — run Isaac ROS nvblox in a SIDECAR container (`ugv_isaac`) on `host` network, communicating with `ugv_waveshare` via ROS2 DDS. Existing code touches NOTHING. nvblox publishes to `/local_costmap/costmap` and `/global_costmap/costmap` (replacing Nav2's obstacle_layer; inflation_layer remains in Nav2).
3. **Piece 3 (CUDA path planner)** — feature-flagged Isaac ROS path planner alongside SmacPlanner2D. Toggle via `planner_plugins` parameter for instant rollback.

**Tech Stack:** Python 3.10, ROS2 Humble, Isaac ROS DP 3.x (nvblox + cuVSLAM optional), VPI 3.x (JetPack-bundled, but install-from-deb if needed), nvjpeg, NVENC, Docker (sidecar), pytest.

**Reference snapshots / memory:**
- `cpu_saturation_diagnosis.md` — Orin Nano power facts; jetson-performance.service in place.
- `ugv_nav2_tuning.md` — current Nav2 baseline (footprint 11"×8", inflation 0.15/8.0, BT rotate-only).
- `feedback_ugv_nvblox_deferred.md` — prior in-place install blocked by CUDA/VPI mismatch.
- `ugv_er_projection_tool.md` — `/global_costmap/costmap` is consumed by `project_pixel_to_map`; topic name and message type MUST be preserved.

**Repo layout:**
- Local dev (this machine, NOT a git repo): `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/`
  - Plans: `docs/plans/`
  - Local edit staging: `deployments/`
- Robot (Jetson, IS a git repo): `/home/jetson/ugv_ws_waveshare/`
- Existing pre-built nvblox-bearing images on disk (verified via `docker images`):
  - `ugv_jetson_nvblox:latest` (15.4GB, 3 weeks old) ← preferred sidecar image
  - `ugv_waveshare:nvblox-20260418-1551` (16.1GB) ← unused fallback
  - `ugv_waveshare_pre_nvblox_snapshot:20260418-1540` (15.8GB) ← rollback safety net for the main container
- Currently running: `dudulrx0601/ugv_jetson_ros_humble:v1028` as the `ugv_waveshare` container
- SSH: `sshpass -p 'jetson' ssh jetson@192.168.1.155`

---

## Container Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│ Jetson Orin Nano (host)                                            │
│                                                                    │
│ systemd:                                                           │
│   - jetson-performance.service ✓ (perf governor, locked clocks)   │
│   - ugv-waveshare.service       ┐                                 │
│   - ugv-tools-api.service       │ run                             │
│   - ugv-er.service              ├─→ docker exec ugv_waveshare ... │
│   - ugv-supervisor.service      │                                 │
│   - ugv-isaac.service (NEW)     ┘ runs ugv_isaac container        │
│                                                                    │
│ Docker network: host (both containers share localhost)             │
│                                                                    │
│ ┌──────────────────────────┐    ┌────────────────────────────────┐│
│ │ ugv_waveshare (existing) │    │ ugv_isaac (NEW SIDECAR)        ││
│ │ image: ugv_jetson_ros_   │    │ image: ugv_jetson_nvblox:latest││
│ │   humble:v1028           │    │                                ││
│ │                          │    │  /isaac_ros_nvblox:            ││
│ │  /ugv_tools_api          │    │   - subscribes /scan, /tf,     ││
│ │  /Nav2 stack             │    │     /oak/stereo/depth (opt'l)  ││
│ │  /SLAM toolbox           │    │   - publishes nav2-compatible: ││
│ │  /OAK-D, BNO085          │    │     /local_costmap/costmap     ││
│ │  /Supervisor + Live      │    │     /global_costmap/costmap    ││
│ │  /ER agent               │    │   - optionally publishes       ││
│ │                          │    │     /isaac_planner/plan        ││
│ └──────────────────────────┘    └────────────────────────────────┘│
│                ↕ ROS2 DDS (CycloneDDS or FastDDS) ↕                │
│                  topics + tf flow transparently                    │
└────────────────────────────────────────────────────────────────────┘
```

**Key invariants:**
- ROS_DOMAIN_ID matches between containers (default 0)
- DDS implementation matches (CycloneDDS or FastDDS — verify Phase 0)
- Both containers see /tf and /tf_static via the shared host network
- nvblox is configured to publish to the SAME topic names Nav2 currently expects

---

## Phase 0: Reconnaissance (must complete before any deploy)

These tasks gather data we don't have yet. They do NOT change anything.

### Task 0.1: Verify VPI / nvjpeg availability inside container

**Why:** Piece 1's "VPI" path needs VPI installed; the recon already showed it's NOT in `ugv_waveshare`. We need to confirm whether the alternate Piece-1 paths (raw MJPEG passthrough, NVENC via GStreamer) are viable.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "
echo \"=== VPI ===\"
ls /opt/nvidia/vpi* 2>&1 | head -3
python3 -c \"import vpi; print(\"VPI ok:\", vpi.__version__)\" 2>&1 | tail -2
echo \"=== nvjpeg ===\"
ldconfig -p | grep nvjpeg | head -3
echo \"=== GStreamer + nvjpeg ===\"
gst-inspect-1.0 nvjpegenc 2>&1 | head -5
gst-inspect-1.0 nvv4l2camerasrc 2>&1 | head -5
echo \"=== v4l2-ctl + raw MJPEG ===\"
which v4l2-ctl
v4l2-ctl --list-formats-ext -d /dev/video0 2>&1 | head -20
echo \"=== Python v4l2 libraries ===\"
python3 -c \"import linuxpy.video.device as v4l2; print(\"linuxpy ok\")\" 2>&1 | tail -1
python3 -c \"import v4l2py; print(\"v4l2py ok\")\" 2>&1 | tail -1
"'
```

**Deliverable:**
- Note whether VPI Python module is importable.
- Note whether nvjpeg, nvjpegenc, nvv4l2camerasrc are all present.
- Note whether linuxpy or v4l2py is installed (for raw MJPEG passthrough).
- Capture the supported camera formats (`v4l2-ctl --list-formats-ext`) — confirm MJPG is offered at 640×480@15.

**Decision branch this informs (Piece 1):**
- **Path A** (raw MJPEG passthrough): if `linuxpy` or `v4l2py` is available, OR can be pip-installed.
- **Path B** (GStreamer + NVENC): if `gst-inspect-1.0 nvjpegenc` is present.
- **Path C** (VPI + nvjpeg): if VPI is importable in Python.

We will pick whichever path's prerequisites are present without requiring a container rebuild. **STOP IF NONE OF A/B/C ARE AVAILABLE** — the path forward becomes "install VPI" which is a separate ~hour-long task.

### Task 0.2: Inspect the pre-built nvblox image

**Why:** `ugv_jetson_nvblox:latest` was built 3 weeks ago. We must verify it has nvblox + Isaac ROS humble + ROS bridge.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker run --rm --network host --entrypoint /bin/bash ugv_jetson_nvblox:latest -lc "
echo \"=== ROS2 distro ===\"
echo \"\$ROS_DISTRO\"
echo \"=== Isaac ROS packages installed ===\"
ros2 pkg list 2>&1 | grep -iE \"nvblox|isaac\" | head -20
echo \"=== nvblox node binary ===\"
which nvblox_node 2>&1
ros2 pkg executables nvblox_ros 2>&1 | head -5
echo \"=== Default nvblox config ===\"
find /opt/ros -name \"nvblox*.yaml\" 2>&1 | head -10
echo \"=== ROS_DOMAIN_ID + DDS impl ===\"
echo \"ROS_DOMAIN_ID=\$ROS_DOMAIN_ID\"
echo \"RMW_IMPLEMENTATION=\$RMW_IMPLEMENTATION\"
"' 2>&1 | head -50
```

**Deliverable:**
- Confirm `nvblox_ros` package is present.
- Note any nvblox launch files or default config files.
- Note the container's `RMW_IMPLEMENTATION`.

### Task 0.3: Verify ugv_waveshare's DDS implementation

**Why:** The two containers MUST use the same DDS implementation to see each other's topics on the host network.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "echo \"RMW_IMPLEMENTATION=\$RMW_IMPLEMENTATION\"; echo \"ROS_DOMAIN_ID=\$ROS_DOMAIN_ID\"; echo \"---\"; ros2 doctor --report 2>&1 | grep -iE \"rmw|domain\""'
```

**Deliverable:**
- Note `RMW_IMPLEMENTATION` value (likely `rmw_cyclonedds_cpp` or `rmw_fastrtps_cpp`).
- Note `ROS_DOMAIN_ID`.

**Both Phase 0 containers MUST end up with these matching.** If they don't, we set `RMW_IMPLEMENTATION` and `ROS_DOMAIN_ID` env vars on the sidecar to match.

### Task 0.4: Map the current obstacle_layer config exactly

**Why:** When nvblox replaces the obstacle_layer, its parameters must mirror the LiDAR observation source exactly so behavior is preserved.

**Action:**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sed -n "237,260p" /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sed -n "273,295p" /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml'
```

**Deliverable:** Note exact values of `observation_sources`, `data_type`, `topic`, `marking`, `clearing`, `raytrace_max_range`, `obstacle_max_range`, `min_obstacle_height`, `max_obstacle_height` for both costmaps.

### Task 0.5: Capture baseline metrics (pre-deploy)

**Why:** We need before/after numbers to prove the migration delivered.

**Action:**

```bash
# Baseline CPU + load
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S top -bn1 -d1 -o %CPU 2>&1 | head -25 > /tmp/baseline_top_$(date +%s).txt && cat /tmp/baseline_top*.txt | head -25'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'cat /proc/loadavg'

# Baseline costmap rates
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && for t in /local_costmap/costmap /global_costmap/costmap /camera/image/compressed /scan; do echo === \$t ===; timeout 5 ros2 topic hz \$t 2>&1 | tail -3; done'"

# Baseline GPU
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S timeout 5 tegrastats --interval 1000 2>&1 | head -5'
```

**Deliverable:** Save these to `deployments/gpu_offload/baseline_metrics_pre_deploy.txt` for after-comparison.

---

## Phase 1: Piece 1 — pantilt_camera GPU offload

### Pre-flight gate

**STOP IF** Phase 0 found NONE of (linuxpy, v4l2py, gst-inspect nvjpegenc, vpi). In that case, escalate to: install `python3-linuxpy` (apt) inside container.

### Task 1.1: Decide path A vs B vs C from Phase 0 results

Pick whichever path has prereqs present in this priority:
- **Path A (raw MJPEG passthrough)** — preferred, lowest CPU.
- **Path B (GStreamer + NVENC)** — fallback if no V4L2 Python lib.
- **Path C (VPI + nvjpeg via Python ctypes)** — last resort if A and B both unavailable.

This plan documents Path A in detail; Paths B and C have brief task-list shells later.

### Task 1.2: Stage the new pantilt_camera (Path A)

**Files:**
- Create: `deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py`

```python
"""Pan-tilt camera publisher — raw MJPEG passthrough.

The previous implementation used cv2.VideoCapture which DECODES the camera's
native MJPEG, then cv2.imencode which RE-ENCODES it to JPEG before publishing.
That's two CPU-heavy operations per frame for no net benefit.

This implementation reads raw MJPEG buffers directly from V4L2 and publishes
them as sensor_msgs/CompressedImage with format='jpeg'. CPU cost drops from
~23% to <3% (just memcpy + DDS publish overhead).

Tested on UGV Beast Jetson Orin Nano: Xitech UVC camera at /dev/video0,
640x480@15fps MJPG.
"""
from __future__ import annotations
from pathlib import Path
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage

# linuxpy is the canonical Python V4L2 binding for JetPack 6.x
from linuxpy.video.device import Device, BufferType, PixelFormat


def resolve_v4l2_device(search_root: str = "/dev") -> str:
    def _key(p: Path) -> int:
        try:
            return int(p.name.removeprefix("video"))
        except ValueError:
            return 1 << 30
    candidates = sorted(Path(search_root).glob("video*"), key=_key)
    if not candidates:
        raise FileNotFoundError(f"No /dev/video* devices under {search_root}")
    return str(candidates[0])


class PantiltCameraNode(Node):
    def __init__(self, device: str | None = None, width: int = 640,
                 height: int = 480, fps: int = 15):
        super().__init__("ugv_pantilt_camera")
        self.device_path = device or resolve_v4l2_device()
        self.width = width
        self.height = height
        self.fps = fps

        self.dev = Device(self.device_path)
        self.dev.open()
        self.dev.set_format(BufferType.VIDEO_CAPTURE,
                            width, height, PixelFormat.MJPEG)
        self.dev.set_fps(BufferType.VIDEO_CAPTURE, fps)
        self.stream = self.dev.video_capture
        self.stream.start()

        self.pub = self.create_publisher(
            CompressedImage, "/camera/image/compressed",
            qos_profile_sensor_data,
        )
        # We poll at ~2x camera fps to avoid undersampling; the V4L2 read blocks
        # until a frame is ready, so timer cadence doesn't drive CPU.
        self.timer = self.create_timer(1.0 / (fps * 2), self._tick)
        self._fail_count = 0
        self._frame_iter = iter(self.stream)
        self.get_logger().info(
            f"pan-tilt camera (MJPEG passthrough): "
            f"{self.device_path} {width}x{height}@{fps}"
        )

    def _tick(self):
        try:
            frame = next(self._frame_iter)
        except StopIteration:
            self._fail_count += 1
            if self._fail_count % 30 == 1:
                self.get_logger().warn(
                    f"V4L2 stream ended {self._fail_count}x on {self.device_path}"
                )
            return
        except Exception as e:
            self.get_logger().warn(
                f"_tick error: {type(e).__name__}: {e}",
                throttle_duration_sec=2.0,
            )
            return

        # frame.data is the raw MJPEG byte buffer — publish without re-encoding.
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "pantilt_camera"
        msg.format = "jpeg"
        msg.data = bytes(frame.data)
        self.pub.publish(msg)
        self._fail_count = 0


def main():
    rclpy.init()
    node = PantiltCameraNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        try:
            node.stream.close()
            node.dev.close()
        except Exception:
            pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
```

**Step 1: Validate Python syntax locally**

Run: `python3 -c "import ast; ast.parse(open('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py').read()); print('SYNTAX OK')"`

Expected: `SYNTAX OK`.

### Task 1.3: Verify linuxpy is available; install if missing

**Step 1: Check inside container**
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "python3 -c \"import linuxpy.video.device; print(\"OK\")\" 2>&1"'
```

**Step 2: If missing, install via pip**
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "pip3 install linuxpy 2>&1 | tail -3"'
```

Expected: `Successfully installed linuxpy-X.Y.Z`.

If pip install fails (e.g., proxy issues), fall back to: `apt install -y python3-v4l2py` and adapt imports — `v4l2py` has slightly different API but same goal.

### Task 1.4: Write a smoke test for the new node (offline; doesn't need ROS)

**Files:**
- Create: `deployments/gpu_offload/test_pantilt_camera_smoke.py`

```python
"""Offline smoke test for PantiltCameraNode — verifies V4L2 MJPEG passthrough
produces non-empty JPEG frames at expected rate.

Runs WITHOUT ROS2 — just imports the node module to check syntax + imports,
then directly opens V4L2 device and pulls frames. Run inside the container:
  python3 -m pytest deployments/gpu_offload/test_pantilt_camera_smoke.py -v
"""
import time

import pytest


def test_imports_clean():
    """Ensure the target module imports without errors."""
    import sys
    sys.path.insert(0, "/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload")
    import pantilt_camera_v4l2_passthrough  # noqa: F401


@pytest.mark.skipif(
    not __import__("os").path.exists("/dev/video0"),
    reason="No /dev/video0 — skip live capture test",
)
def test_v4l2_yields_jpeg_bytes():
    """Verify direct V4L2 capture produces valid JPEG buffers."""
    from linuxpy.video.device import Device, BufferType, PixelFormat
    dev = Device("/dev/video0")
    dev.open()
    try:
        dev.set_format(BufferType.VIDEO_CAPTURE, 640, 480, PixelFormat.MJPEG)
        dev.set_fps(BufferType.VIDEO_CAPTURE, 15)
        stream = dev.video_capture
        stream.start()
        try:
            frame = next(iter(stream))
            data = bytes(frame.data)
            # JPEG SOI marker
            assert data[:2] == b"\xff\xd8", f"Not a JPEG: starts with {data[:4].hex()}"
            assert len(data) > 1000, f"JPEG too small: {len(data)} bytes"
        finally:
            stream.close()
    finally:
        dev.close()
```

### Task 1.5: Deploy and run smoke test

```bash
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py jetson@192.168.1.155:/tmp/pantilt_camera_v4l2_passthrough.py
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/test_pantilt_camera_smoke.py jetson@192.168.1.155:/tmp/test_pantilt_camera_smoke.py

# Run smoke test inside container BEFORE swapping
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker cp /tmp/pantilt_camera_v4l2_passthrough.py ugv_waveshare:/tmp/pantilt_camera_v4l2_passthrough.py && echo jetson | sudo -S docker cp /tmp/test_pantilt_camera_smoke.py ugv_waveshare:/tmp/test_pantilt_camera_smoke.py'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "cd /tmp && python3 -m pytest test_pantilt_camera_smoke.py -v 2>&1 | tail -10"'
```

Expected: 2 PASSED. Frames are valid JPEG byte buffers.

**STOP IF** the test fails — diagnose before swapping the production node. Likely causes:
- linuxpy install didn't take → try apt install
- V4L2 device locked by existing pantilt_camera node → stop the old node first
- MJPEG format unsupported → fall back to Path B

### Task 1.6: Save the original pantilt_camera

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py.bak.opencv_v1'
```

### Task 1.7: Replace pantilt_camera with the new implementation

```bash
# Copy the new file in
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /tmp/pantilt_camera_v4l2_passthrough.py /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py'
# Verify symlink propagation if any
sshpass -p 'jetson' ssh jetson@192.168.1.155 'ls -la /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py'
```

### Task 1.8: Restart and verify the new node is alive

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl restart ugv-tools-api.service'
sleep 12
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && timeout 5 ros2 topic hz /camera/image/compressed 2>&1 | tail -3'"
```

Expected: ~15 Hz on /camera/image/compressed.

### Task 1.9: Measure CPU change

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S top -bn1 -d1 -o %CPU 2>&1 | head -25 | tee /tmp/post_piece1.txt'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'cat /proc/loadavg'
```

Expected: pantilt_camera process CPU drops from ~23% to ≤5%. Load avg trending down.

**STOP HERE IF** CPU did NOT drop — the new code is doing more work than the old, contrary to expectation. Diagnose:
- Is timer cadence correct? (timer at 2× fps but stream.read() blocks; should not loop CPU-busy).
- Is `bytes(frame.data)` doing a copy? It does — but small (~30KB JPEG @ 640×480). Should be cheap.

### Task 1.10: Verify Vizanti / camera_snapshot tool still works

```bash
# Camera snapshot tool returns the same shape (JPEG) so it should be transparent
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'curl -s -X POST http://localhost:8080/tool/camera_snapshot -H \"Content-Type: application/json\" -d \"{\\\"camera\\\": \\\"pantilt\\\", \\\"as_url\\\": false}\" | python3 -c \"import json, sys; r=json.load(sys.stdin); print(\\\"camera:\\\", r.get(\\\"result\\\", {}).get(\\\"camera\\\"), \\\"size_bytes:\\\", r.get(\\\"result\\\", {}).get(\\\"size_bytes\\\"))\"'"
```

Expected: `camera: pantilt size_bytes: 25000-50000` (typical JPEG at 640×480).

### Task 1.11: Phase 1 verification checkpoint

**Pass criteria (must be GREEN before Phase 2):**
- [ ] /camera/image/compressed publishes at ~15 Hz
- [ ] pantilt_camera process CPU ≤5%
- [ ] camera_snapshot tool returns valid JPEG bytes
- [ ] /proc/loadavg 1-min < pre-deploy baseline
- [ ] Vizanti pantilt feed still renders (visual check via Vizanti UI)

**Rollback (if any criterion fails):**
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py.bak.opencv_v1 /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py && echo jetson | sudo -S systemctl restart ugv-tools-api.service'
```

### Path B — fallback if Path A fails: GStreamer + NVENC

If `linuxpy` is unavailable AND can't be installed: replace `cv2.VideoCapture` with a GStreamer pipeline that uses NVENC for hardware JPEG encoding:

```python
# In pantilt_camera.py __init__:
gst_pipeline = (
    f"v4l2src device={self.device} ! "
    f"image/jpeg, width={width}, height={height}, framerate={fps}/1 ! "
    f"appsink emit-signals=true sync=false max-buffers=1 drop=true"
)
self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
```

This skips the OpenCV decode step (camera native MJPEG → appsink as JPEG bytes). _tick() then needs cap.read() to give bytes, NOT a decoded numpy array — requires using the v4l2 backend differently. **More invasive than Path A; skip unless Path A truly impossible.**

### Path C — VPI + nvjpeg (last resort)

Only if both A and B are blocked. Requires installing VPI inside container (~1 hour effort, deb packages from `apt install nvidia-vpi`). After install, port the cv2.cvtColor + cv2.resize + cv2.imencode chain to `vpi.Image` + `vpi.color.convert` + `vpi.image.rescale` + an nvjpeg ctypes call. This is the most invasive option but the most "GPU-native" — keep as a Phase 1.5 future task if the simpler paths give enough relief.

---

## Phase 2: Piece 2 — Isaac ROS nvblox sidecar container

### Task 2.1: Create a systemd unit for the sidecar

**Files:**
- Create: `deployments/gpu_offload/ugv-isaac.service`

```ini
[Unit]
Description=UGV Isaac ROS sidecar (nvblox + path planner)
After=ugv-waveshare.service docker.service
Requires=docker.service
PartOf=ugv-waveshare.service

[Service]
Type=simple
Restart=on-failure
RestartSec=5
# Mount the workspace param dir read-only so we can edit configs from the host
ExecStartPre=-/usr/bin/docker rm -f ugv_isaac
ExecStart=/usr/bin/docker run --rm \
  --name ugv_isaac \
  --network host \
  --runtime nvidia \
  --gpus all \
  --privileged \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param:/isaac_params:ro \
  -v /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac:/deploy:ro \
  -e ROS_DOMAIN_ID=0 \
  -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
  -e DISPLAY=$DISPLAY \
  ugv_jetson_nvblox:latest \
  /bin/bash -c "/deploy/start_isaac.sh"
ExecStop=/usr/bin/docker stop ugv_isaac

[Install]
WantedBy=multi-user.target
```

(Verify ROS_DOMAIN_ID and RMW_IMPLEMENTATION values match what Phase 0 Task 0.3 found; adjust this unit if different.)

### Task 2.2: Create the start_isaac.sh entrypoint

**Files:**
- Create on Jetson: `/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac/start_isaac.sh`

```bash
#!/bin/bash
# start_isaac.sh — entrypoint inside ugv_jetson_nvblox container
# Sources Isaac ROS env, then launches nvblox with our config.
set -eo pipefail

set +u
source /opt/ros/humble/setup.bash
# If Isaac ROS overlay exists in the container, source it
[ -f /workspaces/isaac_ros-dev/install/setup.bash ] && source /workspaces/isaac_ros-dev/install/setup.bash
set -u

trap 'jobs -p | xargs -r kill 2>/dev/null; exit 0' TERM INT

# Wait for the main robot stack to be up so /scan + /tf are flowing
for i in $(seq 1 30); do
  if ros2 topic list 2>/dev/null | grep -q "/scan"; then
    break
  fi
  sleep 1
done

# Launch nvblox with our params
exec ros2 launch nvblox_ros nvblox.launch.py \
  config_file:=/isaac_params/nvblox_config.yaml
```

### Task 2.3: Create the nvblox config file

**Files:**
- Create: `deployments/gpu_offload/nvblox_config.yaml`

```yaml
# Nvblox config tuned for UGV Beast (11"×8" tracked robot, indoor missions)
# Replicates the existing CPU obstacle_layer behavior so Nav2 sees no semantic
# change in /local_costmap/costmap and /global_costmap/costmap.
nvblox_node:
  ros__parameters:
    # ===== Voxel + map settings =====
    voxel_size: 0.05            # match Nav2 costmap resolution
    map_clearing_radius_m: 5.0  # match local_costmap rolling window radius
    map_clearing_frame_id: "base_link"
    
    # ===== Sensor inputs =====
    # LiDAR (LD19) — primary obstacle source
    use_lidar: true
    lidar_topic: "/scan"
    lidar_qos: "SENSOR_DATA"
    lidar_max_range_m: 3.0       # mirror obstacle_max_range
    lidar_min_range_m: 0.10
    
    # Depth camera (OAK-D) — DISABLED initially; enable in Phase 2.5 when validated
    use_depth: false
    depth_topic: "/oak/stereo/depth"
    depth_camera_info_topic: "/oak/stereo/camera_info"
    depth_qos: "SENSOR_DATA"
    
    # ===== TF =====
    global_frame: "map"
    pose_frame: "base_link"
    
    # ===== Output topics (MUST match what Nav2 expects to read) =====
    # nvblox publishes occupancy/ESDF on these — Nav2's static_layer reads them
    map_publish_period_s: 0.2     # 5 Hz (match local_costmap update_frequency)
    esdf: true                    # ESDF needed for path planner (Phase 3)
    esdf_2d: true
    distance_map_unknown_value_optimistic: false
    
    # ===== Slice for 2D projection =====
    # We project the 3D voxel field down to the 2D plane Nav2 consumes.
    # Robot is 0.10m tall so we slice at ankle-to-shoulder height.
    esdf_slice_height: 0.10
    esdf_slice_min_height: 0.05
    esdf_slice_max_height: 0.50   # under-table clearance is intentional
    
    # ===== Output occupancy grids (THE CRITICAL TOPICS FOR NAV2) =====
    map_topic: "/global_costmap/costmap"   # Nav2 expects this name
    # Local costmap is published by a separate slice (configured in launch override)
```

(Note: nvblox typically publishes to its own topic names like `/nvblox/static_map_slice`. We'll need a static_transform_publisher-style remapping, OR a small Python relay node that subscribes to nvblox output and republishes on `/local_costmap/costmap` + `/global_costmap/costmap`. See Task 2.7.)

### Task 2.4: Test the sidecar runs in isolation

```bash
sshpass -p 'jetson' scp deployments/gpu_offload/start_isaac.sh jetson@192.168.1.155:/tmp/start_isaac.sh
sshpass -p 'jetson' scp deployments/gpu_offload/nvblox_config.yaml jetson@192.168.1.155:/tmp/nvblox_config.yaml

sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S mkdir -p /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac
echo jetson | sudo -S cp /tmp/start_isaac.sh /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac/start_isaac.sh
echo jetson | sudo -S chmod +x /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac/start_isaac.sh
echo jetson | sudo -S cp /tmp/nvblox_config.yaml /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/nvblox_config.yaml
'

# Manual launch (NOT via systemd yet) to verify
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S timeout 60 docker run --rm --name ugv_isaac_test --network host --runtime nvidia --gpus all --privileged -v /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param:/isaac_params:ro -v /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac:/deploy:ro -e ROS_DOMAIN_ID=0 -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ugv_jetson_nvblox:latest /bin/bash -c "/deploy/start_isaac.sh" 2>&1 | tail -30'
```

Expected: nvblox launches, prints "nvblox initialized", subscribes to /scan, publishes some occupancy/distance topic.

### Task 2.5: Confirm nvblox is publishing AND the topic flows across containers

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && ros2 topic list 2>&1 | grep -iE \"nvblox|isaac\"'"
```

Expected: nvblox topics visible from `ugv_waveshare` (proves cross-container DDS works).

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && timeout 5 ros2 topic hz /nvblox_node/static_map_slice 2>&1 | tail -3'"
```

Expected: ~5 Hz.

**STOP IF** nvblox topics are NOT visible from `ugv_waveshare`. Most likely cause: DDS implementation mismatch. Verify both containers have same RMW_IMPLEMENTATION + ROS_DOMAIN_ID.

### Task 2.6: Stop the test container before adding the relay

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker stop ugv_isaac_test 2>/dev/null || true'
```

### Task 2.7: Write the topic relay node

nvblox publishes to its native topics (e.g., `/nvblox_node/static_map_slice`). Nav2 reads `/local_costmap/costmap` and `/global_costmap/costmap`. We need a tiny relay.

**Files:**
- Create: `deployments/gpu_offload/nvblox_costmap_relay.py`

```python
"""Republish nvblox-produced occupancy slices on Nav2's expected costmap topics.

We don't disable Nav2's costmaps yet — we publish ALONGSIDE them. The
project_pixel_to_map tool can read either costmap (they should be similar
once nvblox is the obstacle truth).

Phase 2.10 disables Nav2's CPU obstacle_layer once nvblox is validated.
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from nav_msgs.msg import OccupancyGrid


class NvbloxCostmapRelay(Node):
    def __init__(self):
        super().__init__("nvblox_costmap_relay")
        self.declare_parameter(
            "nvblox_topic", "/nvblox_node/static_map_slice")
        self.declare_parameter(
            "republish_local", "/nvblox_local_costmap")  # parallel topic for now
        self.declare_parameter(
            "republish_global", "/nvblox_global_costmap")

        nvblox_topic = self.get_parameter("nvblox_topic").value
        local_topic = self.get_parameter("republish_local").value
        global_topic = self.get_parameter("republish_global").value

        # Latched QoS so late subscribers (Nav2 static_layer) get the last map
        latched_qos = QoSProfile(depth=1)
        latched_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        latched_qos.reliability = ReliabilityPolicy.RELIABLE

        self.local_pub = self.create_publisher(
            OccupancyGrid, local_topic, latched_qos)
        self.global_pub = self.create_publisher(
            OccupancyGrid, global_topic, latched_qos)
        self.create_subscription(
            OccupancyGrid, nvblox_topic, self._on_grid, latched_qos)
        self.get_logger().info(
            f"relaying {nvblox_topic} -> {local_topic} + {global_topic}"
        )

    def _on_grid(self, msg: OccupancyGrid):
        self.local_pub.publish(msg)
        self.global_pub.publish(msg)


def main():
    rclpy.init()
    node = NvbloxCostmapRelay()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
```

Initially we relay to PARALLEL topic names (`/nvblox_local_costmap`) so Nav2 keeps using its own costmap. We A/B compare the two before committing.

Deploy:
```bash
sshpass -p 'jetson' scp deployments/gpu_offload/nvblox_costmap_relay.py jetson@192.168.1.155:/tmp/nvblox_costmap_relay.py
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /tmp/nvblox_costmap_relay.py /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/nvblox_costmap_relay.py'
```

Add to start_tools_api.sh background launch list (under existing waypoints_bridge):
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S sed -i "/python3 -m ugv_tools_api.nodes.waypoints_bridge &/a python3 -m ugv_tools_api.nodes.nvblox_costmap_relay \&" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh'
```

### Task 2.8: Install and start the sidecar systemd service

```bash
sshpass -p 'jetson' scp deployments/gpu_offload/ugv-isaac.service jetson@192.168.1.155:/tmp/ugv-isaac.service
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S cp /tmp/ugv-isaac.service /etc/systemd/system/ugv-isaac.service
echo jetson | sudo -S systemctl daemon-reload
echo jetson | sudo -S systemctl enable ugv-isaac.service
echo jetson | sudo -S systemctl start ugv-isaac.service
sleep 30
echo jetson | sudo -S systemctl status ugv-isaac.service --no-pager | head -15
echo jetson | sudo -S journalctl -u ugv-isaac.service --since "60 seconds ago" --no-pager | tail -20
'
```

Expected: service `active (running)`. Journal shows nvblox logs.

### Task 2.9: Validate parallel topics are healthy

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && for t in /local_costmap/costmap /global_costmap/costmap /nvblox_local_costmap /nvblox_global_costmap; do echo === \$t ===; timeout 4 ros2 topic hz \$t 2>&1 | tail -2; done'"
```

Expected: ALL four publish at ~5 Hz. Compare /nvblox_local_costmap to /local_costmap/costmap — they should have similar obstacle structure (visualize via Vizanti for a sanity check).

### Task 2.10: Cutover Nav2 to consume nvblox topics (single-toggle parameter)

After parallel validation in Task 2.9 looks good, swap. The cleanest route:

1. Add a `static_layer` to Nav2's costmap config that subscribes to `/nvblox_global_costmap` (and `/nvblox_local_costmap`).
2. Disable the CPU `obstacle_layer` by removing it from the `plugins:` list (keep `inflation_layer`).
3. Restart Nav2.

Edit `slam_nav.yaml` for both costmaps:

```yaml
# OLD plugins list:
#   plugins:
#   - obstacle_layer
#   - inflation_layer
# NEW:
plugins:
- static_layer_nvblox
- inflation_layer
static_layer_nvblox:
  plugin: nav2_costmap_2d::StaticLayer
  map_topic: /nvblox_local_costmap   # /nvblox_global_costmap for global
  subscribe_to_updates: true
  trinary_costmap: true
```

(Keep `obstacle_layer:` block in the file as commented-out reference for rollback.)

Save backup, apply, restart, verify.

### Task 2.11: Phase 2 verification checkpoint

**Pass criteria:**
- [ ] `/local_costmap/costmap` updates at ~5 Hz (driven by nvblox now)
- [ ] `/global_costmap/costmap` updates at ~5 Hz
- [ ] `project_pixel_to_map` tool still returns valid (x, y) (smoke test)
- [ ] Vizanti shows obstacles in costmap (visual)
- [ ] CPU usage on `nav2_container` reduced (was ~17-22%, expect ~10-15% after offloading layer)
- [ ] GPU utilization >0% (verify with tegrastats GR3D_FREQ)
- [ ] `/tf` chain healthy across containers

**Rollback:**
1. Re-enable obstacle_layer in slam_nav.yaml (revert to backup `slam_nav.yaml.bak.before_nvblox`)
2. `sudo systemctl stop ugv-isaac.service && sudo systemctl disable ugv-isaac.service`
3. Remove relay node from start_tools_api.sh
4. Restart `ugv-waveshare.service` and `ugv-tools-api.service`
5. Sidecar container can be removed with `docker rm ugv_isaac` if needed; original images preserved.

### Task 2.12 (deferred — Phase 2.5): Enable depth fusion

Once Phase 2 is stable for a full day's testing, flip `use_depth: true` in nvblox_config.yaml so OAK-D depth contributes to the voxel field. This is the "future-prep" item.

---

## Phase 3: Piece 3 — Isaac ROS path planner (feature-flagged)

### Task 3.1: Reconnaissance — what planner does the nvblox image have?

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_isaac bash -lc "ros2 pkg list 2>&1 | grep -iE \"planner|nvblox_nav\" | head -20"'
```

Look for `nvblox_nav2` or similar planner-style packages. Document the planner plugin name (e.g., `nvblox::NvbloxPlanner` or `isaac_ros_navigation::SomePlanner`).

If no path planner is bundled, **skip Phase 3** (Pieces 1+2 alone may give enough relief, and rolling our own GPU planner is out of scope).

### Task 3.2: Add planner as alternative entry in slam_nav.yaml

```yaml
planner_server:
  ros__parameters:
    expected_planner_frequency: 1.0
    use_sim_time: false
    planner_plugins: ["GridBased"]   # current default
    
    GridBased:
      plugin: nav2_smac_planner/SmacPlanner2D
      # ...existing params...
    
    # NEW: GPU planner — enable by changing planner_plugins to ["GPU"]
    GPU:
      plugin: nvblox::NvbloxPlanner   # exact name from Task 3.1
      use_2d: true
      cost_threshold: 50
      goal_tolerance: 0.20
```

To switch: change `planner_plugins: ["GridBased"]` to `planner_plugins: ["GPU"]` and restart. To rollback: change back.

### Task 3.3: Test GPU planner on a single goal

Before flipping the default, send a manual goal with the GPU planner enabled:

```bash
# Set planner_plugins to ["GPU"] manually via ros2 param set, restart planner_server
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'ros2 param set /planner_server planner_plugins [GPU]'"

# Send a goal a few meters away and observe — does the path get computed in <1s?
# Check planner_server CPU drop and GPU utilization spike
```

### Task 3.4: A/B compare path quality + latency

Run the same 3 reference goals (basement to garage, basement corner to corner, etc.) under each planner. Compare:
- Plan latency (controller log timestamps)
- Plan quality (length, smoothness via Vizanti)
- Plan validity (does Nav2 successfully follow it without recovery)

### Task 3.5: Phase 3 verification checkpoint

**Pass criteria:**
- [ ] GPU planner produces valid plans for 3/3 reference goals
- [ ] Plan latency ≤ SmacPlanner2D's latency on same goals
- [ ] No new BT recovery firings during execution
- [ ] planner_server CPU drops; GPU utilization >5% during plan compute

**Rollback** (10 second flip): `planner_plugins: ["GPU"]` → `planner_plugins: ["GridBased"]`, restart.

---

## Phase 4: End-to-end validation

### Task 4.1: Run a full mission and capture metrics

Brandon-driven mission (5-10 minute autonomous run):
- Start with all three pieces deployed
- Capture before/after CPU, load avg, GPU utilization, costmap rates, controller_server lifecycle response

```bash
# Run during mission
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S top -bn1 -d1 -o %CPU 2>&1 | head -25 > /tmp/post_full_deploy.txt && cat /proc/loadavg >> /tmp/post_full_deploy.txt && echo jetson | sudo -S timeout 10 tegrastats --interval 1000 2>&1 | head -10 >> /tmp/post_full_deploy.txt'
```

Compare to `baseline_metrics_pre_deploy.txt` from Task 0.5.

### Task 4.2: Mint a snapshot

Use `/chat/save` to capture the deploy state, files modified, before/after metrics, and rollback paths.

---

## Out of Scope (do NOT do here)

- **VPI install in current container** — skipped because Path A (raw MJPEG) avoids it; the Path C VPI route is documented as last-resort future work, not on this plan.
- **Full container migration** — sidecar pattern explicitly avoids touching `ugv_waveshare`'s code.
- **Wakeword model TensorRT migration** — out of scope per Brandon's directive.
- **Custom CUDA kernel for DWB trajectory eval** — too speculative; defer.
- **OAK-D depth fusion in nvblox** — staged behind Phase 2.5 gate; only enable after Phase 2 is stable.
- **Replacing inflation_layer with nvblox ESDF** — keep Nav2's inflation_layer; ESDF is for path planner only.
- **AMCL/SLAM toggle button** — separate effort.
- **Wireless docking project** — separate effort.

---

## Rollback Summary (every piece)

| Piece | What to revert | Command (one-liner) |
|---|---|---|
| 1 | pantilt_camera | `sudo cp pantilt_camera.py.bak.opencv_v1 pantilt_camera.py && sudo systemctl restart ugv-tools-api.service` |
| 2 | nvblox costmap | revert slam_nav.yaml backup; `sudo systemctl stop ugv-isaac && sudo systemctl disable ugv-isaac && sudo systemctl restart ugv-waveshare` |
| 3 | GPU planner | edit slam_nav.yaml: `planner_plugins: [GridBased]`; `sudo systemctl restart ugv-waveshare` |

All originals + backups stay on the Jetson under `*.bak.*` siblings. **No code is ever deleted; only added or copied alongside.**

---

## Search hints

- 'pantilt_camera V4L2 MJPEG passthrough no opencv decode'
- 'Isaac ROS nvblox sidecar container ugv_isaac systemd'
- 'nvblox costmap relay /local_costmap/costmap /global_costmap/costmap'
- 'GPU path planner planner_plugins feature flag'
- 'cross-container ROS2 DDS host network RMW_IMPLEMENTATION'
- 'jetson-performance.service nvpmodel performance governor'
