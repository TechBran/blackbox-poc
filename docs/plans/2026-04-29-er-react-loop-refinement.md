# ER ReAct Loop Refinement — Perception swap, gimbal handoff, abort fan-out

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refine the existing ER 1.6 agent loop so that (1) ER perceives via OAK-D fixed RGB + local costmap + SLAM map, (2) gimbal pan/tilt control transfers exclusively to Gemini Live, (3) supervisor's emergency_stop fans out to also cancel any active ER mission. The streaming function-call loop, embodied wait, mission lifecycle, and tool-synthesis narration all already exist — this plan refines them, it does not rebuild.

**Architecture:**
- ER service owns the ReAct loop (already does, in `er/agent_loop.py`).
- ER perception inputs: **OAK-D RGB (fixed, 480p15)** + **local costmap (3m radius)** + **SLAM map (full room layout)** + LiDAR top-down + state JSON. **Pantilt RGB and depth are dropped from ER's bundle** — depth becomes redundant once we have local costmap, and pantilt belongs to Gemini Live.
- ER actuation: nav_goto_point, nav_plan_preview, nav_cancel, motion_*, lights_*, explore_*, status_*, system_emergency_stop, mission_done/fail/ask_user. **No gimbal tools.**
- Gemini Live owns gimbal exclusively (no code change needed — it already has them; we just stop ER from competing).
- Supervisor's `system_emergency_stop` adds an `er_mission_cancel` branch in the existing fan-out (`nav_cancel + explore_stop + cmd_vel_pin + fw_T0 + er_mission_cancel`).

**Tech Stack:** rclpy, FastAPI/httpx, google-genai (Vertex), numpy + PIL for rasterizing, pytest.

**Source of truth:** laptop `docs/ugv-beast/setup/ugv_tools_api/`, deploy via targeted rsync to Jetson `/home/jetson/ugv_ws_waveshare/...`. Tests run via `docker exec ugv_waveshare bash -lc "..."`. Commits go on Jetson `ros2-humble-develop` branch (controller handles commits between tasks).

**Out of scope:** any change to the agent loop control flow itself, any retraining of ER, BNO85 IMU swap, wheel slip calibration, custom BT recovery plugins.

---

