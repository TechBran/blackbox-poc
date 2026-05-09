# UGV Beast PT — Command Cheatsheet

## SSH Access

```bash
# Direct into Docker container (ROS2 environment)
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23

# Host Jetson (systemd, Docker, hardware)
sshpass -p 'jetson' ssh jetson@192.168.1.155
```

---

## Autonomous Exploration (Roomba Mode)

```bash
# Start — robot explores entire environment autonomously
ros2 service call /explore/start std_srvs/srv/Trigger

# Stop — emergency stop exploration
ros2 service call /explore/stop std_srvs/srv/Trigger

# Monitor status (IDLE/LAUNCHING/EXPLORING/RETURNING/SAVING)
ros2 topic echo /explore/status
```

---

## Map Management

```bash
# Save current 2D map (GMapping/RTAB-Map grid)
ros2 service call /mapping/save_2d std_srvs/srv/Trigger

# Save current 3D map (RTAB-Map database)
ros2 service call /mapping/save_3d std_srvs/srv/Trigger

# List all saved maps
ros2 service call /mapping/list std_srvs/srv/Trigger
```

Maps stored at: `/home/ws/ugv_ws/maps/2d/` and `/home/ws/ugv_ws/maps/3d/`

---

## YOLO Object Detection

```bash
# Change gimbal tracking target (any COCO class)
ros2 param set /ugv_tracker target_class "person"
ros2 param set /ugv_tracker target_class "cup"
ros2 param set /ugv_tracker target_class "cat"

# Disable/enable gimbal tracking
ros2 param set /ugv_tracker enabled false
ros2 param set /ugv_tracker enabled true

# Tune tracking (live, no reboot needed)
ros2 param set /ugv_tracker iterate 0.015      # P gain (lower=gentler)
ros2 param set /ugv_tracker damping 0.4        # D gain (higher=more braking)
ros2 param set /ugv_tracker dead_px 35         # Deadzone pixels
ros2 param set /ugv_tracker smoothing 0.6      # EMA filter (higher=smoother)
```

### COCO Classes (80)
person, bicycle, car, motorcycle, airplane, bus, train, truck, boat, traffic light, fire hydrant, stop sign, parking meter, bench, bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe, backpack, umbrella, handbag, tie, suitcase, frisbee, skis, snowboard, sports ball, kite, baseball bat, baseball glove, skateboard, surfboard, tennis racket, bottle, wine glass, cup, fork, knife, spoon, bowl, banana, apple, sandwich, orange, broccoli, carrot, hot dog, pizza, donut, cake, chair, couch, potted plant, bed, dining table, toilet, tv, laptop, mouse, remote, keyboard, cell phone, microwave, oven, toaster, sink, refrigerator, book, clock, vase, scissors, teddy bear, hair drier, toothbrush

---

## Lights

```bash
# Set individual lights (0-255)
ros2 topic pub /lights/base std_msgs/msg/Int32 "{data: 200}" --once
ros2 topic pub /lights/head std_msgs/msg/Int32 "{data: 25}" --once

# All on/off via services
ros2 service call /lights/all_on std_srvs/srv/Trigger
ros2 service call /lights/all_off std_srvs/srv/Trigger
ros2 service call /lights/base_on std_srvs/srv/Trigger
ros2 service call /lights/base_off std_srvs/srv/Trigger
ros2 service call /lights/head_on std_srvs/srv/Trigger
ros2 service call /lights/head_off std_srvs/srv/Trigger
```

Boot defaults: Base=200 (OAK-D depth), Head=25 (pan-tilt low-light)

---

## Gimbal Control

```bash
# Absolute position (pan: -180 to 180, tilt: -45 to 90, speed: 1-300)
ros2 topic pub /gimbal/absolute geometry_msgs/msg/Point "{x: 0.0, y: 0.0, z: 100.0}" --once

# Reset to center
ros2 topic pub /gimbal/absolute geometry_msgs/msg/Point "{x: 0.0, y: 0.0, z: 100.0}" --once
```

---

## System Services

```bash
# Emergency stop (all motors)
ros2 service call /system/estop std_srvs/srv/Trigger

# Servo release (limp mode)
ros2 service call /system/servo_release std_srvs/srv/Trigger

# Servo center calibration
ros2 service call /system/servo_set_mid std_srvs/srv/Trigger

# Custom OLED message
ros2 topic pub /system/oled std_msgs/msg/String "{data: 'Hello World'}" --once
```

