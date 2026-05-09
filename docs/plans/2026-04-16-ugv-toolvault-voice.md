# UGV ToolVault Integration + Robot Voice/Ears — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Mint the 22-tool UGV Beast API into the BlackBox ToolVault so every model — chat, realtime voice, gemini_live, grok_live, phone, MCP — can semantically inject robot control when the user's prompt is about the robot, plus wire the Jetson's onboard JBL speaker and microphone into the conversation loop so the robot talks (auto-TTS of every assistant text block via OpenAI onyx) and listens (openWakeWord → Whisper) — making "Black Box Flight Recorder" a spoken wake-word interface to whatever model is currently handling the session.

**Architecture:** Three tracks. (1) **ToolVault Minting** — the 22 UGV tools are defined once in `Orchestrator/tools/tool_registry.py` with `groups=_ALL` and rich descriptions that carry the robot's persona context (since ToolVault does NOT store per-tool instruction snippets — the description is the prompt). `Orchestrator/toolvault/migrate.py` embeds each one and appends it to the append-only `ToolVault/toolvault_volume.txt` + `ToolVault/toolvault_manifest.json`. Semantic injection via `inject_for_prompt(prompt, provider)` then surfaces them to ANY model automatically when the user's prompt talks about the robot (e.g., "drive forward", "look left", "what do you see"). **Semantic gating IS the security layer** — if the user doesn't bring up the robot, the UGV tools aren't in context, and the model can't hallucinate into calling them. No operator mode, no explicit flag, no system-prompt hard-swap. Executors live in `blackbox_tools.py` as 22 thin `_execute_ugv_*` HTTP proxies that forward to `http://ugv-beast:8080/tool/{name}`. (2) **Robot Voice** — a new Jetson-side `ugv-voice.service` exposes `POST /speak {text}` on container port 8081. Chat streaming handlers in the Orchestrator detect when the last tool call in a turn was a UGV tool AND fire-and-forget POST the finalized assistant text block to the Jetson. The Jetson service generates TTS via OpenAI's `tts-1-hd` with voice `onyx` and plays the MP3 through ALSA to the JBL speaker on the 3.5mm jack. (3) **Robot Ears** — a new Jetson-side `ugv-ears.service` captures mic at 16kHz via PyAudio, runs an openWakeWord detector trained on "black box flight recorder," and on trigger buffers audio until VAD silence, then posts the clip to the Orchestrator's `/stt` endpoint and injects the transcription into the active BlackBox chat session as a user message.

**Tech Stack:** ToolVault side — existing Python/FastAPI/rclpy stack, `gemini-embedding-001` / `text-embedding-004` (3072-dim) for semantic search, `mint_tool()` + `migrate.py` for volume append. Voice side — OpenAI TTS `tts-1-hd` (onyx), ALSA via `mpg123`/`aplay`, PyAudio for mic capture, `openwakeword` library + `webrtcvad`, Orchestrator's existing `/stt` endpoint for Whisper.

**Ground Truth (probed 2026-04-16):**
- **Mint entrypoint**: `Orchestrator/toolvault/__init__.py:60-142` `mint_tool(name, description, category, groups, parameters, returns, example, notes, tier, generate_embedding)`. NOT a REST endpoint — Python function.
- **Append-only volume**: `ToolVault/toolvault_volume.txt` (NAME/DESCRIPTION/CATEGORY/GROUPS/PARAMETERS/RETURNS/EXAMPLE/JSON_SCHEMA fields per tool — NO per-tool prompt/instruction field).
- **Manifest**: `ToolVault/toolvault_manifest.json` — byte-offset index + 3072-dim embedding per tool.
- **Migrate CLI**: `Orchestrator/toolvault/migrate.py` batch-re-mints all `TOOL_DEFINITIONS` from `tool_registry.py`, skipping already-minted. Call with `python -m Orchestrator.toolvault.migrate`.
- **Injection at runtime**: `inject_for_prompt()` at `Orchestrator/toolvault/injector.py:180+` — embeds user prompt, semantic-searches vault, returns provider-formatted tool schemas. Tier 1 tools always included; Tier 2+ are semantic-match-gated.
- **Groups control consumer visibility**: a tool with `groups=["chat","realtime","phone","mcp",...]` is eligible to surface in all those consumers' injection calls. Use `_ALL` for all-models access.
- **No per-tool instruction snippet**: tool DESCRIPTION must carry the robot-persona context. Write descriptions like "Drive the UGV Beast tracked robot — your physical body — straight forward…" not just "Move forward."
- **Executors still live in `blackbox_tools.py`**: add 22 `_execute_ugv_*` HTTP-proxy methods to `BlackBoxToolExecutor`.
- **No UGV system prompt or operator mode needed** — ToolVault's semantic gating handles security + relevance.
- **TTS endpoint** `/tts` returns `{audio_url}` pointing at `/ui/uploads/*.mp3`. **STT endpoint** `/stt` accepts multipart upload and returns `{text}`.
- **No existing wake-word code** — `openwakeword` integration is new-build on the Jetson.
- **UGV tool API** (built today) is live at `http://ugv-beast:8080` over Tailscale.

**Design Decisions (user-selected upfront):**
- **Wake word engine:** openWakeWord — open-source, trainable for "black box flight recorder," runs on Jetson CPU.
- **Auto-speak policy:** Every assistant **text** block auto-spoken. Thinking blocks stay silent.
- **TTS engine:** OpenAI `tts-1-hd` with voice `onyx` (deep, robot-adjacent).

**Deployment Notes:**
- BlackBox Orchestrator paths: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/`
- Jetson container paths: `/home/ws/ugv_ws/ugv_tools_api/` (bind mount from `/home/jetson/ugv_ws_waveshare/ugv_tools_api/`)
- UGV reachable at `http://ugv-beast:8080` (Tailscale) or `http://192.168.1.155:8080` (LAN) from BlackBox
- BlackBox reachable at `http://ai-black-box-fc-a620ai-wifi:9091` (Tailscale) or `http://192.168.1.???:9091` (LAN) from UGV
- Jetson voice + ears services run in the **host OS** (not container) — they need access to `/dev/snd/*` and the actual audio hardware. Alternatively, run in container with `--device=/dev/snd` privilege. Plan uses host for simplicity.

