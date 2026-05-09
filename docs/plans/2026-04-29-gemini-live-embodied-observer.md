# Gemini Live Embodied Observer — perception layering plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Gemini Live (the supervisor's voice persona) behave like an embodied observer with continuous ambient sight and on-demand spatial query — the "head and eyes" of the platform while ER 1.6 is the body and reflexes. Three behavioral changes:

1. Pantilt RGB streams to the Live session by default at a configurable low frame rate (target ~1 frame every 3 seconds). The model sees passively, all the time.
2. The existing `get_camera_view` tool stops returning the image as a function_response (the path that produces the "received the image" / "have to ask again" lag) and instead **pushes the frame down the realtime-input channel** the same way watch mode does — so Gemini Live's vision encoder reasons about it natively on the same turn.
3. A new `get_slam_map_view` tool lets Gemini Live, when it chooses, fetch the persistent SLAM occupancy map for cross-room spatial reasoning ("there's another room over to the east — I haven't been there yet"). Tool-driven, not pushed; model decides when it's worth the token cost.

**Architecture invariant (locked from prior conversation):**
- Gemini Live owns the pan-tilt gimbal exclusively (already enforced via `EXCLUDED_FROM_ER` in `er/tools_decl.py`, commit `001b679`).
- Gemini Live receives RGB-only ambient perception. **No costmap channel** — costmap reasoning belongs to ER. Adding it to the supervisor would blur the body/mind split we just carefully drew.
- SLAM map is the only "spatial overview" affordance Gemini Live gets, and only on-request (tool call), not pushed.

**Tech stack:** google-genai bidi-WS (raw_session.py), httpx, PIL/numpy (rasterizer reuse from `er/sensors.py`), pytest.

**Source of truth:** laptop `docs/ugv-beast/setup/ugv_tools_api/`, deploy via targeted rsync to Jetson `/home/jetson/ugv_ws_waveshare/...`. Tests run via `docker exec ugv_waveshare bash -lc "..."`. Commits go on Jetson `ros2-humble-develop`.

**Out of scope:** changing Gemini Live's audio path, AEC mode, model id, or wake-word integration. Changing ER's perception (already settled in the 2026-04-29 ER refinement plan).

---

### Task 1: Watch mode default-on + env-configurable FPS

**Why:** Today `WatchStream` is constructed with a hard-coded `fps=0.25` and starts off. It only flips on when `dispatch_er_mission` succeeds (`session.py:1154-1156`). Outside missions, Gemini Live is blind unless the operator manually calls `set_watch_mode(on=true)`. We make watch mode default-on at session creation and expose the FPS as an env knob.

**Files:**
- Modify: `ugv_tools_api/supervisor/config.py` — add `WATCH_DEFAULT_ON: bool` (default `True`) and `WATCH_FPS: float` (default `0.33`, i.e. one frame every ~3 s).
- Modify: `ugv_tools_api/supervisor/session.py:961-969` — replace hard-coded `fps=0.25` with `config.WATCH_FPS`. Right after `WatchStream(...)` construction, call `watch.set(on=config.WATCH_DEFAULT_ON, source="pantilt")` IFF no operator override is in flight (mirror the existing override-aware pattern).
- Modify: `ugv_tools_api/supervisor/tool_declarations.py:208-228` — extend `set_watch_mode` to accept an optional `fps` param so the operator can ask Gemini Live to "speed up" or "slow down" the camera if a mission needs it. Keep it operator-tunable, not model-self-tunable: clamp to [0.1, 1.0] and document in the description.
- Modify: `ugv_tools_api/supervisor/tool_handlers.py` — `exec_set_watch_mode` accepts `fps` and calls a new `WatchStream.set_period(fps)` setter.
- Modify: `ugv_tools_api/supervisor/watch_stream.py` — add `set_period(fps: float)` method that updates `self._period = 1.0 / fps`. Atomic; the loop reads `self._period` each iteration so the change takes effect on the next tick.
- Test: extend `tests/supervisor/test_watch_stream.py` (or create) — verify `set_period` updates the period, verify default-on behavior at session creation.

**Step 1: Write the failing tests**
- Default-on test: instantiate `WatchStream`, assert `is_on` is `True` after `set(on=config.WATCH_DEFAULT_ON, source="pantilt")` with `WATCH_DEFAULT_ON=True`.
- FPS-tuning test: instantiate `WatchStream(fps=1.0)`, call `set_period(0.25)`, assert `_period == 4.0`.

**Step 2: Add config constants and the `set_period` method.**

**Step 3: Wire session.py to default-on at creation and use `config.WATCH_FPS`.**

**Step 4: Run tests → PASS.**

**Step 5: Commit:** `feat(supervisor): default-on watch mode at 0.33 FPS, expose WATCH_FPS / WATCH_DEFAULT_ON config knobs`

---

### Task 2: Reroute `get_camera_view` through the realtime-input channel

