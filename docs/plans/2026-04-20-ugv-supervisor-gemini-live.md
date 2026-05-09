# UGV Beast Supervisor — Gemini Live Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a production-grade `ugv-supervisor` service on the Jetson that uses Gemini Live (gemini-3.1-flash-live-preview) as a voice-driven mission supervisor over the UGV Beast. The supervisor speaks with the operator, commands the Gemini Robotics-ER agent to execute one mission at a time, watches state via on-demand tool calls (pose, lidar, camera frames, costmap), and issues emergency stops when needed.

**Architecture:**
- Single long-running Python service on the Jetson that owns the operator mic + speaker, opens a Gemini Live WebSocket via `google-genai`, and exposes a fixed tool surface (mission control + perception + aesthetics).
- **Tool-driven perception, not continuous streaming.** Camera and costmap are tools the model calls on demand. Audio is continuous in both directions.
- **Google-recommended session lifecycle:** context-window compression for unlimited duration, session resumption with persisted handle, GoAway-driven proactive reconnect, separate thread pool for mic reads so rclpy can't starve it.
- Wake-word gating via the existing `ugv-ears` stays in the chain but is rewired to POST `/supervisor/open_session` instead of calling the Whisper path.
- ER (`ugv-er.service`) becomes strictly stateless — one POST `/mission`, one completion, then sleep. All orchestration moves to the supervisor.

**Tech Stack:**
- Python 3.10 inside the `ugv_waveshare` container on Jetson Orin Nano
- `google-genai` 1.73+ (Live 3.1 SDK)
- `rclpy` (Humble) for ROS topic subscriptions (`/camera/image/compressed`, `/global_costmap/costmap`, `/odom`, `/scan`)
- `arecord`/`aplay` subprocesses for ALSA audio on camera mic (`plughw:CARD=Camera,DEV=0`) and JBL speaker via USB DAC (`plughw:CARD=Device,DEV=0`)
- `httpx` for HTTP calls to `ugv_tools_api` (`:8080`) and `ugv-er` (`:8082`)
- `pytest` for unit tests, ad-hoc interactive spike replays for integration

**Out of scope:**
- BlackBox-side integration (handled in a follow-up: expose `ugv_dispatch_supervisor` as a BlackBox tool schema once the Jetson service is stable)
- Hardware echo cancellation (we handle speaker→mic loopback via VAD tuning + an operator silent-wait protocol, not AEC)
- Android app changes

---

## Empirical Knowledge Carried Forward From the Spike

The 12 iterations of `supervisor/live_spike.py` on 2026-04-20 surfaced concrete issues that must be baked into the production service. Each task in this plan that touches these areas must include a regression-guard comment or test so the fix doesn't get undone.

| # | Lesson | Where it applies |
|---|---|---|
| 1 | Use `response.data` (SDK concatenator), not manual `model_turn.parts` iteration, to get output audio | `session.py` response pump |
| 2 | Mic reads MUST use a dedicated `ThreadPoolExecutor(max_workers=1)`; default pool gets starved by `rclpy.spin_once` | `audio_io.py` |
| 3 | Live 3.1's SDK can't JSON-serialize `FunctionResponseBlob` with bytes. Return images via `send_realtime_input(video=...)` sidechannel instead | `tools.py` `get_camera_view` + `get_costmap_view` |
| 4 | Set `speech_config.language_code="en-US"` or Gemini auto-detects the ambient mic fuzz as Korean/Spanish | `session.py` config builder |
| 5 | Set `realtime_input_config.automatic_activity_detection` with `start_of_speech_sensitivity=HIGH`, `end_of_speech_sensitivity=LOW` to reliably pick up the operator's first syllable | `session.py` config builder |
| 6 | Play an 880 Hz sine chime (~180 ms) through speaker when session opens — without it, operator has no idea when mic is hot | `audio_io.py` ready-cue |
| 7 | Operator's speaker-to-mic bleed-back confuses Gemini's turn-taking if they speak during its greeting. Document the silent-wait protocol in operator README and mitigate with high sensitivity + low speaker volume | docs + default config |
| 8 | `arecord -q -D plughw:CARD=Camera,DEV=0 -f S16_LE -r 16000 -c 1 -t raw` is the confirmed-working mic command; `aplay -D plughw:CARD=Device,DEV=0 -f S16_LE -r 24000 -c 1 -t raw --buffer-size=24000` is the confirmed-working speaker command | `audio_io.py` |
| 9 | 20 ms mic frames (640 bytes) sent continuously hits 64000 B/s target. Report rate metric every 2 s for debuggability | `audio_io.py` telemetry |
| 10 | SDK's internal keepalive ping will timeout if we let the session sit idle too long with no real audio; expected and doesn't mean the pipeline is broken | reconnect logic |
| 11 | ESP32 IMU is broken; we use OAK-D IMU via EKF. Supervisor's `get_robot_state` pulls from the *fused* `/odom` not raw encoder data | `tools.py` |
| 12 | Nav2 controller is DWB via RotationShim on SMAC — not MPPI. Don't try to tune MPPI params from `nav2_explore_params.yaml` (red herring file) | plan only — no code |

**Google-docs-confirmed production patterns (added on top of the spike lessons):**

| Pattern | Source | Where it applies |
|---|---|---|
| `context_window_compression=ContextWindowCompressionConfig(sliding_window=SlidingWindow())` to remove the 15-min audio / 2-min audio+video session cap | Google Live docs | `session.py` config |
| Persist `session_resumption_update.new_handle` to disk every time it's received (valid 2 hr post-termination) | Google Live docs | `session.py` handle-store |
| Handle `response.go_away` — it tells us `time_left` before disconnect. Proactively open a new session before the old one dies | Google Live docs | `session.py` reconnect |
| `session_resumption=SessionResumptionConfig(handle=stored)` on reconnect | Google Live docs | `session.py` reconnect |

---

## Target Repository Layout

```
docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/
├── __init__.py
├── config.py              # env-resolved constants + dataclasses
├── audio_io.py            # arecord/aplay subprocess wrappers + chime + mic pool
├── video_io.py            # ROS subscriber for camera & costmap → FrameCache
├── tool_declarations.py   # Gemini FunctionDeclaration objects (schema only)
├── tool_handlers.py       # async handlers — HTTP calls to ugv_tools_api / ugv-er
├── session.py             # Live session lifecycle: connect, config, goaway, resume
├── handle_store.py        # Persist session_resumption_update.new_handle to /var/lib
├── service.py             # FastAPI app exposing /health + /supervisor/open_session
├── main.py                # Entry point; systemd target
└── README.md              # Operator usage notes (wake word, silent-wait, etc.)

docs/ugv-beast/setup/ugv_tools_api/deploy/
├── start_supervisor.sh    # Container entrypoint wrapper
└── supervisor.env         # Env-file for systemd unit

/etc/systemd/system/
└── ugv-supervisor.service # systemd unit (host, wraps docker exec)

tests/supervisor/
├── test_config.py
├── test_handle_store.py
├── test_tool_declarations.py
├── test_tool_handlers.py
├── test_audio_io.py
└── test_video_io.py
```

---

## Implementation Tasks

Tasks are ordered so each leaves the repo in a working state. Tasks 1–5 are pure refactor and can ship without any runtime risk. Task 6+ affect the live robot.

---

### Task 1: Scaffold the supervisor package

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/__init__.py`
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py`
- Create: `tests/supervisor/__init__.py`
- Create: `tests/supervisor/test_config.py`

**Step 1: Write the failing test**