---

## Phase 1 — ToolVault Minting (all models, semantic gate = security)

The existing ToolVault already handles everything once a tool is minted with the right `groups`. No new group constant, no new getter, no UGV mode detection, no system-prompt swap. Just register + mint + let semantic injection do its job.

### Task 1.1: Add 22 UGV tool definitions to `TOOL_DEFINITIONS` with `_ALL` group + rich descriptions

**Files:**
- Modify: `Orchestrator/tools/tool_registry.py` (add UGV section at the end of TOOL_DEFINITIONS, around line 1140)

**Step 1: Append the UGV block**

Append at the end of `TOOL_DEFINITIONS` (just before the closing `]`). **Two key points about this block:**

1. **All 22 tools use `groups=_ALL`** so they're eligible to surface in every consumer (chat, chat_cu, realtime, gemini_live, grok_live, phone, mcp). Semantic gating at injection time is the security layer — tools only appear when the user's prompt is about the robot.
2. **Descriptions are *rich and persona-aware*.** ToolVault does NOT store a per-tool instruction snippet, so the DESCRIPTION field is where robot-context lives. Each description makes clear: (a) this controls a physical tracked robot, (b) the robot has a JBL speaker and cameras, (c) what unit / direction / range applies. The model reads these descriptions at inject time and that's its only context about the robot.

```python
# ── UGV Beast (Waveshare tracked robot over Tailscale) ──────────────────
# All 22 tools proxy through Orchestrator → http://ugv-beast:8080 via HTTP.
# Semantic injection surfaces these only when the user's prompt is robot-related
# (drive/look/snapshot/nav/etc.). Rich descriptions carry the persona — there
# is no separate UGV system prompt.

{"name": "ugv_motion_move_forward", "description": "Drive the UGV Beast — a physical Waveshare tracked robot you can control — straight forward for a duration. The robot has a JBL speaker for your voice and cameras for your sight. Safety-clamped server-side: max 0.15 m/s, max 10 seconds per call. Always check LiDAR with ugv_status_get_lidar_summary before moving if you haven't seen a recent reading. If any front sector is under 0.3 m, do NOT move forward.", "parameters": {"type": "object", "properties": {"duration_s": {"type": "number", "description": "Seconds to drive. Clamped at 10.", "minimum": 0.1, "maximum": 10.0}, "speed_m_s": {"type": "number", "description": "Linear speed in m/s (max 0.15).", "default": 0.1}}, "required": ["duration_s"]}, "groups": _ALL},

{"name": "ugv_motion_move_backward", "description": "Drive the UGV Beast tracked robot straight backward for a duration. Safety-clamped: max 0.15 m/s, max 10 s. Blind reverse is riskier than forward — prefer short (<2s) pulses and confirm back-sector LiDAR with ugv_status_get_lidar_summary first.", "parameters": {"type": "object", "properties": {"duration_s": {"type": "number", "minimum": 0.1, "maximum": 10.0, "description": "Seconds to drive."}, "speed_m_s": {"type": "number", "default": 0.08, "description": "Linear speed."}}, "required": ["duration_s"]}, "groups": _ALL},

{"name": "ugv_motion_rotate_left", "description": "Rotate the UGV Beast tracked robot in place counter-clockwise (positive angular velocity). Useful for scanning the environment or aligning heading. Safety-clamped: max 0.8 rad/s, max 5 seconds.", "parameters": {"type": "object", "properties": {"duration_s": {"type": "number", "minimum": 0.1, "maximum": 5.0, "description": "Seconds to rotate."}, "rate_rad_s": {"type": "number", "default": 0.5, "description": "Angular rate in rad/s (max 0.8)."}}, "required": ["duration_s"]}, "groups": _ALL},

{"name": "ugv_motion_rotate_right", "description": "Rotate the UGV Beast tracked robot in place clockwise (negative angular velocity). Safety-clamped: max 0.8 rad/s, max 5 seconds.", "parameters": {"type": "object", "properties": {"duration_s": {"type": "number", "minimum": 0.1, "maximum": 5.0, "description": "Seconds."}, "rate_rad_s": {"type": "number", "default": 0.5, "description": "rad/s."}}, "required": ["duration_s"]}, "groups": _ALL},

{"name": "ugv_motion_stop", "description": "Immediately stop all UGV Beast motion by publishing a zero-velocity twist. Safe to call at any time; idempotent. Use this any time you want the robot to halt — don't wait for duration timers.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_gimbal_look_at", "description": "Point the UGV Beast's pan-tilt gimbal camera to absolute pan and tilt angles in degrees. The gimbal sits on top of the robot and carries the primary camera. Pan: -180 to +180 (negative=right, positive=left, zero=forward). Tilt: -45 to +90 (negative=down, positive=up). Servos are open-loop — commanded position is effectively actual after ~500ms settle.", "parameters": {"type": "object", "properties": {"pan_deg": {"type": "number", "minimum": -180, "maximum": 180, "description": "Pan angle in degrees. Negative=right, positive=left, zero=forward."}, "tilt_deg": {"type": "number", "minimum": -45, "maximum": 90, "description": "Tilt angle in degrees. Negative=down, positive=up."}, "speed": {"type": "integer", "minimum": 1, "maximum": 300, "default": 100, "description": "Servo speed 1-300. 100 is a gentle default."}}, "required": ["pan_deg", "tilt_deg"]}, "groups": _ALL},

{"name": "ugv_gimbal_reset", "description": "Return the UGV Beast's pan-tilt gimbal to the forward-center home position (pan=0, tilt=0). Call this to \"look straight ahead again\" after scanning.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_gimbal_get_state", "description": "Get the current commanded pan and tilt angles of the UGV Beast's gimbal. Returns the last-commanded position (servos are open-loop PWM — no actual encoder feedback available on this hardware).", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_camera_list", "description": "List all cameras available on the UGV Beast — a pan-tilt mounted USB camera (\"pantilt\") and an OAK-D Lite depth camera on top (\"oakd\") — and report whether each is currently streaming fresh frames.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_camera_snapshot", "description": "Capture the latest JPEG frame from one of the UGV Beast's cameras so you can \"see\" what the robot sees. Set as_url=true for a fast URL response, or as_url=false to receive the image as base64 (useful when you need to analyze the image yourself via vision).", "parameters": {"type": "object", "properties": {"camera": {"type": "string", "enum": ["pantilt", "oakd"], "description": "Which camera: 'pantilt' for the pan-tilt camera, 'oakd' for the OAK-D Lite."}, "as_url": {"type": "boolean", "default": False, "description": "If true, return a URL like /snapshot/pantilt. If false, return base64 image bytes."}}, "required": ["camera"]}, "groups": _ALL},

{"name": "ugv_status_get_pose", "description": "Get the UGV Beast's current (x, y, yaw) pose in the map frame. Yaw is in both radians and degrees. Use this to know where the robot is before planning navigation or describing location.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_status_get_odom", "description": "Get the UGV Beast's filtered odometry: position, heading, and current linear/angular velocity. Velocities are the actual observed values (non-zero while moving).", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_status_get_lidar_summary", "description": "Summarize the UGV Beast's 360° LiDAR scan as 8 directional sectors (front, front_left, left, back_left, back, back_right, right, front_right) with the minimum distance in meters per sector plus an overall minimum. Call this before any motion to check for obstacles — robot radius is ~0.2 m, so distances under 0.3 m are danger-close.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_status_list_nodes", "description": "List all running ROS2 nodes on the UGV Beast. Useful for diagnostics — healthy robot should have ~30+ nodes including bt_navigator, controller_server, planner_server, slam_toolbox.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_status_list_topics", "description": "List all active ROS2 topics on the UGV Beast with their message types. Useful for diagnostics when a subsystem seems offline.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_status_health", "description": "Overall UGV Beast health report: whether the ROS bridge is running plus freshness (in seconds since last message) for all key topics — /odom, /scan, /robot_pose, /map, camera streams, gimbal state. A healthy robot shows fresh under 0.5 s for all topics except /map (which updates sparingly).", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_nav_goto_point", "description": "Navigate the UGV Beast to an (x, y, yaw) pose in the map frame using Nav2 autonomous path planning. This is the preferred long-distance movement — Nav2 handles obstacle avoidance and path finding. Non-blocking: returns immediately after goal is accepted; poll ugv_nav_status for progress. Cancel with ugv_nav_cancel.", "parameters": {"type": "object", "properties": {"x": {"type": "number", "description": "Goal x coordinate in the map frame (meters)."}, "y": {"type": "number", "description": "Goal y coordinate in the map frame (meters)."}, "yaw_deg": {"type": "number", "default": 0.0, "description": "Desired goal heading in degrees (-180 to 180)."}}, "required": ["x", "y"]}, "groups": _ALL},

{"name": "ugv_nav_cancel", "description": "Cancel the UGV Beast's currently active Nav2 navigation goal. Idempotent — returns canceled:false if there was no active goal.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_nav_status", "description": "Get the UGV Beast's current Nav2 goal status (idle, navigating, succeeded, aborted, canceled) and the distance remaining in meters to the goal.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_system_emergency_stop", "description": "Emergency stop the UGV Beast: publishes a zero-velocity twist AND sends an ESP32 all-motor cutoff command. Use immediately if anything looks wrong. Preferred panic button over ugv_motion_stop when you suspect runaway motion.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_system_servo_center", "description": "Center the UGV Beast's servos to mid-point. Primarily a calibration operation — returns the gimbal to pan=0, tilt=0.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},

{"name": "ugv_system_servo_release", "description": "Release UGV Beast servo torque (limp mode). After this, the gimbal will flop under gravity — it won't hold its position. Only use when you specifically want free manipulation or storage.", "parameters": {"type": "object", "properties": {}, "required": []}, "groups": _ALL},
```