**Why:** Currently `get_camera_view` returns the JPEG bytes as a `function_response` part. Gemini Live treats function_response inline_data as data-to-acknowledge, not perception-to-reason-about — that's the documented Google behavior producing the "I received the image" / "now describe it" two-prompt lag. Watch mode's `send_video(jpeg, "image/jpeg")` callback uses **realtime_input**, which the vision encoder processes natively. We make `get_camera_view` push down the same path and return a tiny `function_response` ack.

**Files:**
- Modify: `ugv_tools_api/supervisor/session.py` — when registering the `get_camera_view` handler, inject the same `send_video` callback that's already passed to `WatchStream`. Currently `tool_handlers.exec_get_camera_view` only knows about the camera; it needs the realtime-push callable too.
- Modify: `ugv_tools_api/supervisor/tool_handlers.py` — `exec_get_camera_view` now: (a) grabs latest pantilt JPEG, (b) `await send_video(jpeg, "image/jpeg")`, (c) returns `{"ok": True, "pushed_realtime_input": True, "size_bytes": len(jpeg), "age_s": <age>}`. Tool result is a small JSON ack; the image arrives as realtime perception.
- Modify: `ugv_tools_api/supervisor/tool_declarations.py:62-71` — update `GET_CAMERA_VIEW` description to reflect new behavior: "Push a fresh pantilt-camera frame into your perception stream so you can see it on this turn. Use when you need a higher-quality look than the ambient ~0.33 FPS feed gives, or to peek at something specific between watch frames."
- Test: extend `tests/supervisor/test_tool_handlers.py` — mock `send_video` callable, mock the camera, call `exec_get_camera_view`, assert `send_video` was called with `("image/jpeg", <bytes>)`-ish args, assert response is the small ack shape.

**Step 1: Write failing test that asserts `send_video` is called and response shape changed.**

**Step 2: Refactor handler signature to accept `send_video` callable.**

**Step 3: Update the call site in session.py to thread the callable.**

**Step 4: Update tool description.**

**Step 5: Run tests → PASS.**

**Step 6: Commit:** `feat(supervisor): get_camera_view pushes via realtime_input instead of function_response (fixes "received but didn't reason about image" lag)`

---

### Task 3: New `get_slam_map_view` tool, also via realtime-input

**Why:** Brandon's design: Gemini Live should be able to ask "where am I in the floor plan? Are there rooms I haven't been to?" and get a SLAM occupancy-map view. Tool-driven (not pushed) so the token cost only kicks in when the model judges the spatial query is worth it. The rasterizer already exists from today's ER refinement (`er/sensors.py:_rasterize_slam_map`) — reuse, don't rebuild.

**Files:**
- Refactor: `ugv_tools_api/ugv_tools_api/er/sensors.py` — rename `_rasterize_slam_map` → `rasterize_slam_map` (drop underscore = public). The function is pure (`msg, robot_x, robot_y, robot_yaw, max_size_px → bytes`), no ER state; safe to share.
- Modify: `ugv_tools_api/supervisor/tool_handlers.py` — new `exec_get_slam_map_view(send_video)`: (a) read latest `/map` from `RosBridge.instance().node.get_latest("/map")`, (b) read latest `/robot_pose` for the marker, (c) call `er.sensors.rasterize_slam_map(msg, x, y, yaw, max_size_px=512)`, (d) `await send_video(png, "image/png")`, (e) return `{"ok": True, "pushed_realtime_input": True, "map_size_m": <W×H>, "robot_at": [x, y]}`.
- Modify: `ugv_tools_api/supervisor/tool_declarations.py` — add `GET_SLAM_MAP_VIEW = types.FunctionDeclaration(...)`. Description must communicate the intent clearly: "Fetch a top-down view of the SLAM map (the persistent room layout the robot has built so far) and push it into your perception stream. A cyan dot + heading line marks your current position. Use when the operator asks about cross-room navigation, when you want to suggest going to an unmapped area, or when you need to remember whether you've already visited somewhere. This is on-demand — no need to call repeatedly; the map only changes when SLAM updates it." Append to `ALL_TOOLS`.
- Modify: `ugv_tools_api/supervisor/session.py` — wire the new tool name in the dispatch switch (parallel to the `get_camera_view` branch).
- Test: `tests/supervisor/test_tool_handlers.py` — mock the bridge's `get_latest` for `/map` and `/robot_pose`, mock `send_video`, mock the rasterizer (or use a tiny synthetic OccupancyGrid), assert `send_video` called with PNG bytes + correct mime, assert response shape.

**Step 1: Rename `_rasterize_slam_map` → `rasterize_slam_map` in `er/sensors.py`, update the existing internal call site (`_render_slam_map`).**

**Step 2: Write failing test that asserts the new handler pushes a PNG via `send_video`.**

**Step 3: Implement `exec_get_slam_map_view`. Add the tool declaration. Wire the dispatch switch.**

**Step 4: Run tests → PASS.**

