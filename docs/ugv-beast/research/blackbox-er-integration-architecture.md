# BlackBox ↔ Gemini Robotics-ER Integration Architecture

> Designed: 2026-04-06 | Author: Brandon | For: UGV Beast PT

---

## Core Concept

The BlackBox is the **orchestration layer** (frontal cortex/brain). Gemini Robotics-ER provides **reasoning and spatial intelligence**. The Jetson/ROS2 stack provides **motor control and sensor fusion**. Audio I/O is handled by BlackBox's existing TTS/VAD infrastructure.

```
┌─────────────────────────────────────────────────────┐
│                   BLACKBOX (Brain)                    │
│                                                       │
│  Portal / Android MVP                                 │
│  ├── Provider: Google                                 │
│  ├── Model: gemini-robotics-er-1.5-preview            │
│  ├── Camera feed: Pan-tilt (default) / OAK-D toggle   │
│  ├── Chat: ER reasoning + user commands               │
│  └── Audio: TTS output → robot speaker                │
│                                                       │
│  Orchestrator API (FastAPI)                            │
│  ├── /chat endpoint (existing)                        │
│  ├── ER model handler (new, like CU handler)          │
│  ├── Robot tool execution bridge                      │
│  └── Operator lock (one robot, one operator)          │
└───────────────────────┬───────────────────────────────┘
                        │ HTTP/WebSocket
                        ▼
┌─────────────────────────────────────────────────────┐
│              JETSON ORIN NANO (Body)                   │
│                                                       │
│  ROS2 Stack (14 modules, already working)             │
│  ├── Camera feeds → BlackBox (JPEG frames)            │
│  ├── Sensor data → BlackBox (depth, LiDAR, YOLO)     │
│  ├── Tool execution ← BlackBox (navigate, look, etc.) │
│  ├── TTS audio playback ← BlackBox                    │
│  └── Mic/VAD → BlackBox (when ER activates it)        │
└───────────────────────────────────────────────────────┘
```

---

## Model Selection Flow

### In Portal / Android MVP

1. User selects **Provider: Google**
2. Model dropdown shows `gemini-robotics-er-1.5-preview` (from fetch model list)
3. On selection, UI transitions to **Robot Mode**:
   - Camera feed panel appears (dropdown: Pan-tilt / OAK-D)
   - Pan-tilt is default view
   - Chat area shows ER reasoning + actions as messages
   - User can type commands into chat while robot is on a mission
   - ER's text responses get sent as TTS audio to robot speaker

### Reused Patterns
- **Computer Use**: Same SSE streaming, same device targeting, same "observe → reason → act" loop
- **Model config**: Same `MODEL_CONFIG` in state-management.js, same fetch model list API
- **Operator scoping**: Same `getOperator()` pattern, extended with robot lock

---

## Audio Architecture

```
USER INPUT:
  Portal chat input (text) ──→ ER model (as user message)
  Portal mic (voice) ──→ STT ──→ ER model (as text)
  Robot mic (VAD) ──→ STT ──→ ER model (as text)  [ONLY when ER activates it]

ER OUTPUT:
  ER reasoning (text) ──→ Portal chat (displayed)
                      ──→ BlackBox TTS engine ──→ Robot speaker (audio)
  ER tool calls ──→ BlackBox executes on Jetson via ROS2
```

### Key Design Decisions
- **ER model outputs TEXT only** — no audio generation from ER itself
- **BlackBox handles TTS** — uses best available engine (OpenAI HD, Gemini Pro, etc.)
- **Robot mic is a TOOL** — ER model has `activate_mic()` tool to start listening
- **Portal is primary input** — robot mic is secondary, activated on demand
- **Audio is optional** — text chat works without any audio

---

## ER Model Tool Schema

```python
robot_tools = [
    # Navigation
    navigate_to(location: str)           # "kitchen", "hallway", or "x:1.5,y:2.0"
    start_exploration()                   # Roomba mode (existing)
    stop_exploration()                    # Stop autonomous exploration
    stop_robot()                          # Emergency stop all movement
    return_home()                         # Navigate to (0,0)

    # Camera / Gimbal
    look_at(pan: float, tilt: float)      # Point gimbal camera
    reset_camera()                        # Gimbal to home (0,0)
    switch_camera(camera: str)            # "pantilt" or "oakd"
    capture_frame()                       # Get high-res snapshot for analysis

    # Lights
    set_lights(base: int, head: int)      # 0-255 each
    lights_on()
    lights_off()

    # Sensing
    get_depth_at(x: int, y: int)          # Depth at pixel coordinate (mm)
    get_yolo_detections()                 # Current YOLO detection list
    get_slam_map()                        # Current map as image
    get_robot_pose()                      # Current x, y, heading

    # Audio
    activate_mic(duration: float)         # Listen for N seconds, return transcript
    speak(text: str)                      # TTS output to robot speaker
    play_sound(sound: str)                # Alert, chime, etc.

    # Maps
    save_map()                            # Save 2D + 3D maps
    list_maps()                           # List saved maps

    # Mission
    report(message: str)                  # Send status to Portal chat
    set_tracking_target(class_name: str)  # Change YOLO gimbal tracking target
    disable_tracking()                    # Stop gimbal auto-tracking
]
```

---

## Operator Locking

