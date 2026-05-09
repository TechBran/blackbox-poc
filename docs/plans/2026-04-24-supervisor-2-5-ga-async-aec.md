# Supervisor 2.5 GA + Async Tools + Watch Mode + AEC3 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
>
> **MODEL ID CORRECTION (post-Task-0):** This plan body originally referenced `gemini-live-2.5-flash-native-audio` (Vertex AI naming). That id does NOT exist on the Google AI Studio API used by `google-genai`. **The actual locked target is `gemini-2.5-flash-native-audio-latest`** (GA-track evergreen alias). See `docs/plans/notes/2026-04-24-2-5-ga-async-spike.md` for the spike that confirmed it. Anywhere this plan reads `gemini-live-2.5-flash-native-audio`, substitute `gemini-2.5-flash-native-audio-latest`.

**Goal:** Evolve the `ugv-supervisor` service from `gemini-3.1-flash-live-preview` (no async tools, half-duplex echo gate, pull-only perception) to `gemini-2.5-flash-native-audio-latest` (GA-track, NON_BLOCKING confirmed) with NON_BLOCKING `dispatch_er_mission`, streaming mission-progress `FunctionResponse` updates, continuous 1 FPS camera push during missions, in-process WebRTC AEC3 echo cancellation, and proactive token-budget session rotation.

**Architecture:** The supervisor stays a single long-running Python service inside the `ugv_waveshare` Docker container on the Jetson Orin Nano. All shape changes happen on top of the existing wiring (mic/speaker handoff with `ugv-ears`, ROS camera/costmap subscriber, `ugv_tools_api`/`ER` HTTP backends). The four functional shifts are: (1) the model swap unlocks `behavior=NON_BLOCKING` on long tools so the model never goes deaf during multi-second missions; (2) a per-mission async poller streams pose/status as multi-part `FunctionResponse(will_continue=True)` updates with `scheduling=SILENT|WHEN_IDLE|INTERRUPT` so the model has live awareness without having to ask; (3) a "watch mode" pushes pantilt JPEGs at 1 FPS via `send_realtime_input(video=…)` so vision becomes ambient input the model opts out of, not a tool the lazy model forgets to call; (4) WebRTC AEC3 inside `audio_io` replaces the brutal half-duplex silent-chunk gate so barge-in works again. Token-budget rotation prevents context starvation as ambient frames + audio accumulate.

**Tech Stack:**
- Python 3.10 inside the `ugv_waveshare` container on Jetson Orin Nano
- `google-genai` 1.73.1 — Live SDK with `Behavior.NON_BLOCKING`, `FunctionResponseScheduling.{SILENT,WHEN_IDLE,INTERRUPT}` validated
- `webrtc-audio-processing` Python binding (path: pip wheel preferred; build-from-source fallback) for AEC3
- `numpy` + `scipy.signal` for the 24 kHz → 16 kHz speaker reference resample
- `httpx` for the async ER + tools_api HTTP calls (already wired)
- `pytest` + `pytest-asyncio` (already configured in `pyproject.toml`)
- Source-of-truth: laptop at `docs/ugv-beast/setup/ugv_tools_api/`, deploys to Jetson via `scripts/sync-ugv-tools.sh`