**Step 2: Verify all 22 registered with `_ALL`**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc && \
  Orchestrator/venv/bin/python -c "
from Orchestrator.tools.tool_registry import TOOL_DEFINITIONS
ugv = [t for t in TOOL_DEFINITIONS if t['name'].startswith('ugv_')]
print(f'UGV tools registered: {len(ugv)}')
assert len(ugv) == 22, f'expected 22, got {len(ugv)}'
# Every UGV tool must be in ALL groups (so all models can see it via inject_for_prompt)
expected_groups = {'chat','chat_cu','realtime','gemini_live','grok_live','phone','mcp'}
for t in ugv:
    missing = expected_groups - set(t['groups'])
    assert not missing, f'{t[\"name\"]} missing groups: {missing}'
print('all 22 UGV tools have _ALL group coverage ✓')
"
```

**Note on the `ugv_*` prefix:** Every UGV tool name is prefixed `ugv_*` so the model has an unambiguous hint — at inject time, the surfaced tool names read like a robot capability menu and collisions with non-robot tools are impossible.

---

### Task 1.2: Add 22 HTTP-proxy executors to `BlackBoxToolExecutor`

**Files:**
- Modify: `Orchestrator/tools/blackbox_tools.py` (add 22 `_execute_ugv_*` methods)

**Step 1: Add the UGV dispatcher helper**

Near the top of `BlackBoxToolExecutor` class (just after `__init__`), add:
```python
UGV_BASE_URL = "http://ugv-beast:8080"  # Tailscale — falls back to LAN IP if magic DNS down

