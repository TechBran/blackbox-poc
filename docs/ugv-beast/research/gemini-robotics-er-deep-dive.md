# Gemini Robotics ER 1.5 — Deep Dive Research

> Researched: 2026-04-06 | For: UGV Beast PT BlackBox Integration

---

## What Is Gemini Robotics ER?

Google DeepMind's **Gemini Robotics** family (March 2025), three models:

| Model | Type | Purpose | Availability |
|-------|------|---------|-------------|
| **Gemini Robotics** | Vision-Language-Action (VLA) | Direct motor control | Trusted testers only |
| **Gemini Robotics-ER** | Vision-Language Model (VLM) | Embodied Reasoning — perception, planning, spatial reasoning | **Public preview** via Gemini API |
| **Gemini Robotics On-Device** | Compact VLA | Local on-robot inference | Trusted testers / fine-tuning partners |

### "ER" = Embodied Reasoning

The **brain layer** — does NOT output motor commands. Instead it:
- **Spatial understanding**: Object detection, 3D point correspondence, bounding boxes, depth/affordance reasoning
- **Task planning**: Decomposes "clean up the table" into sequenced sub-tasks
- **Progress estimation**: Analyzes video to determine what steps are completed
- **Tool orchestration**: Calls robot APIs via function calling
- **Temporal reasoning**: Per-second analysis of robotic motion from video

### How It Differs from Regular Gemini
- Fine-tuned spatial precision: 2D coordinates normalized 0–1000 scale (SOTA pointing accuracy)
- Physical world understanding: object sizes, weights, affordances, constraints
- Thinking model: tunable "thinking budget" (0=fast, higher=complex reasoning)
- ASIMOV safety benchmark for physical safety

---

## API Access

### Model ID
```
gemini-robotics-er-1.5-preview
```

### Python SDK
```python
from google import genai
from google.genai import types

client = genai.Client(api_key="YOUR_GEMINI_API_KEY")

response = client.models.generate_content(
    model="gemini-robotics-er-1.5-preview",
    contents=[
        types.Part.from_bytes(data=image_bytes, mime_type='image/png'),
        "Point to every graspable object. Return JSON: [{\"point\": [y, x], \"label\": \"name\"}]"
    ],
    config=types.GenerateContentConfig(
        temperature=0.5,
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    )
)
```

### REST API
```bash
curl -X POST \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-robotics-er-1.5-preview:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{
      "parts": [
        {"inline_data": {"mime_type": "image/png", "data": "<base64>"}},
        {"text": "Identify all objects and return bounding boxes"}
      ]
    }]
  }'
```

### Inputs
- **Text**: Natural language instructions, prompts, system instructions
- **Images**: Camera frames (PNG, JPEG)
- **Video**: Video clips for temporal reasoning / progress estimation
- **Up to 1,048,576 input tokens** (massive context for multi-frame sequences)

### Outputs (structured JSON text)

**Object Pointing** (normalized 0–1000):
```json
[{"point": [376, 508], "label": "small banana"},
 {"point": [210, 715], "label": "red cup"}]
```

**Bounding Boxes** (`[ymin, xmin, ymax, xmax]`, normalized 0–1000):
```json
[{"box_2d": [100, 200, 400, 600], "label": "laptop"}]
```

**Trajectories** (sequenced waypoints):
```json
[{"point": [100, 200], "label": "0"},
 {"point": [150, 300], "label": "1"},
 {"point": [200, 400], "label": "2"}]
```

**Function Calls** (robot API orchestration):
```json
[
  {"function": "move", "args": [163, 427, true]},
  {"function": "setGripperState", "args": [false]},
  {"function": "returnToOrigin", "args": []}
]
```

**Task Plans** (multi-step with spatial grounding):
```
Step 1: Pick up the red cup at [376, 508]
Step 2: Move to the sink area at [800, 200]
Step 3: Place cup in sink
```

### Pricing
| Tier | Input | Output (incl. thinking) |
|------|-------|------------------------|
| Free | Free | Free |
| Paid | $0.30/1M tokens | $2.50/1M tokens |

### Supported Features
- Function calling: **YES**
- Search grounding: YES
- Structured outputs: YES
- Thinking (flexible budget): YES
- Code execution: YES
- Live API (WebSocket): **NO** (use Gemini 2.5 for streaming)
- Image generation: NO
- Caching: NO

---

## Robotics Capabilities

### Camera Feed Processing
Poll-based, NOT streaming. Workflow:
1. Capture frame from robot camera
2. Encode as PNG/JPEG
3. Send to API
4. Receive spatial reasoning (objects, coordinates, plans)
5. Map coordinates to robot frame → execute
6. Repeat

### Latency
- **thinking_budget=0**: Sub-second for simple spatial tasks
- **Higher thinking**: Multiple seconds for complex reasoning
- **Practical cloud round-trip**: 500ms–2s per API call

### NOT Suitable For
- Real-time reactive obstacle avoidance (<100ms) — use local Nav2
- Tight control loops — use local controllers
- Emergency stops — use local safety nodes

---

## Architecture for BlackBox + UGV Integration

### Dual-Loop Architecture
```
FAST LOOP (Local, 10-50Hz)                SLOW LOOP (Cloud, 0.5-2Hz)
Nav2 + LiDAR costmap                      Gemini Robotics-ER
  → obstacle avoidance                      → scene understanding
  → path following                          → object identification
  → emergency stop                          → mission planning
  → reactive control                        → progress estimation
```

