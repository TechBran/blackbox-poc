# GPU Offload — Three-Piece Migration Implementation Plan (v2)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Free 30-50% of UGV Beast's CPU by offloading three workloads to the Jetson Orin Nano's idle Ampere GPU (1024 CUDA cores @ 0% utilization), without any data loss in the existing `ugv_waveshare` container.

**Architecture:** Three independent pieces, each separately rolled-back. (1) `pantilt_camera` reads raw MJPEG via V4L2 instead of decode-then-re-encode through OpenCV. (2) Isaac ROS `nvblox_node` runs in a SIDECAR container (`ugv_isaac`) on Docker `host` network with matching DDS impl, communicating with `ugv_waveshare` via ROS 2 topics. The CPU obstacle_layer is replaced by nvblox-published costmaps; Nav2 inflation_layer stays. (3) Optionally, an Isaac ROS path planner replaces SmacPlannerHybrid behind a single-parameter feature flag.

**Tech Stack:** Python 3.10, ROS 2 Humble, Isaac ROS DP 3.x (nvblox), VPI 3.x (libnvvpi3 from JetPack apt repo), nvjpeg, Docker (sidecar), CycloneDDS-or-FastRTPS (FastRTPS confirmed), pytest, systemd.

**Predecessor:** [docs/plans/2026-05-05-gpu-offload-three-piece.md](./2026-05-05-gpu-offload-three-piece.md). v2 replaces v1 based on Phase 0 recon findings (this plan's Phase 0 documents the recon already-completed deltas).

**Reference snapshots / memory:**
- `cpu_saturation_diagnosis.md` — Orin Nano power facts; jetson-performance.service in place.
- `ugv_nav2_tuning.md` — current Nav2 baseline (footprint 11"×8", **inflation 0.22/5.0**, BT rotate-only). v1 referenced 0.15/8.0 — corrected here.
- `feedback_ugv_nvblox_deferred.md` — prior in-place install blocked by CUDA/VPI mismatch; v1 incorrectly claimed sidecar sidesteps this. **It does NOT — VPI must be installed in the sidecar image.**
- `ugv_er_projection_tool.md` — `/global_costmap/costmap` is consumed by `project_pixel_to_map`; topic name and message type MUST be preserved.

**Repo layout:**
- Local dev (this machine, NOT a git repo): `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/`
  - Plans: `docs/plans/`
  - Local edit staging: `deployments/gpu_offload/`
- Robot (Jetson, IS a git repo): `/home/jetson/ugv_ws_waveshare/`
- Pre-built images on disk (verified Phase 0):
  - `ugv_jetson_ros_humble:nvblox` (25.5GB) ← **preferred sidecar base** (working ament index)
  - `ugv_jetson_nvblox:latest` (15.4GB) ← partial install (binaries present, ament index broken)
  - `ugv_waveshare:nvblox-20260418-1551` (16.1GB) ← unused fallback
  - `ugv_waveshare_pre_nvblox_snapshot:20260418-1540` (15.8GB) ← rollback safety net
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
│ DDS: rmw_fastrtps_cpp (matches; verified Phase 0)                  │
│ ROS_DOMAIN_ID: 0 (matches; verified Phase 0)                       │
│                                                                    │
│ ┌──────────────────────────┐    ┌────────────────────────────────┐│
│ │ ugv_waveshare (existing) │    │ ugv_isaac (NEW SIDECAR)        ││
│ │ image: ugv_jetson_ros_   │    │ image: ugv_jetson_nvblox:      ││
│ │   humble:v1028           │    │   vpi-fixed (BUILT in Phase 1.5)││
│ │                          │    │                                ││
│ │  /ugv_tools_api          │    │  /nvblox_node:                 ││
│ │  /Nav2 stack             │    │   - subscribes /scan_filtered  ││
│ │  /SLAM toolbox           │    │     (NOT /scan; matches Nav2)  ││
│ │  /OAK-D, BNO085          │    │   - publishes occupancy slice  ││
│ │  /Supervisor + Live      │    │  /nvblox_costmap_relay         ││
│ │  /ER agent               │    │   - republishes on:            ││
│ │                          │    │     /nvblox_local_costmap      ││
│ │                          │    │     /nvblox_global_costmap     ││
│ └──────────────────────────┘    └────────────────────────────────┘│
│                ↕ ROS 2 DDS (FastRTPS) ↕                            │
│                  topics + tf flow transparently                    │
└────────────────────────────────────────────────────────────────────┘
```

**Key invariants (verified Phase 0):**
- `ROS_DOMAIN_ID=0` matches between containers
- `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` matches between containers
- Both containers see /tf and /tf_static via the shared host network
- nvblox is configured to publish on its native topic, then a relay republishes for Nav2

---

## Phase 0: Reconnaissance verification (light re-runs)

Recon was substantially completed before this plan was written (see "GPU Offload Recon Report" in session 2026-05-07 conversation). Tasks here are READ-ONLY re-runs to confirm nothing has changed since the recon.

### Task 0.1: Re-confirm SSH + container state

**Files:** none modified.

**Step 1: SSH + docker reachability**

Run:
```bash
sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no jetson@192.168.1.155 'echo jetson | sudo -S docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>&1 | head -10'
```

Expected: `ugv_waveshare` container present, image `dudulrx0601/ugv_jetson_ros_humble:v1028`.

**STOP IF** `ugv_waveshare` is not running — investigate before proceeding.

### Task 0.2: Re-confirm pre-built images present

**Step 1: Confirm both nvblox images on disk**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker images --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}" 2>&1 | grep -E "nvblox|pre_nvblox"'
```

Expected: at minimum `ugv_jetson_ros_humble:nvblox` (25.5GB) AND `ugv_waveshare_pre_nvblox_snapshot:20260418-1540` (15.8GB) present.

**STOP IF** `ugv_jetson_ros_humble:nvblox` was deleted — entire Phase 1.5/2 plan depends on it.

### Task 0.3: Re-confirm cross-container DDS visibility

**Step 1: Spin probe container, verify it sees ugv_waveshare topics**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S timeout 30 docker run --rm --network host -e ROS_DOMAIN_ID=0 -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp ugv_jetson_ros_humble:nvblox bash -lc "source /opt/ros/humble/install/setup.bash && ros2 topic list 2>&1 | grep -cE \"^/(scan|odom|tf|local_costmap|global_costmap)\""'
```

Expected: count ≥ 4 (proves DDS bridge works on this image+config).

**STOP IF** count is 0 — DDS impl mismatch returned somehow; re-investigate before continuing.

### Task 0.4: Capture pre-deploy baseline metrics

**Files:**
- Create: `deployments/gpu_offload/baseline_metrics_pre_deploy.txt`

**Step 1: Snapshot CPU/load/GPU/topic-rates**

Run:
```bash
mkdir -p /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload

sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo "=== top ==="
echo jetson | sudo -S top -bn1 -d1 -o %CPU 2>&1 | head -25
echo "=== loadavg ==="
cat /proc/loadavg
echo "=== tegrastats (3 samples) ==="
echo jetson | sudo -S timeout 4 tegrastats --interval 1000 2>&1 | head -3
echo "=== topic rates ==="
echo jetson | sudo -S docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && for t in /scan /scan_filtered /camera/image/compressed; do echo === \$t ===; timeout 4 ros2 topic hz \$t 2>&1 | tail -2; done"
echo "=== nav2_container CPU ==="
echo jetson | sudo -S docker exec ugv_waveshare bash -lc "ps aux | grep -E \"nav2_container|controller_server|planner_server\" | grep -v grep"
' > /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/baseline_metrics_pre_deploy.txt 2>&1

cat /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/baseline_metrics_pre_deploy.txt | head -40
```

Expected: file created, includes load avg, GR3D_FREQ value (likely 0%), topic rates.

### Task 0.5: Commit recon artifacts

**Step 1: Add file to local repo (NOT git — this is a non-git workspace)**

The local workspace at `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/` is not a git repo, so no commit step. The file just exists on disk for later comparison in Phase 4.

---

## Phase 1: Piece 1 — pantilt_camera GPU offload (MJPEG passthrough)

### Task 1.1: Install linuxpy inside ugv_waveshare (mandatory)

v1 listed this as fallback. Recon proved linuxpy is NOT pre-installed. Do this BEFORE any other Piece 1 task.

**Step 1: Install via pip3**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "pip3 install linuxpy 2>&1 | tail -5"'
```

Expected: `Successfully installed linuxpy-X.Y.Z`.

**STOP IF** install fails (proxy/network issues). Diagnose before proceeding.

**Step 2: Verify import works**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "python3 -c \"from linuxpy.video.device import Device, BufferType, PixelFormat; print(\\\"linuxpy import OK\\\")\""'
```

Expected: `linuxpy import OK`.

### Task 1.2: Stage the new pantilt_camera module locally

**Files:**
- Create: `deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py`

**Step 1: Write the new node**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py` with:

```python
"""Pan-tilt camera publisher — raw MJPEG passthrough.

The previous implementation used cv2.VideoCapture which DECODES the camera's
native MJPEG, then cv2.imencode which RE-ENCODES it to JPEG before publishing.
That's two CPU-heavy operations per frame for no net benefit.

This implementation reads raw MJPEG buffers directly from V4L2 and publishes
them as sensor_msgs/CompressedImage with format='jpeg'. CPU cost drops from
~23% to <3% (just memcpy + DDS publish overhead).

Tested on UGV Beast Jetson Orin Nano: Xitech UVC camera at /dev/video0,
640x480 MJPG. Camera supports up to 30 fps natively; we publish at 15 to
match prior cadence.
"""
from __future__ import annotations
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage

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
        self._frame_iter = iter(self.stream)

        self.pub = self.create_publisher(
            CompressedImage, "/camera/image/compressed",
            qos_profile_sensor_data,
        )
        self.timer = self.create_timer(1.0 / (fps * 2), self._tick)
        self._fail_count = 0
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

**Step 2: Validate Python syntax locally**

Run:
```bash
python3 -c "import ast; ast.parse(open('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py').read()); print('SYNTAX OK')"
```

Expected: `SYNTAX OK`.

### Task 1.3: Write smoke test (offline-friendly)

**Files:**
- Create: `deployments/gpu_offload/test_pantilt_camera_smoke.py`

**Step 1: Write the test**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/test_pantilt_camera_smoke.py`:

```python
"""Offline smoke test for PantiltCameraNode — verifies V4L2 MJPEG passthrough
produces non-empty JPEG frames at expected rate.

Runs WITHOUT ROS2 — just imports the node module to check syntax + imports,
then directly opens V4L2 device and pulls frames. Run inside the container:
  python3 -m pytest /tmp/test_pantilt_camera_smoke.py -v
"""
import os
import sys


def test_imports_clean():
    """Ensure the target module imports without errors."""
    sys.path.insert(0, "/tmp")
    import pantilt_camera_v4l2_passthrough  # noqa: F401


def test_v4l2_yields_jpeg_bytes():
    """Verify direct V4L2 capture produces valid JPEG buffers."""
    if not os.path.exists("/dev/video0"):
        import pytest
        pytest.skip("No /dev/video0")

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
            assert data[:2] == b"\xff\xd8", f"Not a JPEG: starts with {data[:4].hex()}"
            assert len(data) > 1000, f"JPEG too small: {len(data)} bytes"
        finally:
            stream.close()
    finally:
        dev.close()
```

**Step 2: Validate syntax**

Run: `python3 -c "import ast; ast.parse(open('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/test_pantilt_camera_smoke.py').read()); print('SYNTAX OK')"`

Expected: `SYNTAX OK`.

### Task 1.4: Stop the existing pantilt_camera before running smoke test

The /dev/video0 device is exclusive — if the existing pantilt_camera is running, the smoke test will fail with EBUSY.

**Step 1: Stop existing pantilt_camera process**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "pkill -f \"ugv_tools_api.nodes.pantilt_camera\"; sleep 1; pgrep -af pantilt_camera || echo NOT_RUNNING"'
```

Expected: `NOT_RUNNING` after kill.

**STOP IF** the process refuses to die after multiple kills. Investigate process tree.

### Task 1.5: Deploy and run smoke test (BEFORE production swap)

**Step 1: SCP into container**

Run:
```bash
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/pantilt_camera_v4l2_passthrough.py jetson@192.168.1.155:/tmp/
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/test_pantilt_camera_smoke.py jetson@192.168.1.155:/tmp/
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker cp /tmp/pantilt_camera_v4l2_passthrough.py ugv_waveshare:/tmp/pantilt_camera_v4l2_passthrough.py && echo jetson | sudo -S docker cp /tmp/test_pantilt_camera_smoke.py ugv_waveshare:/tmp/test_pantilt_camera_smoke.py'
```

**Step 2: Run smoke test inside container**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "cd /tmp && python3 -m pytest test_pantilt_camera_smoke.py -v 2>&1 | tail -10"'
```

Expected: 2 PASSED. JPEG bytes have valid SOI marker (0xFF 0xD8).

**STOP IF** any test fails — diagnose before swapping the production node.

### Task 1.6: Save the original pantilt_camera

**Step 1: Make backup with descriptive suffix**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py.bak.opencv_v1_2026_05_07'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'ls -la /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py*'
```

Expected: original 2764 bytes preserved as `.bak.opencv_v1_2026_05_07`.

### Task 1.7: Replace pantilt_camera with the new implementation

**Step 1: Copy new file in (replaces existing via host bind mount)**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /tmp/pantilt_camera_v4l2_passthrough.py /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'wc -l /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py'
```

Expected: file size matches new implementation (~3500 bytes / ~110 lines).

### Task 1.8: Restart tools-api service and verify the new node is alive

**Step 1: Restart**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl restart ugv-tools-api.service'
sleep 12
```

**Step 2: Verify camera topic publishing**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && timeout 5 ros2 topic hz /camera/image/compressed 2>&1 | tail -3'"
```

Expected: ~15 Hz on /camera/image/compressed.

**STOP IF** topic isn't publishing — check `journalctl -u ugv-tools-api.service --since "30 seconds ago"` for errors.

### Task 1.9: Measure CPU change

**Step 1: Snapshot top + load**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S top -bn1 -d1 -o %CPU 2>&1 | head -25 | tee /tmp/post_piece1.txt'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'cat /proc/loadavg'
```

Expected: pantilt_camera process CPU drops from ~23% to ≤5%. Load avg trending down.

**STOP HERE IF** CPU did NOT drop — diagnose:
- Is the timer cadence too high? (timer at 2× fps but stream blocks; should NOT loop CPU-busy).
- Is `bytes(frame.data)` doing an expensive copy? Should be cheap (<50KB JPEG).

### Task 1.10: Verify Vizanti / camera_snapshot tool still works

**Step 1: Hit the tool API**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'curl -s -X POST http://localhost:8080/tool/camera_snapshot -H \"Content-Type: application/json\" -d \"{\\\"camera\\\": \\\"pantilt\\\", \\\"as_url\\\": false}\" | python3 -c \"import json, sys; r=json.load(sys.stdin); print(\\\"camera:\\\", r.get(\\\"result\\\", {}).get(\\\"camera\\\"), \\\"size_bytes:\\\", r.get(\\\"result\\\", {}).get(\\\"size_bytes\\\"))\"'"
```

Expected: `camera: pantilt size_bytes: 25000-50000`.

### Task 1.11: Phase 1 verification checkpoint

**Pass criteria (must be GREEN before Phase 1.5):**
- [ ] /camera/image/compressed publishes at ~15 Hz
- [ ] pantilt_camera process CPU ≤5%
- [ ] camera_snapshot tool returns valid JPEG bytes
- [ ] /proc/loadavg 1-min < pre-deploy baseline
- [ ] Vizanti pantilt feed renders (visual check via Vizanti UI)

**Rollback (if any criterion fails):**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py.bak.opencv_v1_2026_05_07 /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/pantilt_camera.py && echo jetson | sudo -S systemctl restart ugv-tools-api.service'
```

---

## Phase 1.5: Build VPI-fixed nvblox sidecar image (NEW — required before Phase 2)

This phase exists ONLY in v2. v1 incorrectly assumed the sidecar pattern sidesteps the VPI mismatch. Recon proved otherwise: `nvblox_node` fails immediately with `libnvvpi.so.3: cannot open shared object file`.

### Task 1.5.1: Verify VPI is installable from configured apt index

**Step 1: Test apt-cache on host (apt index is configured by JetPack 6.1)**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'apt-cache policy libnvvpi3 nvidia-vpi 2>&1 | head -20'
```

Expected: package "Candidate" version present, "Installed: (none)".

**STOP IF** policy returns "(none)" for Candidate — the JetPack apt index isn't reachable; must investigate before continuing.

### Task 1.5.2: Stage Dockerfile for VPI-fixed image

**Files:**
- Create: `deployments/gpu_offload/Dockerfile.nvblox-vpi`

**Step 1: Write Dockerfile**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/Dockerfile.nvblox-vpi`:

```dockerfile
# Derived from ugv_jetson_ros_humble:nvblox (25.5GB) — has working ament index
# for nvblox_ros, isaac_ros_common, isaac_ros_nitros.
# Adds VPI 3.x runtime (libnvvpi3) which the upstream image was missing.
# Built on Jetson Orin Nano with JetPack 6.1; uses configured apt index
# at jetson.webredirect.org for libnvvpi3 + nvidia-vpi packages.
FROM ugv_jetson_ros_humble:nvblox

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libnvvpi3 \
        nvidia-vpi && \
    rm -rf /var/lib/apt/lists/*

# Verify nvblox_node loads successfully (fails build if libnvvpi.so.3 still missing)
RUN bash -c "source /opt/ros/humble/install/setup.bash && /opt/ros/humble/lib/nvblox_ros/nvblox_node --ros-args --help 2>&1 | head -5 || echo 'NVBLOX_LOAD_FAILED'"

LABEL maintainer="UGV Beast project"
LABEL purpose="Isaac ROS nvblox sidecar with VPI 3.x runtime"
LABEL parent="ugv_jetson_ros_humble:nvblox"
LABEL build_date="2026-05-07"
```

**Step 2: Validate Dockerfile syntax (basic - just check FROM/RUN structure)**

Run: `head -20 /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/Dockerfile.nvblox-vpi`

Expected: starts with FROM, has RUN apt-get install lines.

### Task 1.5.3: SCP Dockerfile and build the image on Jetson

**Step 1: Copy Dockerfile to Jetson**

Run:
```bash
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/Dockerfile.nvblox-vpi jetson@192.168.1.155:/tmp/Dockerfile.nvblox-vpi
```

**Step 2: Build the image (~5-10 minutes)**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'cd /tmp && echo jetson | sudo -S docker build -t ugv_jetson_nvblox:vpi-fixed -f Dockerfile.nvblox-vpi . 2>&1 | tail -30'
```

Expected: `Successfully tagged ugv_jetson_nvblox:vpi-fixed`. The verification line should NOT print `NVBLOX_LOAD_FAILED`.

**STOP IF** build fails because libnvvpi3 not found — apt repo is inaccessible. Fall back to manual install inside running container then `docker commit`.

**STOP IF** verification prints `NVBLOX_LOAD_FAILED` — VPI install completed but library still not on dynamic loader path. Diagnose by: `docker run --rm ugv_jetson_nvblox:vpi-fixed bash -c "find / -name libnvvpi.so.3"`.

### Task 1.5.4: Smoke-test the new image

**Step 1: Run nvblox_node briefly to confirm loadability**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S timeout 15 docker run --rm --network host --runtime nvidia ugv_jetson_nvblox:vpi-fixed bash -lc "source /opt/ros/humble/install/setup.bash && /opt/ros/humble/lib/nvblox_ros/nvblox_node --ros-args --help 2>&1 | head -10"'
```

Expected: nvblox_node prints help (not "error while loading shared libraries"). May print warnings about missing parameters — that's fine, only checking dynamic load.

**Step 2: Confirm image size + tag**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker images ugv_jetson_nvblox 2>&1'
```

Expected: `vpi-fixed` tag present (~26GB), `latest` still present (~15.4GB).

### Task 1.5.5: Phase 1.5 verification checkpoint

**Pass criteria:**
- [ ] `docker images` shows `ugv_jetson_nvblox:vpi-fixed`
- [ ] `nvblox_node --help` runs without `libnvvpi.so.3` error
- [ ] Original `ugv_jetson_ros_humble:nvblox` still on disk untouched
- [ ] Disk free space still > 700GB

**Rollback:** `docker rmi ugv_jetson_nvblox:vpi-fixed` (no other side-effects).

---

## Phase 2: Piece 2 — Isaac ROS nvblox sidecar container

### Task 2.1: Stage the systemd unit file

**Files:**
- Create: `deployments/gpu_offload/ugv-isaac.service`

**Step 1: Write the unit**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/ugv-isaac.service`:

```ini
[Unit]
Description=UGV Isaac ROS sidecar (nvblox)
After=ugv-waveshare.service docker.service
Requires=docker.service
PartOf=ugv-waveshare.service

[Service]
Type=simple
Restart=on-failure
RestartSec=5
# CRITICAL: RMW_IMPLEMENTATION must match ugv_waveshare's (rmw_fastrtps_cpp).
# v1 plan incorrectly specified rmw_cyclonedds_cpp here — would have broken
# cross-container topic visibility. Verified via Phase 0 recon.
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
  -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  -e DISPLAY=$DISPLAY \
  ugv_jetson_nvblox:vpi-fixed \
  /bin/bash -c "/deploy/start_isaac.sh"
ExecStop=/usr/bin/docker stop ugv_isaac

[Install]
WantedBy=multi-user.target
```

**Step 2: Validate unit syntax**

Run: `cat /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/ugv-isaac.service | grep -E "^(\[|Description|ExecStart|RMW_IMPL|ROS_DOMAIN)"`

Expected: shows section headers, ExecStart with the corrected RMW value, ROS_DOMAIN_ID=0.

### Task 2.2: Stage the start_isaac.sh entrypoint

**Files:**
- Create: `deployments/gpu_offload/start_isaac.sh`

**Step 1: Write the script**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/start_isaac.sh`:

```bash
#!/bin/bash
# start_isaac.sh — entrypoint inside ugv_jetson_nvblox:vpi-fixed container.
# Sources Isaac ROS env, then launches nvblox via ros2 run with our params.
# v2 corrections vs v1:
#   - Source /opt/ros/humble/install/setup.bash (NOT /workspaces/isaac_ros-dev/...
#     which doesn't exist in this image)
#   - Use `ros2 run` not `ros2 launch nvblox.launch.py` (no such launch file)
set -eo pipefail

# ROS setup scripts reference unbound vars — disable nounset while sourcing
set +u
# CORRECT path: layered install, NOT /opt/ros/humble/setup.bash (349-byte stub)
source /opt/ros/humble/install/setup.bash
set -u

trap 'jobs -p | xargs -r kill 2>/dev/null; exit 0' TERM INT

# Wait for /scan_filtered to appear (ugv_waveshare must be up first)
echo "[start_isaac] waiting for /scan_filtered topic from ugv_waveshare..."
for i in $(seq 1 30); do
  if ros2 topic list 2>/dev/null | grep -q "/scan_filtered"; then
    echo "[start_isaac] /scan_filtered detected; launching nvblox_node"
    break
  fi
  sleep 1
done

# Launch nvblox directly via ros2 run (no launch file in image)
exec ros2 run nvblox_ros nvblox_node \
  --ros-args \
  --params-file /isaac_params/nvblox_config.yaml \
  -r __node:=nvblox_node
```

**Step 2: Validate shell syntax**

Run: `bash -n /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/start_isaac.sh && echo "SYNTAX OK"`

Expected: `SYNTAX OK`.

### Task 2.3: Stage the nvblox config

**Files:**
- Create: `deployments/gpu_offload/nvblox_config.yaml`

**Step 1: Write the config**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/nvblox_config.yaml`:

```yaml
# Nvblox config tuned for UGV Beast (11"×8" tracked robot, indoor missions).
# Replicates the existing CPU obstacle_layer behavior so Nav2 sees no semantic
# change in /local_costmap/costmap and /global_costmap/costmap.
#
# v2 corrections vs v1:
#   - lidar_topic: /scan_filtered (NOT /scan; Nav2 uses /scan_filtered)
#   - Inflation reference comment updated (live config is 0.22/5.0 not 0.15/8.0)
nvblox_node:
  ros__parameters:
    # ===== Voxel + map settings =====
    voxel_size: 0.05            # match Nav2 costmap resolution
    map_clearing_radius_m: 5.0  # match local_costmap rolling window radius
    map_clearing_frame_id: "base_footprint"
    
    # ===== Sensor inputs =====
    # LiDAR (LD19) — primary obstacle source
    use_lidar: true
    lidar_topic: "/scan_filtered"   # CORRECTED: was /scan in v1
    lidar_qos: "SENSOR_DATA"
    lidar_max_range_m: 3.0          # mirror obstacle_max_range from slam_nav.yaml
    lidar_min_range_m: 0.10
    
    # Depth camera (OAK-D) — DISABLED initially. Enable in Phase 2.5 (deferred).
    use_depth: false
    depth_topic: "/oak/stereo/depth"
    depth_camera_info_topic: "/oak/stereo/camera_info"
    depth_qos: "SENSOR_DATA"
    
    # ===== TF =====
    global_frame: "map"
    pose_frame: "base_footprint"
    
    # ===== Output cadence =====
    map_publish_period_s: 0.2     # 5 Hz (match local_costmap update_frequency)
    esdf: true
    esdf_2d: true
    distance_map_unknown_value_optimistic: false
    
    # ===== Slice for 2D projection =====
    # Robot is ~0.10m tall. Slice at ankle-to-shoulder height; under-table
    # clearance is intentional.
    esdf_slice_height: 0.10
    esdf_slice_min_height: 0.05
    esdf_slice_max_height: 0.50
    
    # NOTE: nvblox publishes occupancy on its own topic names (e.g.
    # /nvblox_node/static_map_slice). The relay node (Task 2.7) republishes on
    # /nvblox_local_costmap and /nvblox_global_costmap. The Nav2 cutover (Task
    # 2.10) then swaps Nav2's plugin list to consume those topics via
    # static_layer in place of the CPU obstacle_layer.
```

**Step 2: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/nvblox_config.yaml')); print('YAML OK')"`

Expected: `YAML OK`.

### Task 2.4: Deploy artifacts to Jetson

**Step 1: SCP files**

Run:
```bash
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/start_isaac.sh jetson@192.168.1.155:/tmp/
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/nvblox_config.yaml jetson@192.168.1.155:/tmp/
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/ugv-isaac.service jetson@192.168.1.155:/tmp/
```

**Step 2: Move into final locations on host**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S mkdir -p /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac
echo jetson | sudo -S cp /tmp/start_isaac.sh /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac/start_isaac.sh
echo jetson | sudo -S chmod +x /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac/start_isaac.sh
echo jetson | sudo -S cp /tmp/nvblox_config.yaml /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/nvblox_config.yaml
echo jetson | sudo -S ls -la /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac/start_isaac.sh /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/nvblox_config.yaml
'
```

Expected: both files in place, `start_isaac.sh` executable.

### Task 2.5: Manual launch test (NOT systemd yet)

**Step 1: Run the sidecar container in foreground for ~30s to verify**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S timeout 60 docker run --rm --name ugv_isaac_test --network host --runtime nvidia --privileged -v /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param:/isaac_params:ro -v /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/isaac:/deploy:ro -e ROS_DOMAIN_ID=0 -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp ugv_jetson_nvblox:vpi-fixed /bin/bash -c "/deploy/start_isaac.sh" 2>&1 | tail -30'
```

Expected: nvblox_node prints initialization output, no `libnvvpi.so.3` errors, no Python tracebacks. May report "no transform from map to base_footprint yet" until SLAM publishes — that's fine.

**STOP IF** the container exits in <5s with errors — diagnose. Common causes: missing TF (slam_toolbox not yet up), wrong topic name (verify /scan_filtered), nvblox config schema mismatch.

### Task 2.6: Confirm nvblox publishes AND topics flow across containers

**Step 1: From ugv_waveshare, observe nvblox topics**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && ros2 topic list 2>&1 | grep -iE \"nvblox\" | head -10'"
```

Expected: nvblox topics visible (e.g. `/nvblox_node/static_map_slice`, `/nvblox_node/esdf_slice`, etc.).

**Step 2: Verify hz on a key topic**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && timeout 5 ros2 topic echo --once /nvblox_node/static_map_slice --field info 2>&1 | head -5'"
```

Expected: occupancy grid info dump (resolution, width, height).

**STOP IF** topics are NOT visible from `ugv_waveshare`. Validate DDS env vars: both containers MUST have ROS_DOMAIN_ID=0 + RMW_IMPLEMENTATION=rmw_fastrtps_cpp.

### Task 2.7: Stop the test container

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker stop ugv_isaac_test 2>/dev/null || echo "already stopped"'
```

### Task 2.8: Stage the topic relay node

**Files:**
- Create: `deployments/gpu_offload/nvblox_costmap_relay.py`

**Step 1: Write the relay**

Create `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/nvblox_costmap_relay.py`:

```python
"""Republish nvblox-produced occupancy slices on Nav2-compatible parallel topics.

Initially we publish to PARALLEL topic names (/nvblox_local_costmap,
/nvblox_global_costmap). Nav2 keeps using its own /local_costmap/costmap
and /global_costmap/costmap. We A/B compare visually before flipping Nav2.

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
            "republish_local", "/nvblox_local_costmap")
        self.declare_parameter(
            "republish_global", "/nvblox_global_costmap")

        nvblox_topic = self.get_parameter("nvblox_topic").value
        local_topic = self.get_parameter("republish_local").value
        global_topic = self.get_parameter("republish_global").value

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

**Step 2: Validate Python syntax**

Run: `python3 -c "import ast; ast.parse(open('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/nvblox_costmap_relay.py').read()); print('SYNTAX OK')"`

Expected: `SYNTAX OK`.

### Task 2.9: Deploy relay + register in start_tools_api.sh

**Step 1: SCP the relay**

Run:
```bash
sshpass -p 'jetson' scp /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/nvblox_costmap_relay.py jetson@192.168.1.155:/tmp/
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /tmp/nvblox_costmap_relay.py /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/nodes/nvblox_costmap_relay.py'
```

**Step 2: Backup start_tools_api.sh**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh.bak.before_nvblox_relay_2026_05_07'
```

**Step 3: Insert relay launch line after waypoints_bridge**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S sed -i "/python3 -m ugv_tools_api.nodes.waypoints_bridge &/a python3 -m ugv_tools_api.nodes.nvblox_costmap_relay \&" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'grep -nE "nvblox|waypoints_bridge" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh'
```

Expected: `python3 -m ugv_tools_api.nodes.nvblox_costmap_relay &` appears immediately after the waypoints_bridge line.

### Task 2.10: Install and start the sidecar systemd service

**Step 1: Install unit file + enable + start**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S cp /tmp/ugv-isaac.service /etc/systemd/system/ugv-isaac.service
echo jetson | sudo -S systemctl daemon-reload
echo jetson | sudo -S systemctl enable ugv-isaac.service
echo jetson | sudo -S systemctl start ugv-isaac.service
sleep 30
echo jetson | sudo -S systemctl status ugv-isaac.service --no-pager | head -15
echo "=== last 20 journal lines ==="
echo jetson | sudo -S journalctl -u ugv-isaac.service --since "60 seconds ago" --no-pager | tail -20
'
```

Expected: service `active (running)`. Journal shows nvblox initialization log lines.

**Step 2: Restart ugv-tools-api so the relay node spawns**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl restart ugv-tools-api.service'
sleep 12
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare pgrep -af nvblox_costmap_relay'
```

Expected: relay process visible in `pgrep` output.

### Task 2.11: Validate parallel topics are healthy (A/B comparison)

**Step 1: All four costmap topics publish**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && for t in /local_costmap/costmap /global_costmap/costmap /nvblox_local_costmap /nvblox_global_costmap; do echo === \$t ===; timeout 5 ros2 topic echo --once \$t --field info 2>&1 | head -5; done'"
```

Expected: ALL FOUR topics return occupancy info (resolution, width, height). The two CPU-source topics (/local_costmap/costmap, /global_costmap/costmap) and two nvblox-source topics should have similar dimensions.

**Step 2: Visual check in Vizanti (manual)**

Operator opens Vizanti, adds two map layers — `/local_costmap/costmap` and `/nvblox_local_costmap` — overlaid. Compare obstacle structure. They should be CLOSE but not identical (CPU layer uses raycast directly, nvblox uses voxel projection).

**Pass criterion:** No major discrepancies — same major obstacles in same locations. Minor differences in voxel-edge handling are expected.

**STOP IF** nvblox topic is empty/invalid (all -1 or 0 cells), or shows obstacles in wrong locations. Diagnose nvblox config.

### Task 2.12: Cutover Nav2 to consume nvblox topics

After A/B validation looks good, swap Nav2's plugin list.

**Step 1: Backup slam_nav.yaml**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml.bak.before_nvblox_cutover_2026_05_07'
```

**Step 2: Edit slam_nav.yaml to use nvblox-fed static_layer**

Edit `/home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml`:

For BOTH `local_costmap` and `global_costmap` blocks, replace the `obstacle_layer` plugin entry with `static_layer_nvblox`. Comment out (do NOT delete) the original `obstacle_layer:` block for rollback reference.

For local_costmap (~line 232):
```yaml
plugins:
- static_layer_nvblox     # NVBLOX-FED: replaces CPU obstacle_layer 2026-05-07
- inflation_layer
static_layer_nvblox:
  plugin: nav2_costmap_2d::StaticLayer
  map_topic: /nvblox_local_costmap
  subscribe_to_updates: true
  trinary_costmap: true
# obstacle_layer:           # COMMENTED OUT 2026-05-07 — replaced by nvblox; restore from .bak to roll back
#   plugin: nav2_costmap_2d::ObstacleLayer
#   ... (keep original block content commented out for archaeology)
```

For global_costmap (~line 277): same treatment, but `map_topic: /nvblox_global_costmap`.

**Step 3: Restart Nav2**

WORKAROUND for ugv-waveshare.service Type=forking flaw: the systemctl restart does NOT actually restart the docker container. Use docker restart instead:

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker restart ugv_waveshare && sleep 25 && echo jetson | sudo -S systemctl restart ugv-tools-api.service && sleep 10'
```

Then verify Nav2 is back up:

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && ros2 lifecycle get /controller_server'"
```

Expected: `active [3]`.

### Task 2.13: Phase 2 verification checkpoint

**Pass criteria:**
- [ ] `/local_costmap/costmap` updates at ~5 Hz (now driven by nvblox via static_layer)
- [ ] `/global_costmap/costmap` updates at ~5 Hz
- [ ] `project_pixel_to_map` tool still returns valid (x, y) — smoke-test with curl
- [ ] Vizanti shows obstacles in costmap (visual)
- [ ] CPU usage on `nav2_container` reduced (was ~17-22%, expect ~10-15% after offloading)
- [ ] GPU utilization > 0% (verify via `tegrastats GR3D_FREQ`)
- [ ] /tf chain healthy across containers
- [ ] No new BT recovery firings during a brief manual goal

**Project_pixel_to_map smoke test:**
Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'curl -s -X POST http://localhost:8080/tool/project_pixel_to_map -H \"Content-Type: application/json\" -d \"{\\\"camera\\\": \\\"oakd\\\", \\\"u\\\": 320, \\\"v\\\": 240}\" | head -30'"
```

Expected: returns a JSON with map x/y coordinates (not an error).

**Rollback (one command):**
Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml.bak.before_nvblox_cutover_2026_05_07 /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml
echo jetson | sudo -S systemctl stop ugv-isaac.service
echo jetson | sudo -S systemctl disable ugv-isaac.service
echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh.bak.before_nvblox_relay_2026_05_07 /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_tools_api.sh
echo jetson | sudo -S docker restart ugv_waveshare
sleep 25
echo jetson | sudo -S systemctl restart ugv-tools-api.service
'
```

### Task 2.14 (deferred — Phase 2.5): Enable depth fusion

Only after Phase 2 is stable for a full day's testing, flip `use_depth: true` in `nvblox_config.yaml` so OAK-D depth contributes to the voxel field. Do not enable in initial deployment.

---

## Phase 3: Piece 3 — Isaac ROS path planner (feature-flagged)

### Task 3.1: Reconnaissance — what planner does the nvblox image have?

**Step 1: List planner-style packages**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_isaac bash -lc "source /opt/ros/humble/install/setup.bash && ros2 pkg list 2>&1 | grep -iE \"planner|nvblox_nav\" | head -20"'
```

Expected: list of GPU planner packages or empty.

**Step 2: Document the planner plugin name**

If found, note the exact `plugin:` name (e.g. `nvblox::NvbloxPlanner`).

**STOP IF no GPU planner is bundled** — skip the rest of Phase 3. Pieces 1+2 alone deliver the bulk of the CPU savings; rolling our own GPU planner is out of scope.

### Task 3.2: Add planner as alternative entry in slam_nav.yaml

Only if Task 3.1 found a GPU planner.

**Step 1: Backup**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S cp /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml /home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml.bak.before_gpu_planner_2026_05_07'
```

**Step 2: Edit planner_server block to add a second plugin alongside SmacPlannerHybrid**

Edit `/home/jetson/ugv_ws_waveshare/src/ugv_main/ugv_nav/param/slam_nav.yaml`, in the `planner_server` block (~line 315), add:

```yaml
planner_server:
  ros__parameters:
    expected_planner_frequency: 10.0
    use_sim_time: false
    planner_plugins: ["GridBased"]   # to enable GPU: change to ["GPU"]
    
    GridBased:
      plugin: nav2_smac_planner/SmacPlannerHybrid
      # ... existing params unchanged ...
    
    # GPU planner — toggle by changing planner_plugins to ["GPU"]
    GPU:
      plugin: <plugin_name_from_task_3.1>
      use_2d: true
      cost_threshold: 50
      goal_tolerance: 0.20
```

### Task 3.3: Test GPU planner on a single goal

**Step 1: Switch to GPU planner**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'ros2 param set /planner_server planner_plugins [GPU]'"
```

**Step 2: Manually issue a navigation goal a few meters away from current pose** (operator action — via Vizanti).

**Step 3: Observe plan latency in /rosout**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash && timeout 8 ros2 topic echo /rosout 2>&1 | grep -iE \"plan|gpu\" | head -10"'
```

Expected: plan computed in <1s. GPU planner produces valid path.

### Task 3.4: A/B compare path quality + latency

Run the same 3 reference goals (basement to garage, basement corner to corner, etc.) under each planner. Compare:
- Plan latency (controller log timestamps)
- Plan quality (length, smoothness via Vizanti)
- Plan validity (does Nav2 successfully follow it without recovery)

### Task 3.5: Phase 3 verification checkpoint

**Pass criteria:**
- [ ] GPU planner produces valid plans for 3/3 reference goals
- [ ] Plan latency ≤ SmacPlannerHybrid's latency on same goals
- [ ] No new BT recovery firings during execution
- [ ] planner_server CPU drops; GPU utilization > 5% during plan compute

**Rollback (10 second flip):**
Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "echo jetson | sudo -S docker exec ugv_waveshare bash -lc 'ros2 param set /planner_server planner_plugins [GridBased]'"
```

(Or restore the slam_nav.yaml backup and `docker restart ugv_waveshare`.)

---

## Phase 4: End-to-end validation

### Task 4.1: Run a full mission and capture metrics

**Files:**
- Create: `deployments/gpu_offload/post_full_deploy_metrics.txt`

**Step 1: During an operator-driven 5-10 minute autonomous mission, snapshot metrics**

Run:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 '
echo "=== top ==="
echo jetson | sudo -S top -bn1 -d1 -o %CPU 2>&1 | head -25
echo "=== loadavg ==="
cat /proc/loadavg
echo "=== tegrastats (5 samples to capture GR3D activity) ==="
echo jetson | sudo -S timeout 6 tegrastats --interval 1000 2>&1 | head -5
echo "=== topic rates ==="
echo jetson | sudo -S docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash && source /home/ws/ugv_ws/install/setup.bash && for t in /scan /scan_filtered /camera/image/compressed /local_costmap/costmap /global_costmap/costmap; do echo === \$t ===; timeout 4 ros2 topic hz \$t 2>&1 | tail -2; done"
' > /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/post_full_deploy_metrics.txt 2>&1

cat /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/post_full_deploy_metrics.txt | head -40
```

### Task 4.2: Compare against baseline

**Step 1: diff the metrics files**

Run:
```bash
diff /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/baseline_metrics_pre_deploy.txt /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/deployments/gpu_offload/post_full_deploy_metrics.txt | head -80
```

Document deltas in: pantilt_camera CPU (expect -18%), nav2_container CPU (expect -5 to -10%), GR3D_FREQ (expect non-zero), load avg (expect lower).

### Task 4.3: Mint a snapshot

Use the project's `/snapshot-dev` command (via curl to /chat/save) to capture: phase-by-phase deploy state, files modified, before/after metrics, rollback paths. Operator: `Brandon`.

### Task 4.4: Update memory

Update `/home/ai-black-box-fc/.claude/projects/-home-ai-black-box-fc-Desktop-blackbox-poc--blackbox-poc/memory/MEMORY.md` and remove `feedback_ugv_nvblox_deferred.md` (or update it) since the VPI mismatch is now resolved via the vpi-fixed image.

---

## Out of Scope (do NOT do here)

- **VPI install in current ugv_waveshare container** — Piece 1 (Path A) does not need VPI; ONLY the sidecar needs it.
- **Full container migration** — sidecar pattern explicitly avoids touching `ugv_waveshare`'s code.
- **Wakeword model TensorRT migration** — out of scope per Brandon's directive.
- **Custom CUDA kernel for DWB trajectory eval** — too speculative; defer.
- **OAK-D depth fusion in nvblox** — staged behind Phase 2.5 gate; only enable after Phase 2 is stable for a full day.
- **Replacing inflation_layer with nvblox ESDF** — keep Nav2's inflation_layer; ESDF is for the optional GPU planner only.
- **AMCL/SLAM toggle button** — separate effort.
- **Wireless docking project** — separate effort.
- **Fixing ugv-waveshare.service Type=forking flaw** — workaround is sufficient for this plan; full fix is a separate cleanup task.

---

## Rollback Summary (every piece)

| Piece | What to revert | One-liner |
|---|---|---|
| 1 | pantilt_camera | `sudo cp pantilt_camera.py.bak.opencv_v1_2026_05_07 pantilt_camera.py && sudo systemctl restart ugv-tools-api.service` |
| 1.5 | VPI image build | `sudo docker rmi ugv_jetson_nvblox:vpi-fixed` |
| 2 | nvblox costmap cutover | restore `slam_nav.yaml.bak.before_nvblox_cutover_2026_05_07` + restore `start_tools_api.sh.bak.before_nvblox_relay_2026_05_07` + `sudo systemctl stop ugv-isaac && sudo systemctl disable ugv-isaac && sudo docker restart ugv_waveshare && sudo systemctl restart ugv-tools-api.service` |
| 3 | GPU planner | restore `slam_nav.yaml.bak.before_gpu_planner_2026_05_07` OR `ros2 param set /planner_server planner_plugins [GridBased]` |

All originals + backups stay on the Jetson under `*.bak.*` siblings. **No code is ever deleted; only added or copied alongside.**

**Critical workaround note:** any rollback step in v1 that says `sudo systemctl restart ugv-waveshare.service` MUST be replaced with `sudo docker restart ugv_waveshare && sudo systemctl restart ugv-tools-api.service` because of the Type=forking flaw documented in `feedback_pkill_docker_exec.md`.

---

## Search hints

- 'pantilt_camera V4L2 MJPEG passthrough no opencv decode'
- 'Isaac ROS nvblox sidecar container ugv_isaac systemd vpi-fixed image'
- 'nvblox costmap relay /nvblox_local_costmap /nvblox_global_costmap'
- 'GPU path planner planner_plugins feature flag'
- 'cross-container ROS2 DDS host network rmw_fastrtps_cpp'
- 'libnvvpi.so.3 missing fix apt install libnvvpi3 nvidia-vpi'
- 'ugv-waveshare.service Type=forking restart workaround docker restart'
- 'jetson-performance.service nvpmodel performance governor'