async def _ugv_call(self, api_tool_name: str, args: Dict[str, Any]) -> ToolResult:
    """Proxy a call to the UGV Beast tool schema API over Tailscale."""
    url = f"{self.UGV_BASE_URL}/tool/{api_tool_name}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=args, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    txt = await r.text()
                    return ToolResult(success=False, result=f"UGV API {r.status}: {txt[:200]}")
                data = await r.json()
                return ToolResult(success=True, result=f"UGV {api_tool_name} ok", data=data.get("result", data))
    except asyncio.TimeoutError:
        return ToolResult(success=False, result=f"UGV API timeout calling {api_tool_name}")
    except Exception as e:
        return ToolResult(success=False, result=f"UGV API error: {e}")
```

**Step 2: Add the 22 proxy methods**

Append after the last existing `_execute_*` method:
```python
# ── UGV Beast proxies ──────────────────────────────────────────────────

async def _execute_ugv_motion_move_forward(self, duration_s, speed_m_s=0.1):
    return await self._ugv_call("motion_move_forward", {"duration_s": duration_s, "speed_m_s": speed_m_s})

async def _execute_ugv_motion_move_backward(self, duration_s, speed_m_s=0.08):
    return await self._ugv_call("motion_move_backward", {"duration_s": duration_s, "speed_m_s": speed_m_s})

async def _execute_ugv_motion_rotate_left(self, duration_s, rate_rad_s=0.5):
    return await self._ugv_call("motion_rotate_left", {"duration_s": duration_s, "rate_rad_s": rate_rad_s})

async def _execute_ugv_motion_rotate_right(self, duration_s, rate_rad_s=0.5):
    return await self._ugv_call("motion_rotate_right", {"duration_s": duration_s, "rate_rad_s": rate_rad_s})

async def _execute_ugv_motion_stop(self):
    return await self._ugv_call("motion_stop", {})

async def _execute_ugv_gimbal_look_at(self, pan_deg, tilt_deg, speed=100):
    return await self._ugv_call("gimbal_look_at", {"pan_deg": pan_deg, "tilt_deg": tilt_deg, "speed": speed})

async def _execute_ugv_gimbal_reset(self):
    return await self._ugv_call("gimbal_reset", {})

async def _execute_ugv_gimbal_get_state(self):
    return await self._ugv_call("gimbal_get_state", {})

async def _execute_ugv_camera_list(self):
    return await self._ugv_call("camera_list", {})

async def _execute_ugv_camera_snapshot(self, camera, as_url=False):
    return await self._ugv_call("camera_snapshot", {"camera": camera, "as_url": as_url})

async def _execute_ugv_status_get_pose(self):
    return await self._ugv_call("status_get_pose", {})

async def _execute_ugv_status_get_odom(self):
    return await self._ugv_call("status_get_odom", {})

async def _execute_ugv_status_get_lidar_summary(self):
    return await self._ugv_call("status_get_lidar_summary", {})

async def _execute_ugv_status_list_nodes(self):
    return await self._ugv_call("status_list_nodes", {})

async def _execute_ugv_status_list_topics(self):
    return await self._ugv_call("status_list_topics", {})

async def _execute_ugv_status_health(self):
    return await self._ugv_call("status_health", {})

async def _execute_ugv_nav_goto_point(self, x, y, yaw_deg=0.0):
    return await self._ugv_call("nav_goto_point", {"x": x, "y": y, "yaw_deg": yaw_deg})

async def _execute_ugv_nav_cancel(self):
    return await self._ugv_call("nav_cancel", {})

async def _execute_ugv_nav_status(self):
    return await self._ugv_call("nav_status", {})

async def _execute_ugv_system_emergency_stop(self):
    return await self._ugv_call("system_emergency_stop", {})

async def _execute_ugv_system_servo_center(self):
    return await self._ugv_call("system_servo_center", {})

async def _execute_ugv_system_servo_release(self):
    return await self._ugv_call("system_servo_release", {})
```

**Step 3: Write a test**

Create `Orchestrator/tests/test_ugv_proxy.py`:
```python
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from Orchestrator.tools.blackbox_tools import BlackBoxToolExecutor

@pytest.mark.asyncio
async def test_ugv_proxy_calls_correct_url():
    exec_ = BlackBoxToolExecutor(operator="test")
    with patch("Orchestrator.tools.blackbox_tools.aiohttp.ClientSession") as MS:
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"tool": "motion_stop", "result": {"stopped": True}})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        sess = MS.return_value.__aenter__.return_value
        sess.post = MagicMock(return_value=resp)
        r = await exec_.execute("ugv_motion_stop", {})
        assert r.success
        args, kwargs = sess.post.call_args
        assert "ugv-beast:8080/tool/motion_stop" in args[0] or "ugv-beast:8080/tool/motion_stop" in str(kwargs)
```

**Step 4: Run**
```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc && \
  Orchestrator/venv/bin/python -m pytest Orchestrator/tests/test_ugv_proxy.py -v
```
Expected: 1 passing.

**Step 5: Live smoke test against the real UGV**
```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc && \
  Orchestrator/venv/bin/python -c "
import asyncio
from Orchestrator.tools.blackbox_tools import BlackBoxToolExecutor
async def main():
    exec_ = BlackBoxToolExecutor(operator='Brandon')
    r = await exec_.execute('ugv_status_get_pose', {})
    print('success=', r.success, 'data=', r.data)
asyncio.run(main())
"
```
Expected: `success= True data= {'x': ..., 'y': ..., 'yaw_deg': ...}`.

---

### Task 1.3: Mint the 22 UGV tools into ToolVault via `migrate.py`

**Files:**
- Run: `Orchestrator/toolvault/migrate.py` (existing batch mint CLI)
- Inspect: `ToolVault/toolvault_volume.txt`, `ToolVault/toolvault_manifest.json` (will gain new entries)

ToolVault already has a batch mint script that re-reads `TOOL_DEFINITIONS` from `tool_registry.py`, skips tools already minted (by name), and mints any new ones — computing their embedding via `gemini-embedding-001` / `text-embedding-004` and appending to the volume + manifest atomically. With 22 new `ugv_*` tools added in Task 1.1, running migrate will mint exactly those.

**Step 1: Confirm TOOLVAULT_ENABLED + feature flag**

```bash
grep '^TOOLVAULT_ENABLED' /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/.env
```
Expected: `TOOLVAULT_ENABLED=true`. If `false`, flip it + restart the orchestrator service first (without it, the `inject_for_prompt()` path isn't used).

**Step 2: Dry run**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc && \
  Orchestrator/venv/bin/python -m Orchestrator.toolvault.migrate --dry-run 2>&1 | tail -30
```
Expected output: lists 22 `ugv_*` tools as "would mint" (plus any other new tools if present). If any fewer, go back to Task 1.1 and check the `groups` field (migrate may filter by group membership).

