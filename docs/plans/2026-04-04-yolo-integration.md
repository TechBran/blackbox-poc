# YOLO Object Detection Integration Plan — UGV Beast PT

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add real-time YOLO object detection to both cameras (pan-tilt + OAK-D) with bounding box visualization, depth-based distance estimation, and auto-tracking gimbal control.

**Architecture:** ONNX Runtime + TensorRT (no PyTorch — saves ~1.5GB RAM). One shared YOLO node processes both camera feeds through a single TensorRT engine. Separate tracker node handles gimbal auto-follow with proportional control. Detections published as standard `vision_msgs/Detection2DArray`.

**Tech Stack:** YOLOv8n (ONNX), ONNX Runtime + TensorRT 10.4, ROS2 Humble, OpenCV, vision_msgs

**Target Machine:** Jetson Orin Nano 8GB @ `192.168.1.155` (SSH port 23 into container)

---

## Context

The UGV Beast has clean SLAM mapping working (RTAB-Map + LiDAR + OAK-D at 640x480). The next step is object detection for:
1. Pan-tilt camera: auto-track any selected object class (person, cat, ball, etc.)
2. OAK-D: obstacle awareness with distance estimation for autonomous driving
3. Both: bounding boxes visible in Foxglove + compressed camera feeds

## Key Decisions

- **ONNX Runtime + TensorRT** over PyTorch: TensorRT already installed, saves 1.5GB RAM, 3-5x faster inference
- **Jetson GPU** over Myriad X: Myriad X already maxed on stereo depth + USB 2.0 bandwidth
- **Single YOLO node, two pipelines**: One TensorRT engine in GPU memory serves both cameras
- **Proportional controller** for gimbal tracking (servos have internal PID, no need for full PID)
- **vision_msgs/Detection2DArray**: Standard ROS2 message, no custom msgs, no colcon build needed

## Architecture

```
ugv_yolo_node.py (single TensorRT engine, two subscriber callbacks)
  IN:  /camera/image/compressed (pan-tilt JPEG 640x480)
  IN:  /oak/rgb/image_rect (OAK-D raw BGR8 640x480)
  IN:  /oak/stereo/image_raw (depth for distance lookup)
  OUT: /yolo/pantilt/detections (Detection2DArray)
  OUT: /yolo/oak/detections (Detection2DArray + distance)
  OUT: /yolo/pantilt/image/compressed (annotated feed)
  OUT: /yolo/oak/image/compressed (annotated feed)
  OUT: /yolo/markers (MarkerArray for Foxglove 3D)

ugv_tracker_node.py (proportional gimbal controller)
  IN:  /yolo/pantilt/detections
  OUT: /gimbal/absolute (Point: x=pan, y=tilt, z=speed)
  PARAM: target_class (change at runtime: ros2 param set /ugv_tracker target_class "cat")
```

## RAM Budget: ~500MB total for YOLO (5.4GB free, well within budget)

---

### Task 1: Install Dependencies

**Files:** None (apt/pip installs in container)

```bash
# Inside container (ssh root@192.168.1.155 -p 23)
apt-get update && apt-get install -y ros-humble-vision-msgs
pip3 install onnxruntime-gpu --extra-index-url https://elinux.org/Jetson_Zoo
# If above fails: pip3 install https://nvidia.box.com/shared/static/onnxruntime_gpu-1.19.0-cp310-cp310-linux_aarch64.whl
mkdir -p /home/ws/ugv_ws/models
```

**Test:**
```python
import onnxruntime as ort
print(ort.get_available_providers())
# Must include: 'TensorrtExecutionProvider', 'CUDAExecutionProvider'
```

---

### Task 2: Export and Deploy YOLOv8n ONNX Model

**On dev machine (not Jetson):**
```bash
pip install ultralytics
yolo export model=yolov8n.pt format=onnx imgsz=640 opset=17 simplify=True half=False
scp yolov8n.onnx root@192.168.1.155:/home/ws/ugv_ws/models/yolov8n.onnx
```

**Test on Jetson:**
```python
import onnxruntime as ort
sess = ort.InferenceSession('/home/ws/ugv_ws/models/yolov8n.onnx',
    providers=['TensorrtExecutionProvider', 'CUDAExecutionProvider'])
print("Input shape:", sess.get_inputs()[0].shape)  # [1, 3, 640, 640]
```