```python
# tests/supervisor/test_config.py
import os
from ugv_tools_api.supervisor import config as cfg


def test_defaults(monkeypatch):
    for v in ("SUPERVISOR_MODEL", "SUPERVISOR_VOICE", "SUPERVISOR_MIC",
              "SUPERVISOR_SPK", "TOOLS_API_URL", "ER_URL",
              "SUPERVISOR_CAMERA_TOPIC", "SUPERVISOR_COSTMAP_TOPIC"):
        monkeypatch.delenv(v, raising=False)
    s = cfg.load()
    assert s.model == "gemini-3.1-flash-live-preview"
    assert s.voice == "Orus"
    assert s.mic_device == "plughw:CARD=Camera,DEV=0"
    assert s.spk_device == "plughw:CARD=Device,DEV=0"
    assert s.tools_api_url == "http://localhost:8080"
    assert s.er_url == "http://localhost:8082"
    assert s.camera_topic == "/camera/image/compressed"
    assert s.costmap_topic == "/global_costmap/costmap"
    assert s.handle_store_path.endswith("session_handle.txt")


def test_override_via_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_VOICE", "Charon")
    monkeypatch.setenv("TOOLS_API_URL", "http://10.0.0.5:8080")
    s = cfg.load()
    assert s.voice == "Charon"
    assert s.tools_api_url == "http://10.0.0.5:8080"


def test_google_api_key_required(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        cfg.load()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'ugv_tools_api.supervisor.config'`)

**Step 3: Write minimal implementation**

```python
# docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py
"""Env-resolved configuration for the supervisor service.

Fails fast at import/load time if GOOGLE_API_KEY is missing so systemd
surfaces the error instead of silently failing on first tool call.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SupervisorConfig:
    google_api_key: str
    model: str
    voice: str
    language_code: str
    mic_device: str
    spk_device: str
    tools_api_url: str
    er_url: str
    camera_topic: str
    costmap_topic: str
    handle_store_path: str
    # Audio format constants
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    chunk_ms: int = 20


def load() -> SupervisorConfig:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is required but not set")
    return SupervisorConfig(
        google_api_key=key,
        model=os.environ.get("SUPERVISOR_MODEL", "gemini-3.1-flash-live-preview"),
        voice=os.environ.get("SUPERVISOR_VOICE", "Orus"),
        language_code=os.environ.get("SUPERVISOR_LANG", "en-US"),
        mic_device=os.environ.get("SUPERVISOR_MIC", "plughw:CARD=Camera,DEV=0"),
        spk_device=os.environ.get("SUPERVISOR_SPK", "plughw:CARD=Device,DEV=0"),
        tools_api_url=os.environ.get("TOOLS_API_URL", "http://localhost:8080"),
        er_url=os.environ.get("ER_URL", "http://localhost:8082"),
        camera_topic=os.environ.get("SUPERVISOR_CAMERA_TOPIC", "/camera/image/compressed"),
        costmap_topic=os.environ.get("SUPERVISOR_COSTMAP_TOPIC", "/global_costmap/costmap"),
        handle_store_path=os.environ.get("SUPERVISOR_HANDLE_STORE", "/var/lib/ugv_supervisor/session_handle.txt"),
    )
```

```python
# ugv_tools_api/supervisor/__init__.py
# (empty marker file)
```

```python
# tests/supervisor/__init__.py
# (empty marker file)
```

**Step 4: Run test to verify it passes**

Run: `GOOGLE_API_KEY=test pytest tests/supervisor/test_config.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor tests/supervisor
git commit -m "feat(supervisor): scaffold package with env-resolved config"
```

---

### Task 2: Session handle persistence

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/handle_store.py`
- Create: `tests/supervisor/test_handle_store.py`

**Why:** Google Live docs state session resumption handles are valid for 2 hr after session termination. We persist the latest handle to disk so a service restart can reconnect without losing mid-mission conversation state.

**Step 1: Write the failing test**

```python
# tests/supervisor/test_handle_store.py
from pathlib import Path
from ugv_tools_api.supervisor.handle_store import HandleStore


def test_set_and_get(tmp_path):
    p = tmp_path / "session_handle.txt"
    s = HandleStore(p)
    assert s.get() is None
    s.set("handle-abc-123")
    assert s.get() == "handle-abc-123"
    # Survives new instance (simulates restart)
    s2 = HandleStore(p)
    assert s2.get() == "handle-abc-123"


def test_clear(tmp_path):
    p = tmp_path / "session_handle.txt"
    s = HandleStore(p)
    s.set("x")
    s.clear()
    assert s.get() is None


def test_creates_parent_dir(tmp_path):
    p = tmp_path / "sub" / "dir" / "session_handle.txt"
    s = HandleStore(p)
    s.set("y")
    assert p.exists()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_handle_store.py -v`
Expected: FAIL (import error)

**Step 3: Write minimal implementation**

```python
# ugv_tools_api/supervisor/handle_store.py
"""Persist Gemini Live session_resumption handles across process restarts.

Google Live sessions emit SessionResumptionUpdate events carrying a handle
that lets us resume a session later. Handles are valid for 2 hr after the
session terminates. We persist the newest handle to disk so a systemd
restart (or brief crash) can resume the operator's mission conversation
without starting over.
"""
from pathlib import Path
from typing import Optional, Union