**Step 3: Real mint run**

```bash
Orchestrator/venv/bin/python -m Orchestrator.toolvault.migrate 2>&1 | tee /tmp/ugv_mint.log | tail -50
```
Expected: 22 `Minted: ugv_XXX as TOOL NNN` lines, one per UGV tool. Each embed call takes ~0.5-2s, total ~30 s for 22 tools (network-bound on Gemini embedding API).

**Step 4: Verify volume + manifest state**

```bash
Orchestrator/venv/bin/python -c "
import json
m = json.load(open('ToolVault/toolvault_manifest.json'))
# manifest format varies; accept either top-level dict of name→entry OR {'tools': {...}}
entries = m.get('tools', m)
ugv = {k: v for k, v in entries.items() if isinstance(v, dict) and k.startswith('ugv_')}
print(f'UGV tools in manifest: {len(ugv)}')
for name, e in sorted(ugv.items()):
    emb_len = len(e.get('embedding', []))
    print(f'  {name:40s}  byte=[{e[\"byte_start\"]},{e[\"byte_end\"]}]  emb_dim={emb_len}')
assert len(ugv) == 22, f'expected 22 ugv entries, got {len(ugv)}'
"
```
Expected: all 22 entries present, each with an embedding vector (length 3072 for `gemini-embedding-001` or 768 for `text-embedding-004`).

**Step 5: Verify volume append-only contract**

```bash
grep -c '^NAME: ugv_' ToolVault/toolvault_volume.txt
```
Expected: `22`. The volume should include fresh blocks at the end — existing tools unchanged.

---

### Task 1.4: Verify semantic injection surfaces UGV tools for robot-related prompts (and NOT for unrelated ones)

**Files:**
- Test only — no code changes here. This is the security-model verification task.

The whole security rationale is: if the user isn't talking about the robot, the UGV tools shouldn't be in the model's context. Confirm this end-to-end with `inject_for_prompt()`.

**Step 1: Robot-related prompt SHOULD surface UGV tools**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc && \
  Orchestrator/venv/bin/python -c "
from Orchestrator.toolvault.injector import inject_for_prompt
schemas, instructions = inject_for_prompt('drive the robot forward for two seconds', provider='anthropic', group='chat')
names = [t['name'] for t in schemas]
print('tools injected:', names)
ugv_names = [n for n in names if n.startswith('ugv_')]
print(f'ugv_* tools found: {len(ugv_names)}')
assert 'ugv_motion_move_forward' in names, 'semantic search missed the obvious one'
assert len(ugv_names) >= 3, 'expected at least 3 ugv tools for a forward-drive prompt'
"
```

**Step 2: Camera-related prompt should surface camera + status UGV tools**

```bash
Orchestrator/venv/bin/python -c "
from Orchestrator.toolvault.injector import inject_for_prompt
schemas, _ = inject_for_prompt('what do you see? take a snapshot from the depth camera', provider='anthropic', group='chat')
names = [t['name'] for t in schemas]
print(names)
assert 'ugv_camera_snapshot' in names or 'ugv_camera_list' in names
"
```

**Step 3: Unrelated prompt should NOT surface UGV tools (security gate)**

```bash
Orchestrator/venv/bin/python -c "
from Orchestrator.toolvault.injector import inject_for_prompt
schemas, _ = inject_for_prompt('what is the weather in San Francisco today?', provider='anthropic', group='chat')
names = [t['name'] for t in schemas]
ugv_leakage = [n for n in names if n.startswith('ugv_')]
print('ugv_* leakage for weather prompt:', ugv_leakage)
assert len(ugv_leakage) == 0, f'UGV tools leaked into weather query: {ugv_leakage}'
"
```
Expected: empty list — the robot tools stayed dormant.

**Step 4: Realtime voice prompt — confirm UGV tools also inject for realtime provider**

```bash
Orchestrator/venv/bin/python -c "
from Orchestrator.toolvault.injector import inject_for_prompt
schemas, _ = inject_for_prompt('pan left forty-five degrees', provider='openai_realtime', group='realtime')
names = [t.get('name') for t in schemas]
print('realtime tools:', names)
assert 'ugv_gimbal_look_at' in names, 'realtime provider did not get UGV tool'
"
```
Same for provider `'gemini_live'` and `'grok_live'` — robot control must work in voice-mode sessions too.

**Step 5: End-to-end over HTTP**

Restart the orchestrator so the running process picks up the new volume + manifest:

```bash
sudo systemctl restart blackbox.service
sleep 90
curl -s http://localhost:9091/health
```

Then from BlackBox, hit `/chat` with a robot prompt and inspect the tool list the model received. Add a log line or temp debug endpoint if needed.

```bash
curl -s -X POST http://localhost:9091/chat \
  -H "Content-Type: application/json" \
  -d '{"operator":"Brandon","provider":"anthropic","model":"claude-sonnet-4-6",
       "streaming":false,
       "messages":[{"role":"user","content":"What is the robot\\u2019s current pose?"}]}' \
  | python3 -m json.tool | head -40
```
Expected: the model actually calls `ugv_status_get_pose` (or its executor name), the Orchestrator proxies the call to `http://ugv-beast:8080/tool/status_get_pose`, and the response is speakable text like "The robot is at x equals zero, y equals zero, facing about one degree west."

If the model doesn't call the tool:
- Check `inject_for_prompt()` returned UGV tools for this query (Step 1 above works, but live may differ).
- Check that `chat_routes.py` actually uses `inject_for_prompt()` when `TOOLVAULT_ENABLED=true`.
- Check the system prompt assembly is NOT stripping tool instructions.