---

## Monitoring

```bash
# List all nodes
ros2 node list

# Check topic rates
ros2 topic hz /scan              # LiDAR (~10Hz)
ros2 topic hz /oak/rgb/image_rect  # OAK-D RGB (~15fps)
ros2 topic hz /odom              # Filtered odometry (~10Hz)
ros2 topic hz /cmd_vel           # Motor commands

# Check TF tree
ros2 run tf2_ros tf2_echo map base_footprint

# Memory usage
free -h
```

---

## Web Interfaces

| Interface | URL | Purpose |
|-----------|-----|---------|
| **Vizanti** | `http://192.168.1.155:5100` | 2D map, joystick, Nav2 goals |
| **Foxglove** | `app.foxglove.dev` → `ws://192.168.1.155:8765` | 3D point cloud, YOLO markers, TF |
| **Flask App** | `http://192.168.1.155:5000` | Waveshare original demos |

### Foxglove Setup
1. Open `https://app.foxglove.dev`
2. Open connection → Foxglove WebSocket → `ws://192.168.1.155:8765`
3. 3D panel: Display frame = `map`, Follow mode = `none`
4. Image panels: `/yolo/pantilt/image/compressed`, `/yolo/oak/image/compressed`

### Foxglove Teleop (Drive)
- Topic: `/cmd_vel`, Rate: 10Hz, Stop on release: On
- Up: linear-x=0.2, Down: linear-x=-0.2, Left: angular-z=0.5, Right: angular-z=-0.5

### Foxglove Teleop (Camera Gimbal)
- Topic: `/gimbal/cmd`, Rate: 10Hz, Stop on release: On
- Up: linear-x=0.5, Down: linear-x=-0.5, Left: angular-z=0.5, Right: angular-z=-0.5

---

## Boot Modules (12 total)

| # | Module | Node Name |
|---|--------|-----------|
| 1 | Bringup (motors/LiDAR/TF) | `/ugv_bringup`, `/LD19`, `/base_node` |
| 2 | Odom Filter | `/ugv_odom_filter` |
| 3 | RTAB-Map 3D SLAM | `/rtabmap` |
| 4 | Camera (pan-tilt 640x480) | `/ugv_camera` |
| 5 | Gimbal control | `/ugv_gimbal` |
| 6 | Lights | `/ugv_lights` |
| 7 | System (OLED, e-stop) | `/ugv_system` |
| 8 | Map manager | `/ugv_mapping` |
| 9 | Vizanti web UI | `/vizanti_*` |
| 10 | Foxglove bridge | `/foxglove_bridge` |
| 11 | OAK-D depth (640x480) | `/ugv_depth` |
| 12 | YOLO detection | `/ugv_yolo` |
| 13 | Gimbal tracker | `/ugv_tracker` |
| 14 | Explore orchestrator | `/ugv_explore` (IDLE until triggered) |

---

## Service Restart

```bash
# On host Jetson (not container)
sudo systemctl restart blackbox.service   # BlackBox (if applicable)
sudo systemctl restart ugv-ros2.service   # Robot ROS2 stack

# Default mapping mode: slam_3d (RTAB-Map)
# To change: edit /home/ws/ugv_ws/start_ros2.sh → MAPPING_MODE variable
```

---

## Key Files on Jetson Container

```
/home/ws/ugv_ws/
├── start_ros2.sh              # Main startup script
├── ugv_depth_node.py          # OAK-D 640x480 + RTAB-Map topics
├── ugv_camera_node.py         # Pan-tilt 640x480 MJPEG
├── ugv_gimbal_node.py         # Pan-tilt servo control
├── ugv_lights_node.py         # LED control (IO4 base, IO5 head)
├── ugv_system_node.py         # OLED, e-stop, servo calibration
├── ugv_mapping_node.py        # Map save/list services
├── ugv_odom_filter.py         # rf2o EMA smoothing + TF
├── ugv_yolo_node.py           # Dual-camera YOLOv8n CUDA FP16
├── ugv_tracker_node.py        # PD gimbal auto-tracker
├── ugv_explore_node.py        # Autonomous exploration orchestrator
├── coco_names.py              # 80 COCO class definitions
├── rtabmap_custom.launch.py   # RTAB-Map 3D SLAM config
├── nav2_explore_params.yaml   # Nav2 navigation parameters
├── nav2_explore.launch.py     # Nav2 launch (navigation only)
├── explore_lite.launch.py     # Frontier exploration config
├── models/
│   └── yolov8n.onnx           # YOLOv8 nano model (13MB)
└── maps/
    ├── 2d/                    # Saved 2D maps (.pgm + .yaml)
    └── 3d/                    # Saved 3D maps (.db)
```