First run triggers TensorRT engine compilation (~2-5 min, cached thereafter).

---

### Task 3: Create COCO Class Names

**File:** Create `/home/ws/ugv_ws/coco_names.py`

80 COCO class names matching YOLOv8n output indices. Import from both YOLO and tracker nodes.

---

### Task 4: Implement ugv_yolo_node.py

**File:** Create `/home/ws/ugv_ws/ugv_yolo_node.py`

**Pipeline per camera:**
1. Receive frame (decode JPEG for pan-tilt, raw BGR for OAK-D)
2. Letterbox 640x480 → 640x640, normalize to [0,1], CHW RGB
3. ONNX Runtime inference (TensorRT FP16)
4. Postprocess: NMS via `cv2.dnn.NMSBoxes()`, scale boxes to original
5. For OAK-D: depth lookup at each bbox center (5x5 median patch)
6. Publish Detection2DArray per camera
7. Draw colored boxes + labels, publish annotated CompressedImage
8. For OAK-D: compute 3D positions, publish MarkerArray

**Parameters:** model_path, confidence_threshold (0.45), nms_threshold (0.50), enable_pantilt, enable_oak

**Reference pattern:** Follow `ugv_depth_node.py` for node structure, threading, publisher setup.

---

### Task 5: Implement ugv_tracker_node.py

**File:** Create `/home/ws/ugv_ws/ugv_tracker_node.py`

**Tracking logic:**
1. Subscribe to `/yolo/pantilt/detections`
2. Filter by `target_class` parameter (default: "person")
3. Select largest detection of target class
4. Compute pixel error from frame center (320, 240)
5. Apply deadzone (25px) — no jitter when object is centered
6. Proportional control: `pan_delta = -err_x * kp_pan` (0.15 deg/px)
7. Publish absolute position to `/gimbal/absolute`

**Parameters:** target_class, kp_pan (0.15), kp_tilt (0.12), dead_px (25), track_lost_timeout (2.0s)

**Change target at runtime:** `ros2 param set /ugv_tracker target_class "cup"`

---

### Task 6: Update start_ros2.sh

**File:** Modify `/home/ws/ugv_ws/start_ros2.sh`

Add after OAK-D depth node (section 9):
```bash
# 10. YOLO object detection (both cameras)
echo "=== Starting YOLO detection ==="
python3 /home/ws/ugv_ws/ugv_yolo_node.py &
sleep 5  # TensorRT engine cache loading

# 11. Pan-tilt object tracker
echo "=== Starting object tracker ==="
python3 /home/ws/ugv_ws/ugv_tracker_node.py &
sleep 2
```

---

### Task 7: Deploy and Test

1. SCP all files to Jetson
2. Start YOLO node standalone, verify topics:
   ```bash
   ros2 topic hz /yolo/pantilt/detections  # ~15Hz
   ros2 topic hz /yolo/oak/detections      # ~15Hz
   ```
3. Foxglove: add Image panels for `/yolo/pantilt/image/compressed` and `/yolo/oak/image/compressed`
4. Foxglove: verify `/yolo/markers` shows 3D cubes at detected objects
5. Test tracker: walk in front of pan-tilt camera — gimbal should follow
6. Test class switching: `ros2 param set /ugv_tracker target_class "cup"`

---

### Task 8: Performance Tuning

1. Measure inference latency: `ros2 topic delay /yolo/pantilt/detections`
2. If slow: reduce inference_size to 320
3. Check RAM: `free -m` inside container
4. Tune tracker gains if gimbal oscillates or is sluggish

---

## Dependency Chain

```
Task 1 (deps) ─┐
Task 2 (model) ─┼─> Task 4 (yolo node) ──> Task 6 (startup) ──> Task 7 (test) ──> Task 8 (tune)
Task 3 (names) ─┘         │
                           └──> Task 5 (tracker node) ──> Task 6
```

## Verification

1. Both `/yolo/*/detections` publishing at ~15Hz
2. Annotated camera feeds visible in Foxglove with colored bounding boxes
3. OAK-D detections include distance in mm
4. 3D markers appear in Foxglove 3D panel at correct positions
5. Gimbal auto-tracks selected object class
6. Runtime class switching works via `ros2 param set`
7. System stable with SLAM + YOLO + tracking running simultaneously
8. RAM usage stays under 3GB total (leaving 4.4GB headroom)