```
When operator selects ER model:
  1. Check: is another operator using the robot?
     → YES: Show warning "Robot in use by {operator}. Please wait."
             Disable send button.
     → NO:  Lock robot to this operator.
            Store: robot_lock = {operator, session_id, timestamp}

When operator disconnects or switches model:
  → Release lock
  → Robot returns to IDLE (optional: stop exploration)

Implementation:
  - Redis key or in-memory dict in Orchestrator
  - Check on every /chat request with ER model
  - Auto-expire after 5 min inactivity
```

---

## SSE Event Types (Portal ← Orchestrator)

Reuse Computer Use SSE pattern:

```
event: er_frame        # Camera frame from robot (JPEG base64)
data: {"camera": "pantilt", "image": "base64...", "timestamp": ...}

event: er_reasoning    # ER model's thinking/reasoning text
data: {"text": "I see a hallway with two doors...", "thinking": true}

event: er_action       # ER model executed a tool
data: {"tool": "navigate_to", "args": {"location": "kitchen"}, "status": "executing"}

event: er_detection    # YOLO detection update
data: {"detections": [{"class": "person", "distance": "3'10\"", ...}]}

event: er_status       # Robot status update
data: {"state": "exploring", "battery": 72, "pose": {"x": 1.5, "y": 2.0}}

event: er_audio        # TTS audio chunk for robot speaker
data: {"audio_url": "/ui/uploads/tts_chunk.mp3"}

event: er_error        # Error message
data: {"error": "Navigation failed: path blocked"}
```

---

## Request Flow: User Sends Command

```
1. User types: "Go check the kitchen for my keys"
   → Portal sends to /chat endpoint with operator + ER model

2. Orchestrator:
   a. Captures current camera frame from robot
   b. Sends to ER: [camera_frame, "Go check the kitchen for my keys", tool_declarations]
   c. Streams SSE events to Portal as ER responds

3. ER responds with function calls:
   → navigate_to("kitchen")
   → SSE: er_action {tool: "navigate_to", args: {location: "kitchen"}}

4. Orchestrator executes on Jetson:
   → ROS2: publish goal to /goal_pose
   → Wait for Nav2 to reach goal

5. Orchestrator captures new frame, sends back to ER:
   → ER: "I'm in the kitchen. I see a counter with several items."
   → SSE: er_reasoning {text: "..."}
   → ER: look_at(pan=30, tilt=-15)
   → ER: "I can see keys on the counter near the toaster."
   → ER: report("Found your keys on the kitchen counter, near the toaster, approximately 2'8\" away")
   → SSE: er_action {tool: "report", args: {message: "..."}}

6. Portal displays ER's reasoning as chat + TTS to robot speaker
```

---

## Implementation Phases

### Phase 1: Basic ER Chat + Camera View
- Add ER model to model list (chat_routes.py)
- New handler: `stream_er_robotics()` (like `stream_computer_use()`)
- Camera frame capture from robot (HTTP endpoint on Jetson)
- Display camera feed in Portal (like CU screenshot)
- ER reasoning displayed as chat messages
- No tool execution yet — just "look and think"

### Phase 2: Tool Execution
- Define tool schemas for robot actions
- Tool execution bridge: Orchestrator → ROS2 (HTTP/WebSocket to Jetson)
- Navigate, look_at, lights, save_map
- SSE events for tool actions

### Phase 3: Audio Integration
- TTS: ER text → BlackBox TTS → robot speaker
- Mic activation as a tool
- VAD on robot for wake-word or on-demand listening

### Phase 4: Full Mission Autonomy
- Multi-step mission planning
- Progress estimation from camera frames
- Dynamic replanning when obstacles/changes detected
- Operator override at any time via chat
- Mission logging to BlackBox snapshots

### Phase 5: Android MVP
- Robot mode in Android app
- Camera feed panel with gimbal controls
- Mission status overlay
- Voice input/output

---

## Existing BlackBox Components to Reuse

| Component | Existing | Adapt for Robot |
|-----------|----------|----------------|
| Chat endpoint | `/chat` | Add ER model handler |
| SSE streaming | `stream_computer_use()` | `stream_er_robotics()` |
| Tool execution | CU bash/click actions | ROS2 navigate/look/etc |
| Device targeting | CU `device_id` param | Robot as a "device" |
| Camera view | CU screenshot viewer | Robot camera JPEG stream |
| Model config | `MODEL_CONFIG` | Add ER model entry |
| Operator scope | `getOperator()` | Add robot lock |
| TTS | `text_to_speech` tool | Route to robot speaker |
| STT | `speech_to_text` tool | Robot mic input |
| Snapshots | Auto-mint | Mission logging |

---

## File Structure (planned)

```
Orchestrator/
├── routes/
│   └── robotics_routes.py        # NEW: /robotics/* endpoints
├── robotics/
│   ├── __init__.py
│   ├── er_handler.py             # ER model streaming handler
│   ├── robot_bridge.py           # ROS2 command execution bridge
│   ├── camera_capture.py         # Frame capture from robot cameras
│   └── tools.py                  # Robot tool declarations
Portal/
├── modules/
│   └── robotics-panel.js         # NEW: Robot control UI
├── styles/features/
│   └── _robotics.css             # NEW: Robot mode styles
```