---

## Phase 2 — Robot Voice (TTS → JBL speaker)

### Task 2.1: Jetson-side `ugv-voice` service (HTTP `/speak`)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/voice/__init__.py` (empty)
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/voice/speak_server.py`
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/test_speak_server.py`

**Step 1: Failing test**

```python
# tests/test_speak_server.py
from fastapi.testclient import TestClient
from ugv_tools_api.voice.speak_server import app

c = TestClient(app)

def test_speak_health():
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True

def test_speak_rejects_empty_text():
    r = c.post("/speak", json={"text": ""})
    assert r.status_code == 400
```

**Step 2: Implement**

```python
# ugv_tools_api/voice/speak_server.py
"""FastAPI service on the Jetson host: receives text, plays it on the JBL speaker.

Runs on port 8081 (container or host — configurable via UGV_VOICE_PORT).
Endpoint: POST /speak {text, voice?='onyx', rate?=1.0}
Uses OpenAI TTS (tts-1-hd) by default. Requires OPENAI_API_KEY in env.
"""
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_VOICE = os.environ.get("UGV_VOICE", "onyx")
ALSA_DEVICE = os.environ.get("UGV_ALSA_DEVICE", "default")  # "hw:1,0" for JBL on 3.5mm

class SpeakRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    rate: float = 1.0

app = FastAPI(title="UGV Voice", version="0.1.0")

@app.get("/health")
def health():
    return {"ok": True, "openai_key_set": bool(OPENAI_API_KEY), "alsa": ALSA_DEVICE}

@app.post("/speak")
async def speak(req: SpeakRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not set")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": "tts-1-hd", "voice": req.voice, "input": text, "response_format": "mp3"},
        )
        if r.status_code != 200:
            raise HTTPException(500, f"OpenAI TTS {r.status_code}: {r.text[:200]}")
        mp3 = r.content
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3)
        path = f.name
    try:
        subprocess.run(["mpg123", "-q", "-a", ALSA_DEVICE, path], check=False, timeout=60)
    except FileNotFoundError:
        # Fallback: ffmpeg + aplay
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", path, "-ar", "48000", "-ac", "2",
             "-f", "alsa", ALSA_DEVICE],
            check=False, timeout=60
        )
    finally:
        os.unlink(path)
    return {"played": True, "chars": len(text)}

def main():
    import uvicorn
    port = int(os.environ.get("UGV_VOICE_PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
```

**Step 3: Install deps in container**

Append to `container-bootstrap.sh`:
```bash
pip3 install 'httpx==0.28.*'
# mpg123 is an apt package
echo 'jetson' | sudo -S apt-get install -y mpg123
```

**Step 4: Test inside container**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 "cd /home/ws/ugv_ws/ugv_tools_api && \
  PYTHONPATH=.:\$PYTHONPATH python3 -m pytest tests/test_speak_server.py -v -p no:anyio"
```

**Step 5: Live speak test**

```bash
# Launch speak server on the Jetson, pipe OPENAI_API_KEY via env
# Copy the key from BlackBox .env:
OPENAI_KEY=$(grep '^OPENAI_API_KEY' .env | cut -d= -f2-)

sshpass -p 'jetson' ssh root@192.168.1.155 -p 23 \
  "OPENAI_API_KEY='$OPENAI_KEY' UGV_ALSA_DEVICE=hw:1,0 \
   PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api \
   python3 -m ugv_tools_api.voice.speak_server &
   sleep 3 && \
   curl -X POST http://localhost:8081/speak \
     -H 'Content-Type: application/json' \
     -d '{\"text\": \"Hello Brandon. Robot voice is online.\"}'"
```
Expected: Brandon hears the robot say "Hello Brandon. Robot voice is online." through the JBL speaker. `played: true` returned.

---

### Task 2.2: systemd `ugv-voice.service`

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-voice.service`
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/start_voice.sh`

**Step 1: Write systemd unit**

```ini
[Unit]
Description=UGV Voice (TTS → JBL speaker)
Requires=ugv-tools-api.service
After=ugv-tools-api.service

[Service]
Type=simple
Restart=on-failure
RestartSec=5
EnvironmentFile=/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/voice.env
ExecStart=/usr/bin/docker exec -e OPENAI_API_KEY ugv_waveshare /home/ws/ugv_ws/ugv_tools_api/deploy/start_voice.sh
ExecStop=/usr/bin/docker exec ugv_waveshare pkill -f "ugv_tools_api.voice.speak_server"

[Install]
WantedBy=multi-user.target
```

`voice.env` (host-side, contains `OPENAI_API_KEY=sk-...`). Mode 0600, owned root.

**Step 2: Write `start_voice.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api
export UGV_VOICE_PORT=8081
export UGV_ALSA_DEVICE=${UGV_ALSA_DEVICE:-hw:1,0}
exec python3 -m ugv_tools_api.voice.speak_server
```

**Step 3: Install + enable**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  "sudo cp /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/ugv-voice.service /etc/systemd/system/ && \
   chmod +x /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/start_voice.sh && \
   sudo systemctl daemon-reload && sudo systemctl enable --now ugv-voice.service && \
   sleep 5 && sudo systemctl status ugv-voice.service --no-pager"
```

**Step 4: Verify reachable from BlackBox**

```bash
curl -s http://ugv-beast:8081/health
```
Expected: `{"ok": true, "openai_key_set": true, "alsa": "hw:1,0"}`.

---

### Task 2.3: Orchestrator streaming hook — auto-POST assistant text to robot

**Files:**
- Modify: `Orchestrator/routes/chat_routes.py` (find assistant-text streaming path for Anthropic/OpenAI/Gemini)
- Create: `Orchestrator/robot/speak_client.py`

**Step 1: Write speak client**