class HandleStore:
    def __init__(self, path: Union[str, Path]):
        self._path = Path(path)

    def set(self, handle: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(handle)

    def get(self) -> Optional[str]:
        if not self._path.exists():
            return None
        text = self._path.read_text().strip()
        return text or None

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/supervisor/test_handle_store.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/handle_store.py tests/supervisor/test_handle_store.py
git commit -m "feat(supervisor): persistent session-resumption handle store"
```

---

### Task 3: Tool declarations (schema only)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/tool_declarations.py`
- Create: `tests/supervisor/test_tool_declarations.py`

**Why:** Separate schema from execution so the declarations can be inspected, exported to BlackBox later, and tested without any runtime deps.

**Tool surface (final):**

| Tool | Purpose |
|---|---|
| `get_robot_state` | Pose + velocity + 8-sector lidar minima (existing, proven in spike) |
| `get_camera_view` | Single pan-tilt JPEG on demand (existing, proven in spike) |
| `get_costmap_view` | Current global costmap rendered as PNG (new in this plan) |
| `dispatch_er_mission` | Send a natural-language mission to ER `:8082/mission`. Optionally cancels any active mission first. |
| `cancel_er_mission` | POST `/mission/{active_id}/abort` to ER |
| `get_er_mission_status` | GET `/mission/{active_id}` from ER |
| `emergency_stop` | Direct POST to `ugv_tools_api:8080/tool/system_emergency_stop` — bypasses ER |
| `lights_on` / `lights_off` | Aesthetics, direct to `ugv_tools_api` |
| `gimbal_look_at` | Direct pan/tilt, direct to `ugv_tools_api` |

**Step 1: Write the failing test**

```python
# tests/supervisor/test_tool_declarations.py
from ugv_tools_api.supervisor.tool_declarations import ALL_TOOLS, tool_names


EXPECTED = {
    "get_robot_state", "get_camera_view", "get_costmap_view",
    "dispatch_er_mission", "cancel_er_mission", "get_er_mission_status",
    "emergency_stop", "lights_on", "lights_off", "gimbal_look_at",
}


def test_all_tools_present():
    assert tool_names() == EXPECTED


def test_dispatch_er_mission_schema_has_required_mission_param():
    d = next(t for t in ALL_TOOLS if t.name == "dispatch_er_mission")
    props = d.parameters.properties
    assert "mission" in props
    assert "mission" in (d.parameters.required or [])


def test_gimbal_params_have_ranges():
    d = next(t for t in ALL_TOOLS if t.name == "gimbal_look_at")
    props = d.parameters.properties
    for axis in ("pan_deg", "tilt_deg"):
        assert axis in props
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_tool_declarations.py -v`
Expected: FAIL (import error)

**Step 3: Write minimal implementation**

```python
# ugv_tools_api/supervisor/tool_declarations.py
"""Gemini Live FunctionDeclaration objects for the UGV Beast supervisor.

Schema-only; execution lives in tool_handlers.py. Keeping these split lets
the supervisor tool surface be inspected and reused (e.g., exported as a
BlackBox tool schema) without importing the handler's runtime deps.
"""
from google.genai import types


def _obj(properties: dict, required=None) -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        properties=properties,
        required=list(required or []),
    )


def _str(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc)


def _num(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.NUMBER, description=desc)


def _bool(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.BOOLEAN, description=desc)


def _enum(desc: str, values: list) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc, enum=values)


GET_ROBOT_STATE = types.FunctionDeclaration(
    name="get_robot_state",
    description=(
        "Read the robot's current fused pose (x, y, yaw), linear and "
        "angular velocity, and 8-sector lidar minimum distances. Use when "
        "the operator asks where the robot is, which way it is facing, or "
        "whether something is close to it."
    ),
    parameters=_obj({}),
)

GET_CAMERA_VIEW = types.FunctionDeclaration(
    name="get_camera_view",
    description=(
        "Capture a current frame from the pan-tilt camera and look at it. "
        "Use when the operator asks what you see, to verify a landmark, to "
        "check whether a path is clear, or to investigate a stall."
    ),
    parameters=_obj({}),
)

GET_COSTMAP_VIEW = types.FunctionDeclaration(
    name="get_costmap_view",
    description=(
        "Render the current Nav2 global costmap as an image and look at "
        "it. Use when you need to understand where the robot is on the "
        "map, where obstacles have been recorded, or why a navigation "
        "attempt failed."
    ),
    parameters=_obj({}),
)

DISPATCH_ER_MISSION = types.FunctionDeclaration(
    name="dispatch_er_mission",
    description=(
        "Send a natural-language mission to the robot's on-device "
        "execution agent (Gemini Robotics-ER). The ER agent will translate "
        "the mission into Nav2 goals and tool calls and execute one "
        "complete task before returning control. Examples: 'Drive to the "
        "kitchen and stop there', 'Map this room', 'Inspect the charger.'"
    ),
    parameters=_obj(
        {
            "mission": _str(
                "Plain-English mission instruction. Be specific about "
                "the goal and any constraints (e.g., 'slow speed', 'stop "
                "and report when you see a cat')."
            ),
            "replace_current": _bool(
                "If true, abort any in-flight mission before dispatching. "
                "If false (default) and a mission is already running, the "
                "call returns an error so the operator can decide."
            ),
        },
        required=["mission"],
    ),
)

CANCEL_ER_MISSION = types.FunctionDeclaration(
    name="cancel_er_mission",
    description=(
        "Abort the currently running ER mission. Use when the operator "
        "says 'stop' or 'cancel', or when you observe the mission is "
        "going wrong (e.g., robot is stuck, lost, or heading somewhere "
        "it shouldn't)."
    ),
    parameters=_obj(
        {"reason": _str("Short explanation of why the mission is being canceled.")},
    ),
)

GET_ER_MISSION_STATUS = types.FunctionDeclaration(
    name="get_er_mission_status",
    description=(
        "Read the state of the currently running ER mission: id, status, "
        "last text the ER agent said, and recent events. Use when the "
        "operator asks how the mission is going."
    ),
    parameters=_obj({}),
)

EMERGENCY_STOP = types.FunctionDeclaration(
    name="emergency_stop",
    description=(
        "Immediately halt all robot motion at the firmware level. "
        "Bypasses ER. Use if you see imminent collision, the robot is "
        "doing something dangerous, or the operator shouts 'stop!'. "
        "After this, the ER mission is NOT canceled — call "
        "cancel_er_mission separately if you want to end the task."
    ),
    parameters=_obj({}),
)

LIGHTS_ON = types.FunctionDeclaration(
    name="lights_on",
    description=(
        "Turn on the robot's LEDs. Useful in dark environments or for "
        "visual acknowledgement when asked to 'light up' or 'say hi.'"
    ),
    parameters=_obj(
        {"which": _enum("Which LEDs to illuminate. Default 'both'.", ["gimbal", "bottom", "both"])},
    ),
)

LIGHTS_OFF = types.FunctionDeclaration(
    name="lights_off",
    description="Turn off the robot's LEDs.",
    parameters=_obj(
        {"which": _enum("Which LEDs to turn off. Default 'both'.", ["gimbal", "bottom", "both"])},
    ),
)

GIMBAL_LOOK_AT = types.FunctionDeclaration(
    name="gimbal_look_at",
    description=(
        "Point the pan-tilt gimbal to an absolute pan/tilt angle in "
        "degrees. Pan: -180..180 (negative=right, positive=left, 0=forward). "
        "Tilt: -45..90 (negative=down, positive=up). Use when you want to "
        "look around before taking a camera_view."
    ),
    parameters=_obj(
        {
            "pan_deg": _num("Pan angle in degrees (-180 to 180)."),
            "tilt_deg": _num("Tilt angle in degrees (-45 to 90)."),
        },
        required=["pan_deg", "tilt_deg"],
    ),
)


ALL_TOOLS = [
    GET_ROBOT_STATE, GET_CAMERA_VIEW, GET_COSTMAP_VIEW,
    DISPATCH_ER_MISSION, CANCEL_ER_MISSION, GET_ER_MISSION_STATUS,
    EMERGENCY_STOP, LIGHTS_ON, LIGHTS_OFF, GIMBAL_LOOK_AT,
]


def tool_names() -> set:
    return {t.name for t in ALL_TOOLS}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/supervisor/test_tool_declarations.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/tool_declarations.py tests/supervisor/test_tool_declarations.py
git commit -m "feat(supervisor): tool declarations for mission + perception + aesthetic"
```

---

### Task 4: Tool handlers (pure functions, mocked HTTP)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/tool_handlers.py`
- Create: `tests/supervisor/test_tool_handlers.py`

**Why:** Handlers are the executable body. Isolating them from session + audio lets us unit-test them against mocked `httpx` without setting up a Live session.

**Step 1: Write the failing test**

```python
# tests/supervisor/test_tool_handlers.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from ugv_tools_api.supervisor.tool_handlers import (
    exec_get_robot_state,
    exec_dispatch_er_mission,
    exec_cancel_er_mission,
    exec_get_er_mission_status,
    exec_emergency_stop,
    exec_lights,
    exec_gimbal_look_at,
    MissionTracker,
)


@pytest.fixture
def cfg():
    from ugv_tools_api.supervisor.config import SupervisorConfig
    return SupervisorConfig(
        google_api_key="x", model="m", voice="v", language_code="en-US",
        mic_device="", spk_device="", tools_api_url="http://tools",
        er_url="http://er", camera_topic="", costmap_topic="",
        handle_store_path="",
    )


@pytest.mark.asyncio
async def test_get_robot_state_calls_both_endpoints(cfg):
    mock = AsyncMock()
    mock.post.side_effect = [
        AsyncMock(json=lambda: {"result": {"x": 1, "y": 2, "yaw_deg": 90}}),
        AsyncMock(json=lambda: {"result": {"sectors_m": {"front": 0.5}}}),
    ]
    with patch("httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value = mock
        res = await exec_get_robot_state(cfg)
    assert res["odom"]["x"] == 1
    assert res["lidar"]["sectors_m"]["front"] == 0.5
    assert mock.post.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_er_mission_returns_id(cfg):
    tracker = MissionTracker()
    mock = AsyncMock()
    mock.post.return_value = AsyncMock(
        json=lambda: {"mission_id": "m-123", "status": "pending"}
    )
    with patch("httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value = mock
        res = await exec_dispatch_er_mission(cfg, tracker, mission="drive forward")
    assert res["mission_id"] == "m-123"
    assert tracker.active_id == "m-123"


@pytest.mark.asyncio
async def test_dispatch_without_replace_fails_if_active(cfg):
    tracker = MissionTracker()
    tracker.active_id = "prior"
    res = await exec_dispatch_er_mission(cfg, tracker, mission="x")
    assert "error" in res
    assert "active" in res["error"].lower()


@pytest.mark.asyncio
async def test_cancel_mission_clears_tracker(cfg):
    tracker = MissionTracker()
    tracker.active_id = "m-7"
    mock = AsyncMock()
    mock.post.return_value = AsyncMock(json=lambda: {"status": "aborted"})
    with patch("httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value = mock
        res = await exec_cancel_er_mission(cfg, tracker, reason="test")
    assert res["status"] == "aborted"
    assert tracker.active_id is None


@pytest.mark.asyncio
async def test_emergency_stop_posts_to_tools_api(cfg):
    mock = AsyncMock()
    mock.post.return_value = AsyncMock(json=lambda: {"result": "ok"})
    with patch("httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value = mock
        res = await exec_emergency_stop(cfg)
    args, _ = mock.post.call_args
    assert args[0].endswith("/tool/system_emergency_stop")
    assert res["result"] == "ok"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_tool_handlers.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

```python
# ugv_tools_api/supervisor/tool_handlers.py
"""Async handlers for the supervisor's tool surface.

Each handler is a pure async function: it takes the config, optional state
(MissionTracker for dispatch/cancel correlation), and tool-specific args,
and returns a JSON-serializable dict. The session.py module knows nothing
about what these tools do — it just maps tool_call.name to a handler.

Kept separate from tool_declarations so the schemas can be reused (e.g.
BlackBox integration) without importing runtime deps.
"""
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .config import SupervisorConfig


@dataclass
class MissionTracker:
    """Holds the in-flight ER mission id across turns.

    ER only runs one mission at a time. We track the id so cancel and
    status tools have a target, and so dispatch can refuse to stomp an
    active mission unless replace_current=True.
    """
    active_id: Optional[str] = None


def _timeout() -> httpx.Timeout:
    # Short but not instant; ER/tools_api run on localhost so this is
    # network-transparent. 5s covers slow nav_status queries during busy
    # periods without hanging the supervisor's turn.
    return httpx.Timeout(5.0)


async def _post(url: str, json: dict) -> dict:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.post(url, json=json)
        return r.json()


async def _get(url: str) -> dict:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(url)
        return r.json()


async def exec_get_robot_state(cfg: SupervisorConfig) -> dict:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        import asyncio
        odom, lidar = await asyncio.gather(
            c.post(f"{cfg.tools_api_url}/tool/status_get_odom", json={}),
            c.post(f"{cfg.tools_api_url}/tool/status_get_lidar_summary", json={}),
        )
    return {"odom": odom.json().get("result", {}),
            "lidar": lidar.json().get("result", {})}


async def exec_dispatch_er_mission(
    cfg: SupervisorConfig, tracker: MissionTracker,
    *, mission: str, replace_current: bool = False,
) -> dict:
    if tracker.active_id and not replace_current:
        return {"error": "a mission is already active; set replace_current=true to override"}
    if tracker.active_id and replace_current:
        try:
            await _post(f"{cfg.er_url}/mission/{tracker.active_id}/abort", {})
        except Exception:
            pass  # best-effort; we'll still try to dispatch
        tracker.active_id = None
    body = await _post(f"{cfg.er_url}/mission", {"operator": "supervisor", "mission": mission})
    mid = body.get("mission_id")
    if mid:
        tracker.active_id = mid
    return {"mission_id": mid, "status": body.get("status", "unknown")}


async def exec_cancel_er_mission(
    cfg: SupervisorConfig, tracker: MissionTracker, *, reason: str = "",
) -> dict:
    if not tracker.active_id:
        return {"error": "no active mission"}
    body = await _post(
        f"{cfg.er_url}/mission/{tracker.active_id}/abort", {"reason": reason}
    )
    tracker.active_id = None
    return body


async def exec_get_er_mission_status(
    cfg: SupervisorConfig, tracker: MissionTracker,
) -> dict:
    if not tracker.active_id:
        return {"status": "idle"}
    return await _get(f"{cfg.er_url}/mission/{tracker.active_id}")


async def exec_emergency_stop(cfg: SupervisorConfig) -> dict:
    return await _post(f"{cfg.tools_api_url}/tool/system_emergency_stop", {})


async def exec_lights(cfg: SupervisorConfig, *, on: bool, which: str = "both") -> dict:
    tool = "ugv_lights_on" if on else "ugv_lights_off"
    return await _post(f"{cfg.tools_api_url}/tool/{tool}", {"which": which})


async def exec_gimbal_look_at(cfg: SupervisorConfig, *, pan_deg: float, tilt_deg: float) -> dict:
    return await _post(
        f"{cfg.tools_api_url}/tool/gimbal_look_at",
        {"pan_deg": pan_deg, "tilt_deg": tilt_deg, "speed": 100},
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/supervisor/test_tool_handlers.py -v`
Expected: 5 passed

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/tool_handlers.py tests/supervisor/test_tool_handlers.py
git commit -m "feat(supervisor): async tool handlers with MissionTracker"
```

---

### Task 5: Frame cache for ROS topics (camera + costmap)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/video_io.py`
- Create: `tests/supervisor/test_video_io.py`

**Why:** Camera topic publishes a ready-to-use JPEG. Costmap publishes an `OccupancyGrid` that has to be rendered to PNG. Both get cached so the tool handlers have a fresh artifact on-demand. rclpy spin runs in a background thread; the cache is lock-protected.

**Rendering rules:** Reuse ER's costmap renderer constants (`COSTMAP_IMAGE_SIZE=512`, `COSTMAP_METERS_PER_CELL=0.08`) so supervisor sees the same frame ER does. Apply a colormap (yellow=free, red=lethal, grey=unknown) and overlay the robot's current pose as a small triangle.

**Step 1: Write the failing test**

```python
# tests/supervisor/test_video_io.py
from ugv_tools_api.supervisor.video_io import FrameCache


def test_get_empty():
    c = FrameCache()
    assert c.get() is None
    assert c.received == 0


def test_set_updates_latest_and_counter():
    c = FrameCache()
    c.set(b"frame1")
    c.set(b"frame2")
    assert c.get() == b"frame2"
    assert c.received == 2


def test_render_occupancy_grid_to_png(tmp_path):
    """Rendering a synthetic occupancy grid must yield a valid PNG blob."""
    from ugv_tools_api.supervisor.video_io import render_costmap_png
    # Simulated OccupancyGrid message shape
    class Info:
        width = 10; height = 10; resolution = 0.1
        class origin:
            class position: x, y, z = 0.0, 0.0, 0.0
            class orientation: x, y, z, w = 0, 0, 0, 1
    class Msg:
        info = Info
        data = [0]*50 + [100]*10 + [-1]*40  # free, lethal, unknown
    png = render_costmap_png(Msg)
    assert png[:8] == b'\x89PNG\r\n\x1a\n'  # PNG magic
    assert len(png) > 100
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_video_io.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

```python
# ugv_tools_api/supervisor/video_io.py
"""ROS-subscriber-backed caches for camera JPEG and costmap PNG.

The supervisor does NOT stream video to Gemini continuously. Instead, the
get_camera_view and get_costmap_view tools fetch the latest frame from
these caches on demand. This keeps the session context lean and forces
intentional vision use by the model.

Camera comes in as a CompressedImage (JPEG bytes, passthrough). Costmap
comes in as an OccupancyGrid that we render to PNG here.
"""
import io
import threading
from typing import Optional


class FrameCache:
    """Thread-safe latest-only cache for opaque bytes.

    Writers (rclpy subscriber callback, rendered from any thread) call
    set(); the async tool handler reads via get(). get() never blocks on
    set() and vice-versa beyond a tiny lock.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Optional[bytes] = None
        self._received = 0

    def set(self, data: bytes) -> None:
        with self._lock:
            self._data = data
            self._received += 1

    def get(self) -> Optional[bytes]:
        with self._lock:
            return self._data

    @property
    def received(self) -> int:
        with self._lock:
            return self._received


def render_costmap_png(msg) -> bytes:
    """Render a nav_msgs/OccupancyGrid to PNG bytes.

    Color scheme matches RViz-style convention:
      - 0 (free)      -> light grey
      - 100 (lethal)  -> red
      - -1 (unknown)  -> dark grey
      - 1..99 inflation -> yellow-to-red gradient

    We keep this in supervisor/ rather than reusing ER's renderer because
    ER's version drops to tmp files; we need in-memory bytes for direct
    push into Gemini.
    """
    import numpy as np
    from PIL import Image

    w = msg.info.width
    h = msg.info.height
    arr = np.array(msg.data, dtype=np.int16).reshape(h, w)
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    # Unknown (-1) -> dark grey 60,60,60
    mask_unknown = arr == -1
    rgb[mask_unknown] = (60, 60, 60)
    # Free (0) -> light grey 200,200,200
    mask_free = arr == 0
    rgb[mask_free] = (200, 200, 200)
    # Lethal (100) -> red 200,30,30
    mask_lethal = arr == 100
    rgb[mask_lethal] = (200, 30, 30)
    # Inflation (1..99) -> yellow-to-red gradient
    mask_inf = (arr > 0) & (arr < 100)
    if mask_inf.any():
        t = (arr[mask_inf].astype(np.float32) / 99.0)
        rgb[mask_inf, 0] = (255 * (1 - 0.2 * t)).astype(np.uint8)
        rgb[mask_inf, 1] = (255 * (1 - t)).astype(np.uint8)
        rgb[mask_inf, 2] = 30

    # Flip vertically so north-up in map frame shows as up in the image
    img = Image.fromarray(np.flipud(rgb), mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/supervisor/test_video_io.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/video_io.py tests/supervisor/test_video_io.py
git commit -m "feat(supervisor): FrameCache + OccupancyGrid->PNG renderer"
```

---

### Task 6: ROS subscriber thread (integration; container-only)

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/video_io.py` (append subscriber class)
- Create: `tests/supervisor/test_video_io_ros.py` (integration test, marked skip unless on Jetson)

**Why:** Wrap the pure FrameCache with an rclpy node that owns subscriptions. Must use a **dedicated rclpy Context** so supervisor's ROS activity doesn't collide with the existing `ugv_tools_api_bridge` node running in the same process tree.

**Step 1: Write the failing test**

```python
# tests/supervisor/test_video_io_ros.py
"""Integration test — requires ROS + running camera + running Nav2.

Skipped by default; run manually on Jetson with:
    pytest tests/supervisor/test_video_io_ros.py -v --run-ros
"""
import pytest
import time

ros_only = pytest.mark.skipif(
    not pytest.importorskip("rclpy", reason="rclpy not available"),
    reason="needs ROS2",
)


@ros_only
def test_camera_subscriber_captures_frames(tmp_path):
    from ugv_tools_api.supervisor.video_io import RosCamera
    cam = RosCamera(camera_topic="/camera/image/compressed",
                    costmap_topic="/global_costmap/costmap")
    cam.start()
    time.sleep(3.0)
    try:
        jpeg = cam.get_camera_jpeg()
        assert jpeg is not None and len(jpeg) > 5000
        # Costmap may not always be present in test env; accept None
        png = cam.get_costmap_png()
        if png is not None:
            assert png[:8] == b'\x89PNG\r\n\x1a\n'
    finally:
        cam.stop()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_video_io_ros.py -v` — will skip on the developer machine, will fail on the Jetson because `RosCamera` doesn't exist yet.

**Step 3: Implement**

Append to `video_io.py`:

```python
# ugv_tools_api/supervisor/video_io.py (additions)

def _spin_node_in_thread(node, stop_evt, context):
    """Helper run on a dedicated thread that spins an rclpy context/node.

    Uses a private context so this node's executor doesn't interfere with
    or depend on ugv_tools_api_bridge's global rclpy state.
    """
    import rclpy
    rclpy.init(args=None, context=context)
    try:
        while not stop_evt.is_set() and context.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown(context=context)
        except Exception:
            pass


class RosCamera:
    """Background rclpy subscriber for camera JPEG + costmap OccupancyGrid.

    The costmap callback runs render_costmap_png() on the spinning thread,
    so the tool handler reads a ready-to-send PNG.
    """

    def __init__(self, camera_topic: str, costmap_topic: str) -> None:
        self._camera_topic = camera_topic
        self._costmap_topic = costmap_topic
        self._camera_cache = FrameCache()
        self._costmap_cache = FrameCache()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._context = None
        self._node = None

    def start(self) -> None:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import CompressedImage
        from nav_msgs.msg import OccupancyGrid

        self._context = rclpy.Context()
        # Create the node and subscriptions inside the spin thread so they
        # bind to the right context. We bootstrap by passing them via self.
        def build_node():
            class _Cam(Node):
                def __init__(node_self):
                    super().__init__("ugv_supervisor_ros", context=self._context)
                    node_self.create_subscription(
                        CompressedImage, self._camera_topic,
                        lambda m: self._camera_cache.set(bytes(m.data)),
                        qos_profile_sensor_data,
                    )
                    node_self.create_subscription(
                        OccupancyGrid, self._costmap_topic,
                        lambda m: self._costmap_cache.set(render_costmap_png(m)),
                        1,  # costmap updates are infrequent; depth 1 is fine
                    )
            self._node = _Cam()

        def run():
            import rclpy
            rclpy.init(args=None, context=self._context)
            build_node()
            try:
                while not self._stop.is_set() and self._context.ok():
                    rclpy.spin_once(self._node, timeout_sec=0.1)
            finally:
                if self._node is not None:
                    self._node.destroy_node()
                try:
                    rclpy.shutdown(context=self._context)
                except Exception:
                    pass

        self._thread = threading.Thread(target=run, daemon=True, name="ros-supervisor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def get_camera_jpeg(self) -> Optional[bytes]:
        return self._camera_cache.get()

    def get_costmap_png(self) -> Optional[bytes]:
        return self._costmap_cache.get()
```

**Step 4: Verify on Jetson**

```bash
# On jetson (from ugv_waveshare container):
cd /home/ws/ugv_ws/ugv_tools_api
pytest tests/supervisor/test_video_io_ros.py -v
```
Expected: PASS (with `/camera/image/compressed` alive and usually `/global_costmap/costmap` too).

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/video_io.py tests/supervisor/test_video_io_ros.py
git commit -m "feat(supervisor): RosCamera with private rclpy context"
```

---

### Task 7: Audio I/O wrapper with dedicated mic pool + chime

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/audio_io.py`
- Create: `tests/supervisor/test_audio_io.py`

**Why:** Spike proved that mic reads must run on a dedicated `ThreadPoolExecutor` to avoid starvation by rclpy. Also houses chime generator and speaker pipe. Speaker pipe must never block for long — we flush but allow Gemini's bursty per-turn output to use the full 1 s ring buffer.

**Step 1: Write the failing test**

```python
# tests/supervisor/test_audio_io.py
import pytest
from ugv_tools_api.supervisor.audio_io import make_chime


def test_chime_is_pcm_s16le_and_not_empty():
    data = make_chime(duration_s=0.1, freq_hz=880.0, sample_rate=24000)
    assert isinstance(data, bytes)
    assert len(data) == int(0.1 * 24000) * 2  # 2 bytes per sample


def test_chime_fades_in_and_out():
    # First and last sample should be near-zero due to 20ms fade
    import struct
    data = make_chime(duration_s=0.1, freq_hz=880.0, sample_rate=24000)
    samples = struct.unpack(f"<{len(data)//2}h", data)
    assert abs(samples[0]) < 100
    assert abs(samples[-1]) < 100
    # middle should be loud
    mid = samples[len(samples) // 2]
    assert abs(mid) > 2000
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/supervisor/test_audio_io.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

```python
# ugv_tools_api/supervisor/audio_io.py
"""ALSA mic + speaker wrappers for the supervisor.

Mic is read on a DEDICATED single-threaded executor — never on asyncio's
default pool, which rclpy's spin_once can starve. Spike validated that
the default pool starvation manifests as "mic bytes stop flowing to
Gemini" which is catastrophic and silent. Do not share the executor.

Speaker writes bytes to a long-lived aplay subprocess with a 1s ring
buffer so Gemini's bursty per-turn output never drops. Chime uses the
same pipe.
"""
import concurrent.futures
import math
import struct
import subprocess
from typing import Optional

from .config import SupervisorConfig


def make_chime(duration_s: float = 0.18, freq_hz: float = 880.0,
               sample_rate: int = 24000, volume: float = 0.25) -> bytes:
    """Build a short sine-tone chime as raw S16_LE at output rate.

    880 Hz (A5) cuts through room noise but isn't shrill. 180 ms is long
    enough to notice, short enough to feel instantaneous. 20 ms linear
    fade-in/out eliminates the click artifacts speakers produce when raw
    PCM starts or stops mid-waveform.
    """
    n = int(duration_s * sample_rate)
    amp = int(volume * 32767)
    samples = [int(amp * math.sin(2 * math.pi * freq_hz * i / sample_rate))
               for i in range(n)]
    fade = int(0.02 * sample_rate)
    for i in range(fade):
        samples[i] = int(samples[i] * i / fade)
        samples[-(i + 1)] = int(samples[-(i + 1)] * i / fade)
    return struct.pack(f"<{n}h", *samples)


class MicStream:
    """arecord subprocess + dedicated thread pool for blocking reads.

    The pool has exactly 1 worker so scheduling is deterministic. Using
    the default asyncio executor instead would share with rclpy's
    spin_once blocking calls and cause sporadic mic read stalls.
    """

    def __init__(self, cfg: SupervisorConfig) -> None:
        self._cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="supervisor-mic",
        )

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["arecord", "-D", self._cfg.mic_device, "-f", "S16_LE",
             "-r", str(self._cfg.input_sample_rate), "-c", "1",
             "-t", "raw", "-q"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def read_chunk(self, n_bytes: int) -> bytes:
        """Blocking read; call from event loop via run_in_executor(self.pool, ...)."""
        assert self._proc and self._proc.stdout
        return self._proc.stdout.read(n_bytes)

    @property
    def pool(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._pool

    def stop(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._pool.shutdown(wait=False, cancel_futures=True)


class SpeakerStream:
    """aplay subprocess with a 1s buffer; accepts chunks and chime bytes."""

    def __init__(self, cfg: SupervisorConfig) -> None:
        self._cfg = cfg
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["aplay", "-D", self._cfg.spk_device, "-f", "S16_LE",
             "-r", str(self._cfg.output_sample_rate), "-c", "1",
             "-t", "raw", f"--buffer-size={self._cfg.output_sample_rate}"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def write(self, data: bytes) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except BrokenPipeError:
                # aplay died; surface error. Real service restarts the
                # speaker subprocess; spike used to stop the session.
                err = b""
                if self._proc.stderr:
                    try:
                        err = self._proc.stderr.read()
                    except Exception:
                        pass
                raise RuntimeError(f"aplay pipe closed: {err.decode(errors='replace')[:500]}")

    def stop(self) -> None:
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/supervisor/test_audio_io.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add ugv_tools_api/supervisor/audio_io.py tests/supervisor/test_audio_io.py
git commit -m "feat(supervisor): MicStream + SpeakerStream with dedicated mic pool"
```

---

### Task 8: Session controller with compression, resumption, GoAway

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py`

**Why:** This is the heart. Owns the Gemini Live WebSocket. Applies all Google-recommended production patterns: context window compression, session resumption via persisted handle, GoAway handling, input/output transcription. Uses the audio + video + tool modules built earlier.

This task has no direct unit test — the session orchestration is tested end-to-end via the interactive spike replay in Task 11. Unit test coverage comes from Tasks 1-7 which this module composes.

**Implementation:**

```python
# ugv_tools_api/supervisor/session.py
"""Gemini Live session controller.

Owns the WebSocket, wires mic/speaker/video to model I/O, dispatches tool
calls, persists resumption handles, and reconnects on GoAway. Long-lived:
runs until the supervisor service terminates.

Google-recommended patterns applied (per https://ai.google.dev/gemini-api/docs/live-session):
  1. context_window_compression with SlidingWindow removes duration cap
  2. session_resumption handle persisted across restarts (2-hr TTL)
  3. GoAway event triggers proactive reconnect
  4. language_code pinned prevents auto-detect hallucination
  5. AutomaticActivityDetection tuned for operator mic environment

Empirical regression guards (from the spike):
  - Use response.data for audio out (NOT manual part iteration)
  - Mic reads via MicStream.pool (NOT default asyncio executor)
  - FunctionResponseBlob can't carry bytes through SDK; use
    send_realtime_input(video=...) sidechannel for image-returning tools
  - Play 880Hz chime on session open so operator knows mic is hot
"""
import asyncio
import time
from typing import Optional

from google import genai
from google.genai import types

from .config import SupervisorConfig
from .handle_store import HandleStore
from .audio_io import MicStream, SpeakerStream, make_chime
from .video_io import RosCamera
from .tool_declarations import ALL_TOOLS
from . import tool_handlers as th


_READY_CHIME = make_chime()


def _build_config(cfg: SupervisorConfig, resume_handle: Optional[str]) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=types.Content(parts=[types.Part(text=_SYSTEM_PROMPT)]),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=cfg.voice)
            ),
            language_code=cfg.language_code,
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            ),
        ),
        tools=[types.Tool(function_declarations=ALL_TOOLS)],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(),
        ),
        session_resumption=types.SessionResumptionConfig(handle=resume_handle)
            if resume_handle else None,
    )


_SYSTEM_PROMPT = (
    "You are the supervisor for the UGV Beast, a small tracked exploration robot. "
    "You converse with the operator by voice and can command the robot's on-device "
    "execution agent (ER) via the dispatch_er_mission tool. ER handles one complete "
    "task per invocation (drive here, map this room, etc.) and returns control to "
    "you when done. You watch the mission by calling get_er_mission_status, "
    "get_camera_view, get_costmap_view, and get_robot_state on demand. If something "
    "looks wrong you can cancel_er_mission or emergency_stop. Be concise and "
    "conversational. Never describe the scene unless you have just captured it via "
    "get_camera_view — do not guess at what the robot sees."
)


class Supervisor:
    def __init__(self, cfg: SupervisorConfig) -> None:
        self._cfg = cfg
        self._handle_store = HandleStore(cfg.handle_store_path)
        self._client = genai.Client(api_key=cfg.google_api_key)
        self._mic = MicStream(cfg)
        self._spk = SpeakerStream(cfg)
        self._cam = RosCamera(cfg.camera_topic, cfg.costmap_topic)
        self._tracker = th.MissionTracker()
        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._mic.start(); self._spk.start(); self._cam.start()
        try:
            while not self._stop.is_set():
                try:
                    await self._run_one_session()
                except Exception as e:
                    print(f"[supervisor] session error, reconnecting in 2s: {e}")
                    await asyncio.sleep(2.0)
        finally:
            self._cam.stop(); self._spk.stop(); self._mic.stop()

    async def _run_one_session(self) -> None:
        cfg = _build_config(self._cfg, self._handle_store.get())
        async with self._client.aio.live.connect(model=self._cfg.model, config=cfg) as session:
            print("[supervisor] session opened")
            self._spk.write(_READY_CHIME)

            # Shared task stop for this session; both pumps watch it.
            session_stop = asyncio.Event()

            async def pump_mic():
                loop = asyncio.get_running_loop()
                chunk = int(self._cfg.input_sample_rate * self._cfg.chunk_ms / 1000) * 2
                while not session_stop.is_set():
                    data = await loop.run_in_executor(self._mic.pool, self._mic.read_chunk, chunk)
                    if not data:
                        break
                    await session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"),
                    )

            async def pump_responses():
                async for response in session.receive():
                    # Tool calls
                    if response.tool_call and response.tool_call.function_calls:
                        await self._dispatch_tool_calls(session, response.tool_call.function_calls)

                    # Audio out
                    audio = response.data
                    if audio:
                        self._spk.write(audio)

                    sc = response.server_content
                    if sc:
                        if sc.input_transcription and sc.input_transcription.text:
                            print(f"[user]  {sc.input_transcription.text}")
                        if sc.output_transcription and sc.output_transcription.text:
                            print(f"[model] {sc.output_transcription.text}")

                    # Google-recommended lifecycle events
                    if getattr(response, "session_resumption_update", None):
                        upd = response.session_resumption_update
                        if upd and upd.new_handle:
                            self._handle_store.set(upd.new_handle)
                    if getattr(response, "go_away", None):
                        left = response.go_away.time_left if response.go_away else None
                        print(f"[supervisor] GoAway received, time_left={left}; reconnecting")
                        session_stop.set()
                        return

            await asyncio.gather(pump_mic(), pump_responses())

    async def _dispatch_tool_calls(self, session, function_calls) -> None:
        for fc in function_calls:
            name, args = fc.name, dict(fc.args or {})
            print(f"[supervisor] tool_call {name}({args})")
            if name == "get_robot_state":
                resp = await th.exec_get_robot_state(self._cfg)
                await self._respond(session, fc, resp)
            elif name == "get_camera_view":
                await self._vision_tool(session, fc, self._cam.get_camera_jpeg(), "image/jpeg", "camera")
            elif name == "get_costmap_view":
                await self._vision_tool(session, fc, self._cam.get_costmap_png(), "image/png", "costmap")
            elif name == "dispatch_er_mission":
                resp = await th.exec_dispatch_er_mission(self._cfg, self._tracker, **args)
                await self._respond(session, fc, resp)
            elif name == "cancel_er_mission":
                resp = await th.exec_cancel_er_mission(self._cfg, self._tracker, **args)
                await self._respond(session, fc, resp)
            elif name == "get_er_mission_status":
                resp = await th.exec_get_er_mission_status(self._cfg, self._tracker)
                await self._respond(session, fc, resp)
            elif name == "emergency_stop":
                resp = await th.exec_emergency_stop(self._cfg)
                await self._respond(session, fc, resp)
            elif name == "lights_on":
                resp = await th.exec_lights(self._cfg, on=True, **args)
                await self._respond(session, fc, resp)
            elif name == "lights_off":
                resp = await th.exec_lights(self._cfg, on=False, **args)
                await self._respond(session, fc, resp)
            elif name == "gimbal_look_at":
                resp = await th.exec_gimbal_look_at(self._cfg, **args)
                await self._respond(session, fc, resp)
            else:
                await self._respond(session, fc, {"error": f"unknown tool {name}"})

    async def _respond(self, session, fc, resp: dict) -> None:
        await session.send_tool_response(
            function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response=resp)],
        )

    async def _vision_tool(self, session, fc, payload: Optional[bytes], mime: str, label: str) -> None:
        """Vision sidechannel: acknowledge the tool, then push the image as
        a video input so Gemini can see it. FunctionResponseBlob can't carry
        bytes through the SDK's JSON serializer on Live 3.1 preview, so this
        two-step is the pattern that works (spike iteration v3-v5 validated).
        """
        if not payload:
            await self._respond(session, fc, {"error": f"no {label} frame available"})
            return
        await self._respond(session, fc, {"status": "ok", "note": f"{label} image delivered as video frame"})
        await session.send_realtime_input(video=types.Blob(data=payload, mime_type=mime))
```

**Step 2: Verify compile**

```bash
python3 -m py_compile ugv_tools_api/supervisor/session.py
```
Expected: no output (success)

**Step 3: Commit**

```bash
git add ugv_tools_api/supervisor/session.py
git commit -m "feat(supervisor): Live session controller with compression + resumption + GoAway"
```

---

### Task 9: FastAPI service with /health and /open_session

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/service.py`
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/main.py`

**Why:** Expose a minimal HTTP surface the wake-word service will POST to. Also gives us a health endpoint for systemd monitoring.

**Step 1: Write the implementation**

```python
# ugv_tools_api/supervisor/service.py
"""HTTP control plane for the supervisor service.

The supervisor itself is always running; this surface is for external
control. Currently used by the wake-word service to notify "operator is
about to speak," and by health checks.
"""
import asyncio
from fastapi import FastAPI

from .config import load
from .session import Supervisor


app = FastAPI(title="UGV Supervisor")

_cfg = load()
_supervisor: Supervisor = Supervisor(_cfg)
_task = None


@app.on_event("startup")
async def _startup():
    global _task
    _task = asyncio.create_task(_supervisor.run())


@app.on_event("shutdown")
async def _shutdown():
    _supervisor._stop.set()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": _cfg.model,
        "active_mission_id": _supervisor._tracker.active_id,
    }


@app.post("/open_session")
async def open_session():
    """Called by the wake-word service when the operator says the wake word.

    For now a no-op placeholder — the supervisor keeps a continuous session
    open and listens anyway. If we later add wake-word-gated session
    opening (to cut idle-mic cost), this endpoint will break out of a
    "suspended" state into a live session. Returning 200 is enough for
    the wake-word service to confirm the supervisor is up.
    """
    return {"ok": True}
```

```python
# ugv_tools_api/supervisor/main.py
"""Entry point for `python -m ugv_tools_api.supervisor`."""
import uvicorn
import os


def main():
    host = os.environ.get("SUPERVISOR_HOST", "0.0.0.0")
    port = int(os.environ.get("SUPERVISOR_PORT", "8083"))
    uvicorn.run("ugv_tools_api.supervisor.service:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
```

**Step 2: Smoke test (on Jetson)**

```bash
# Jetson, ugv_waveshare container
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
export GOOGLE_API_KEY=<from er.env>
export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api
python3 -m ugv_tools_api.supervisor.main &
sleep 5
curl http://localhost:8083/health
```
Expected: JSON with `status: ok`

**Step 3: Commit**

```bash
git add ugv_tools_api/supervisor/service.py ugv_tools_api/supervisor/main.py
git commit -m "feat(supervisor): FastAPI /health and /open_session"
```

---

### Task 10: systemd unit + deploy script

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/start_supervisor.sh`
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/supervisor.env` (template; actual file goes to `/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env` on the robot)
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-supervisor.service`

**Step 1: Write the files**

```bash
# deploy/start_supervisor.sh
#!/bin/bash
# start_supervisor.sh — container entrypoint
set -eo pipefail

set +u
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
set -u

export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:${PYTHONPATH:-}
export SUPERVISOR_HOST=${SUPERVISOR_HOST:-0.0.0.0}
export SUPERVISOR_PORT=${SUPERVISOR_PORT:-8083}

# Wait up to 30s for ugv-tools-api at :8080 (needed for tools) and ugv-er
# at :8082 (needed for missions) to be reachable before we try to go live.
for i in $(seq 1 30); do
  if curl -sf "http://localhost:8080/health" >/dev/null 2>&1 \
     && curl -sf "http://localhost:8082/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

exec python3 -m ugv_tools_api.supervisor.main
```

```ini
# deploy/supervisor.env (template)
GOOGLE_API_KEY=REPLACE_ME_FROM_er.env
SUPERVISOR_MODEL=gemini-3.1-flash-live-preview
SUPERVISOR_VOICE=Orus
SUPERVISOR_LANG=en-US
SUPERVISOR_MIC=plughw:CARD=Camera,DEV=0
SUPERVISOR_SPK=plughw:CARD=Device,DEV=0
TOOLS_API_URL=http://localhost:8080
ER_URL=http://localhost:8082
SUPERVISOR_CAMERA_TOPIC=/camera/image/compressed
SUPERVISOR_COSTMAP_TOPIC=/global_costmap/costmap
SUPERVISOR_HANDLE_STORE=/home/ws/ugv_ws/ugv_supervisor_state/session_handle.txt
```

```ini
# deploy/ugv-supervisor.service
[Unit]
Description=UGV Beast Gemini Live Supervisor
Requires=ugv-tools-api.service ugv-er.service docker.service
After=ugv-tools-api.service ugv-er.service

[Service]
Type=simple
EnvironmentFile=/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env
ExecStart=/usr/bin/docker exec \
  -e GOOGLE_API_KEY \
  -e SUPERVISOR_MODEL -e SUPERVISOR_VOICE -e SUPERVISOR_LANG \
  -e SUPERVISOR_MIC -e SUPERVISOR_SPK \
  -e TOOLS_API_URL -e ER_URL \
  -e SUPERVISOR_CAMERA_TOPIC -e SUPERVISOR_COSTMAP_TOPIC \
  -e SUPERVISOR_HANDLE_STORE \
  -e SUPERVISOR_HOST -e SUPERVISOR_PORT \
  ugv_waveshare /bin/bash /home/ws/ugv_ws/ugv_tools_api/deploy/start_supervisor.sh
# systemctl restart only kills the wrapper; kill the inner python too
ExecStopPost=-/usr/bin/docker exec ugv_waveshare pkill -9 -f "ugv_tools_api.supervisor"
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60

[Install]
WantedBy=multi-user.target
```

**Step 2: Install on Jetson**

```bash
# From laptop:
scp deploy/start_supervisor.sh jetson@ugv-beast:/home/ws/ugv_ws/ugv_tools_api/deploy/
scp deploy/supervisor.env jetson@ugv-beast:/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/
scp deploy/ugv-supervisor.service jetson@ugv-beast:/tmp/

# On Jetson host:
sudo mv /tmp/ugv-supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
# Edit /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env to
# paste real GOOGLE_API_KEY value
sudo systemctl enable --now ugv-supervisor.service
sudo systemctl status ugv-supervisor.service --no-pager -l
```
Expected: service is Active (running), `curl http://localhost:8083/health` returns JSON.

**Step 3: Commit**

```bash
git add ugv_tools_api/deploy/start_supervisor.sh ugv_tools_api/deploy/supervisor.env ugv_tools_api/deploy/ugv-supervisor.service
git commit -m "feat(supervisor): deploy scripts + systemd unit"
```

---

### Task 11: Interactive integration test on the robot

**Files:**
- Create: `docs/plans/notes/2026-04-21-supervisor-integration-checkpoint.md` (test record)

**Why:** The session module can only be verified end-to-end with a real mic, speaker, and robot. This task is the equivalent of the spike's manual verification, but now against the productionized service.

**Test protocol:**

For each test below, observe logs via `sudo journalctl -u ugv-supervisor.service -f` while speaking at a normal volume in front of the robot, waiting for the chime, then issuing the prompt.

| # | Prompt | Expected tool call | Expected observable outcome |
|---|---|---|---|
| 1 | "What's the robot's state?" | `get_robot_state` | Gemini speaks pose + lidar summary |
| 2 | "What do you see right now?" | `get_camera_view` | Gemini describes actual scene matching camera view |
| 3 | "Show me the map." | `get_costmap_view` | Gemini describes obstacle distribution |
| 4 | "Drive forward 50 centimeters." | `dispatch_er_mission` | ER `/mission` returns mission_id; tracker.active_id set; robot moves |
| 5 | "How's the mission going?" | `get_er_mission_status` | Gemini reports status |
| 6 | "Cancel the mission." | `cancel_er_mission` | Robot stops; tracker.active_id cleared |
| 7 | "Stop!" (shouted mid-mission) | `emergency_stop` | Motors halt within 200 ms |
| 8 | "Turn on the lights." | `lights_on` | LEDs illuminate |
| 9 | "Look up." | `gimbal_look_at` | Gimbal tilts up |

**Acceptance:** all 9 prompts produce the correct tool call and observable outcome. Record any misses in the checkpoint doc with prompt / expected / actual / hypothesis.

**Regression tests from the spike** (must still pass):
- Session opens and chime plays within 3 s of service start
- After silent-wait-post-chime, first prompt reaches Gemini (verified by `[user]` transcript OR tool_call)
- Mic→Gemini byte flow hits 64000 B/s during speech
- No "bytes not JSON serializable" errors anywhere in the log
- Response audio plays on the speaker (not just transcripts)

**Commit:** Record the result in the checkpoint doc; commit the doc.

---

### Task 12: Audit and enforce ER statelessness

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/agent_loop.py` (if any auto-continuation is present)
- Create: `tests/er/test_er_single_shot.py`

**Why:** The supervisor assumes ER is strictly one-mission-per-invocation. If ER's agent_loop has any auto-continuation (loop-until-frontiers-clear, re-plan-on-fail, etc.), that logic must move to the supervisor so the architecture stays clean.

**Step 1: Read `er/agent_loop.py`** and map every branch that could continue execution past a single `mission_done` / `mission_failed`.

**Step 2: Write a test** that POSTs a mission, waits for terminal state, then verifies no second turn was taken.

```python
# tests/er/test_er_single_shot.py
"""Ensure ER runs exactly one mission per invocation and then sleeps."""
import httpx
import pytest
import time


@pytest.mark.integration
def test_single_shot_mission():
    # Only runs on Jetson with ugv-er up
    r = httpx.post("http://localhost:8082/mission", json={
        "operator": "test", "mission": "say hello and stop",
    })
    mid = r.json()["mission_id"]
    # Poll until terminal
    deadline = time.time() + 60
    while time.time() < deadline:
        s = httpx.get(f"http://localhost:8082/mission/{mid}").json()
        if s["status"] in ("completed", "failed", "aborted"):
            break
        time.sleep(1)
    # After terminal, events ring should show exactly one mission_done or mission_fail
    events = s.get("events", [])
    terminal_events = [e for e in events if e.get("type") in ("mission_done", "mission_fail")]
    assert len(terminal_events) == 1, f"expected exactly one terminal event, got {terminal_events}"
```

**Step 3: Fix any found auto-continuation.** If `agent_loop.py` has a `while frontiers_remaining: ...` or similar, move that logic up to the supervisor, leaving ER to handle one atomic task.

**Step 4: Commit**

```bash
git add ugv_tools_api/er/agent_loop.py tests/er/test_er_single_shot.py
git commit -m "refactor(er): enforce single-shot mission; supervisor owns orchestration"
```

---

### Task 13: Retire ugv-voice.service (cleanup)

**Files:**
- Modify: nothing in the supervisor repo
- Action: on Jetson host, `sudo systemctl disable --now ugv-voice.service`

**Why:** Gemini Live's native voice output replaces `/speak` HTTP → mpg123 → ALSA entirely. Without disabling, ugv-voice may still be invoked by stale code paths on ER or ears, confusing audio output.

**Step 1: Grep for remaining callers**

```bash
# On laptop
grep -r "ugv-voice\|SPEAK_URL\|/speak" docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/
```

Remove or guard any remaining references.

**Step 2: On Jetson**

```bash
sudo systemctl disable --now ugv-voice.service
```

**Step 3: Verify the supervisor still speaks correctly.** Run test 1 from Task 11. Audio should come out of the JBL via supervisor's direct `aplay` path.

**Step 4: Commit (if code changes)**

```bash
git commit -m "refactor: retire ugv-voice path in favor of supervisor's native audio"
```

---

### Task 14: Wake-word rewire

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/voice/ears.py`

**Why:** The old ears path: wake word → Whisper STT → BlackBox chat. The new path: wake word → POST `/supervisor/open_session` (and nothing else; supervisor already owns the mic).

The ears service should now be stripped down to just wake-word detection, with `/open_session` POST as the only side effect.

**Step 1: Read `voice/ears.py`** and identify the wake-word detection block vs the Whisper/chat block.

**Step 2: Simplify to only the wake-word block**, with the `requests.post(f"{SUPERVISOR_URL}/open_session")` action on wake.

**Step 3: Test**

Say "Black Box Flight Recorder" — ears should fire the HTTP POST visible in supervisor's `journalctl` logs. Nothing else should happen (no Whisper, no chat).

**Step 4: Commit**

```bash
git commit -m "refactor(ears): wake word now only notifies supervisor"
```

---

## Post-implementation verification

After Task 14:

1. **Boot test:** `sudo reboot`. After ~90 s, verify:
   - `systemctl is-active ugv-waveshare.service ugv-tools-api.service ugv-er.service ugv-supervisor.service ugv-ears.service` returns `active` for all
   - `curl localhost:8083/health` returns OK
   - Pantilt camera topic is publishing
   - Costmap topic is publishing (if Nav2 started in SLAM mode)

2. **Happy path demo:**
   - Say "Black Box Flight Recorder"
   - Hear 880 Hz chime
   - Wait 3 seconds
   - Say "Drive forward 30 centimeters"
   - Expected: robot drives forward 30 cm, supervisor reports "Mission complete" when ER returns

3. **Failure recovery demo:**
   - Start any mission
   - Unplug the network cable for 30 seconds (forcing Live disconnect)
   - Reconnect the cable
   - Supervisor should reconnect using the stored session handle within 10 s
   - Continue the conversation — Gemini still remembers context

---

## Post-plan follow-ups (not implemented here)

- **BlackBox tool schema:** expose `ugv_dispatch_supervisor(mission_or_question: str)` as a BlackBox tool. BlackBox posts to the supervisor's `/open_session` endpoint (new body shape that includes an initial utterance). Lets BlackBox issue missions as if it were the operator. Separate PR.
- **Hardware echo cancellation:** add PulseAudio + `module-echo-cancel` if the silent-wait protocol proves annoying in practice. Separate task.
- **Costmap overlay:** add robot pose triangle on top of the PNG so Gemini can reason about position on the map. Currently pose is only in `get_robot_state`.
- **Per-mission snapshot:** after each completed mission, supervisor posts a summary to BlackBox `/chat` for the ledger. Touches BlackBox integration; deferred.

---
