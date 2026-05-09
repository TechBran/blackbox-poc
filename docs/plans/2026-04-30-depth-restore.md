# Restore OAK-D Depth to ER's Observation Bundle

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Re-add OAK-D stereo depth to ER's per-tick observation bundle at 400p15 (matching RGB rate). Yesterday's perception swap (`29e1977`) removed depth to free USB headroom for the OAK-D RGB re-enable; with the bandwidth math now confirmed (5 Mbps RGB + ~50 Mbps depth + 1 Mbps IMU = 56 Mbps total, well under USB 2.0's ~280 Mbps practical ceiling), depth is reinstated. ER 1.6 was trained on RGB+depth+spatial inputs together; this fix gives it back its native 3D distance sense and supersedes today's prompt-drift workaround (`2c9112f`).

**Architecture invariants (locked from prior plans):**
- Gemini Live owns gimbal exclusively. No regression.
- ER perceives only via OAK-D fixed body cam (no pantilt). RGB and depth both come from the OAK-D body camera, so they're spatially aligned (same optical frame, same intrinsics, same timestamp window).
- ER bundle after this plan: **RGB + depth + local costmap + SLAM map + LiDAR + ROBOT_STATE_JSON** (six channels).
- Local costmap stays the immediate-obstacle view; SLAM map stays the cross-room layout view. Depth complements both — RGB+depth gives ER native 3D triangulation for things in front, costmaps give it map-frame planning context.

**Tech stack:** rclpy, depthai (already running 720p RGB → 400-line preview), google-genai, numpy + PIL for the existing `_colorize_depth` rasterizer in `er/sensors.py`, pytest.

**Source of truth:** laptop `docs/ugv-beast/setup/ugv_tools_api/`, deploy via targeted rsync to Jetson `/home/jetson/ugv_ws_waveshare/...`. Tests run via `docker exec ugv_waveshare bash -lc "..."`. Commits go on Jetson `ros2-humble-develop`.

**Out of scope:** any change to RGB rate, Gemini Live's perception, the gimbal-ownership invariant. This plan is depth-only. Physical USB-3.0 port migration is also out of scope (worth experimenting with separately if Brandon wants more headroom later).

---

### Task DR1: Re-enable OAK-D stereo depth at 400p15

**Why:** `oakd_camera.py:main()` currently instantiates `OakdCameraNode(enable_rgb=True, enable_depth=False)` (per yesterday's `2ab95bd`). The depth pipeline plumbing is still in `build_pipeline()` and the node's `_tick_depth` method — only the toggle is off. Flipping it back on at the existing `depth_fps=15` default produces a 400p15 publication on `/oak/stereo/depth`.

**Files:**
- Modify: `ugv_tools_api/ugv_tools_api/nodes/oakd_camera.py:main()` — change `OakdCameraNode(enable_rgb=True, enable_depth=False)` to `OakdCameraNode(enable_rgb=True, enable_depth=True)`. Update the comment block above `main()` to reflect the new "RGB+depth+IMU" mode.

**Steps:**
1. Edit the `main()` instantiation flag.
2. Update the multi-line comment to explain the new bandwidth allocation: "RGB + depth + IMU mode. 720p source → 640×480 preview RGB at 15 Hz (~5 Mbps), 400p depth at 15 Hz (~50 Mbps), IMU at 200 Hz (~1 Mbps). Total ~56 Mbps, well under USB 2.0 practical ceiling. The 9-pin round USB 3.0 cable installed 2026-04-28 keeps signal integrity clean for all three streams."
3. Rsync to Jetson, restart `ugv-tools-api.service`.
4. **Verification gate (DO NOT proceed if any fails):**
   - `ros2 topic hz /oak/stereo/depth` reads ~15 Hz.
   - `ros2 topic hz /oak/rgb/image_rect/compressed` still reads ~15 Hz (RGB unbroken).
   - `ros2 topic hz /oak/imu_zupt` still reads >50 Hz (IMU unbroken).
   - `journalctl -u ugv-tools-api.service --since '60 seconds ago'` shows zero `X_LINK_ERROR` cascades, zero `OAK-D` warnings beyond startup.
5. Commit on `ros2-humble-develop`: `feat(oakd): re-enable stereo depth at 400p15 — restore ER's 3D distance sense`.

If verification fails (X_LINK_ERROR cascades on the OAK-D Lite USB 2.0 link): fall back to `depth_fps=10` (~33 Mbps, gives ~89 Mbps total — still safe). If 10 Hz fails: revert and report.

---

### Task DR2: Add `_render_depth()` back into ER's observation bundle

**Why:** Yesterday's commit `1798201` (then refined in `29e1977`) removed the `_render_depth()` call from `gather_observation()` in `er/sensors.py`. The function itself is intact (still ~lines 162-204) — only the caller was excised. We re-add it.

**Files:**
- Modify: `ugv_tools_api/ugv_tools_api/er/sensors.py:gather_observation()` — re-add `_render_depth()` to the `asyncio.gather(...)` tuple. Re-add the labeled Part block: `if depth is not None: parts.append(genai_types.Part.from_text(text="Depth (OAK-D body cam, turbo colormap, 0.3-5.0 m. Black = no return / out of range."))` followed by `parts.append(depth)`. Place between RGB and LiDAR labels (so the order is RGB → Depth → LiDAR → Local costmap → SLAM map → State, matching the natural "what I see in front" → "what's around at chassis height" → "where am I" flow).
- Modify: `tests/er/test_sensors.py` — update `test_gather_observation_includes_local_costmap_and_slam_map` to also assert `"Depth (OAK-D body cam"` is in the labels (revert the assertion `assert "Depth (OAK-D" not in labels` to a positive). Add an AsyncMock for `_render_depth` in both tests. Add a third test `test_gather_observation_omits_depth_when_renderer_returns_none` mirroring the existing local-costmap-omitted test.

**Steps:**
1. Write failing test: extend `test_sensors.py` with the depth-positive-assertion change. Run it → fails because depth label isn't currently emitted.
2. Edit `gather_observation` to gather + label depth.
3. Run test → passes.
4. Add the new omits-depth-when-None test. Run → passes.
5. Smoke test: `python3 -c "from ugv_tools_api.er import sensors; ..."` to confirm module imports cleanly post-edit.
6. Commit on `ros2-humble-develop`: `feat(er-sensors): restore depth channel in gather_observation bundle`.

---

### Task DR3: Update ER system prompt — depth is a sense again

**Why:** Today's `2c9112f` fix to the spatial-reasoning paragraph and the senses-block from `29e1977` both treat ER as if depth doesn't exist. With DR2 landing depth back in the bundle, the prompt needs to advertise it as a 6th channel and restore depth to the spatial-triangulation guidance.

**Files:**
- Modify: `ugv_tools_api/ugv_tools_api/er/agent_loop.py:_SYSTEM_PROMPT`. Three edits:
  1. **Senses block.** Currently lists 5 channels (OAK-D RGB / LiDAR / ROBOT_STATE_JSON / Local costmap / SLAM map). Insert a new channel #2 between RGB and LiDAR: "Depth image from the OAK-D body camera, colorized TURBO (blue = close, red = far, black = no return / out of range, 0.3-5.0 m useful). The depth is spatially aligned with the RGB above (same optical frame), so a pixel that's red in RGB at the same image coordinates as 1.5 m in depth is at 1.5 m. Use this for distance to anything in your forward view." Renumber subsequent channels.
  2. **Spatial reasoning paragraph.** Currently (post-`2c9112f`) tells ER to "triangulate its direction and distance using the local costmap, the LiDAR bird's-eye, and ROBOT_STATE_JSON.lidar_sectors_m." Rewrite to lead with depth: "When asked to go to or look at something, locate it in the OAK-D RGB image, read its distance directly from the OAK-D depth image at the same pixel coordinates, then cross-reference with the local costmap (is the path clear?) and LiDAR bird's-eye (anything at chassis height the camera might miss?). Depth is your primary distance sense; costmap and LiDAR are reachability and safety cross-checks. The OAK-D doesn't pan/tilt — rotate the body to look elsewhere."
  3. **Verify no other stale "depth no longer exists" language remains** by grep.
- Smoke test: extend `tests/supervisor/test_session_system_prompt.py` (or add a new ER-prompt smoke test if one doesn't exist for ER specifically) — assert the depth channel description text is in `_build_system_prompt()`. Make this test sync-wrapped (no pytest-asyncio).

**Steps:**
1. Write the failing assertion (depth-mention substring should be in the prompt; the post-`2c9112f` "no depth" claim should NOT be in it).
2. Run → fails.
3. Edit the system prompt.
4. Run → passes.
5. Smoke check: `python3 -c "from ugv_tools_api.er.agent_loop import _build_system_prompt; sp = _build_system_prompt(); assert 'Depth image' in sp; assert 'turbo' in sp.lower(); print('size=', len(sp))"`.
6. Restart `ugv-er.service`, verify `/health` 200.
7. Commit on `ros2-humble-develop`: `feat(er-prompt): restore depth as a primary distance sense`.

---

### Task DR4: Operator-led bench validation

**Files:** none (operational).

**Setup:**
1. **Pre-flight:** `curl http://localhost:8082/health` (or the `urllib.request` equivalent). Must return 200 with `models_visible >= 50`. If 503, restart `ugv-er.service` first — same stale-state trap from this morning.
2. Battery: charged to a state Brandon judges sufficient for the test duration.
3. Workspace: same lap-the-couch obstacle layout from last night, OR the find-the-water-jug mission from the prior plan (operator's choice — both exercise depth+costmap+RGB triangulation).

**Pass criteria:**
- ER plans visibly tighter `nav_goto_point` targets — fewer "drift to a random point, replan, drift again" cycles than the pre-fix runs.
- Mission ends with `mission_done` (target reached) or `mission_fail` (clean exhausted-plan), NOT step-limit timeout.
- ER's `narrate()` output includes confident distance phrases like "the chair is about 2 meters ahead" rather than vague "I see a chair nearby" — that's the qualitative signal that depth is being read and used.
- No new `X_LINK_ERROR` in the OAK-D log throughout the mission.
- Gemini Live's narration via the embodied-observer pipeline still works (this isn't an ER-only test — verify the EO stack didn't regress).

**Pass condition for this plan:** at least one of the bench attempts produces a measurably tighter mission than the pre-DR runs. Subjective is fine — this isn't a unit test, it's "did giving ER its depth back fix the spatial reasoning."

**Snapshot the result.**

---

## Notes on order

DR1 → DR2 → DR3 must run sequentially. DR1 is a hardware-validation gate (verify USB stability before depending on depth). DR2 depends on DR1 (the topic must publish before the bundle can subscribe). DR3 depends on DR2 (the prompt advertises behavior the bundle implements). DR4 depends on all three.

If DR1 surfaces X_LINK_ERROR cascades that won't clear, the fall-back is `depth_fps=10` per Task DR1 step 5. If even 10Hz fails: this plan is blocked on hardware investigation (probably the OAK-D Lite PCB-level USB 3.0 capability question Brandon raised). Surface and stop.

## Subagent dispatch cadence

Same as yesterday's two stacks: one fresh subagent per task, sequential, with controller-level spec compliance review between tasks. End-of-stack code-reviewer pass before bench. Auto mode unless something surprising surfaces.