```python
# Orchestrator/robot/speak_client.py
"""Fire-and-forget client that sends assistant text blocks to the UGV's voice service."""
import asyncio
import aiohttp
import logging

logger = logging.getLogger(__name__)
UGV_VOICE_URL = "http://ugv-beast:8081/speak"

async def speak_on_robot(text: str, voice: str = "onyx") -> None:
    """Best-effort POST; do not block the chat stream on robot-side failures."""
    text = text.strip()
    if not text:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(UGV_VOICE_URL, json={"text": text, "voice": voice}) as r:
                if r.status != 200:
                    logger.warning("speak_on_robot %s: %s", r.status, await r.text())
    except Exception as e:
        logger.warning("speak_on_robot error: %s", e)

def speak_fire_and_forget(text: str) -> None:
    """Schedule without awaiting; logs failures but never raises."""
    loop = asyncio.get_running_loop() if asyncio._get_running_loop() else None
    if loop and loop.is_running():
        asyncio.create_task(speak_on_robot(text))
    # else: no loop — skip
```

**Step 2: Hook into chat_routes.py streaming**

Find the assistant-text accumulation path (look for `text_delta` or similar in the Anthropic/OpenAI/Gemini streaming handlers). When `_is_ugv_mode(operator, session_metadata)` is true, after each complete text block is flushed to the client, also call `speak_fire_and_forget(block_text)`.

Concrete patch locations depend on current code shape — the reviewer should:
1. Grep for where assistant text blocks are finalized per provider.
2. Wrap the finalization call with a mode check and a `speak_fire_and_forget` invocation.
3. Handle streaming deltas correctly: buffer deltas until a sentence boundary or block end, THEN speak. Speaking each 10-char delta is horrible UX.

Simplest implementation: speak the ENTIRE assistant text block when the streaming loop hits `message_stop` or equivalent (one utterance per turn, not per delta).

**Step 3: Live test**

```bash
curl -X POST http://localhost:9091/chat \
  -H "Content-Type: application/json" \
  -d '{"operator":"ugv-beast","provider":"anthropic","model":"claude-sonnet-4-6",
       "streaming":true,
       "messages":[{"role":"user","content":"Say hello to Brandon and tell him what robot you are."}]}' \
  --no-buffer | head -40
```
Expected:
- Stream returns text.
- Brandon hears the robot speaking that text on the JBL.

---

## Phase 3 — Robot Ears (Wake word → STT → model input)

### Task 3.1: Train openWakeWord for "Black Box Flight Recorder"

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/voice_models/black_box_flight_recorder.onnx` (trained model)
- Create: `docs/ugv-beast/setup/ugv_tools_api/scripts/train_wakeword.sh`

**Step 1: Install openWakeWord training deps in container**

Append to `container-bootstrap.sh`:
```bash
pip3 install 'openwakeword' 'onnxruntime' 'pyaudio' 'webrtcvad' 'numpy<2'
echo 'jetson' | sudo -S apt-get install -y portaudio19-dev
```

**Step 2: Training script**

openWakeWord's custom-model training pipeline: generate 500+ synthetic TTS samples of the phrase using multiple voices, mix with background negatives, train.

```bash
#!/usr/bin/env bash
# train_wakeword.sh — trains an openWakeWord model for "Black Box Flight Recorder"
set -euo pipefail
PHRASE="black box flight recorder"
OUT=docs/ugv-beast/setup/ugv_tools_api/voice_models/black_box_flight_recorder.onnx

# Use openWakeWord's built-in Piper-based synthetic sample generator
# (requires TTS model + background audio corpus)
python3 -m openwakeword.train \
  --phrase "$PHRASE" \
  --num_positives 2000 \
  --num_negatives 2000 \
  --output "$OUT"
```

Realistic caveat: the openWakeWord project provides a more elaborate training notebook. Point the implementer at: https://github.com/dscripka/openWakeWord/blob/main/docs/custom_models.md — they should follow it, produce the `.onnx`, commit it to the repo under `voice_models/`.

**Step 3: Verify the model loads**

```bash
python3 -c "
from openwakeword.model import Model
m = Model(wakeword_models=['voice_models/black_box_flight_recorder.onnx'])
print('loaded:', list(m.models.keys()))
"
```

---

### Task 3.2: Jetson-side `ugv-ears` service

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/voice/ears.py`
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/test_ears.py`

**Step 1: Implement the listen loop**

```python
# ugv_tools_api/voice/ears.py
"""Continuous microphone listen loop on the Jetson.

Flow: PyAudio 16kHz mono capture → openWakeWord scorer → on trigger, buffer
audio until webrtcvad says 'silence' → POST to Orchestrator /stt → POST
transcription to BlackBox /chat as user message."""
import asyncio
import collections
import logging
import os
import wave

import numpy as np
import pyaudio
import webrtcvad
from openwakeword.model import Model
import httpx

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 16-bit mono
PRE_ROLL_SEC = 1.5
SILENCE_TIMEOUT_SEC = 1.0

BLACKBOX_URL = os.environ.get("BLACKBOX_URL", "http://100.74.17.54:9091")
BLACKBOX_OPERATOR = os.environ.get("BLACKBOX_OPERATOR", "ugv-beast")
BLACKBOX_MODEL = os.environ.get("BLACKBOX_MODEL", "claude-sonnet-4-6")
BLACKBOX_PROVIDER = os.environ.get("BLACKBOX_PROVIDER", "anthropic")

WAKEWORD_MODEL = os.environ.get("WAKEWORD_MODEL",
    "/home/ws/ugv_ws/ugv_tools_api/voice_models/black_box_flight_recorder.onnx")
WAKEWORD_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.5"))