### Task 1: Subscribe to `/local_costmap/costmap` in RosBridge

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/ros_bridge.py:107-112` (extend the existing global-costmap subscription block to also cover `/local_costmap/costmap` with the same TRANSIENT_LOCAL QoS)
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/test_bridge_subscriptions.py` (add assertion that `/local_costmap/costmap` is in the bridge's subscription list)

**Step 1: Write the failing test**
Mirror existing test pattern: spin up the bridge, assert `/local_costmap/costmap` appears in `node.get_topic_names_and_types()`, expect FAIL until subscription added.

**Step 2: Add the subscription**
```python
# /local_costmap/costmap — transient_local + reliable, mirror of global costmap
node.create_subscription(
    OccupancyGrid, "/local_costmap/costmap",
    node._cache_cb("/local_costmap/costmap"), costmap_qos,
)
```

**Step 3: Re-run test, confirm PASS.**

**Step 4: Commit (controller, on Jetson):** `feat(ros_bridge): subscribe to /local_costmap/costmap`

---

### Task 2: Add `_render_local_costmap` and `_render_slam_map` in sensors.py; swap observation channels

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/sensors.py` (add two new render functions, modify `gather_observation`)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/config.py` (add topic constants + per-channel image size/scale settings)
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/test_er_sensors.py` (new or extended — verify both channels appear in `gather_observation` output and that global costmap is gone)

**Design:**
- `_render_local_costmap` — clones `_render_costmap`, sources from `LOCAL_COSTMAP_TOPIC = "/local_costmap/costmap"`, smaller window (8m × 8m at 0.04 m/cell = 200×200 px output). This is the "what's immediately blocking me" view.
- `_render_slam_map` — uses the same `_rasterize_costmap` engine (it accepts any `OccupancyGrid`), sources from `SLAM_MAP_TOPIC = "/map"`, larger window scaled to fit the entire map (computed dynamically from `msg.info.width × msg.info.height × resolution`, capped at 512px). This is the "spatial layout of the whole space" view.
- `gather_observation` — drops `_render_costmap` (global), drops `_render_depth` (redundant for ER now that it has local costmap and OAK-D RGB), adds local costmap + SLAM map. Updates the labelling text Parts.

**Step 1: Add config constants for both new channels (LOCAL_COSTMAP_TOPIC, SLAM_MAP_TOPIC, dimensions, cache TTL).**

**Step 2: Write failing test that calls `gather_observation()` with mocked-out RosBridge `get_latest`, asserts the result has parts labelled "Local costmap" and "SLAM map" but NOT "Global costmap" or "Depth".**

**Step 3: Implement `_render_local_costmap` and `_render_slam_map`. Update `gather_observation` to include them and remove the global+depth channels.**

**Step 4: Re-run test → PASS.**

**Step 5: Commit:** `feat(er): swap observation bundle to local-costmap + slam-map, drop global-costmap and depth`

---

### Task 3: Re-enable OAK-D RGB at 480p15 (RGB-only, depth stays disabled)

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/nodes/oakd_camera.py` (re-enable RGB pipeline at 480p15, keep depth and IMU-only mode otherwise — the b3d2814 trim left depth off, this task partially un-does the RGB part of that)
- Test: integration via systemd restart on Jetson; verify `/oak/rgb/image_rect/compressed` publishes at ~15 Hz and OAK-D doesn't X_LINK_ERROR.

**Why this is risky:** Re-enabling RGB re-enters the USB bandwidth conversation. At 480p15 RGB-only (no depth), the bus headroom should still be safe (~9 MB/s per estimate, <30% of practical USB 2.0 ceiling), but it must be validated on hardware.

**Step 1: Modify `OakdCameraNode.__init__` defaults so `enable_rgb=True` and `enable_depth=False`. Set the ColorCamera resolution to 480p (THE_480_P) and FPS to 15.**

**Step 2: Update `main()` to pass `enable_rgb=True, enable_depth=False`.**

**Step 3: Deploy to Jetson via rsync. Restart camera node. Verify topic publish rate via `ros2 topic hz /oak/rgb/image_rect/compressed`. Confirm no X_LINK_ERROR or VPU disconnect for 60 seconds.**

**Step 4: Commit:** `feat(oakd): re-enable RGB at 480p15 for ER perception`

---

### Task 4: Switch ER's RGB topic to OAK-D and update system prompt

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/config.py:78` (`RGB_TOPIC = "/oak/rgb/image_rect/compressed"`)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/config.py:64-65` (`RGB_WIDTH=854, RGB_HEIGHT=480` to match 480p15 — or smaller resize for token economy, e.g., 640×360)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/agent_loop.py:21-230` (system prompt rewrite — see details below)

**System prompt changes:**
- Replace "RGB image from the pantilt camera" → "RGB image from the OAK-D fixed body camera (the OAK-D points forward and does not pan or tilt; to look elsewhere, you must rotate or drive the body)"
- Remove the entire "Use the gimbal to look before you use the body" doctrine point — ER doesn't have the gimbal anymore
- Remove gimbal entries from the tool quick-reference
- Replace "Global costmap" doctrine block with **two** blocks: "Local costmap (8m × 8m around me, transient obstacles)" and "SLAM map (full room layout, persistent)"
- Update `gather_observation` labelling text Parts to match
- Note: depth is gone too — remove "Depth image from the OAK-D, colorized TURBO" reference

**Step 1: Make the config + prompt edits.**

**Step 2: Run existing ER tests; ensure nothing else relies on pantilt RGB or depth being in the ER bundle.**

**Step 3: Commit:** `feat(er): switch RGB to OAK-D body camera, drop gimbal doctrine from system prompt`

---

### Task 5: Filter gimbal tools out of ER's tool declarations

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/er/tools_decl.py:31-47` (`_build_passthrough_declarations` adds an exclusion set; gimbal tool names are excluded)
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/test_er_tools_decl.py` (assert `gimbal_look_at`, `gimbal_reset`, `gimbal_get_state` NOT in `ALL_DECLARATIONS`; assert `nav_goto_point`, `mission_done` ARE present)

**Step 1: Add `EXCLUDED_FROM_ER: set[str] = {"gimbal_look_at", "gimbal_reset", "gimbal_get_state"}` constant.**

**Step 2: Skip excluded names in the passthrough builder loop.**

**Step 3: Write the test, expect PASS.**

**Step 4: Commit:** `feat(er): exclude gimbal tools from ER's harness — Gemini Live owns gimbal`

---

### Task 6: Extend supervisor's emergency_stop to also cancel active ER mission

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/tools/system.py` (add `er_mission_cancel` branch to the existing fan-out)
- Test: `docs/ugv-beast/setup/ugv_tools_api/tests/test_tools_system.py` (extend the existing fan-out test to assert the ER cancel POST happens)

**Design:**
- Add a 5th branch to `emergency_stop`: HTTP POST to `http://localhost:8082/mission/abort` (the ER service abort endpoint — `er/server.py` already exposes mission lifecycle).
- Best-effort, wrapped in try/except like the existing branches; failure logs warning but doesn't block other branches.
- Updates the returned `fanout` array to include `er_cancel: ok|skipped|error`.

**Step 1: Verify `mission_manager.py` has an abort path. Confirm the ER service exposes `POST /mission/abort` or equivalent. If not, add it as part of this task.**

**Step 2: Write the failing test using `httpx_mock` or `respx` to verify the POST is dispatched on emergency_stop.**

**Step 3: Add the branch in `system.py`. Re-run test → PASS.**

**Step 4: Commit:** `feat(estop): cancel active ER mission as a 5th branch in emergency_stop fan-out`

---

### Task 7: Operator-in-the-loop bench validation — "find the water jug" search mission

**Files:** none (operational test only).

**Setup:**
- Brandon places a water jug (or any visually distinct object) somewhere in a known room.
- Brandon issues mission via Gemini Live: "Find the water jug — it's in one of the upstairs rooms."
- Mission gets dispatched to ER via the supervisor.

**Pass criteria:**
- ER calls `nav_goto_point` to traverse, NOT `gimbal_*` (gimbal tools are gone).
- OAK-D RGB stream stays live for the full mission (no X_LINK_ERROR in journalctl).
- ER ends with `mission_done` (target found) or `mission_fail` (exhausted plan), not step-limit failure.
- Supervisor's `system_emergency_stop` (if invoked mid-mission) cancels both Nav2 AND the ER mission session — no orphaned ER turns burn after stop.
- Gemini Live can pan/tilt the gimbal mid-mission without conflict.

**Pass through the snapshots and memory entries documenting the result.**

---

## Notes on Order

Tasks 1, 2, 5 can be done laptop-side without touching the robot. Task 3 is the only hardware-validation gate. Task 4 depends on Task 3 (changing RGB topic before OAK-D RGB is alive will starve the ER bundle). Task 6 depends on Task 5 being shipped (so we know which tools ER actually has). Task 7 depends on everything else.

Recommended sequencing: 1 → 2 → 5 → 3 → 4 → 6 → 7.
