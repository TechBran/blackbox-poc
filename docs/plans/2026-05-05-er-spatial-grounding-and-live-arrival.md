# ER Spatial Grounding + Gemini Live Arrival Narration

**Date:** 2026-05-05
**Operator:** Brandon
**Status:** Designed, NOT yet implemented (next-session work)

## Problem statement

Two related deficiencies in the autonomous-perception loop:

### Problem 1: ER plots points "all over the place"
The ER (Embodied Reasoning) Gemini 1.6 agent is supposed to identify objects in the OAK-D RGB+depth view and translate them into map-frame nav goals. The system prompt instructs it to:
- Read pixel + depth from OAK-D
- Cross-reference with robot pose (in ROBOT_STATE_JSON)
- Pick a map-frame (x, y) target
- Call `nav_goto_point(x, y)`

In practice, **points end up far from where they should be**. Brandon's report: "it just plots them somewhere completely off the map."

Root cause: the model is doing mental geometric reasoning to convert (pixel_u, pixel_v, depth_m) + robot_pose + camera-to-base TF + camera intrinsics → world (x, y). LLMs are notoriously inconsistent at trig-heavy multi-step spatial computation, even when given all the inputs.

### Problem 2: Gemini Live doesn't describe surroundings on arrival
When a nav goal or waypoint completes, the supervisor (Gemini Live, which owns voice + pan-tilt gimbal) announces "navigation complete" but does NOT capture an OAK-D image and describe what it sees. Brandon's report: "in between each waypoint or if a nav goal reaches the nav goal, then it should tell us about where it is, describing where it is, that way it sounds more like the robot is actually doing it itself."

Root cause: `mission_poller.py` has status-change events but no path to inject a visual snapshot into the Gemini Live session for narration.

## Design — Problem 1: `project_pixel_to_map` tool

Add a deterministic tool the ER can call instead of doing mental projection:

**Input:**
- `pixel_u`: int (column in OAK-D RGB image)
- `pixel_v`: int (row in OAK-D RGB image)
- `frame`: string, default "map" (output frame; could also be "odom" or "base_link")

**Output (JSON):**
```json
{
  "ok": true,
  "frame": "map",
  "x": 4.21,
  "y": -1.85,
  "z": 0.32,
  "depth_m": 2.18,
  "azimuth_from_robot_deg": -12.3,
  "in_costmap_lethal": false,
  "in_costmap_inflation": false,
  "near_wall_distance_m": 0.74
}
```

**Implementation steps:**
1. New tool in `ugv_tools_api/ugv_tools_api/tools/` (or wherever existing tools live)
2. Subscribe to:
   - `/oak/rgb/camera_info` (intrinsics — fx, fy, cx, cy)
   - `/oak/depth/image_raw` (depth at each pixel, in mm or m)
   - TF chain: `oak_optical_frame` → `base_link` → `odom` → `map`
3. On call:
   - Look up depth at (pixel_u, pixel_v) from latest depth msg
   - Back-project to camera frame: `X = (u - cx) * d / fx`, `Y = (v - cy) * d / fy`, `Z = d`
   - Transform via TF to requested output frame
   - Optionally annotate: is target cell in lethal/inflation zone? distance to nearest wall?
4. Add tool declaration to `tools_decl.py` so ER model sees it
5. Update ER system prompt to instruct using this tool instead of mental projection

**Sample updated prompt section:**
> When you spot a target in the OAK-D RGB image, get its precise map-frame location by calling `project_pixel_to_map(pixel_u, pixel_v)`. The returned (x, y) is exactly the map-frame coordinate. Use this directly in `nav_goto_point(x, y)`. Do NOT mentally compute coordinates from pixel+depth+pose — the tool does it deterministically.

**Why this fixes the failure:** the math becomes mechanical — camera intrinsics, depth lookup, and TF chain are all known precisely. LLM only has to identify the pixel of interest in the image, which is what vision models are good at.

**Estimated effort:** 2-3 hours (tool implementation + integration + prompt update + test).

## Design — Problem 2: Gemini Live arrival narration

When a nav goal or waypoint completes, fire a "scene description" event into the Gemini Live session.

**Hook point:** `mission_poller.py` already detects status transitions (`succeeded`, `aborted`, etc). Extend its terminal-status callback.

**Logic:**
1. On nav status transition `navigating → succeeded`, fetch latest OAK-D RGB snapshot
2. Push the image into the Gemini Live session as a multimodal user-turn message: "Robot has reached its destination. Describe what you see in this image, as if you arrived and are reporting your surroundings."
3. Live model generates a narration via TTS (already wired through SPEAK_URL)
4. User hears: "I've arrived. I see a workbench to my left, a chair ahead, and a doorway on the right..."

**Implementation considerations:**

a) **Image latency**: OAK-D image must be fresh. Either trigger an explicit snap on arrival (~200ms) or use the most recent buffered frame (~100ms latency).

b) **Live session injection**: the supervisor's `session.py` manages the Gemini Live session. Need to expose a method like `session.inject_arrival_event(image_bytes, prompt)`.

c) **Avoid stepping on ER**: if the ER agent is mid-mission, the ER itself calls `narrate()` and may already be describing arrival. We need a deduplication: if ER is active, skip Live's auto-narration. If only Nav2 + waypoint follower (no ER), Live narrates.

d) **Gimbal sweep before narration**: optional — the user mentioned the gimbal could pan to scan the environment. The Live model could call `gimbal_look_at` to sweep, capture multiple snaps, then describe a panorama.

**Phased approach:**

Phase A (simple): on nav success, send last OAK-D RGB to Live with "describe what you see" prompt. Live narrates via TTS.

Phase B (richer): pre-narration gimbal sweep. Live calls `gimbal_look_at(45°)`, snaps, `gimbal_look_at(-45°)`, snaps, then narrates the union.

Phase C (full integration): Live announcement coordinated with ER's mission_done. ER says "Mission complete." → Live says "I see a workbench, a chair, and a doorway." → user gets clean two-sentence arrival summary.

**Estimated effort:** 1-2 hours for Phase A, +1 hour each for B and C.

## Sequencing recommendation

Implement Problem 2 (Live arrival narration) FIRST because:
- Smaller scope, faster ROI
- Improves the demo immediately
- Doesn't depend on Problem 1's tool

Then Problem 1 (project_pixel_to_map) because:
- More engineering, but unlocks reliable autonomous targeting
- Will dramatically improve the "robot autonomously navigates to things it sees" use case

Finally tie them together: ER spots an object, calls `project_pixel_to_map`, navigates there, on arrival Live describes the scene. End-to-end visual autonomy.

## Out of scope (don't do here)

- Adding new vision capabilities to OAK-D (it has what we need)
- Moving away from Gemini ER (current model is appropriate)
- Custom panoramic stitching (single-shot description is enough for Phase A)

## Search hints

- 'ER spatial grounding pixel projection map frame'
- 'project_pixel_to_map deterministic tool'
- 'Gemini Live arrival narration scene description'
- 'mission_poller terminal status hook image inject'
- 'OAK-D depth back-projection TF chain'
