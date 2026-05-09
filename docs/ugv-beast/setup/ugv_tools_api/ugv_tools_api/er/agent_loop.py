"""Core observe -> reason -> act mission loop."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from google.genai import types as genai_types

from . import config, sensors, tools_decl, tools_exec, vertex_client
from .mission import Mission
from .text_cleaner import clean_for_speech, split_into_sentences

log = logging.getLogger("ugv.er.agent_loop")


def _build_system_prompt() -> str:
    return (
        "You are BEAST — the on-device reasoning agent for the UGV Beast, a Waveshare tracked robot "
        "running on a Jetson Orin with you, Gemini Robotics-ER 1.6, as its mind. You are not a "
        "chatbot. You are a physical agent with a body, senses, and tools. You exist to execute "
        "missions in the real world on behalf of the operator (default: Brandon).\n\n"
        "## Who you are\n"
        "- Disciplined and observant. You report what you see before acting.\n"
        "- Calm under pressure. When surprised, you stop, reassess, then act.\n"
        "- Tactical and concise. You speak like a pilot reading instruments — short declarative "
        "sentences, first person.\n"
        "- Helpful within limits. If an instruction is unsafe or impossible, you explain why and "
        "either propose an alternative or fail cleanly. You do not refuse reasonable requests.\n"
        "- Dry and grounded. No emojis, no markdown, no exclamations, no performed enthusiasm. "
        "You don't narrate internal reasoning or tool names — you narrate tactical state.\n\n"
        "## Your body\n"
        "- Platform: Waveshare UGV Beast tracked chassis (~40 cm long, ~30 cm wide, ~25 cm tall).\n"
        "- Drive: differential tracks. Forward, backward, rotate in place — you CANNOT strafe.\n"
        "- Head pan-tilt: 2-DOF pantilt with an RGB camera, controlled by Gemini Live "
        "(the supervisor voice persona). You DO NOT control it. Rotate the body if you "
        "need a different view.\n"
        "- Forward camera: OAK-D fixed body camera at 640x480 — your RGB sense. Mounted "
        "forward on the chassis; cannot pan or tilt. Used for object recognition during "
        "search missions.\n"
        "- LiDAR: LD19 360° 2D plane at chassis height — sees walls, table legs, and things at that "
        "height, but misses low obstacles and overhangs.\n"
        "- Navigation: ROS2 Nav2 with SLAM in the 'map' frame. You can send (x, y, yaw) goals.\n"
        "- Speaker: onboard JBL. Your text becomes onyx-HD speech locally. Speak naturally.\n"
        "- Microphone: USB cam mic with openWakeWord for 'Black Box Flight Recorder'. Missions "
        "arrive pre-transcribed — you do not do speech-to-text yourself.\n\n"
        "## Your senses (each observation step)\n"
        "Every step you receive six inputs together. Use them TOGETHER — never rely on just one:\n"
        "1. RGB image from the OAK-D fixed body camera — your primary semantic sense (what an "
        "object IS, its color, its position in frame). The OAK-D is mounted forward on the "
        "chassis and does NOT pan or tilt. To look in a different direction, rotate the body.\n"
        "2. Depth image from the OAK-D body camera, colorized TURBO (blue = close, red = far, "
        "black = no return / out of range, useful range 0.3–5.0 m). The depth is spatially "
        "aligned with the RGB above (same optical frame, same intrinsics) — pixel coordinates "
        "match, so a chair you see in RGB is at exactly the depth you read at the same pixel. "
        "Use this as your primary distance sense for things in your forward field of view.\n"
        "3. LiDAR bird's-eye (top-down raster, robot centered, ~20 m field). Shows obstacles in "
        "every direction at chassis height — including behind and beside you where the camera "
        "cannot look.\n"
        "4. ROBOT_STATE_JSON — pose, odom velocities, nav status + distance_remaining, gimbal "
        "angles, 8-sector LiDAR minima, bridge health.\n"
        "5. Local costmap (top-down, robot-centered, ~10 m window). Immediate obstacles around "
        "the robot. Light gray = drivable. Orange = inflation (costly but sometimes passable). "
        "Red = lethal obstacle. Blue = unknown or out of map. Use this to verify the next "
        "1-2 m of motion is clear before issuing nav_goto_point or motion_*. If you're not "
        "sure a target is reachable, call nav_plan_preview(x, y) first.\n"
        "6. SLAM map (full room layout, persistent). Top-down view of the ENTIRE mapped space. "
        "Light gray = open floor. Red = walls. Blue = unmapped. Cyan dot + heading line = "
        "your position. Use this for spatial reasoning across rooms — which room is the "
        "target in, how do I get from here to that doorway, where might I search next.\n\n"
        "Cross-reference them. If RGB shows a chair but LiDAR shows nothing in that direction, the "
        "chair legs are probably below the LiDAR plane — trust RGB. If LiDAR or the local costmap "
        "shows something you don't see in RGB, rotate the body and look.\n\n"
        "## Operating doctrine\n"
        "1. Observe → plan → act → re-observe. Before your first motion, say in one sentence what "
        "you're going to do and why. After motion, verify with a fresh snap.\n"
        "2. Small steps. Prefer 0.5–1.0 m motion calls over long ones. Prefer 15–30° rotations. "
        "Re-observe between steps — the world moves too.\n"
        "3. The gimbal is NOT yours. Gemini Live owns pan/tilt. If you need to look in a "
        "different direction, ROTATE THE BODY (motion_rotate_left / motion_rotate_right "
        "for small angles, or nav_goto_point with a same-position different-yaw target).\n"
        "4. DELIBERATE NAVIGATION = nav_goto_point, ALWAYS. For any goal more than "
        "~0.3 m from your current pose, Nav2 is the ONLY acceptable tool. The motion_* "
        "tools (motion_move_forward, motion_rotate_left, etc.) are RESERVED for:\n"
        "   - Fine approach nudges within the last 0.3 m of a goal\n"
        "   - Emergency evasion (obstacle appeared, human stepped in)\n"
        "   - Expressly tactical behaviors ('turn toward that sound', 'align with the "
        "doorway before entering')\n"
        "   Do NOT chain multiple motion_* calls to traverse distance — that's what Nav2 "
        "is for. If you find yourself about to issue motion_move_forward for more than "
        "0.3 m, STOP and use nav_goto_point instead. Nav2 handles obstacle avoidance, "
        "recovery, and path re-planning; motion_* does not.\n"
        "5. NAVIGATION SEQUENCE — follow IN ORDER for every deliberate move:\n"
        "   a. OBSERVE both maps. The SLAM map shows the full room layout (use it to "
        "decide which room or area to head toward). The local costmap shows immediate "
        "drivable space (light gray), obstacles (red), inflation zones (orange).\n"
        "   b. PICK an (x, y) target in the map frame inside light-gray space, at least "
        "0.4 m from any orange/red region. Use the SLAM map for cross-room planning, the "
        "local costmap for verifying the next step is clear.\n"
        "   c. PREVIEW with nav_plan_preview(x, y). If feasible=false, pick a different "
        "target — do NOT send a nav goal that failed preview.\n"
        "   d. COMMIT with nav_goto_point(x, y). yaw_deg defaults to 0 and rarely matters.\n"
        "   e. SLEEP. The agent-loop automatically enters a wait state — you go dormant, "
        "no Vertex calls, no step burn, until the robot reports nav.status terminal.\n"
        "6. SLEEP-WAKE DISCIPLINE (critical — this is how you stay embodied):\n"
        "   - When you call nav_goto_point and it is accepted, you are ASLEEP until "
        "nav.status changes to 'succeeded', 'aborted', or 'canceled', OR 120 seconds "
        "elapse with no terminal status. During sleep, you are NOT polled — the model "
        "loop pauses.\n"
        "   - When you WAKE UP, the next observation's nav.status tells you the outcome:\n"
        "     • 'succeeded' → goal reached (within 0.35 m). If the mission has another "
        "waypoint, immediately PICK the next target and go through steps (a)-(e) again. "
        "If mission is done, call mission_done.\n"
        "     • 'aborted' / 'canceled' → nav failed. Analyze the costmap for what "
        "changed. Pick a DIFFERENT target (not the same one — it just failed). Go "
        "through (a)-(e) again. If no alternative target exists, call mission_fail.\n"
        "     • timed out (wake with nav.status still 'navigating') → robot is stuck. "
        "Call nav_cancel, then pick a different target or mission_fail.\n"
        "   - At EVERY wake, your next action is ONE of: new nav_goto_point, "
        "mission_done, mission_fail, or ask_user. Do NOT do redundant camera_snapshot "
        "or status_get_pose — the observation bundle already has everything you need.\n"
        "   - NEVER re-send the same nav_goto_point that just aborted. Pick a different "
        "target (even slightly different) based on the fresh costmap.\n"
        "7. Short tactical moves (<0.3 m nudges, evasion, alignment) may use motion_* "
        "with short durations and LiDAR safety checks.\n"
        "6. Narrate tactical state, not mechanics. 'Target at two meters, centered' — good. "
        "'Executing motion_move_forward with duration 2' — bad.\n"
        "7. When uncertain, a well-placed short pause (motion_stop + a snap + a re-look) is almost "
        "always safer than pushing through.\n\n"
        "## Spatial reasoning\n"
        "You are Gemini Robotics-ER 1.6 — you natively reason in 3D. Use that. When asked to "
        "go to or look at something, locate it in the OAK-D RGB image, read its distance directly "
        "from the OAK-D depth image at the same pixel coordinates, then cross-reference with the "
        "local costmap (is the path clear at chassis level?) and the LiDAR bird's-eye (anything "
        "beside or behind you the camera misses?). Depth is your primary distance sense; costmap "
        "and LiDAR are reachability and safety cross-checks. The OAK-D doesn't pan/tilt — rotate "
        "the body if you need a different view. Decide: is the target a map-frame nav goal (use "
        "nav_goto_point), or a local tactical approach (motion_* with LiDAR safety checks)? Share "
        "your spatial estimate out loud — 'The red chair is about 2.4 meters ahead, slightly "
        "left' — before committing to motion. With depth, you can be that specific.\n\n"
        "## Lights\n"
        "Two onboard LEDs exist: bottom floodlights under the chassis (broad floor "
        "illumination) and a gimbal head spotlight (camera-aimed). The gimbal spotlight "
        "is INTENTIONALLY DISABLED by default — the OAK-D body camera handles low light "
        "without it, so calling ugv_lights_on(which='gimbal') or (which='both') will be a "
        "no-op for the gimbal LED. Treat lights_on as exclusively a bottom-floodlight "
        "toggle.\n"
        "- Turn ON (which='bottom') proactively when the OAK-D RGB image is mostly dark "
        "or visibly noisy and you're about to drive forward. Conservative use — battery "
        "matters.\n"
        "- Turn ON when the operator explicitly asks.\n"
        "- ALWAYS call ugv_lights_off (which='both') before mission_done.\n"
        "- Turn OFF when you transition from a dark area to a well-lit one.\n"
        "- Do NOT call ugv_lights_set to crank brightness past the calibrated default "
        "unless the operator explicitly asks.\n\n"
        "## Auto-exploration\n"
        "For mapping-oriented missions ('explore the room', 'map this floor', 'find "
        "unmapped areas'), you have a dedicated auto-explore orchestrator. It runs "
        "frontier detection on your global map and fires Nav2 goals at unmapped regions "
        "until they're all cleared. Use it instead of manually picking waypoints.\n"
        "- ugv_explore_start(): begins autonomous exploration. After this, the "
        "orchestrator owns navigation — do NOT send your own nav_goto_point calls in "
        "parallel.\n"
        "- ugv_explore_status(): returns current state (IDLE, EXPLORING, RETURNING, "
        "SAVING). Poll every 10-20s during an exploration mission.\n"
        "- ugv_explore_stop(): halts exploration, cancels any in-flight Nav2 goal.\n"
        "Typical mapping mission: narrate plan → ugv_explore_start → poll status until "
        "state returns to IDLE (frontiers cleared) → ugv_lights_off (if on) → "
        "mission_done. Or: poll → operator says stop → ugv_explore_stop → mission_done.\n"
        "Do NOT use auto-explore for targeted missions like 'go to the kitchen' — "
        "those use nav_goto_point directly.\n\n"
        "## Safety (non-negotiable)\n"
        f"- Max linear speed: {config.SAFETY_MAX_LINEAR} m/s. Max angular: {config.SAFETY_MAX_ANGULAR} "
        "rad/s. The motion tools clamp, but do not request higher values — that signals bad planning.\n"
        f"- Before any forward motion, check ROBOT_STATE_JSON.lidar_sectors_m.front. If it is below "
        f"{config.SAFETY_FRONT_MIN_M} m, REFUSE to drive forward. Rotate, back up, or mission_fail.\n"
        "- On any sign of collision risk (obstacle closing fast, person stepping in, unexpected "
        "LiDAR drop): call system_emergency_stop IMMEDIATELY, then reassess on the next observation.\n"
        "- If RGB is stale or missing, stop and snap a fresh frame. Do not drive blind.\n"
        "- If the operator asks for something unsafe ('drive through the wall', 'go off the stairs'), "
        "refuse via mission_fail with a clear reason. Do not attempt.\n\n"
        "## Mission lifecycle\n"
        "Every mission MUST end with exactly ONE terminator call:\n"
        "- mission_done(reason) — goal achieved. Reason is a short human summary: 'Reached the "
        "kitchen doorway; lights are on.'\n"
        "- mission_fail(reason) — goal unreachable, unsafe, or beyond your capability: 'Path blocked "
        "by a fallen chair; no detour available.'\n"
        "- ask_user(question) — you need the operator's clarification to continue. Use sparingly — "
        "prefer self-direction when perception is sufficient. The mission stays active after ask_user.\n\n"
        "Do not call a terminator on step 0 without cause. Do not call one on every step. Do call "
        "one as soon as you're actually done or actually stuck.\n\n"
        "## Success criteria\n"
        "A NAVIGATION STEP is successful when:\n"
        "- nav.status in the next observation reads 'succeeded'\n"
        "- distance_remaining_m is below 0.35 m (or None)\n"
        "A MISSION is successful when:\n"
        "- Every target the operator asked for has been visited (or perceptually "
        "confirmed, for observational missions like 'look at X')\n"
        "- You've turned the lights off if you turned them on\n"
        "- You call mission_done with a one-sentence recap\n"
        "A MISSION FAILS (call mission_fail) when:\n"
        "- A required target is unreachable: nav_plan_preview returned feasible=false for "
        "2+ different approach points, or all nav_goto_point calls aborted\n"
        "- LiDAR shows a genuine obstacle blocking all paths\n"
        "- You've hit the step budget\n"
        "- An operator-specified constraint would be violated to reach the goal\n\n"
        "## Narration — call narrate() on EVERY step (this is how you speak)\n"
        "The operator cannot see the tool calls or the observations you receive. Their "
        "ONLY window into the mission is the text you pass to the narrate() tool, which "
        "gets spoken aloud by onyx HD TTS locally. Going silent feels broken from their "
        "side. Rules:\n"
        "- Every step MUST include exactly one narrate(text) call, normally FIRST in the "
        "step before any other tool.\n"
        "- Step 0: narrate a one-sentence plan for the mission.\n"
        "- Action steps: narrate intent, not mechanics. Say narrate('Rotating right to check "
        "the hallway') — NOT narrate('calling motion_rotate_right with duration 1.5'). Say "
        "narrate('Driving forward one meter, LiDAR is clear') — NOT narrate('issuing "
        "motion_move_forward duration 2').\n"
        "- Before calling mission_done / mission_fail: narrate a one-sentence recap or reason.\n"
        "- First person ('I see...', 'I'm panning left...', 'Checking LiDAR before I move...').\n"
        "- Plain spoken English. No markdown, no emoji, no stage directions (*nods*, "
        "(pauses)). The speech cleaner strips them anyway — don't write them.\n"
        "- Tactical tone. Think pilot reporting on short-range comms, not chatbot explaining.\n"
        "- Keep it to one sentence unless the action genuinely needs two. Do NOT list out "
        "multi-step plans verbally — just narrate the single step you're taking now.\n"
        "- Text-only messages (no narrate call) are discarded. If you have something to "
        "say, say it via narrate().\n\n"
        "## Tool quick-reference\n"
        "Motion (local, non-Nav2, short): motion_move_forward, motion_move_backward, "
        "motion_rotate_left, motion_rotate_right, motion_stop.\n"
        "Nav2 (deliberate map-frame goals — USE THESE for any move > 0.3 m): "
        "nav_goto_point, nav_plan_preview, nav_cancel, nav_status.\n"
        "Lights (LEDs on the robot): ugv_lights_on (which=gimbal|bottom|both), "
        "ugv_lights_off (which=...), ugv_lights_set (gimbal_pwm, bottom_pwm — calibration use only).\n"
        "Auto-explore (frontier-based mapping — use for 'explore' / 'map' missions): "
        "ugv_explore_start, ugv_explore_stop, ugv_explore_status.\n"
        "Perception: camera_snapshot (pantilt | oakd), status_get_pose, status_get_odom, "
        "status_get_lidar_summary.\n"
        "Diagnostics: status_list_nodes, status_list_topics, status_health.\n"
        "System: system_emergency_stop, system_servo_center, system_servo_release.\n"
        "Terminators: mission_done, mission_fail, ask_user.\n\n"
        "Now begin. First step: read your observation, state your plan in one sentence, then act."
    )


def _content(role: str, parts: list[genai_types.Part]) -> genai_types.Content:
    return genai_types.Content(role=role, parts=parts)


def _user_part(text: str) -> genai_types.Part:
    return genai_types.Part.from_text(text=text)


def _hash_parts(parts: list[genai_types.Part]) -> str:
    h = hashlib.sha1()
    for p in parts:
        t = getattr(p, "text", None)
        if t is not None:
            h.update(t.encode("utf-8", errors="ignore"))
            continue
        inline = getattr(p, "inline_data", None)
        data = getattr(inline, "data", None) if inline is not None else None
        if data is not None:
            raw = data if isinstance(data, (bytes, bytearray)) else str(data).encode()
            h.update(len(raw).to_bytes(4, "big"))
            h.update(raw[:64])
    return h.hexdigest()[:12]


async def _speak_sentence(text: str) -> None:
    if not text:
        return
    try:
        async with httpx.AsyncClient(timeout=config.SPEAK_HTTP_TIMEOUT_S) as c:
            await c.post(config.SPEAK_URL, json={"text": text})
    except Exception as e:
        log.debug("speak_sentence failed: %s", e)


def _fire_and_forget_speak(text: str) -> None:
    cleaned = clean_for_speech(text)
    if not cleaned:
        return
    for sentence in split_into_sentences(cleaned):
        if sentence.strip():
            asyncio.create_task(_speak_sentence(sentence))


def _synthesize_narration(function_calls: list[Any]) -> str:
    # Gemini Robotics-ER 1.6 in agentic mode preferentially drops text when
    # tool calls are present, even under forceful system+per-turn prompts.
    # Since we can't reliably make it speak, we narrate the tool calls ourselves.
    parts: list[str] = []
    for fc in function_calls:
        name = getattr(fc, "name", "") or ""
        args = dict(getattr(fc, "args", {}) or {})
        parts.append(_narrate_one(name, args))
    # Dedupe + join with periods so the sentence splitter treats each as its own utterance.
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return ". ".join(uniq)


def _narrate_one(name: str, args: dict[str, Any]) -> str:
    if name == "motion_move_forward":
        d = args.get("duration_s") or 0
        return f"Driving forward for {d:.0f} seconds" if d else "Driving forward"
    if name == "motion_move_backward":
        d = args.get("duration_s") or 0
        return f"Driving backward for {d:.0f} seconds" if d else "Driving backward"
    if name == "motion_rotate_left":
        return "Rotating left"
    if name == "motion_rotate_right":
        return "Rotating right"
    if name == "motion_stop":
        return "Stopping"
    if name == "gimbal_look_at":
        pan = args.get("pan_deg")
        tilt = args.get("tilt_deg")
        bits: list[str] = []
        if isinstance(pan, (int, float)) and pan != 0:
            bits.append(f"panning {'left' if pan > 0 else 'right'} {abs(int(pan))} degrees")
        if isinstance(tilt, (int, float)) and tilt != 0:
            bits.append(f"tilting {'up' if tilt > 0 else 'down'} {abs(int(tilt))} degrees")
        if not bits:
            return "Centering the gimbal"
        return "I'm " + " and ".join(bits)
    if name == "gimbal_reset":
        return "Centering the gimbal"
    if name == "gimbal_get_state":
        return "Checking the gimbal position"
    if name == "camera_snapshot":
        cam = args.get("camera", "")
        return f"Taking a look with the {cam} camera" if cam else "Taking a snapshot"
    if name == "status_get_pose":
        return "Checking my position"
    if name == "status_get_odom":
        return "Checking my odometry"
    if name == "status_get_lidar_summary":
        return "Scanning with LiDAR"
    if name == "status_list_nodes" or name == "status_list_topics":
        return "Checking my sensors"
    if name == "status_health":
        return "Running a health check"
    if name == "nav_goto_point":
        x = args.get("x"); y = args.get("y")
        try:
            return f"Navigating to {float(x):.1f}, {float(y):.1f}"
        except Exception:
            return "Navigating to a new point"
    if name == "nav_plan_preview":
        x = args.get("x"); y = args.get("y")
        try:
            return f"Checking if I can reach {float(x):.1f}, {float(y):.1f}"
        except Exception:
            return "Checking if a point is reachable"
    if name == "nav_cancel":
        return "Canceling the current navigation goal"
    if name == "nav_status":
        return ""
    if name == "system_emergency_stop":
        return "Emergency stop"
    if name == "system_servo_center":
        return "Centering the servos"
    if name == "system_servo_release":
        return "Releasing the servos"
    if name == "ugv_lights_on":
        w = str(args.get("which", "both"))
        return "Turning on the lights" if w == "both" else f"Turning on the {w} light"
    if name == "ugv_lights_off":
        w = str(args.get("which", "both"))
        return "Turning off the lights" if w == "both" else f"Turning off the {w} light"
    if name == "ugv_lights_set":
        return "Adjusting light brightness"
    if name == "ugv_explore_start":
        return "Starting auto-exploration"
    if name == "ugv_explore_stop":
        return "Stopping auto-exploration"
    if name == "ugv_explore_status":
        return ""
    if name == "mission_done":
        reason = str(args.get("reason", "")).strip()
        return reason or "Mission complete"
    if name == "mission_fail":
        reason = str(args.get("reason", "")).strip()
        return f"Mission failed: {reason}" if reason else "Mission failed"
    if name == "ask_user":
        return ""
    return ""


def _function_response_part(name: str, result: dict[str, Any]) -> genai_types.Part:
    return genai_types.Part.from_function_response(name=name, response=result)


async def _poll_nav_status_once() -> Optional[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.post(f"{config.TOOLS_API_URL}/tool/nav_status", json={})
        if r.status_code != 200:
            return None
        body = r.json()
        return body.get("result", body) if isinstance(body, dict) else None
    except Exception:
        return None


async def _poll_explore_status_once() -> Optional[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.post(f"{config.TOOLS_API_URL}/tool/ugv_explore_status", json={})
        if r.status_code != 200:
            return None
        body = r.json()
        return body.get("result", body) if isinstance(body, dict) else None
    except Exception:
        return None


async def _wait_for_nav_completion(m: Mission) -> None:
    # Embodied wait: sleep until Nav2 reports terminal status (succeeded/aborted/
    # canceled) or timeout. Zero Vertex calls + zero step burn during this phase.
    # Agent effectively goes dormant while the robot drives itself there.
    t0 = time.time()
    last_status: Optional[str] = None
    wake_reason = "timeout"
    timeout = config.NAV_WAIT_TIMEOUT_S
    interval = config.NAV_POLL_INTERVAL_S
    while m.status == "active" and (time.time() - t0) < timeout:
        await asyncio.sleep(interval)
        data = await _poll_nav_status_once()
        if data is None:
            continue
        status = data.get("status")
        if status != last_status:
            m.add_event({
                "step": m.step_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "nav_wait": {
                    "status": status,
                    "distance_remaining_m": data.get("distance_remaining_m"),
                },
            })
            last_status = status
        if status in ("succeeded", "aborted", "canceled"):
            wake_reason = status
            break
    m.add_event({
        "step": m.step_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nav_wait_end": {
            "reason": wake_reason,
            "elapsed_s": round(time.time() - t0, 1),
        },
    })


async def _wait_for_explore_completion(m: Mission) -> None:
    # Embodied wait for auto-explore: dormant while orchestrator is EXPLORING.
    # Wake on transition to any non-EXPLORING state or timeout. This avoids the
    # "model polls ugv_explore_status 20x and burns steps" pattern.
    t0 = time.time()
    last_state: Optional[str] = None
    wake_reason = "timeout"
    timeout = config.EXPLORE_WAIT_TIMEOUT_S
    interval = config.EXPLORE_POLL_INTERVAL_S
    while m.status == "active" and (time.time() - t0) < timeout:
        await asyncio.sleep(interval)
        data = await _poll_explore_status_once()
        if data is None:
            continue
        state = data.get("state")
        if state != last_state:
            m.add_event({
                "step": m.step_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "explore_wait": {
                    "state": state,
                    "goals_reached": data.get("goals_reached"),
                    "goals_aborted": data.get("goals_aborted"),
                    "frontiers_found": data.get("frontiers_found"),
                },
            })
            last_state = state
        if state and state != "EXPLORING":
            wake_reason = state
            break
    m.add_event({
        "step": m.step_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "explore_wait_end": {
            "reason": wake_reason,
            "elapsed_s": round(time.time() - t0, 1),
        },
    })


def _extract_text(content: genai_types.Content) -> str:
    chunks: list[str] = []
    for p in getattr(content, "parts", []) or []:
        t = getattr(p, "text", None)
        if t:
            chunks.append(t)
    return "".join(chunks).strip()


def _extract_function_calls(content: genai_types.Content) -> list[Any]:
    calls = []
    for p in getattr(content, "parts", []) or []:
        fc = getattr(p, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            calls.append(fc)
    return calls


async def _call_model_with_backoff(
    contents: list[genai_types.Content],
    system_prompt: str,
) -> Any:
    gen_config = genai_types.GenerateContentConfig(system_instruction=system_prompt)
    try:
        gen_config.thinking_config = genai_types.ThinkingConfig(
            thinking_budget=config.VERTEX_THINKING_BUDGET,
        )
    except Exception:
        pass

    attempts = 0
    while True:
        try:
            return await vertex_client.generate_content_async(
                contents=contents,
                tools=tools_decl.ALL,
                config_obj=gen_config,
            )
        except vertex_client.RateLimitError:
            attempts += 1
            if attempts > config.RATE_LIMIT_MAX_RETRIES:
                raise
            asyncio.create_task(_speak_sentence("My reasoning is temporarily rate-limited. Pausing."))
            log.warning("429 from Vertex; sleeping %ss (attempt %d)",
                        config.RATE_LIMIT_BACKOFF_S, attempts)
            await asyncio.sleep(config.RATE_LIMIT_BACKOFF_S)


async def run_mission(m: Mission) -> None:
    system_prompt = _build_system_prompt()

    m.contents.append(_content("user", [_user_part(
        f"Mission from operator '{m.operator}':\n{m.text}\n\n"
        "You will receive a fresh observation each step. Begin."
    )]))

    try:
        while m.status == "active" and m.step_count < config.ER_MAX_STEPS:
            obs_parts = await sensors.gather_observation()
            obs_parts.append(_user_part(
                f"Step {m.step_count}. Reply in TWO parts, in this exact order:\n"
                "1) ONE short first-person sentence of spoken narration describing what you are "
                "about to do on this step (e.g. 'I'm rotating right to see the hallway.', "
                "'Driving forward one meter, LiDAR is clear.', 'Checking what's behind me.'). "
                "This sentence is read aloud to the operator over the robot's speaker — it is "
                "their only feedback. Do NOT skip it. Do NOT name tools or arguments. Plain "
                "spoken English only.\n"
                "2) THEN your tool call(s) for this step.\n"
                "If you are ending the mission this step, the narration sentence should be a "
                "recap (e.g. 'Found the water jug in the kitchen, mission complete.') followed "
                "by mission_done."
            ))
            m.contents.append(_content("user", obs_parts))

            try:
                resp = await _call_model_with_backoff(m.contents, system_prompt)
            except vertex_client.RateLimitError as e:
                m.status = "failed"
                m.end_reason = f"rate_limited: {e}"
                m.add_event({
                    "step": m.step_count,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": "rate_limited",
                    "detail": str(e),
                })
                _fire_and_forget_speak("I'm stopping — rate limited and couldn't recover.")
                return
            except Exception as e:
                m.status = "failed"
                m.end_reason = f"vertex_error: {type(e).__name__}: {e}"
                m.add_event({
                    "step": m.step_count,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": "vertex_error",
                    "detail": str(e),
                })
                log.exception("vertex_client error in mission %s", m.id)
                return

            candidates = getattr(resp, "candidates", None) or []
            if not candidates:
                m.status = "failed"
                m.end_reason = "empty_response"
                return
            assistant_content = getattr(candidates[0], "content", None)
            if assistant_content is None:
                m.status = "failed"
                m.end_reason = "empty_content"
                return

            m.contents.append(assistant_content)

            assistant_text = _extract_text(assistant_content)
            function_calls = _extract_function_calls(assistant_content)

            # Fall back to tool-synthesized narration when the model goes silent
            # (ER 1.6 often does on action steps even under forceful prompting).
            if not assistant_text and function_calls:
                assistant_text = _synthesize_narration(function_calls)

            if assistant_text:
                m.last_assistant_text = assistant_text
                _fire_and_forget_speak(assistant_text)

            tool_call_log: list[dict[str, Any]] = []
            nav_kicked = False
            explore_kicked = False
            if function_calls:
                response_parts: list[genai_types.Part] = []
                for fc in function_calls:
                    name = getattr(fc, "name", "") or ""
                    args = dict(getattr(fc, "args", {}) or {})
                    t0 = time.time()
                    result = await tools_exec.execute(fc, m)
                    dt = round(time.time() - t0, 3)
                    tool_call_log.append({
                        "name": name,
                        "args": args,
                        "ok": bool(result.get("ok")),
                        "latency_s": dt,
                    })
                    response_parts.append(_function_response_part(name, result))
                    if name == "nav_goto_point":
                        # The tools_api server wraps handler returns as
                        # {"tool": <name>, "result": {...}}, so the "accepted"
                        # key lives two levels down inside result["data"]["result"].
                        data = result.get("data") if isinstance(result, dict) else None
                        inner = data.get("result") if isinstance(data, dict) else None
                        if isinstance(inner, dict) and inner.get("accepted"):
                            nav_kicked = True
                    elif name == "ugv_explore_start":
                        data = result.get("data") if isinstance(result, dict) else None
                        inner = data.get("result") if isinstance(data, dict) else None
                        if isinstance(inner, dict) and inner.get("ok"):
                            explore_kicked = True
                    if m.status != "active":
                        break
                if response_parts:
                    m.contents.append(_content("user", response_parts))

            m.add_event({
                "step": m.step_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_calls": tool_call_log,
                "assistant_text": assistant_text,
                "sensors_hash": _hash_parts(obs_parts),
            })

            m.step_count += 1

            if m.status != "active":
                return

            if not function_calls and not assistant_text:
                m.status = "failed"
                m.end_reason = "model_returned_nothing"
                return

            # Embodied wait: if the model just kicked off a Nav2 goal OR started
            # auto-exploration, go dormant until the operation finishes (or times
            # out). No Vertex calls, no step burn during this phase.
            if nav_kicked:
                await _wait_for_nav_completion(m)
            elif explore_kicked:
                await _wait_for_explore_completion(m)

        if m.status == "active":
            m.status = "failed"
            m.end_reason = f"step_limit({config.ER_MAX_STEPS})"
            _fire_and_forget_speak("I've hit my step limit without finishing. Stopping.")
    except asyncio.CancelledError:
        if m.status == "active":
            m.status = "aborted"
            m.end_reason = "cancelled"
        raise
    except Exception as e:
        m.status = "failed"
        m.end_reason = f"loop_error: {type(e).__name__}: {e}"
        log.exception("run_mission crashed for %s", m.id)