**Step 5: Commit:** `feat(supervisor): add get_slam_map_view tool — on-demand SLAM map for Gemini Live spatial reasoning`

---

### Task 4: System prompt nudge for proactive narration

**Why:** With watch mode default-on, Gemini Live continuously sees the pantilt view. The model needs to be told it should narrate observations spontaneously, not only when asked — otherwise it'll keep its default "respond when prompted" posture and the embodied-observer feel won't emerge.

**Files:**
- Modify: `ugv_tools_api/supervisor/session.py:102` — `_SYSTEM_PROMPT` string. Add a new section (after the existing identity/persona block) along these lines:

  ```
  ## Your senses
  
  - You receive a continuous ambient video feed from the pan-tilt camera —
    one frame every ~3 seconds. This is your eyes. Treat it like a passenger
    watching the world go by from the robot's vantage. Comment on what
    changes and what's interesting: "I see a chair in the corner now",
    "we're approaching a doorway", "the lighting just dropped". Do NOT
    narrate every frame — only when something is worth saying.
  - You can call get_camera_view to push a fresher frame between watch
    ticks if you need a sharper look at something specific.
  - You can call get_slam_map_view to fetch a top-down floor plan with
    your current position marked. Use this when you need to reason about
    rooms, doorways, or places the robot has or hasn't been.
  - You control the pan-tilt gimbal via gimbal_look_at. ER does not
    have access to the gimbal — you are its eyes for everything outside
    the OAK-D's fixed forward view. Pan and tilt freely; ER will rotate
    the body if it needs to look in a direction the gimbal can't reach.
  - The ER 1.6 agent on this robot is your body and reflexes. It plans
    and executes missions. You observe and narrate. When the operator
    gives a mission, dispatch_er_mission to ER and then describe what
    you see as ER drives.
  ```

- Test: a smoke-test that builds the system prompt and asserts the new strings ("ambient video feed", "get_slam_map_view", "ER 1.6 agent on this robot is your body") are present.

**Step 1: Write the smoke test asserting key new strings appear in `_SYSTEM_PROMPT`.**

**Step 2: Edit `_SYSTEM_PROMPT` to add the senses block.**

**Step 3: Run test → PASS.**

**Step 4: Commit:** `feat(supervisor): system prompt nudges Gemini Live toward proactive embodied narration + advertises the two new image-fetch behaviors`

---

### Task 5: Live deploy + service restart + smoke probe

**Files:** none (operational).

**Steps:**
1. Rsync all modified files to Jetson host path (same dance as today's 6-commit stack).
2. `sudo systemctl restart ugv-supervisor.service` to load the new code.
3. `sudo journalctl -u ugv-supervisor.service --since '30 seconds ago'` — verify watch mode prints `WatchStream on@0.33 source=pantilt` near startup, no exceptions.
4. From host: `python3 -c "import urllib.request; r = urllib.request.urlopen('http://localhost:8080/health'); print(r.status, r.read())"` — supervisor still healthy.

---

### Task 6: Operator-led bench validation

**Files:** none.

**Setup:** Open a fresh Gemini Live session (e.g., via `EMEET` wake word "Black Box Flight Recorder, hello").

**Pass criteria — narration & vision:**
- Gemini Live narrates spontaneously without an explicit "what do you see" prompt. Some narration should happen within 30-60 s of session start as the camera frame cycles.
- Asking "what do you see" produces description on the **same turn** — no double-prompt lag.
- Asking "is there another room you haven't explored?" should cause Gemini Live to call `get_slam_map_view`, then describe the map content and propose a target.

**Pass criteria — invariant preservation:**
- `dispatch_er_mission` still works end-to-end (ER drives autonomously while Gemini Live narrates).
- `system_emergency_stop` mid-mission cancels Nav2 + ER + cmd_vel pin (the 5-branch fan-out from `d373455`). Gemini Live's narration also stops.
- `set_watch_mode(on=false)` still silences the ambient feed.
- The ER agent never receives gimbal-control affordances (already enforced by T5 of yesterday's plan; this should not regress).

**Pass criteria — token economy:**
- Session rotation (TokenBudget) should still trigger before Gemini's context limit. With watch at 0.33 FPS and ~50 KB/frame, that's ~17 KB/s — about 60 KB/min of context — well within the rotation window. If sessions rotate noticeably more often than before, raise `WATCH_FPS` floor (e.g., 0.2) or gate watch on "human voice heard in last N seconds" via existing audio_io VAD.

**Snapshot the result.**

---

## Notes on order

T1 → T2 → T3 → T4 are file-independent enough to be done in parallel by different subagents if dispatched. T5 requires all four. T6 requires T5.

If we hit a token-cost problem at T6, the fix is in T1's config knobs — bump `WATCH_FPS` down (0.2 = every 5 s) or set `WATCH_DEFAULT_ON=False` and rely on the existing ER-mission auto-on. The architecture stays.