### Data Flow
```
Camera (ROS2) ──┐
                 ▼
         BlackBox Orchestrator
         (WebSocket bridge node)
                 │
         ┌───────┼───────┐
         ▼               ▼
    Gemini ER API    Gemini 2.5 Live API
    (spatial reasoning)  (voice + camera streaming)
         │               │
         ▼               ▼
    Parse JSON       Parse function calls
         │               │
         └───────┬───────┘
                 ▼
         ROS2 Action Execution
         /cmd_vel, /goal_pose, /gimbal/absolute
```

### Function Calling for Robot APIs
```python
robot_tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="navigate_to",
            description="Navigate the mobile base to a location",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(type="STRING"),
                }
            )
        ),
        types.FunctionDeclaration(
            name="start_exploration",
            description="Start autonomous frontier exploration"
        ),
        types.FunctionDeclaration(
            name="stop_robot",
            description="Emergency stop all movement"
        ),
        types.FunctionDeclaration(
            name="look_at",
            description="Point the gimbal camera at coordinates",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "pan": types.Schema(type="NUMBER"),
                    "tilt": types.Schema(type="NUMBER"),
                }
            )
        ),
    ])
]
```

### Coordinate Mapping (0–1000 → real world)
```python
# Gemini returns [y, x] normalized to 0-1000
gemini_y, gemini_x = point["point"]

# Convert to pixel coordinates
pixel_x = (gemini_x / 1000.0) * 640  # image width
pixel_y = (gemini_y / 1000.0) * 480  # image height

# Use OAK-D camera intrinsics + depth for 3D
depth_mm = depth_frame[int(pixel_y), int(pixel_x)]
x_3d = (pixel_x - 327.6) * (depth_mm/1000) / 461.3  # fx from EEPROM
y_3d = (pixel_y - 239.3) * (depth_mm/1000) / 461.3  # fy from EEPROM
z_3d = depth_mm / 1000.0
```

### Mission Execution Pattern
1. Send initial scene image + high-level goal ("inspect all rooms")
2. ER decomposes into sub-tasks
3. Execute first sub-task on robot (Nav2 goal, gimbal command)
4. Send updated camera frame + previous plan context
5. ER evaluates progress and issues next command
6. Repeat until ER reports completion

---

## Gemini ER vs Anthropic Computer Use

| Aspect | Gemini Robotics-ER | Anthropic Computer Use |
|--------|-------------------|----------------------|
| Domain | Physical robotics (real world) | Digital interfaces (screens) |
| Input | Camera images, video, sensors | Screenshots |
| Output | Spatial coords, function calls, task plans | Mouse clicks, keyboard input |
| Precision | 0–1000 normalized, SOTA pointing | Pixel coordinates on display |
| Real-time | Poll-based (~500ms-2s) | Screenshot-based loop |
| Best for | Navigation, manipulation, warehouse | Software automation, web interaction |

**Complementary, not competing.** Both use "observe → reason → act" loop but in different domains.

---

## BlackBox Integration Plan

### Phase 1: Basic Camera → ER → Display
- Send pan-tilt camera frame to ER every 2 seconds
- Display ER's object annotations in Portal (like CU screenshot viewer)
- Show spatial reasoning in chat

### Phase 2: Function Calling for Robot Control
- Define robot tools (navigate, explore, stop, look_at, lights)
- ER calls functions → BlackBox executes on robot via ROS2
- Mission execution loop with progress tracking

### Phase 3: Live Voice + Camera (Gemini 2.5 Live API)
- WebSocket streaming of camera + voice
- Talk to the robot, it responds and acts
- ER called on-demand for complex spatial reasoning

### Phase 4: Full Autonomy
- ER plans entire missions from high-level goals
- Local Nav2 handles reactive obstacle avoidance
- ER monitors progress and replans as needed
- BlackBox snapshots document every mission

---

## Key Resources

### Official Docs
- [Gemini Robotics-ER API Docs](https://ai.google.dev/gemini-api/docs/robotics-overview)
- [DeepMind Gemini Robotics](https://deepmind.google/models/gemini-robotics/)
- [ER Product Page](https://deepmind.google/models/gemini-robotics/gemini-robotics-er/)

### Blog Posts
- [Gemini Robotics Launch (March 2025)](https://deepmind.google/blog/gemini-robotics-brings-ai-into-the-physical-world/)
- [Gemini Robotics 1.5 Update (Sept 2025)](https://deepmind.google/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/)
- [Developer Deep Dive](https://developers.googleblog.com/building-the-next-generation-of-physical-agents-with-gemini-robotics-er-15/)

### Code
- [Official Cookbook Notebook](https://github.com/google-gemini/cookbook/blob/main/quickstarts/gemini-robotics-er.ipynb)
- [Awesome Gemini Robotics (community)](https://github.com/GitHub30/Awesome-Gemini-Robotics)

### Technical Paper
- [Gemini Robotics 1.5 Tech Report (PDF)](https://storage.googleapis.com/deepmind-media/gemini-robotics/Gemini-Robotics-1-5-Tech-Report.pdf)
- [arXiv Paper](https://arxiv.org/html/2503.20020v1)

---

## Limitations
1. No direct motor output — reasoning only, needs translation layer
2. No Live API / WebSocket — poll-based only (use Gemini 2.5 for streaming)
3. Cloud latency 500ms–2s — not for tight control loops
4. Preview status — may change, stricter rate limits
5. 2D coordinates only — need depth camera for 3D projection
6. Prompt sensitivity — output quality depends on prompt clarity