**Out of scope (deferred to follow-up plans):**
- BlackBox-side `ugv_dispatch_supervisor` tool exposed to Claude in the laptop chat (separate plan)
- ER `agent_loop.py` auto-continuation audit (already complete in original plan Task 12)
- Hardware echo-cancellation USB mic swap (only if AEC3 in Task 5 proves insufficient)
- Vertex AI vs `google-genai` client switch (no need; we're sticking with `google-genai`)

---

## Empirical Knowledge Carried Forward

These twelve regression guards from the 2026-04-20 supervisor spike (and the original 14-task plan that productionized it) MUST NOT be reintroduced by any task in this plan. Where applicable, each task body calls out which guards it touches.

| # | Lesson | Touched by |
|---|---|---|
| 1 | Use `response.data` (SDK concatenator) for output audio, NOT manual `model_turn.parts` iteration | (none in this plan; inherited and preserved) |
| 2 | Mic reads MUST go through a dedicated `ThreadPoolExecutor(max_workers=1)`; default pool starves under `rclpy.spin_once` | Task 5 (the AEC `process()` call runs inside `pump_mic`, must stay on the same dedicated pool) |
| 3 | `FunctionResponseBlob` cannot carry bytes through the SDK's JSON serializer; image-returning tools use `send_realtime_input(video=Blob(…))` sidechannel | Task 4 (the watch-mode push uses the sidechannel — same pattern as `_vision_tool`) |
| 4 | `language_code='en-US'` must be pinned (otherwise auto-detect mishears as Korean/Spanish on noisy mic) | Task 1 (verify the swap doesn't drop this) |
| 5 | `AutomaticActivityDetection.end_of_speech_sensitivity = END_SENSITIVITY_LOW` for the operator's mic environment | Task 1 (verify preserved) |
| 6 | 880 Hz ready chime + 660 Hz close chime so the operator knows mic state | (preserved as-is) |
| 7 | Speaker bleed-back exists; current half-duplex `silent_chunk` gate is the **fallback** after Task 5 | Task 5 (replaces this with AEC3 but keeps env flag for rollback) |
| 8 | `arecord plughw:CARD=Camera,DEV=0 -f S16_LE -r 16000 -c 1 -t raw` is the confirmed-working mic command | Task 5 |
| 9 | `aplay -D plughw:CARD=Device,DEV=0 -f S16_LE -r 24000 -c 1` is the confirmed-working speaker command | Task 5 (24 kHz → 16 kHz resample for AEC reference) |
| 10 | `SpeakerStream.write` blocks on ALSA backpressure — dispatch via `run_in_executor` | Task 5 (the speaker reference ring write must not stall the AEC path) |
| 11 | ESP32 IMU is broken — fused odometry comes from `/odom` (OAK-D BMI270 + EKF) | Task 3 (poller reads pose from `/odom`-backed `status_get_pose`, NOT raw encoders) |
| 12 | Patch `websockets.client.connect` to `ping_interval=60, ping_timeout=60` BEFORE importing `google.genai` | Task 1 (verify `_patch_ws_keepalive()` still runs first) |

**Three Google-docs-grounded production patterns also stay:**

| Pattern | Source | Status |
|---|---|---|
| `context_window_compression(SlidingWindow())` to remove session duration cap | ai.google.dev/live-session | Already in `_build_config`; preserved |
| `session_resumption` handle persisted across reconnects (2 hr TTL) | ai.google.dev/live-session | Already in `_run_one_session`; preserved |
| GoAway is NOT terminal — keep pumping responses until the server actually closes | ai.google.dev/live-session | Already in `pump_responses`; preserved |

**One new Google-docs-grounded pattern this plan introduces:**

| Pattern | Source | Where |
|---|---|---|
| NON_BLOCKING tool: send first `FunctionResponse(will_continue=True, scheduling=SILENT)` immediately, then 1+ updates with same `id`, then terminal `will_continue=False` | ai.google.dev/live-api/tools | Tasks 2 and 3 |

---

## Pre-Plan Reconciliation: Pull Jetson `tests/supervisor/` Back to Laptop

The original supervisor plan was executed in place on the Jetson, so `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/` exists on the Jetson but **not on the laptop**. Before any TDD task starts, pull it back so the laptop is the source of truth.

```bash
# From laptop
LOCAL=/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/docs/ugv-beast/setup/ugv_tools_api
sshpass -p 'jetson' rsync -avz --include='supervisor/' --include='supervisor/**' --include='er/' --include='er/**' --exclude='*' \
  jetson@192.168.1.155:/home/jetson/ugv_ws_waveshare/ugv_tools_api/tests/ \
  $LOCAL/tests/

# Verify
ls $LOCAL/tests/supervisor/   # __init__.py, test_audio_io.py, test_config.py, ...
ls $LOCAL/tests/er/            # whatever was created during the original plan

# Commit the parity fix BEFORE any task starts
cd $LOCAL && git add tests/supervisor tests/er && \
  git commit -m "chore(tests): reconcile supervisor/er tests from Jetson to laptop"
```

If the rsync produces unexpected diffs (e.g. files on laptop newer than Jetson), stop and audit — do not just clobber.

---

## Target Repository Layout (after this plan)

Additions are marked **NEW**. Modifications are marked *MOD*.

```
docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/
├── __init__.py
├── config.py                  *MOD* — default model swap, AEC + budget knobs
├── audio_io.py                *MOD* — SpeakerStream reference ring, AEC integration point
├── video_io.py                  (unchanged; FrameCache reused by watch_stream)
├── tool_declarations.py       *MOD* — NON_BLOCKING flag on dispatch_er_mission, new SET_WATCH_MODE
├── tool_handlers.py           *MOD* — exec_set_watch_mode, exec_dispatch_er_mission spawns poller
├── session.py                 *MOD* — NON_BLOCKING dispatch path, watch auto-on/off, budget rotation
├── handle_store.py              (unchanged)
├── service.py                   (unchanged)
├── main.py                      (unchanged)
├── mission_poller.py            **NEW** — per-mission status streamer
├── watch_stream.py              **NEW** — 1 FPS camera push loop
├── aec.py                       **NEW** — Aec3Wrapper around webrtc-audio-processing
├── budget.py                    **NEW** — TokenBudget tracker + rotation signal
├── README.md                  *MOD* — operator notes for watch mode, barge-in, AEC debug
└── live_spike.py                (unchanged; legacy spike file)

docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/
├── __init__.py
├── test_audio_io.py           *MOD* — SpeakerStream reference ring tests
├── test_config.py             *MOD* — model default + AEC/budget knob defaults
├── test_handle_store.py         (unchanged)
├── test_tool_declarations.py  *MOD* — DISPATCH_ER_MISSION.behavior == NON_BLOCKING; SET_WATCH_MODE present
├── test_tool_handlers.py      *MOD* — exec_set_watch_mode; dispatch returns immediately
├── test_video_io.py             (unchanged)
├── test_video_io_ros.py         (unchanged)
├── test_mission_poller.py       **NEW**
├── test_watch_stream.py         **NEW**
├── test_aec.py                  **NEW**
└── test_budget.py               **NEW**

docs/ugv-beast/setup/ugv_tools_api/deploy/
├── start_supervisor.sh          (unchanged)
├── supervisor.env             *MOD* — SUPERVISOR_MODEL=gemini-live-2.5-flash-native-audio, SUPERVISOR_AEC_MODE, SUPERVISOR_AEC_DELAY_MS, SUPERVISOR_BUDGET_*
└── (Dockerfile or requirements.txt update — see Task 5)

docs/plans/notes/
├── 2026-04-24-2-5-ga-async-spike.md    **NEW** — Task 0 outcome record
└── 2026-04-25-supervisor-2-5-bench.md  **NEW** — Task 8 bench session record (date adjusted to actual bench day)
```

---

## Implementation Tasks

Tasks are ordered so each leaves the repo in a working state. Task 0 is pure spike (no production code). Task 1 is a single-line config flip with tests. Tasks 2–3 deliver the NON_BLOCKING + status-streaming feature pair. Task 4 turns on ambient vision. **Task 5 (AEC3) is the highest-risk task** — it touches the live audio pipeline; do not start it unless Tasks 0–4 are landed and stable. Tasks 6–8 are budget rotation, docs, and the on-robot bench record.

---

### Task 0: Empirical NON_BLOCKING + multi-FunctionResponse spike on `gemini-live-2.5-flash-native-audio`

**Files:**
- Create (throwaway): `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/spike_2_5_async.py`
- Create: `docs/plans/notes/2026-04-24-2-5-ga-async-spike.md` (outcome record)

**Why:** The official Gemini Live capabilities matrix lists `gemini-2.5-flash-live-preview` as supporting `NON_BLOCKING` but does NOT explicitly list the GA `gemini-live-2.5-flash-native-audio` variant. The whole architecture in this plan depends on NON_BLOCKING + multi-`FunctionResponse` streaming actually working on the GA model. We MUST verify empirically before locking the model swap. The spike is a 1-shot Python script run from the Jetson container against the real API; outcome is a markdown note, not production code.

**Regression guard touched:** #12 (websockets keepalive patch) — the spike script must apply it the same way `session.py` does. Copy-paste the `_patch_ws_keepalive()` block.

**Step 1: Write the spike script**

```python
# supervisor/spike_2_5_async.py
"""Throwaway: verify NON_BLOCKING + multi-FunctionResponse streaming
on gemini-live-2.5-flash-native-audio (GA). Delete after the run."""
import asyncio
import os

# Mirror session.py's keepalive patch BEFORE importing google.genai.
def _patch_ws_keepalive() -> None:
    try:
        import websockets.asyncio.client as _ws_mod
    except ImportError:
        import websockets.client as _ws_mod  # type: ignore
    _orig = _ws_mod.connect
    def _patched(*args, **kwargs):
        kwargs.setdefault("ping_interval", 60)
        kwargs.setdefault("ping_timeout", 60)
        return _orig(*args, **kwargs)
    _ws_mod.connect = _patched
_patch_ws_keepalive()

from google import genai
from google.genai import types

MODEL = "gemini-live-2.5-flash-native-audio"

# One NON_BLOCKING tool that pretends to be a long task.
LONG_TOOL = types.FunctionDeclaration(
    name="long_task",
    description="A long-running task. Returns immediately, then streams progress.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    behavior=types.Behavior.NON_BLOCKING,
)

CONFIG = types.LiveConnectConfig(
    response_modalities=[types.Modality.AUDIO],
    system_instruction=types.Content(parts=[types.Part(text=(
        "You are a test harness. When asked to run a long task, call long_task. "
        "Then narrate any updates you receive about it. Speak briefly."
    ))]),
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Orus")
        ),
        language_code="en-US",
    ),
    tools=[types.Tool(function_declarations=[LONG_TOOL])],
    output_audio_transcription=types.AudioTranscriptionConfig(),
    input_audio_transcription=types.AudioTranscriptionConfig(),
)


async def main() -> None:
    key = os.environ["GOOGLE_API_KEY"]
    client = genai.Client(api_key=key)
    saw_tool_call = False
    saw_audio_during_tool = False
    accepted_multi_part = False

    async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
        # Kick the model with a text turn (no mic).
        await session.send_client_content(
            turns=[types.Content(role="user", parts=[types.Part(text="Please run the long task.")])]
        )
        in_flight_id = None
        sent_silent = False
        sent_when_idle = False
        sent_terminal = False

        async for resp in session.receive():
            if resp.tool_call and resp.tool_call.function_calls:
                saw_tool_call = True
                fc = resp.tool_call.function_calls[0]
                in_flight_id = fc.id
                # 1) Immediate placeholder — SILENT, will_continue=True
                await session.send_tool_response(function_responses=[types.FunctionResponse(
                    id=fc.id, name=fc.name,
                    response={"status": "started"},
                    will_continue=True,
                    scheduling=types.FunctionResponseScheduling.SILENT,
                )])
                sent_silent = True

            # If the model produces audio while the tool is in flight,
            # that proves NON_BLOCKING is honored.
            if resp.data and in_flight_id and not sent_terminal:
                saw_audio_during_tool = True

            # After 2 s, send a WHEN_IDLE update.
            if in_flight_id and sent_silent and not sent_when_idle:
                await asyncio.sleep(2.0)
                await session.send_tool_response(function_responses=[types.FunctionResponse(
                    id=in_flight_id, name="long_task",
                    response={"status": "halfway", "note": "still working"},
                    will_continue=True,
                    scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
                )])
                sent_when_idle = True

            # After another 2 s, send the terminal.
            if in_flight_id and sent_when_idle and not sent_terminal:
                await asyncio.sleep(2.0)
                await session.send_tool_response(function_responses=[types.FunctionResponse(
                    id=in_flight_id, name="long_task",
                    response={"status": "done", "result": "ok"},
                    will_continue=False,
                    scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
                )])
                sent_terminal = True
                accepted_multi_part = True

            # Terminate after the model says one more thing post-terminal.
            if sent_terminal and resp.server_content and getattr(
                resp.server_content, "turn_complete", False,
            ):
                break

    print("RESULTS:")
    print(f"  saw_tool_call            = {saw_tool_call}")
    print(f"  accepted_multi_part      = {accepted_multi_part}")
    print(f"  saw_audio_during_tool    = {saw_audio_during_tool}")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Run the spike on the Jetson**

```bash
# From laptop, push the spike to Jetson and run it inside the container.
LOCAL=/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
sshpass -p 'jetson' scp \
  $LOCAL/docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/spike_2_5_async.py \
  jetson@192.168.1.155:/home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/supervisor/
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -lc "
    PID=\$(pgrep -f ugv_tools_api.supervisor | head -1)
    export \$(tr \\\\0 \\\\n < /proc/\$PID/environ | grep -E \"^(GOOGLE_API_KEY|HTTPS_PROXY|HTTP_PROXY|NO_PROXY)=\" )
    cd /home/ws/ugv_ws/ugv_tools_api && python3 -m ugv_tools_api.supervisor.spike_2_5_async
  "'
```

Expected output (all three lines `True`):
```
RESULTS:
  saw_tool_call            = True
  accepted_multi_part      = True
  saw_audio_during_tool    = True
```

**Step 3: Record the outcome**

Write `docs/plans/notes/2026-04-24-2-5-ga-async-spike.md` with:
- Date / time of run
- Exact model id used
- The three result booleans
- Stdout/stderr first 50 lines
- A `**Verdict:**` section: `PROCEED` (all three True) or `BLOCK` (any False, with a recommended fallback — typically `gemini-2.5-flash-live-preview`)

If `BLOCK`: STOP and ask the user before continuing. Do not proceed to Task 1.

**Step 4: Delete the spike file**

```bash
rm $LOCAL/docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/spike_2_5_async.py
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'rm /home/jetson/ugv_ws_waveshare/ugv_tools_api/ugv_tools_api/supervisor/spike_2_5_async.py'
```

**Step 5: Commit**

```bash
cd $LOCAL && git add docs/plans/notes/2026-04-24-2-5-ga-async-spike.md && \
  git commit -m "chore(supervisor): record 2.5 GA async-tool spike outcome"
```

---

### Task 1: Switch supervisor default model to `gemini-live-2.5-flash-native-audio`

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/supervisor.env`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_config.py`

**Why:** All subsequent tasks depend on the GA model being the default. The override remains env-controllable so we can roll back to 3.1-preview in <5 seconds without a redeploy by editing one line in `supervisor.env`. Regression guards #4 (language_code) and #5 (end_of_speech_sensitivity LOW) are unaffected — they live in `_build_config`, not `config.py`. Regression guard #12 (websockets patch) is also unaffected — it runs before `from google import genai`, regardless of model id.

**Step 1: Update the failing test**

```python
# tests/supervisor/test_config.py — modify test_defaults
def test_defaults(monkeypatch):
    for v in ("SUPERVISOR_MODEL", "SUPERVISOR_VOICE", "SUPERVISOR_MIC",
              "SUPERVISOR_SPK", "TOOLS_API_URL", "ER_URL",
              "SUPERVISOR_CAMERA_TOPIC", "SUPERVISOR_COSTMAP_TOPIC"):
        monkeypatch.delenv(v, raising=False)
    s = cfg.load()
    # Was: assert s.model == "gemini-3.1-flash-live-preview"
    assert s.model == "gemini-live-2.5-flash-native-audio"
```

Add a new test that proves env override still works (rollback path):

```python
def test_model_env_override_for_rollback(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_MODEL", "gemini-3.1-flash-live-preview")
    s = cfg.load()
    assert s.model == "gemini-3.1-flash-live-preview"
```

**Step 2: Run tests to verify failure**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
GOOGLE_API_KEY=test pytest tests/supervisor/test_config.py -v
```

Expected: `test_defaults` FAILs (expected `gemini-live-2.5-flash-native-audio`, got `gemini-3.1-flash-live-preview`).

**Step 3: Implementation**

```python
# supervisor/config.py — change ONE line in load()
        model=os.environ.get("SUPERVISOR_MODEL", "gemini-live-2.5-flash-native-audio"),
```

```bash
# deploy/supervisor.env — change ONE line, keep the old value as a comment
SUPERVISOR_MODEL=gemini-live-2.5-flash-native-audio
# Rollback: set to gemini-3.1-flash-live-preview to revert
```

**Step 4: Run tests**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_config.py -v
```

Expected: all green (test_defaults, test_override_via_env, test_google_api_key_required, test_model_env_override_for_rollback).

**Step 5: Sync and bounce on Jetson; verify health**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo systemctl restart ugv-supervisor.service'
sleep 5
sshpass -p 'jetson' ssh jetson@192.168.1.155 'curl -s http://localhost:8083/health'
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo journalctl -u ugv-supervisor.service --since "30 seconds ago" | grep -E "model|session opened|error" | tail'
```

Expected: `/health` returns `{"ok": true, ...}`; logs show `[supervisor] session opened` against the new model id; no errors.

**Step 6: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/config.py deploy/supervisor.env tests/supervisor/test_config.py
git commit -m "feat(supervisor): default to gemini-live-2.5-flash-native-audio (GA)"
```

---

### Task 2: Mark `dispatch_er_mission` as NON_BLOCKING; emit immediate placeholder `FunctionResponse`

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/tool_declarations.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py` (the `_dispatch_tool_calls` and `_respond` methods)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_tool_declarations.py`

**Why:** With the GA model now supporting NON_BLOCKING, `dispatch_er_mission` becomes the canonical async tool: it returns a placeholder FunctionResponse immediately so the model can keep talking, and Task 3's poller streams real progress under the same `id`. Critically, `cancel_er_mission` and `emergency_stop` STAY BLOCKING — they're imperative, the operator expects acknowledgement before the model says anything else, and ordering matters (cancel must complete before next dispatch). This task only adds the immediate-placeholder path; the actual streaming-update mechanism comes in Task 3.

**Regression guard touched:** None directly. New regression guard introduced: the `id` of the placeholder FunctionResponse MUST equal the original `tool_call.function_calls[*].id` so Task 3's poller can stream additional parts under the same id.

**Step 1: Write the failing test**

```python
# tests/supervisor/test_tool_declarations.py — add at bottom
from google.genai import types as gtypes


def test_dispatch_er_mission_is_non_blocking():
    from ugv_tools_api.supervisor.tool_declarations import DISPATCH_ER_MISSION
    assert DISPATCH_ER_MISSION.behavior == gtypes.Behavior.NON_BLOCKING


def test_safety_and_imperative_tools_stay_blocking():
    from ugv_tools_api.supervisor.tool_declarations import (
        CANCEL_ER_MISSION, EMERGENCY_STOP,
    )
    # behavior unset == BLOCKING (default). Either None or BLOCKING is acceptable.
    for d in (CANCEL_ER_MISSION, EMERGENCY_STOP):
        assert d.behavior in (None, gtypes.Behavior.BLOCKING), (
            f"{d.name} must stay BLOCKING — it is imperative and must be "
            f"acknowledged before the model speaks again"
        )
```

**Step 2: Run to verify failure**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_tool_declarations.py::test_dispatch_er_mission_is_non_blocking -v
```

Expected: FAIL — `DISPATCH_ER_MISSION.behavior` is `None`.

**Step 3: Implementation — declaration**

```python
# supervisor/tool_declarations.py — modify DISPATCH_ER_MISSION constructor
DISPATCH_ER_MISSION = types.FunctionDeclaration(
    name="dispatch_er_mission",
    description=(
        "Send a natural-language mission to the robot's on-device "
        "execution agent (Gemini Robotics-ER). The ER agent will translate "
        "the mission into Nav2 goals and tool calls and execute one "
        "complete task before returning control. Examples: 'Drive to the "
        "kitchen and stop there', 'Map this room', 'Inspect the charger.' "
        "This call returns immediately; mission progress is streamed back "
        "as it happens."
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
                "If false (default), and a mission is already running, the "
                "call returns an error so the operator can decide.",
                default=False,
            ),
        },
        required=["mission"],
    ),
    # NON_BLOCKING: the model can keep talking while ER works. Progress
    # streams back via Task 3's mission_poller as additional FunctionResponse
    # parts under the same id (will_continue=True with SILENT/WHEN_IDLE,
    # then will_continue=False on terminal).
    behavior=types.Behavior.NON_BLOCKING,
)
```

**Step 4: Implementation — session dispatch path**

```python
# supervisor/session.py — modify _dispatch_tool_calls dispatch case for dispatch_er_mission
            elif name == "dispatch_er_mission":
                # NON_BLOCKING: emit an immediate placeholder so the model
                # keeps generating. The handler kicks off the HTTP call to
                # ER and (in Task 3) spawns the progress-streaming poller
                # under the same fc.id.
                resp = await th.exec_dispatch_er_mission(
                    self._cfg, self._tracker, **args,
                )
                await session.send_tool_response(
                    function_responses=[types.FunctionResponse(
                        id=fc.id, name=fc.name,
                        response=resp,
                        will_continue=True,
                        scheduling=types.FunctionResponseScheduling.SILENT,
                    )],
                )
                # Task 3 hooks here: spawn mission_poller with fc.id, fc.name,
                # mission_id from resp. Until then, this single SILENT placeholder
                # is the only update the model gets. (Task 3 ALSO sends the
                # terminal will_continue=False once ER is done.)
```

For other NON_BLOCKING tools we add later, factor a small helper. For this task, leave the explicit branch — DRY can wait until Task 3 needs it too.

**Step 5: Run tests**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_tool_declarations.py -v
GOOGLE_API_KEY=test pytest tests/supervisor/ -v   # full sweep, no regressions
```

Expected: all green.

**Step 6: Sync and smoke test on Jetson**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo systemctl restart ugv-supervisor.service'
# Manually wake the supervisor and say "Drive forward 30 cm." Verify the
# model speaks ack + the robot moves. Watch journalctl for the placeholder
# response being sent. (Mission progress not yet streamed — that's Task 3.)
```

**Step 7: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/tool_declarations.py ugv_tools_api/supervisor/session.py tests/supervisor/test_tool_declarations.py
git commit -m "feat(supervisor): mark dispatch_er_mission NON_BLOCKING + immediate placeholder"
```

---

### Task 3: Mission-progress poller (the heart of the change)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/mission_poller.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/tool_handlers.py` (`exec_dispatch_er_mission` returns instantly + records context for the poller)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py` (start/stop poller alongside dispatch; cancel on `cancel_er_mission` / `emergency_stop` / session end)
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_mission_poller.py`

**Why:** With NON_BLOCKING dispatch landed in Task 2, the model has no live awareness of what's happening during a mission. Pull-only (`get_er_mission_status`, `get_robot_state`) means the model has to guess when to ask. Pushing 1 Hz `SILENT` pose ticks + `WHEN_IDLE` Nav2-state-transition events means the model sees ground truth without spending a turn on it. The poller is per-mission and per-session: it dies when the mission terminates, when the operator says "cancel", or when the Live session ends/reconnects (the next session re-establishes its own poller from the persisted `mission_id` if needed).

**Regression guards touched:**
- #11: pose comes from `status_get_pose` (which proxies `/odom`, the EKF-fused output), NOT raw encoders
- #2: the poller runs as an asyncio task on the main loop, NOT on the mic's dedicated executor pool; it doesn't compete with mic I/O for that pool slot

**Cadence design (locked):**
- 1 Hz `SILENT` ticks: `{pose: {x, y, yaw}, distance_to_goal: m, mission_status: 'active'}`
- Nav2 state transition (`pending → active`, `active → completed/failed/aborted`): `WHEN_IDLE` event with the new state
- Terminal: `will_continue=False` with `WHEN_IDLE` and the full result body
- INTERRUPT is reserved for `emergency_stop`-class events; the poller never emits INTERRUPT itself

**Step 1: Write the failing test**

```python
# tests/supervisor/test_mission_poller.py
"""Mission progress streamer: cadence, state transitions, terminal handling."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ugv_tools_api.supervisor.mission_poller import MissionPoller, PollerCallbacks
from ugv_tools_api.supervisor.config import SupervisorConfig


@pytest.fixture
def cfg():
    return SupervisorConfig(
        google_api_key="x", model="m", voice="v", language_code="en-US",
        mic_device="m", spk_device="s",
        tools_api_url="http://t", er_url="http://e",
        camera_topic="/c", costmap_topic="/m",
        handle_store_path="/tmp/h",
    )


@pytest.mark.asyncio
async def test_silent_ticks_at_1hz_during_active(cfg):
    sent = []
    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=lambda body: sent.append(("silent", body))),
        send_when_idle=AsyncMock(side_effect=lambda body: sent.append(("when_idle", body))),
        send_terminal=AsyncMock(side_effect=lambda body: sent.append(("terminal", body))),
    )
    # Mock httpx so /mission/{id} stays "active" for 3 ticks then "completed".
    states = ["active", "active", "active", "completed"]
    pose = {"x": 1.0, "y": 2.0, "yaw": 0.5}

    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status", new=AsyncMock(side_effect=[
        {"status": s, "events": [], "distance_to_goal": 0.3} for s in states
    ])), patch("ugv_tools_api.supervisor.mission_poller._fetch_pose", new=AsyncMock(return_value=pose)):
        p = MissionPoller(cfg, fc_id="call-1", fc_name="dispatch_er_mission",
                          mission_id="m-1", callbacks=cb, tick_s=0.05)  # fast for test
        await p.run()
    # Expect at least 3 SILENT ticks (active states) + 1 terminal
    silent = [s for s in sent if s[0] == "silent"]
    terminal = [s for s in sent if s[0] == "terminal"]
    assert len(silent) >= 3
    assert len(terminal) == 1
    assert terminal[0][1]["mission_status"] == "completed"


@pytest.mark.asyncio
async def test_when_idle_on_state_transition(cfg):
    sent = []
    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=lambda body: sent.append(("silent", body))),
        send_when_idle=AsyncMock(side_effect=lambda body: sent.append(("when_idle", body))),
        send_terminal=AsyncMock(side_effect=lambda body: sent.append(("terminal", body))),
    )
    # pending -> active is a transition; active -> completed is terminal.
    states = ["pending", "active", "active", "completed"]
    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status", new=AsyncMock(side_effect=[
        {"status": s, "events": [], "distance_to_goal": None} for s in states
    ])), patch("ugv_tools_api.supervisor.mission_poller._fetch_pose", new=AsyncMock(return_value={"x":0,"y":0,"yaw":0})):
        p = MissionPoller(cfg, fc_id="call-2", fc_name="dispatch_er_mission",
                          mission_id="m-2", callbacks=cb, tick_s=0.05)
        await p.run()
    when_idle = [s for s in sent if s[0] == "when_idle"]
    # We expect a transition event for pending->active
    transitions = [body for (_, body) in when_idle if body.get("event") == "state_transition"]
    assert any(t["from"] == "pending" and t["to"] == "active" for t in transitions)


@pytest.mark.asyncio
async def test_cancel_stops_poller_quickly(cfg):
    sent = []
    cb = PollerCallbacks(
        send_silent=AsyncMock(side_effect=lambda body: sent.append(("silent", body))),
        send_when_idle=AsyncMock(side_effect=lambda body: sent.append(("when_idle", body))),
        send_terminal=AsyncMock(side_effect=lambda body: sent.append(("terminal", body))),
    )
    # Stays "active" forever; we cancel externally.
    with patch("ugv_tools_api.supervisor.mission_poller._fetch_status",
               new=AsyncMock(return_value={"status": "active", "events": [], "distance_to_goal": 0.5})), \
         patch("ugv_tools_api.supervisor.mission_poller._fetch_pose",
               new=AsyncMock(return_value={"x":0,"y":0,"yaw":0})):
        p = MissionPoller(cfg, fc_id="call-3", fc_name="dispatch_er_mission",
                          mission_id="m-3", callbacks=cb, tick_s=0.02)
        task = asyncio.create_task(p.run())
        await asyncio.sleep(0.1)
        p.cancel()
        await asyncio.wait_for(task, timeout=1.0)
    # Cancel should emit a terminal with mission_status=cancelled
    assert any(s[0] == "terminal" and s[1]["mission_status"] == "cancelled" for s in sent)
```

**Step 2: Run to verify failure**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_mission_poller.py -v
```

Expected: ImportError (module doesn't exist).

**Step 3: Implementation — `mission_poller.py`**

```python
# supervisor/mission_poller.py
"""Streams ER mission progress back into a NON_BLOCKING tool call.

Spawned by the supervisor right after a successful dispatch_er_mission HTTP
call. Polls ER's /mission/{id} and tools_api's status_get_pose on a 1 Hz
tick, sending FunctionResponse parts under the original tool_call id:

  * 1 Hz SILENT pose ticks while the mission is active
  * WHEN_IDLE on Nav2 state transitions (e.g. pending->active, distance hits
    a "near goal" threshold, ER emits a notable event)
  * Terminal (will_continue=False) WHEN_IDLE with the full result on
    completed / failed / aborted / cancelled

Cancel paths:
  * cancel_er_mission tool fires -> session calls poller.cancel() which
    sends a terminal "cancelled" and exits
  * emergency_stop tool fires -> same as cancel (the supervisor will also
    issue cancel_er_mission separately if it wants to end the task)
  * session ends/reconnects -> the inner reconnect loop calls cancel(); a
    new session can re-spawn a poller from the active mission_id if
    self._tracker.active_id is still set.

Regression guards honored:
  * #11 — pose comes from status_get_pose (EKF-fused /odom), not raw encoders
  * #2  — runs on the main asyncio loop, NOT the mic's dedicated executor
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

from .config import SupervisorConfig


_TERMINAL = {"completed", "failed", "aborted", "cancelled"}


async def _fetch_status(cfg: SupervisorConfig, mission_id: str) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
        r = await c.get(f"{cfg.er_url}/mission/{mission_id}")
        if r.status_code >= 400:
            return {"status": "failed", "error": f"http {r.status_code}", "events": []}
        return r.json()


async def _fetch_pose(cfg: SupervisorConfig) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
        r = await c.post(f"{cfg.tools_api_url}/tool/status_get_pose", json={})
        if r.status_code >= 400:
            return None
        try:
            return r.json().get("result", {}) or None
        except ValueError:
            return None


@dataclass
class PollerCallbacks:
    """The session injects three coroutines so the poller doesn't need to
    know how to talk to the Gemini Live session directly. Each takes a
    JSON-serializable body dict; the session wraps it in a FunctionResponse
    with the appropriate scheduling and id.
    """
    send_silent: Callable[[dict], Awaitable[None]]
    send_when_idle: Callable[[dict], Awaitable[None]]
    send_terminal: Callable[[dict], Awaitable[None]]


class MissionPoller:
    def __init__(
        self,
        cfg: SupervisorConfig,
        *,
        fc_id: str,
        fc_name: str,
        mission_id: str,
        callbacks: PollerCallbacks,
        tick_s: float = 1.0,
    ) -> None:
        self._cfg = cfg
        self._fc_id = fc_id           # for logging only; session uses the id
        self._fc_name = fc_name
        self._mission_id = mission_id
        self._cb = callbacks
        self._tick_s = tick_s
        self._cancelled = asyncio.Event()
        self._last_status: Optional[str] = None

    @property
    def fc_id(self) -> str:
        return self._fc_id

    @property
    def mission_id(self) -> str:
        return self._mission_id

    def cancel(self) -> None:
        """Idempotent. Wakes the poller; it sends a terminal cancelled and exits."""
        self._cancelled.set()

    async def run(self) -> None:
        try:
            while not self._cancelled.is_set():
                status_doc = await _fetch_status(self._cfg, self._mission_id)
                pose = await _fetch_pose(self._cfg)
                status = status_doc.get("status", "unknown")

                # Transition event (any change including pending->active)
                if self._last_status is not None and status != self._last_status:
                    await self._cb.send_when_idle({
                        "event": "state_transition",
                        "from": self._last_status, "to": status,
                        "mission_id": self._mission_id,
                    })
                self._last_status = status

                # Terminal check
                if status in _TERMINAL:
                    await self._cb.send_terminal({
                        "mission_status": status,
                        "mission_id": self._mission_id,
                        "result": status_doc,
                    })
                    return

                # Active-state SILENT tick
                tick = {
                    "mission_status": status,
                    "mission_id": self._mission_id,
                    "distance_to_goal": status_doc.get("distance_to_goal"),
                }
                if pose:
                    tick["pose"] = pose
                await self._cb.send_silent(tick)

                # Sleep with cancellation responsiveness
                try:
                    await asyncio.wait_for(self._cancelled.wait(), timeout=self._tick_s)
                except asyncio.TimeoutError:
                    pass
            # Cancelled externally — emit terminal cancelled
            await self._cb.send_terminal({
                "mission_status": "cancelled",
                "mission_id": self._mission_id,
            })
        except Exception as e:
            # Never crash the supervisor over a poller failure. Best-effort
            # terminal so the model isn't left waiting forever.
            try:
                await self._cb.send_terminal({
                    "mission_status": "failed",
                    "mission_id": self._mission_id,
                    "error": f"{type(e).__name__}: {e}",
                })
            except Exception:
                pass
```

**Step 4: Implementation — `tool_handlers.exec_dispatch_er_mission` (no change needed!)**

It already returns immediately with `mission_id` from the ER `/mission` POST. The session will read that mission_id from the response dict and use it to spawn the poller. **No code change in `tool_handlers.py`** — the handler stays as-is. This is a deliberate simplification of the original task description: the poller lives in `session.py` next to the Live session it talks to, so we don't have to plumb the session reference through `tool_handlers`.

**Step 5: Implementation — `session.py` poller wiring**

Add a `_poller_tasks: dict[str, asyncio.Task]` field on `Supervisor`, scoped per-session. Wire dispatch + cancel + emergency_stop + session-end:

```python
# supervisor/session.py — add inside _run_one_session, after session_stop is created
            poller_tasks: dict[str, asyncio.Task] = {}

            def _make_callbacks(fc_id: str, fc_name: str):
                async def _send(body: dict, *, will_continue: bool, scheduling):
                    await session.send_tool_response(function_responses=[types.FunctionResponse(
                        id=fc_id, name=fc_name, response=body,
                        will_continue=will_continue, scheduling=scheduling,
                    )])
                from .mission_poller import PollerCallbacks
                return PollerCallbacks(
                    send_silent=lambda b: _send(b, will_continue=True,
                                                scheduling=types.FunctionResponseScheduling.SILENT),
                    send_when_idle=lambda b: _send(b, will_continue=True,
                                                  scheduling=types.FunctionResponseScheduling.WHEN_IDLE),
                    send_terminal=lambda b: _send(b, will_continue=False,
                                                  scheduling=types.FunctionResponseScheduling.WHEN_IDLE),
                )

            async def _spawn_poller(fc_id: str, fc_name: str, mission_id: str) -> None:
                from .mission_poller import MissionPoller
                cb = _make_callbacks(fc_id, fc_name)
                p = MissionPoller(self._cfg, fc_id=fc_id, fc_name=fc_name,
                                  mission_id=mission_id, callbacks=cb)
                poller_tasks[fc_id] = asyncio.create_task(p.run())
```

Replace the dispatch_er_mission branch in `_dispatch_tool_calls` to spawn the poller:

```python
            elif name == "dispatch_er_mission":
                resp = await th.exec_dispatch_er_mission(
                    self._cfg, self._tracker, **args,
                )
                # Send the immediate SILENT placeholder
                await session.send_tool_response(function_responses=[types.FunctionResponse(
                    id=fc.id, name=fc.name, response=resp,
                    will_continue=True,
                    scheduling=types.FunctionResponseScheduling.SILENT,
                )])
                # If dispatch succeeded, spawn the poller under the same id.
                # If it failed (resp has 'error'), send terminal will_continue=False
                # so the model isn't left waiting forever.
                if "error" in resp:
                    await session.send_tool_response(function_responses=[types.FunctionResponse(
                        id=fc.id, name=fc.name, response=resp,
                        will_continue=False,
                        scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
                    )])
                else:
                    mid = resp.get("mission_id")
                    if mid:
                        await _spawn_poller(fc.id, fc.name, mid)
```

Wire poller cancellation on `cancel_er_mission` and `emergency_stop`:

```python
            elif name == "cancel_er_mission":
                resp = await th.exec_cancel_er_mission(self._cfg, self._tracker, **args)
                # Cancel any in-flight poller for the cancelled mission. We don't
                # know which poller-fc-id corresponds, so we cancel ALL active
                # pollers — there's only one mission at a time anyway.
                for t in list(poller_tasks.values()):
                    if not t.done():
                        t.cancel()
                await self._respond(session, fc, resp)
            elif name == "emergency_stop":
                resp = await th.exec_emergency_stop(self._cfg)
                for t in list(poller_tasks.values()):
                    if not t.done():
                        t.cancel()
                await self._respond(session, fc, resp)
```

Cancel pollers on session teardown — extend the `finally` block of `_run_one_session`:

```python
            finally:
                for t in (mic_task, resp_task, watch_task, idle_task):
                    if not t.done(): t.cancel()
                # Also kill any in-flight pollers — they're per-session
                for t in poller_tasks.values():
                    if not t.done(): t.cancel()
                # ... existing await-with-CancelledError-swallowing logic stays ...
```

**Step 6: Run tests**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_mission_poller.py -v
GOOGLE_API_KEY=test pytest tests/supervisor/ -v
```

Expected: all green.

**Step 7: Sync, restart, and live-fire**

```bash
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo systemctl restart ugv-supervisor.service'
# Wake up the supervisor and say "Drive forward 50 cm." While it's moving,
# DO NOT speak. Watch journalctl for "[supervisor] tool_call dispatch_er_mission",
# then a stream of FunctionResponse parts. Mid-mission ask "where are we?" —
# the model should answer with current pose without calling get_robot_state
# (because the SILENT ticks already gave it the data).
```

**Step 8: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/mission_poller.py ugv_tools_api/supervisor/session.py tests/supervisor/test_mission_poller.py
git commit -m "feat(supervisor): stream mission progress as multi-part FunctionResponse"
```

---

### Task 4: Continuous 1 FPS camera push (watch mode)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/watch_stream.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/tool_declarations.py` (add `SET_WATCH_MODE`)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/tool_handlers.py` (add `exec_set_watch_mode` stub that toggles a flag the session reads)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py` (own the WatchStream; auto-on during dispatch, auto-off on terminal-no-override)
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_watch_stream.py`

**Why:** Currently the model "sees" only when it explicitly calls `get_camera_view`, which it does lazily because most operator questions can be answered from text context. Watch mode flips the frame: vision is ambient, so the model decides whether to *act* on what it sees, not whether to look. Token budget is the cost — Task 6 will track and rotate sessions before the window fills. Default is OFF; auto-on when a mission dispatches; auto-off when the mission terminates AND the operator hasn't overridden manually.

**Regression guards touched:**
- #3 (FunctionResponseBlob can't carry bytes): N/A here — `send_realtime_input(video=…)` is the bytes-capable sidechannel, used identically to `_vision_tool`'s push path

**Locked decisions:**
- Cadence: exactly 1 FPS (Google docs cap is `≤ 1 FPS`)
- Source: only pantilt to start. OAK-D enabled in a follow-up if needed.
- Toggle policy: tool `set_watch_mode(on, source)`; auto-on with `source='pantilt'` when `dispatch_er_mission` succeeds; auto-off when ALL pollers terminate AND no manual override is in effect

**Step 1: Write the failing test**

```python
# tests/supervisor/test_watch_stream.py
import asyncio
import pytest
from unittest.mock import AsyncMock

from ugv_tools_api.supervisor.watch_stream import WatchStream


class _FakeCam:
    def __init__(self, jpeg=b"\xff\xd8\xff\xe0fake"):
        self.jpeg = jpeg
    def get_camera_jpeg(self): return self.jpeg
    def get_costmap_png(self): return None


class _FakeSession:
    def __init__(self):
        self.video_calls = []
    async def send_realtime_input(self, *, video=None, audio=None):
        if video is not None:
            self.video_calls.append((video.mime_type, len(video.data)))


@pytest.mark.asyncio
async def test_pushes_at_1hz_when_on():
    sess = _FakeSession()
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0)  # 20 FPS for fast test
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run(sess))
    await asyncio.sleep(0.25)  # ~5 frames at 20 FPS
    w.stop()
    await task
    assert 3 <= len(sess.video_calls) <= 8


@pytest.mark.asyncio
async def test_no_push_when_off():
    sess = _FakeSession()
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0)
    w.set(on=False, source="pantilt")
    task = asyncio.create_task(w.run(sess))
    await asyncio.sleep(0.25)
    w.stop()
    await task
    assert sess.video_calls == []


@pytest.mark.asyncio
async def test_toggle_mid_run():
    sess = _FakeSession()
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0)
    task = asyncio.create_task(w.run(sess))
    await asyncio.sleep(0.1)
    assert sess.video_calls == []
    w.set(on=True, source="pantilt")
    await asyncio.sleep(0.25)
    pre_off = len(sess.video_calls)
    w.set(on=False, source="pantilt")
    await asyncio.sleep(0.2)
    w.stop()
    await task
    assert len(sess.video_calls) == pre_off  # no new pushes after off


@pytest.mark.asyncio
async def test_skips_when_no_jpeg_available():
    sess = _FakeSession()
    cam = _FakeCam(jpeg=None)
    w = WatchStream(cam, fps=20.0)
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run(sess))
    await asyncio.sleep(0.2)
    w.stop()
    await task
    assert sess.video_calls == []
```

**Step 2: Run to verify failure**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_watch_stream.py -v
```

Expected: ImportError.

**Step 3: Implementation — `watch_stream.py`**

```python
# supervisor/watch_stream.py
"""1 FPS ambient JPEG push into the Live session.

The supervisor owns one WatchStream per Gemini Live session. When `on=True`,
the loop pulls the latest JPEG from RosCamera once per FPS-window and pushes
it via session.send_realtime_input(video=Blob(...)). When `on=False`, the
loop sleeps. Mid-run toggling is supported (see set()).

Token-cost note: 1 FPS at ~30-80 KB/frame is the documented Google ceiling.
Task 6's TokenBudget tracks bytes shipped so we rotate the session before
context fills. Do NOT raise the FPS — Google rejects > 1 FPS.

Regression guard #3 is N/A here: send_realtime_input(video=...) takes a
Blob with raw bytes. That's the supported sidechannel.
"""
from __future__ import annotations
import asyncio
from typing import Optional, Protocol


class _CameraLike(Protocol):
    def get_camera_jpeg(self) -> Optional[bytes]: ...


class WatchStream:
    def __init__(self, camera: _CameraLike, fps: float = 1.0) -> None:
        self._cam = camera
        self._period = 1.0 / fps
        self._on = False
        self._source = "pantilt"
        self._stop = asyncio.Event()
        self._bytes_sent = 0
        self._frames_sent = 0

    @property
    def is_on(self) -> bool:
        return self._on

    @property
    def stats(self) -> dict:
        return {"on": self._on, "source": self._source,
                "bytes_sent": self._bytes_sent, "frames_sent": self._frames_sent}

    def set(self, *, on: bool, source: str = "pantilt") -> None:
        self._on = bool(on)
        self._source = source

    def stop(self) -> None:
        self._stop.set()

    async def run(self, session) -> None:
        from google.genai import types
        while not self._stop.is_set():
            t0 = asyncio.get_event_loop().time()
            if self._on:
                jpeg = self._cam.get_camera_jpeg() if self._source == "pantilt" else None
                if jpeg:
                    try:
                        await session.send_realtime_input(
                            video=types.Blob(data=jpeg, mime_type="image/jpeg"),
                        )
                        self._bytes_sent += len(jpeg)
                        self._frames_sent += 1
                    except Exception as e:
                        # Swallow & continue; never let a transient send error
                        # kill the watch loop. session.py will log via
                        # _patch_ws_keepalive sleep diagnostics.
                        print(f"[watch] send error swallowed: {type(e).__name__}: {e}")
            # Sleep for the rest of the period or until stop fires
            elapsed = asyncio.get_event_loop().time() - t0
            sleep = max(0.0, self._period - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep or 0.001)
                break
            except asyncio.TimeoutError:
                pass
```

**Step 4: Implementation — `tool_declarations.SET_WATCH_MODE`**

```python
# supervisor/tool_declarations.py — add at top of declarations
SET_WATCH_MODE = types.FunctionDeclaration(
    name="set_watch_mode",
    description=(
        "Turn ambient camera push on or off. When on, the pantilt camera "
        "sends a frame every second so you can see what the robot sees "
        "without explicitly calling get_camera_view. Default is off; "
        "automatically turns on during ER missions and off when they end. "
        "Operator can override either way."
    ),
    parameters=_obj(
        {
            "on": _bool("Enable (true) or disable (false) ambient camera push."),
            "source": _enum(
                "Which camera to stream from.",
                ["pantilt"],  # OAK-D not yet wired; future
                default="pantilt",
            ),
        },
        required=["on"],
    ),
)


# Append to ALL_TOOLS:
ALL_TOOLS = (
    GET_ROBOT_STATE, GET_CAMERA_VIEW, GET_COSTMAP_VIEW,
    DISPATCH_ER_MISSION, CANCEL_ER_MISSION, GET_ER_MISSION_STATUS,
    EMERGENCY_STOP,
    LIGHTS_ON, LIGHTS_OFF, GIMBAL_LOOK_AT,
    SET_WATCH_MODE,
)
```

Update `test_tool_declarations.test_all_tools_present` to include `set_watch_mode` in the expected set.

**Step 5: Implementation — `tool_handlers.exec_set_watch_mode`**

The handler does NOT itself touch the WatchStream (which is owned by the live session); it returns a structured request that `session.py` picks up:

```python
# supervisor/tool_handlers.py — add at bottom
async def exec_set_watch_mode(*, on: bool, source: str = "pantilt") -> dict:
    """Pure echo of the requested state. session.py reads this to flip
    the WatchStream. Kept as a separate handler for symmetry; could be
    inlined later if it stays this trivial."""
    return {"watch_on": bool(on), "source": source}
```

**Step 6: Implementation — `session.py` lifecycle wiring**

```python
# supervisor/session.py — add inside _run_one_session
            from .watch_stream import WatchStream
            watch = WatchStream(self._cam, fps=1.0)
            watch_task = asyncio.create_task(watch.run(session))
            # Operator manual override; reset on session start
            operator_override = None  # None = auto, True = forced-on, False = forced-off
```

Hook in `_dispatch_tool_calls`:

```python
            elif name == "set_watch_mode":
                resp = await th.exec_set_watch_mode(**args)
                operator_override = bool(resp["watch_on"])  # noqa: closure
                watch.set(on=operator_override, source=resp["source"])
                await self._respond(session, fc, resp)
```

Auto-on on dispatch, auto-off on terminal — extend the dispatch_er_mission branch from Task 3:

```python
            elif name == "dispatch_er_mission":
                resp = await th.exec_dispatch_er_mission(self._cfg, self._tracker, **args)
                # Auto-on watch mode unless operator has explicitly turned it off
                if operator_override is None:
                    watch.set(on=True, source="pantilt")
                # ... rest of existing dispatch path stays ...
```

In `_make_callbacks`, when send_terminal fires, auto-off (only if no operator override):

```python
            def _make_callbacks(fc_id: str, fc_name: str):
                async def _send(body, *, will_continue, scheduling):
                    await session.send_tool_response(function_responses=[types.FunctionResponse(
                        id=fc_id, name=fc_name, response=body,
                        will_continue=will_continue, scheduling=scheduling,
                    )])
                async def _terminal(body):
                    await _send(body, will_continue=False,
                                scheduling=types.FunctionResponseScheduling.WHEN_IDLE)
                    # Auto-off watch on terminal mission, unless operator overrode
                    if operator_override is None:
                        watch.set(on=False, source="pantilt")
                from .mission_poller import PollerCallbacks
                return PollerCallbacks(
                    send_silent=lambda b: _send(b, will_continue=True,
                                                scheduling=types.FunctionResponseScheduling.SILENT),
                    send_when_idle=lambda b: _send(b, will_continue=True,
                                                  scheduling=types.FunctionResponseScheduling.WHEN_IDLE),
                    send_terminal=_terminal,
                )
```

Stop the watch task in the finally block:

```python
            finally:
                watch.stop()
                # ... existing cancellations stay ...
```

**Step 7: Run tests + live-fire**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_watch_stream.py tests/supervisor/test_tool_declarations.py -v
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo systemctl restart ugv-supervisor.service'
# Wake up the supervisor, say "Drive forward 50 cm." Watch journalctl for
# WatchStream-frames-shipped logs. Mid-mission ask "what do you see right
# now?" — model should answer from the most recent watch frame WITHOUT
# calling get_camera_view.
```

**Step 8: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/watch_stream.py ugv_tools_api/supervisor/tool_declarations.py \
        ugv_tools_api/supervisor/tool_handlers.py ugv_tools_api/supervisor/session.py \
        tests/supervisor/test_watch_stream.py tests/supervisor/test_tool_declarations.py
git commit -m "feat(supervisor): watch mode — auto 1 FPS camera push during missions"
```

---

### Task 5: WebRTC AEC3 integration — replace half-duplex gate (HIGHEST RISK)

> **⚠️ HIGHEST-RISK TASK IN THIS PLAN.** This touches the live audio pipeline that all operator interaction depends on. Land Tasks 0-4 first and verify they're stable for at least one full bench session before starting this task. Keep the half-duplex fallback live throughout. **Do not merge this task without an operator-led validation pass.**

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/Dockerfile` (or whatever container build mechanism is in use — verify path; if pip-only, modify `requirements.txt`)
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/aec.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/audio_io.py` (SpeakerStream reference ring; SUPERVISOR_AEC_DELAY_MS knob)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py` (`pump_mic` integration)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py` (knobs: `aec_mode`, `aec_delay_ms`)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/supervisor.env` (`SUPERVISOR_AEC_MODE`, `SUPERVISOR_AEC_DELAY_MS`)
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_aec.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_audio_io.py`

**Why:** The current half-duplex `silent_chunk` gate in `pump_mic` (the line `if self._spk.is_playing(): outgoing = silent_chunk`) keeps Gemini from hearing its own voice but kills barge-in entirely — the operator can't interrupt the model mid-sentence because their voice is replaced by silence whenever the speaker is talking. WebRTC AEC3 (Chrome's production AEC, used by Pipecat and PA's `module-echo-cancel`) lets us send the *real* mic audio after subtracting the speaker reference, so barge-in works. The reference signal is free — we already have the bytes we wrote to `aplay`. We resample them 24 kHz → 16 kHz (mic rate), feed them as the far-end signal to AEC3, and feed the mic chunk as the near-end. AEC3 self-calibrates within 1-2 seconds.

**Regression guards touched:**
- #2: AEC `process()` runs inside `pump_mic` which is on the dedicated mic executor pool. `process()` is fast (~100 µs per 10 ms frame) and pure-Python on small numpy arrays — safe.
- #7: Half-duplex stays as the fallback path, gated on `SUPERVISOR_AEC_MODE`.
- #8/#9: Mic 16 kHz, speaker 24 kHz — these are the inputs the AEC sees; the resample is in-process.
- #10: SpeakerStream.write keeps blocking on ALSA backpressure; we tap the bytes BEFORE write so the reference ring isn't gated by the ALSA writer's run_in_executor dispatch.

**Rollback plan (drilled in advance, kept executable):**
1. Set `SUPERVISOR_AEC_MODE=halfduplex` in `supervisor.env` on Jetson; `sudo systemctl restart ugv-supervisor.service`. Old behavior restored in <10 s.
2. If `webrtc-audio-processing` itself is broken (init throws, segfault, etc.), the `Aec3Wrapper` constructor catches it and `pump_mic` defaults to half-duplex without crashing the supervisor. (Self-healing fallback.)
3. If AEC3 cannot install on aarch64 (wheel missing AND build-from-source fails), implement Path A.alt with `speexdsp-python` instead — same wrapper signature; smaller dB suppression but works on the same platforms.

**Step 1: Container library install**

First, check inside the container which install path works:

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "
  pip3 install --dry-run webrtc-audio-processing 2>&1 | head -10
"'
```

If that succeeds, add it to the container's `requirements.txt` (or whatever the supervisor's pinned dep file is — verify path; possibly `docs/ugv-beast/setup/ugv_tools_api/pyproject.toml` `[project.optional-dependencies] dev` or a separate runtime-deps file). Add `webrtc-audio-processing>=0.1.3` and `scipy>=1.10` (for `scipy.signal.resample_poly`).

If pip wheel is unavailable on aarch64:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "
  apt-get update && apt-get install -y libwebrtc-audio-processing-dev cython3 \
  && pip3 install webrtc-audio-processing
"'
```

If neither works, fall back to `pip3 install speexdsp-python` and update `aec.py` to use `speexdsp` (smaller dB suppression but the wrapper API is the same).

Document the chosen path in a one-line comment at the top of `aec.py`.

**Step 2: Write the failing test**

```python
# tests/supervisor/test_aec.py
"""AEC3 wrapper: synthesized echo cancellation test.

Generates a 16 kHz sine "speaker" signal, mixes a delayed copy into the
mic capture, runs AEC3, and verifies that the residual echo energy is at
least 12 dB below the original. WebRTC AEC3 typically delivers > 25 dB
in real-world conditions; 12 dB is a safe threshold that catches "broken"
without false-failing on a synthetic test.
"""
import numpy as np
import pytest

from ugv_tools_api.supervisor.aec import Aec3Wrapper


def _sine(freq_hz: float, dur_s: float, sr: int = 16000) -> np.ndarray:
    t = np.arange(int(dur_s * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * freq_hz * t) * 32767).astype(np.int16)


def test_residual_at_least_12db_below_input():
    # 1.0 s of "speaker" signal
    speaker = _sine(440.0, 1.0)
    # Mic = speaker delayed 50 ms (the "echo path")
    delay_samples = int(0.05 * 16000)
    mic = np.concatenate([np.zeros(delay_samples, dtype=np.int16), speaker[:-delay_samples]])

    aec = Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)
    out = []
    frame = int(0.01 * 16000)  # 10 ms at 16 kHz = 160 samples
    for i in range(0, len(mic) - frame, frame):
        ref_chunk = speaker[i:i+frame].tobytes()
        mic_chunk = mic[i:i+frame].tobytes()
        out.append(aec.process(mic_chunk, ref_chunk))
    out_arr = np.frombuffer(b"".join(out), dtype=np.int16).astype(np.float32)
    mic_arr = mic.astype(np.float32)[:len(out_arr)]
    # Use windowed-RMS after the first 200 ms so AEC has time to converge
    skip = int(0.2 * 16000)
    in_rms = np.sqrt(np.mean(mic_arr[skip:]**2))
    out_rms = np.sqrt(np.mean(out_arr[skip:]**2))
    db = 20.0 * np.log10(max(out_rms, 1e-6) / max(in_rms, 1e-6))
    assert db <= -12.0, f"residual only {db:.1f} dB below input (need <= -12 dB)"


def test_passes_through_when_no_reference():
    # If reference is silence, AEC should leave the mic chunk roughly intact
    aec = Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)
    speech = (np.random.randn(160) * 5000).astype(np.int16).tobytes()
    silence = b"\x00" * 320  # 160 samples of silence, S16
    out = aec.process(speech, silence)
    arr = np.frombuffer(out, dtype=np.int16)
    # AEC may apply some gain control but shouldn't zero out the signal
    assert np.std(arr) > 100


def test_fallback_on_init_failure(monkeypatch):
    """If the AEC library fails to load, Aec3Wrapper.process should raise
    a single named error type so session.py can catch it once and fall back."""
    monkeypatch.setattr("ugv_tools_api.supervisor.aec._make_aec",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("fake")))
    with pytest.raises(RuntimeError):
        Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)


# tests/supervisor/test_audio_io.py — APPEND
import time

def test_speaker_reference_ring_returns_aligned_chunk():
    from ugv_tools_api.supervisor.audio_io import SpeakerReferenceRing
    ring = SpeakerReferenceRing(sample_rate_hz=24000, capacity_seconds=2.0)
    pcm = b"\x01\x00" * 2400  # 100 ms at 24 kHz
    ring.write(pcm, ts=0.0)
    chunk = ring.read_aligned(now=0.05, size_bytes=480, delay_ms=50)  # 10 ms @ 24kHz
    assert len(chunk) == 480

def test_speaker_reference_ring_returns_zeros_when_empty():
    from ugv_tools_api.supervisor.audio_io import SpeakerReferenceRing
    ring = SpeakerReferenceRing(sample_rate_hz=24000, capacity_seconds=2.0)
    chunk = ring.read_aligned(now=1.0, size_bytes=480, delay_ms=50)
    assert chunk == b"\x00" * 480
```

**Step 3: Run to verify failure**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_aec.py tests/supervisor/test_audio_io.py -v
```

Expected: ImportError on `Aec3Wrapper` and `SpeakerReferenceRing`.

**Step 4: Implementation — `aec.py`**

```python
# supervisor/aec.py
"""WebRTC AEC3 wrapper for the supervisor's mic path.

Choice of backend resolved at module import time:
  Path A (preferred): webrtc-audio-processing (Chrome's AEC3)
  Path A.alt: speexdsp echo canceller (fallback if AEC3 unavailable)

Both expose process(mic_pcm, ref_pcm) -> echo_cancelled_pcm. The wrapper
hides the binding so session.py doesn't need to care which one we got.

Usage:
    aec = Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)
    out_pcm = aec.process(mic_pcm_chunk, ref_pcm_chunk)

Frame size MUST match what the AEC backend expects. WebRTC AEC3 = 10 ms
frames; speexdsp = configurable. We standardize on 10 ms for simplicity.
"""
from __future__ import annotations
import numpy as np
from typing import Protocol


class _AECBackend(Protocol):
    def process(self, mic: bytes, ref: bytes) -> bytes: ...


def _make_aec(sample_rate_hz: int, frame_ms: int) -> _AECBackend:
    """Try webrtc-audio-processing first; fall back to speexdsp."""
    try:
        # Path A
        from webrtc_audio_processing import AudioProcessingModule
        ap = AudioProcessingModule(aec_type=2, enable_ns=False, agc_type=0)
        ap.set_stream_format(sample_rate_hz, 1)
        ap.set_reverse_stream_format(sample_rate_hz, 1)

        class _WebRtcAec:
            def process(self, mic: bytes, ref: bytes) -> bytes:
                ap.process_reverse_stream(ref)
                return ap.process_stream(mic)
        return _WebRtcAec()
    except Exception as e:
        print(f"[aec] webrtc-audio-processing unavailable ({type(e).__name__}: {e}); trying speexdsp")

    # Path A.alt
    from speexdsp import EchoCanceller
    frame_size = int(sample_rate_hz * frame_ms / 1000)
    filter_length = int(sample_rate_hz * 0.2)  # 200 ms tail
    ec = EchoCanceller.create(frame_size, filter_length, sample_rate_hz)

    class _SpeexAec:
        def process(self, mic: bytes, ref: bytes) -> bytes:
            return ec.process(mic, ref)
    return _SpeexAec()


class Aec3Wrapper:
    def __init__(self, sample_rate_hz: int = 16000, frame_ms: int = 10) -> None:
        self._sr = sample_rate_hz
        self._frame_ms = frame_ms
        self._backend = _make_aec(sample_rate_hz=sample_rate_hz, frame_ms=frame_ms)

    def process(self, mic_pcm: bytes, ref_pcm: bytes) -> bytes:
        """Echo-cancel one frame. mic and ref are S16_LE mono at sample_rate_hz.
        Both must be exactly frame_ms long. Returns the cancelled mic frame."""
        return self._backend.process(mic_pcm, ref_pcm)
```

**Step 5: Implementation — SpeakerReferenceRing in `audio_io.py`**

Append to `audio_io.py`:

```python
# supervisor/audio_io.py — APPEND
import time
import threading
import numpy as np
from scipy.signal import resample_poly


class SpeakerReferenceRing:
    """Last-N-seconds ring buffer of speaker output PCM bytes, plus a
    sample-rate resampler so AEC3 (16 kHz) can read aligned chunks from
    a 24 kHz speaker stream.

    Producers call write(pcm, ts) every time SpeakerStream.write() runs.
    Consumers (the AEC step in pump_mic) call read_aligned(now, size, delay_ms)
    to fetch a chunk that lines up with the mic capture timestamp.

    'now' is a wall-clock float (time.monotonic()) — the consumer's "now",
    NOT the producer's "ts".
    """
    def __init__(self, sample_rate_hz: int, capacity_seconds: float) -> None:
        self._sr = sample_rate_hz
        self._capacity_samples = int(sample_rate_hz * capacity_seconds)
        self._buf = np.zeros(self._capacity_samples, dtype=np.int16)
        self._write_pos = 0  # next write index
        self._wrote_total = 0  # total samples written, monotonic
        self._first_write_ts: float | None = None
        self._lock = threading.Lock()

    def write(self, pcm: bytes, ts: float | None = None) -> None:
        """Append S16_LE mono PCM to the ring."""
        if ts is None:
            ts = time.monotonic()
        arr = np.frombuffer(pcm, dtype=np.int16)
        with self._lock:
            if self._first_write_ts is None:
                self._first_write_ts = ts
            n = len(arr)
            end = self._write_pos + n
            if end <= self._capacity_samples:
                self._buf[self._write_pos:end] = arr
            else:
                first = self._capacity_samples - self._write_pos
                self._buf[self._write_pos:] = arr[:first]
                self._buf[:n - first] = arr[first:]
            self._write_pos = end % self._capacity_samples
            self._wrote_total += n

    def read_aligned(self, now: float, size_bytes: int, delay_ms: float) -> bytes:
        """Return size_bytes of S16_LE mono PCM corresponding to what the
        speaker emitted (now - delay_ms) seconds ago. Zero-fills if the
        requested window predates the first write."""
        size_samples = size_bytes // 2
        with self._lock:
            if self._first_write_ts is None:
                return b"\x00" * size_bytes
            # Sample index of (now - delay_ms) in the speaker timeline
            target_ts = now - (delay_ms / 1000.0)
            elapsed = target_ts - self._first_write_ts
            target_sample = int(elapsed * self._sr)
            if target_sample < 0 or target_sample >= self._wrote_total:
                return b"\x00" * size_bytes
            # Translate to ring index
            ring_idx = target_sample % self._capacity_samples
            end = ring_idx + size_samples
            if end <= self._capacity_samples:
                out = self._buf[ring_idx:end]
            else:
                first = self._capacity_samples - ring_idx
                out = np.concatenate([self._buf[ring_idx:], self._buf[:size_samples - first]])
            return out.tobytes()


def resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Resample S16_LE mono 24 kHz to 16 kHz using polyphase resampling.
    Used to align speaker reference (24 kHz) with mic capture (16 kHz)."""
    if not pcm_24k:
        return b""
    arr = np.frombuffer(pcm_24k, dtype=np.int16).astype(np.float32)
    out = resample_poly(arr, up=2, down=3)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()
```

Wire `SpeakerStream.write()` to also write to the ring:

```python
# supervisor/audio_io.py — modify SpeakerStream
class SpeakerStream:
    def __init__(self, cfg: SupervisorConfig) -> None:
        # ... existing fields ...
        self.reference_ring = SpeakerReferenceRing(
            sample_rate_hz=cfg.output_sample_rate, capacity_seconds=2.0,
        )

    def write(self, pcm: bytes) -> None:
        # ... existing aplay-pipe write ...
        # Tap the bytes for AEC reference; write timestamp = wall-clock now
        self.reference_ring.write(pcm)
```

**Step 6: Implementation — `config.py` knobs**

```python
# supervisor/config.py — extend SupervisorConfig
@dataclass(frozen=True)
class SupervisorConfig:
    # ... existing fields ...
    aec_mode: str = "aec3"            # "aec3" or "halfduplex"
    aec_delay_ms: float = 50.0        # Speaker→mic acoustic + DAC + buffer latency

# In load():
    return SupervisorConfig(
        # ... existing args ...
        aec_mode=os.environ.get("SUPERVISOR_AEC_MODE", "aec3"),
        aec_delay_ms=float(os.environ.get("SUPERVISOR_AEC_DELAY_MS", "50")),
    )
```

`deploy/supervisor.env`:
```
SUPERVISOR_AEC_MODE=aec3
SUPERVISOR_AEC_DELAY_MS=50
# Rollback: set SUPERVISOR_AEC_MODE=halfduplex if AEC3 misbehaves
```

**Step 7: Implementation — `pump_mic` integration**

```python
# supervisor/session.py — modify pump_mic preamble
            # Initialize AEC if enabled. Failure -> log once, fall back to halfduplex.
            from .aec import Aec3Wrapper
            from .audio_io import resample_24k_to_16k
            aec: Optional[Aec3Wrapper] = None
            if self._cfg.aec_mode == "aec3":
                try:
                    aec = Aec3Wrapper(sample_rate_hz=self._cfg.input_sample_rate, frame_ms=10)
                    print("[supervisor] AEC3 enabled")
                except Exception as e:
                    print(f"[supervisor] AEC3 init failed ({type(e).__name__}: {e}); "
                          f"falling back to halfduplex for this session")
                    aec = None
```

Replace the `if self._spk is not None and self._spk.is_playing(): outgoing = silent_chunk` block:

```python
                        if aec is not None:
                            # AEC3 path: feed mic + delay-aligned 16 kHz speaker reference
                            now = loop.time()
                            ref_24k = self._spk.reference_ring.read_aligned(
                                now=now, size_bytes=int(chunk * 24000 / 16000),
                                delay_ms=self._cfg.aec_delay_ms,
                            ) if self._spk is not None else b"\x00" * int(chunk * 24000 / 16000)
                            ref_16k = resample_24k_to_16k(ref_24k)
                            # Belt-and-suspenders: ensure exact length match
                            if len(ref_16k) != chunk:
                                # Pad/truncate to chunk
                                if len(ref_16k) < chunk:
                                    ref_16k = ref_16k + b"\x00" * (chunk - len(ref_16k))
                                else:
                                    ref_16k = ref_16k[:chunk]
                            try:
                                outgoing = aec.process(data, ref_16k)
                            except Exception as e:
                                print(f"[supervisor] AEC process failed ({type(e).__name__}: {e}); "
                                      f"falling back to halfduplex for remainder of this session")
                                aec = None
                                outgoing = silent_chunk if (self._spk and self._spk.is_playing()) else data
                        else:
                            # Half-duplex fallback (regression guard #7)
                            outgoing = silent_chunk if (self._spk and self._spk.is_playing()) else data
```

**Step 8: Run tests + bench**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_aec.py tests/supervisor/test_audio_io.py -v
./scripts/sync-ugv-tools.sh
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo systemctl restart ugv-supervisor.service'
# Wake supervisor. While the model is speaking a long sentence, INTERRUPT
# IT mid-word with operator speech. Verify:
# - The model stops speaking promptly (barge-in works again)
# - Operator speech is correctly transcribed (not silenced)
# - Journalctl shows "[supervisor] AEC3 enabled" not "falling back"
```

**Step 9: Operator-led validation**

Run the spike's 9-prompt regression suite (Task 11 of the original plan) once more, plus three new prompts:
1. "(while model is speaking) Stop, listen — what did I just say about the kitchen?"
2. "(turn JBL volume to 80%) Is the lighting good in here?"
3. "(after a long mission) Tell me everything you saw during the drive."

Acceptance: barge-in works on prompt 1. Prompt 2 doesn't false-trigger off speaker bleed. Prompt 3 shows the model used watch frames rather than asking for new ones.

**Step 10: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/aec.py ugv_tools_api/supervisor/audio_io.py \
        ugv_tools_api/supervisor/config.py ugv_tools_api/supervisor/session.py \
        deploy/supervisor.env tests/supervisor/test_aec.py tests/supervisor/test_audio_io.py \
        # plus pyproject.toml or requirements update from Step 1
git commit -m "feat(supervisor): WebRTC AEC3 mic path; preserves halfduplex fallback"
```

---

### Task 6: Token-budget session rotation (proactive)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/budget.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py` (rotation hook)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py` (`budget_*` knobs)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/supervisor.env`
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_budget.py`

**Why:** Once watch mode is shipping ~80 KB per second of JPEGs into the session and audio is constant, the context window fills faster than the SlidingWindow eviction can keep up. Reactive GoAway rotation is too late — the operator hears Gemini choke. Proactive rotation: estimate token spend from the streams we control, and force a clean session re-open with a 1-paragraph mission summary as the system prompt seed at 80% of budget. The numbers come from the Task 0 spike (or Task 8 bench) — until then, use the conservative values below and refine later.

**Locked initial estimates** (refine with bench data in Task 8):
- Audio: 1 s ≈ 25 input tokens + 25 output tokens
- JPEG @ ~30-80 KB: ≈ 250 tokens/frame at 1 FPS
- Default budget: 80% of 32k tokens (assumed context window for native-audio variant; verify in Task 0)

**Step 1: Write the failing test**

```python
# tests/supervisor/test_budget.py
from ugv_tools_api.supervisor.budget import TokenBudget


def test_no_rotation_below_threshold():
    b = TokenBudget(window_tokens=10000, threshold=0.8,
                    audio_tokens_per_s=25.0, jpeg_tokens_per_frame=250.0)
    b.audio_seconds(60)        # 1500 tokens
    b.jpeg_frames(10)          # 2500 tokens
    assert not b.should_rotate
    assert b.usage_pct < 0.8


def test_rotation_at_threshold():
    b = TokenBudget(window_tokens=10000, threshold=0.8,
                    audio_tokens_per_s=25.0, jpeg_tokens_per_frame=250.0)
    b.audio_seconds(200)       # 5000 tokens
    b.jpeg_frames(15)          # 3750 tokens
    # 8750 / 10000 = 87.5% > 80%
    assert b.should_rotate


def test_reset_after_rotation():
    b = TokenBudget(window_tokens=10000, threshold=0.8)
    b.audio_seconds(200); b.jpeg_frames(15)
    assert b.should_rotate
    b.reset()
    assert not b.should_rotate
    assert b.usage_pct == 0.0
```

**Step 2: Run to verify failure**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_budget.py -v
```

Expected: ImportError.

**Step 3: Implementation — `budget.py`**

```python
# supervisor/budget.py
"""TokenBudget — approximate context-window tracker for proactive rotation.

We can't ask the API how many tokens are in the session at any moment, but
we control the things going IN: audio seconds (both directions, since the
model both hears and speaks) and ambient JPEG frames. Using documented
per-token rates, we estimate usage and rotate at threshold% of window.

This is intentionally conservative — over-rotating is fine (operator hears
a brief audio gap), under-rotating risks a hard GoAway mid-sentence.
"""
from __future__ import annotations


class TokenBudget:
    def __init__(
        self,
        *,
        window_tokens: int = 32000,
        threshold: float = 0.8,
        audio_tokens_per_s: float = 25.0,    # input + output combined
        jpeg_tokens_per_frame: float = 250.0,
    ) -> None:
        self._window = float(window_tokens)
        self._threshold = float(threshold)
        self._a_tps = audio_tokens_per_s
        self._j_tpf = jpeg_tokens_per_frame
        self._used = 0.0

    @property
    def used_tokens(self) -> float:
        return self._used

    @property
    def usage_pct(self) -> float:
        return self._used / self._window if self._window > 0 else 0.0

    @property
    def should_rotate(self) -> bool:
        return self.usage_pct >= self._threshold

    def audio_seconds(self, s: float) -> None:
        self._used += s * self._a_tps

    def jpeg_frames(self, n: int) -> None:
        self._used += n * self._j_tpf

    def reset(self) -> None:
        self._used = 0.0
```

**Step 4: Implementation — `session.py` rotation hook**

Inside `_run_one_session`, instantiate a `TokenBudget` and feed it from the mic/speaker/watch loops. Add a watchdog task that triggers session close when threshold hit:

```python
            from .budget import TokenBudget
            budget = TokenBudget(
                window_tokens=self._cfg.budget_window_tokens,
                threshold=self._cfg.budget_threshold,
            )
            # Feed budget from existing tasks: in pump_mic, after each frame
            # send: budget.audio_seconds(self._cfg.chunk_ms / 1000)
            # In pump_responses, after each audio response: same
            # In watch.run wrapper: budget.jpeg_frames(1) per send

            async def watch_budget():
                while not session_stop.is_set():
                    await asyncio.sleep(2.0)
                    if budget.should_rotate:
                        print(f"[supervisor] budget rotate: usage={budget.usage_pct:.2%}, "
                              f"closing session for resumption-handle reconnect")
                        session_stop.set()
                        return
            budget_task = asyncio.create_task(watch_budget())
```

Add `budget_task` to the `asyncio.wait()` set with the others. After session-close, the inner reconnect loop in `run()` opens a new session via the persisted resumption handle — automatically inherits the conversation, but the new server-side context starts fresh below the threshold.

Add the budget feeders into `pump_mic` and `pump_responses`:

```python
                        # in pump_mic, after each successful send:
                        budget.audio_seconds(self._cfg.chunk_ms / 1000)
                        # in pump_responses, after `audio = response.data` if audio:
                        budget.audio_seconds(len(audio) / (24000 * 2))
                        # 24kHz S16 = 48000 bytes/s; 1/48000 s per byte; 2 bytes per sample
```

For watch.run, wrap with a counter — easiest is to extend WatchStream to take an optional `on_frame: Callable` that the session passes as `lambda: budget.jpeg_frames(1)`.

**Step 5: Add `config.py` knobs**

```python
@dataclass(frozen=True)
class SupervisorConfig:
    # ... existing ...
    budget_window_tokens: int = 32000
    budget_threshold: float = 0.8

# in load():
    budget_window_tokens=int(os.environ.get("SUPERVISOR_BUDGET_WINDOW_TOKENS", "32000")),
    budget_threshold=float(os.environ.get("SUPERVISOR_BUDGET_THRESHOLD", "0.8")),
```

`deploy/supervisor.env`:
```
SUPERVISOR_BUDGET_WINDOW_TOKENS=32000
SUPERVISOR_BUDGET_THRESHOLD=0.8
```

**Step 6: Run tests + soak**

```bash
GOOGLE_API_KEY=test pytest tests/supervisor/test_budget.py -v
./scripts/sync-ugv-tools.sh && sshpass -p 'jetson' ssh jetson@192.168.1.155 'sudo systemctl restart ugv-supervisor.service'
# Soak: keep an active session open with watch mode on, verify rotation
# fires at the configured threshold. Operator should hear a ~3-5 s gap
# during rotation, then continue talking and the model still has context.
```

**Step 7: Commit**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/budget.py ugv_tools_api/supervisor/session.py \
        ugv_tools_api/supervisor/config.py deploy/supervisor.env tests/supervisor/test_budget.py
git commit -m "feat(supervisor): proactive token-budget session rotation"
```

---

### Task 7: Operator README updates

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/README.md`

**Why:** Operator behavior changes meaningfully with this plan: barge-in works again (used to be killed by half-duplex), watch mode turns on automatically during missions, AEC has a rollback knob. Document so the operator + the next person on-call doesn't reverse-engineer it from logs.

Sections to add or update:
- **Watch mode** — what it is, how to turn it on/off via voice ("turn on watch mode" / "turn off watch mode"), default behavior during missions, token-budget impact
- **Barge-in** — explicit note that you can interrupt the model now; the silent-wait protocol from the spike notes is no longer required
- **Debugging AEC** — how to verify AEC3 vs halfduplex from logs (`grep "AEC3 enabled"`), how to roll back (`SUPERVISOR_AEC_MODE=halfduplex`), expected dB suppression (≥12 dB synthesized, ≥25 dB real-world)
- **Session rotation** — the operator might hear a ~3-5 s pause; that's normal; the model retains context via resumption handle

No code changes. Pure docs.

**Step 1: Write the section drafts; review them; commit.**

```bash
cd docs/ugv-beast/setup/ugv_tools_api
git add ugv_tools_api/supervisor/README.md
git commit -m "docs(supervisor): operator notes for watch mode + barge-in + AEC + rotation"
```

---

### Task 8: Empirical bench session record

**Files:**
- Create: `docs/plans/notes/2026-04-XX-supervisor-2-5-bench.md` (replace XX with actual bench date)

**Why:** The plan made several point estimates (audio tokens/s, jpeg tokens/frame, AEC dB suppression, rotation cadence) that should be empirically validated on the actual robot. Without a bench record, the next person tweaking knobs has no anchor.

**Test protocol:**

For each measurement below, run on the actual robot in normal operating conditions (operator + JBL + camera mic at standard distance), with logs captured to a file.

| # | Measurement | How to capture | Expected (refine if off) |
|---|---|---|---|
| 1 | Tokens/min idle (no mission, no watch) | Run a 10-min idle session, watch journalctl for the SDK's `usage_metadata` per response, divide. | ~1500 tokens/min |
| 2 | Tokens/min during mission with watch on | Dispatch a 5-min mission, log usage during. | ~3500 tokens/min |
| 3 | AEC3 dB suppression on the model's voice | Capture mic raw with AEC bypass for 30 s of model speech; capture mic AEC-on for 30 s of model speech; compute RMS of each window in numpy. | ≥ 25 dB |
| 4 | Session rotation actually fires at right point | Set `SUPERVISOR_BUDGET_THRESHOLD=0.5` (force quick rotation), run 5-min watch session, observe rotation in logs and operator continuity | rotation triggers within 30 s of crossing threshold |
| 5 | Mid-mission pose-grounded answer | After dispatch_er_mission + 30 s, ask "where are we?" — verify model answers with current pose WITHOUT calling get_robot_state in the same turn. | answer cites specific x/y/yaw values |

**Acceptance:** all five measurements within ±30% of expected. If any are off by >30%, file a follow-up task to refine the budget constants in `budget.py`.

**Step 1: Run the bench, capture logs and `usage_metadata`, fill out the markdown table.**

**Step 2: Commit**

```bash
git add docs/plans/notes/2026-04-XX-supervisor-2-5-bench.md
git commit -m "docs(supervisor): bench record for 2.5 GA + async + watch + AEC"
```

---

## Post-Implementation Verification

After Task 8:

1. **Boot test:** `sudo reboot`. After ~90 s on the Jetson:
   - `systemctl is-active ugv-waveshare.service ugv-tools-api.service ugv-er.service ugv-supervisor.service ugv-ears.service` returns `active` for all
   - `curl localhost:8083/health` returns OK
   - `journalctl -u ugv-supervisor.service` shows `gemini-live-2.5-flash-native-audio` and `AEC3 enabled`

2. **Happy path:**
   - Wake word, "Drive forward 30 cm."
   - Watch mode auto-on, mission progress streamed, watch mode auto-off on terminal
   - Mid-mission interrupt with "stop" — barge-in works, emergency_stop fires

3. **Failure recovery:**
   - Mid-session, set `SUPERVISOR_BUDGET_THRESHOLD=0.1` and let it rotate
   - Operator continues conversation; model retains context via resumption handle

4. **AEC rollback drill:**
   - `SUPERVISOR_AEC_MODE=halfduplex`, restart service, verify operator interaction still works (barge-in won't, but everything else should)
   - Restore `SUPERVISOR_AEC_MODE=aec3`, restart, verify barge-in works again

---

## Out-of-Plan Follow-Ups

- **BlackBox-side `ugv_dispatch_supervisor` tool:** expose `ugv_dispatch_supervisor(mission_or_question: str)` to the BlackBox laptop's chat path. Posts to supervisor's `/open_session` with an initial utterance. Separate plan.
- **OAK-D watch source:** add `oakd` to `SET_WATCH_MODE.source` enum. Requires OAK-D RGB topic plumbing in `RosCamera`. Separate plan.
- **Hardware AEC mic:** if Task 5's AEC3 dB suppression measurement <15 dB in real-world conditions, swap to a Jabra/eMeet conferencing mic with hardware AEC. Separate hardware task.
- **AEC3 delay calibration:** `SUPERVISOR_AEC_DELAY_MS=50` is a guess. Add a calibration script that emits a chirp through the speaker, measures arrival at the mic, and recommends a value. Separate spike.
- **NON_BLOCKING for `get_costmap_view`:** rendering a large costmap PNG can take >100 ms. If we see it gating model output in production, mark NON_BLOCKING with a quick placeholder + sidechannel image. Watch logs first; only do this if we measure it.