def capture_loop():
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                     input=True, frames_per_buffer=int(SAMPLE_RATE * FRAME_MS / 1000))
    ww = Model(wakeword_models=[WAKEWORD_MODEL])
    vad = webrtcvad.Vad(2)  # aggressiveness 0-3
    pre_roll = collections.deque(maxlen=int(PRE_ROLL_SEC * 1000 / FRAME_MS))
    logger.info("ears: listening for wake word…")

    while True:
        frame = stream.read(int(SAMPLE_RATE * FRAME_MS / 1000), exception_on_overflow=False)
        pre_roll.append(frame)
        samples = np.frombuffer(frame, dtype=np.int16)
        scores = ww.predict(samples)
        triggered = any(s > WAKEWORD_THRESHOLD for s in scores.values())
        if not triggered:
            continue

        logger.info("ears: wake word detected, recording…")
        buf = list(pre_roll)  # include 1.5s of pre-roll
        silent_count = 0
        silent_target = int(SILENCE_TIMEOUT_SEC * 1000 / FRAME_MS)
        while silent_count < silent_target:
            frame = stream.read(int(SAMPLE_RATE * FRAME_MS / 1000), exception_on_overflow=False)
            buf.append(frame)
            if vad.is_speech(frame, SAMPLE_RATE):
                silent_count = 0
            else:
                silent_count += 1
            if len(buf) > int(30 * 1000 / FRAME_MS):  # 30s hard cap
                break

        wav_path = "/tmp/ugv_ears_utterance.wav"
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(buf))
        asyncio.run(transcribe_and_send(wav_path))

async def transcribe_and_send(wav_path: str):
    async with httpx.AsyncClient(timeout=60) as c:
        # 1. STT
        with open(wav_path, "rb") as f:
            r = await c.post(f"{BLACKBOX_URL}/stt", files={"audio": ("utt.wav", f, "audio/wav")})
        if r.status_code != 200:
            logger.error("stt failed %s: %s", r.status_code, r.text[:200])
            return
        text = r.json().get("text", "").strip()
        if not text:
            logger.warning("empty transcript, skipping")
            return
        logger.info("heard: %s", text)

        # 2. Push to /chat
        r = await c.post(f"{BLACKBOX_URL}/chat", json={
            "operator": BLACKBOX_OPERATOR,
            "provider": BLACKBOX_PROVIDER,
            "model": BLACKBOX_MODEL,
            "streaming": True,
            "messages": [{"role": "user", "content": text}],
        })
        logger.info("chat response: %s", r.status_code)
        # Assistant text → speak_server will be hit by Orchestrator's auto-speak hook.

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    capture_loop()

if __name__ == "__main__":
    main()
```

**Step 2: systemd unit `ugv-ears.service`**

```ini
[Unit]
Description=UGV Ears (wake word + STT)
Requires=ugv-voice.service
After=ugv-voice.service

[Service]
Type=simple
Restart=on-failure
RestartSec=5
EnvironmentFile=/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/ears.env
ExecStart=/usr/bin/docker exec -e BLACKBOX_URL -e BLACKBOX_OPERATOR -e WAKEWORD_THRESHOLD \
          ugv_waveshare /home/ws/ugv_ws/ugv_tools_api/deploy/start_ears.sh
ExecStop=/usr/bin/docker exec ugv_waveshare pkill -f "ugv_tools_api.voice.ears"

[Install]
WantedBy=multi-user.target
```

**Step 3: Live wake-word test**

Install + start the service. Then say "Black Box Flight Recorder, what's your pose?" into the mic.

Expected:
- `journalctl -u ugv-ears.service -f` shows `wake word detected` + `heard: what's your pose`
- Orchestrator's `/chat` receives the message
- Model calls `ugv_status_get_pose` (auto-injected via UGV mode)
- Response text streams back
- `ugv-voice.service` auto-speaks the response
- Brandon hears the robot say something like "I'm at x=0.0, y=0.0, facing minus one degree."

---

### Task 3.3: Service ordering + cold-boot test

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-ears.service`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-voice.service`

Ensure all three services (`ugv-tools-api`, `ugv-voice`, `ugv-ears`) have correct `Requires=` + `After=` chains and all `WantedBy=multi-user.target`.

**Step 1: Cold reboot**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 "sudo reboot"
sleep 120
```

**Step 2: After reboot, verify from BlackBox**

```bash
UGV_HOST=ugv-beast ./scripts/test-ugv-tools-remote.sh      # existing script
curl -s http://ugv-beast:8081/health                        # voice
curl -s -X POST http://ugv-beast:8081/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"I am back online, Brandon."}'
# Speak into mic: "Black Box Flight Recorder, give me a LiDAR summary"
# Watch journalctl -u ugv-ears.service -f on the Jetson for confirmation
```

Expected: all three services active, end-to-end wake-word → tools → voice loop works.

---

## Phase 4 — Integration test

### Task 4.1: Full E2E demo script

**Files:**
- Create: `scripts/ugv-voice-demo.sh`

```bash
#!/usr/bin/env bash
# Full-loop demo:
# 1. Confirm all three services are up
# 2. Speak a greeting via /speak (confirm JBL output)
# 3. Wait for the operator to say the wake phrase + a question
# 4. Tail journalctl for the transcription + model turn + spoken reply
```

Run, iterate, then commit the known-good baseline.

---

## Out of Scope (V2)

- Bidirectional streaming voice (barge-in during speech, interrupting robot mid-sentence)
- Multi-turn voice context (robot remembering the prior turn across wake-word re-triggers)
- Face-tracking / attention behaviors
- Visual SLAM context (feeding snapshot into model's system prompt on each turn)

## Risk Register

| Risk | Mitigation |
|------|------------|
| openWakeWord custom model accuracy | Train with 2000+ positives, adjust threshold, allow re-training |
| ALSA device numbering changes across reboots | Use USB-specific `hw:N,0` OR use `default` + asound.conf mapping |
| OpenAI TTS latency (>2s) feels sluggish | Pre-warm the HTTP connection; cache common phrases |
| BlackBox IP changes (tailnet or LAN) | `ears.env` env var override; test both tailnet + magicDNS |
| Mic picks up its own voice output (feedback) | Mute mic during TTS playback (simple state flag in `ears.py`) |
| STT latency for long utterances | Cap recording at 10s before sending; warn operator |

---

Plan complete and saved to `docs/plans/2026-04-16-ugv-toolvault-voice.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Parallel Session (separate)** — Open new session with `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