---

## Tool Schema API (BlackBox ↔ UGV)

**Endpoint:** `http://ugv-beast:8080` (Tailscale, preferred) · `http://192.168.1.155:8080` (LAN)

### HTTP routes

| Route | Purpose |
|-------|---------|
| `GET /health` | Liveness + bridge status |
| `GET /tools?format=anthropic\|openai\|gemini` | LLM-formatted tool registry (22 tools) |
| `POST /tool/{name}` | Dispatch a tool call; JSON body is the args |
| `GET /snapshot/{pantilt\|oakd}` | Latest JPEG frame (sensor_msgs/CompressedImage payload) |

### Tools by domain

- `motion_*` — move_forward, move_backward, rotate_left, rotate_right, stop  (safety-clamped: 0.15 m/s max linear, 0.8 rad/s max angular, 10 s max duration)
- `gimbal_*` — look_at (pan/tilt deg), reset, get_state
- `camera_*` — list, snapshot (base64 or `/snapshot/<cam>` URL)
- `status_*` — get_pose, get_odom, get_lidar_summary, list_nodes, list_topics, health
- `nav_*` — goto_point (Nav2 NavigateToPose action), cancel, status
- `system_*` — emergency_stop, servo_center, servo_release

### Controlling from Claude / ChatGPT / Gemini

```python
tools = requests.get("http://ugv-beast:8080/tools?format=anthropic").json()
# Hand tools to the LLM as-is. When it returns a tool_use block:
requests.post(f"http://ugv-beast:8080/tool/{name}", json=args).json()
```
See `scripts/ugv-llm-demo.py` for a working multi-turn tool-use demo.

### Service management

| Action | Command (on Jetson host) |
|--------|--------------------------|
| Restart API | `sudo systemctl restart ugv-tools-api.service` |
| Stop API | `sudo systemctl stop ugv-tools-api.service` |
| Status | `sudo systemctl status ugv-tools-api.service --no-pager` |
| Logs | `sudo journalctl -u ugv-tools-api.service -f` |

The Nav2/SLAM stack (`ugv-waveshare.service`) must be up first — tool API depends on `bt_navigator` being available.

### Reachability test

```bash
# LAN:
./scripts/test-ugv-tools-remote.sh
# Tailscale:
UGV_HOST=ugv-beast ./scripts/test-ugv-tools-remote.sh
```

### Voice Control

**Wake phrase:** "Black Box Flight Recorder, \<your command\>"

What happens: mic → `ugv-ears` detects wake word (openWakeWord) → VAD cuts
your utterance → BlackBox `/stt` (Whisper) → BlackBox `/chat` (Claude + 22
UGV tools auto-injected) → Claude replies → Orchestrator streams the reply
to `ugv-voice` `/speak` → mpg123 → JBL. No API keys live on the Jetson.

**Three services on the Jetson host:**

| Service         | Port | Role                                    |
|-----------------|------|-----------------------------------------|
| `ugv-tools-api` | 8080 | HTTP→ROS2 tool bridge                   |
| `ugv-voice`     | 8081 | `POST /speak` → JBL                     |
| `ugv-ears`      | —    | mic loop (wake + VAD + STT + chat)      |

```bash
# Full E2E demo (pre-wake checks + 60s journal tail for wake phrase)
./scripts/ugv-voice-demo.sh
# Pre-wake checks only (no interaction required)
SKIP_WAKE=1 ./scripts/ugv-voice-demo.sh

# Direct speak test (bypasses wake + STT + chat)
curl -X POST http://192.168.1.155:8081/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"UGV online"}'

# Live diagnosis
sudo journalctl -u ugv-ears.service  -f   # wake + STT + chat
sudo journalctl -u ugv-voice.service -f   # /speak + mpg123
```

Common fixes: wake never fires → `arecord -l` + adjust `MIC_DEVICE_HINT`;
JBL silent → `plughw:CARD=Device` + volume wheel; `blackbox_reachable:false`
→ wait 30 s for Tailscale. Full troubleshooting in
`docs/ugv-beast/setup/ugv_tools_api/README.md#voice-interface`.
