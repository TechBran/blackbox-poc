# Waypoint Orientation Rewriter + DWB Rotation Tuning

**Date:** 2026-05-04
**Operator:** Brandon
**Goal:** Make the robot's heading at every intermediate waypoint automatically point at the *next* waypoint, so it drives smoothly path-direction-first instead of 360-spinning at each stop. Restore tight goal tolerances (now safe to tighten because rotation is no longer fighting an arbitrary heading target). Increase DWB rotation strength so turning feels appropriately responsive.

## Problem statement

**Symptom (observed during waypoint missions):** robot reaches each intermediate waypoint, then spins 360° trying to align to yaw=0 in the map frame, before proceeding to the next waypoint.

**Root cause:** Vizanti's `waypoints` widget publishes `nav_msgs/Path` with identity quaternion (yaw=0) on every pose. Nav2's waypoint follower respects whatever orientation the publisher specifies, so it tries to reach yaw=0 at every waypoint — which is "back toward map +X axis," typically the robot's startup heading, almost never the path direction.

**Side effect of the workaround:** we set `yaw_goal_tolerance: 3.14` (any direction) to mask the symptom. This means even single nav-to-pose loses heading control, even though Vizanti's `simplegoal` widget DOES capture orientation from drag.

**Side note:** rotation feels weak at our current DWB params (`max_vel_theta: 0.8`, `acc_lim_theta: 1.6`) — both well below typical Nav2 defaults (`max_vel_theta: 1.0`, `acc_lim_theta: 3.2`). Bumping rotation strength makes turns snappier without affecting linear motion.

## Design decisions

### Decision 1: Integrate orientation rewriter INTO existing `waypoints_bridge.py` (not separate node)

**Why:** Single ROS node, less plumbing, existing bridge already lives in the right place in the pipeline (`Vizanti → /waypoints → waypoints_bridge → Nav2 follow_waypoints action`). Adding a parallel intercept node would require remapping `/waypoints` and adds operational complexity for no architectural benefit. The orientation logic is ~25 lines.

### Decision 2: Final-waypoint heading mode = `face_last_segment` (configurable)

**Why:** Three options for the FINAL waypoint:
- `face_last_segment`: extrapolate direction from the second-to-last → last segment (robot ends facing along path direction)
- `preserve_input`: keep whatever orientation Vizanti published (identity = yaw=0 = useless for current Vizanti)
- `specified`: a configured fixed yaw (rare use case)

Default `face_last_segment` is the most natural for free-form waypoint missions. Configurable via parameter for future flexibility (e.g., when docking, the dock approach pose has a specific final heading).

### Decision 3: Restore `yaw_goal_tolerance` to 0.35 rad (~20°)

**Why now safe:** With the orientation rewriter, each waypoint's target heading IS the path direction. The robot approaches with heading already roughly aligned. So at goal-arrival, only minor yaw correction needed (well within 0.35 rad braking distance + IMU noise).

### Decision 4: Bump DWB rotation params

| Parameter | Current | New | Reason |
|---|---|---|---|
| `max_vel_theta` | 0.8 rad/s | 1.2 rad/s | ~69°/s — closer to Nav2 default 1.0, still safe for tracked robot |
| `acc_lim_theta` | 1.6 rad/s² | 2.5 rad/s² | Faster ramp-up — turns feel responsive |
| `decel_lim_theta` | -1.6 rad/s² | -2.5 rad/s² | Faster brake to land within tighter yaw tolerance |
| `rotate_to_heading_angular_vel` | 0.8 rad/s | 1.2 rad/s | RotationShim turns faster |
| `max_angular_accel` (RotationShim) | 1.6 rad/s² | 2.5 rad/s² | Match DWB |

**Braking distance check:** `v² / (2a) = 1.2² / (2 × 2.5) = 0.288 rad ≈ 16.5°`. Yaw tolerance 0.35 rad = 20°. Margin = 3.5° — tight but safe with the orientation rewriter eliminating most rotation needs at waypoints.

### Decision 5: Keep `xy_goal_tolerance` at 0.20 m

**Why:** Already at the localization noise floor + margin. Tightening below 0.15 m caused orbit-around-goal earlier today. 0.20 m is the empirically-validated sweet spot for this hardware.

## Implementation

### File: `ugv_tools_api/ugv_tools_api/nodes/waypoints_bridge.py`

**Add imports:**
```python
import math
from typing import List
from geometry_msgs.msg import PoseStamped
```

**Add parameters in `__init__`:**
```python
self.declare_parameter("orient_intermediate", True)
self.declare_parameter("final_orient_mode", "face_last_segment")
# Modes: "face_last_segment", "preserve_input"
self._orient_intermediate = self.get_parameter("orient_intermediate").value
self._final_mode = self.get_parameter("final_orient_mode").value
```

**Add rewriter call in `_on_path` before dispatching:**
```python
poses = list(msg.poses)
if self._orient_intermediate and len(poses) >= 2:
    poses = self._reorient_poses(poses)
goal.poses = poses
```

**New helper method:**
```python
def _reorient_poses(self, poses: List[PoseStamped]) -> List[PoseStamped]:
    """Rewrite each pose's quaternion to face the next waypoint.
    
    For 0 or 1 poses, returns input unchanged (nothing to compute).
    For 2+ poses:
      - Each intermediate pose i (0 <= i < n-1) faces pose i+1
      - The final pose's orientation depends on final_orient_mode:
          face_last_segment: faces direction of segment (n-2 -> n-1)
          preserve_input: keeps incoming quaternion as-is
    """
    n = len(poses)
    for i in range(n - 1):
        cur = poses[i].pose.position
        nxt = poses[i + 1].pose.position
        yaw = math.atan2(nxt.y - cur.y, nxt.x - cur.x)
        poses[i].pose.orientation.x = 0.0
        poses[i].pose.orientation.y = 0.0
        poses[i].pose.orientation.z = math.sin(yaw / 2.0)
        poses[i].pose.orientation.w = math.cos(yaw / 2.0)

    if self._final_mode == "face_last_segment":
        prv = poses[n - 2].pose.position
        cur = poses[n - 1].pose.position
        yaw = math.atan2(cur.y - prv.y, cur.x - prv.x)
        poses[n - 1].pose.orientation.x = 0.0
        poses[n - 1].pose.orientation.y = 0.0
        poses[n - 1].pose.orientation.z = math.sin(yaw / 2.0)
        poses[n - 1].pose.orientation.w = math.cos(yaw / 2.0)
    # else preserve_input — leave final pose's quaternion alone
    return poses
```

**Logging enhancement** (to verify it's working):
```python
# In _on_path after self._reorient_poses call:
if self._orient_intermediate and len(poses) >= 2:
    yaws = [math.atan2(2*(p.pose.orientation.w * p.pose.orientation.z),
                       1 - 2*(p.pose.orientation.z**2)) for p in poses]
    yaw_str = ", ".join(f"{math.degrees(y):.0f}°" for y in yaws)
    self.get_logger().info(f"reoriented waypoints: yaws=[{yaw_str}]")
```

### File: `src/ugv_main/ugv_nav/param/slam_nav.yaml`

**Six edits:**

1. `goal_checker.yaw_goal_tolerance`: `3.14` → `0.35`
2. `FollowPath.max_vel_theta`: `0.8` → `1.2`
3. `FollowPath.acc_lim_theta`: `1.6` → `2.5`
4. `FollowPath.decel_lim_theta`: `-1.6` → `-2.5`
5. `FollowPath.rotate_to_heading_angular_vel`: `0.8` → `1.2`
6. `FollowPath.max_angular_accel`: `1.6` → `2.5`

### Restart sequence

`sudo systemctl restart ugv-waveshare.service` — both the waypoints_bridge (in ugv-tools-api) and the Nav2 stack (in ugv-waveshare) need to reload. tools-api restart auto-cascades when waveshare restarts.

Wait — actually the bridge is in `ugv-tools-api.service`, not `ugv-waveshare.service`. So we need to restart BOTH. Check during deploy.

## Test plan

After deploy, send a multi-waypoint mission and verify:

| Test | Pass criteria |
|---|---|
| `ros2 topic echo /rosout \| grep waypoint_bridge` shows reorient log | Confirms rewriter ran |
| Robot drives waypoint A → B → C without 360 spin at B | Visual + map-based confirmation |
| At each waypoint, robot heading ≈ direction toward next waypoint | Echo `/odom` orientation, check yaw ≈ atan2(next-cur) |
| Final waypoint: robot stops facing path direction (not arbitrary) | Visual check |
| Single nav-to-pose: heading still respected | Send pose with simplegoal, verify final orientation matches |
| Rotation feels snappier (subjective) | Brandon driving feedback |
| No oscillation around yaw goal | Robot lands and stops cleanly within 1-2 rotations |

## Rollback path

Each change is a single line in YAML or a parameter setting. Rollback steps if anything misbehaves:
1. Set `orient_intermediate: False` parameter on waypoints_bridge → orientation rewriter disabled, falls back to previous identity-quaternion behavior
2. `yaw_goal_tolerance` back to `3.14` → restore earlier "no yaw enforcement" mode
3. Revert DWB rotation params if rotation feels too aggressive

All parameters are at known-good values from earlier in the session, fully recoverable in <2 minutes.

## Out of scope (queued for future)

- AMCL toggle button (still queued from earlier session)
- Vizanti widget UX improvement (per-waypoint orientation drag) — this rewriter makes it unnecessary for the common "drive through points" case
- DWB critics retuning — current `GoalAlign: 0` is correct per project memory; not changing critics

## Search hints

- 'waypoint orientation rewriter atan2 face next'
- 'waypoints_bridge intermediate pose rewriter'
- 'DWB rotation strength tracked robot tuning'
- 'yaw_goal_tolerance restore after orientation rewriter'

## Implementation Log

### 2026-05-04 — Landed in single session

- **`waypoints_bridge.py`** — added imports (`math`, `typing.List`, `geometry_msgs.msg.PoseStamped`), declared two new parameters (`orient_intermediate=True`, `final_orient_mode="face_last_segment"`), added `_reorient_poses` helper method, added log line confirming yaws after rewrite. Module-level helpers `_set_yaw` and `_yaw_from_q` for clean code structure.
- **`slam_nav.yaml`** — six edits applied via sed:
  - `goal_checker.yaw_goal_tolerance: 3.14 → 0.35`
  - `FollowPath.max_vel_theta: 0.8 → 1.2`
  - `FollowPath.acc_lim_theta: 1.6 → 2.5`
  - `FollowPath.decel_lim_theta: -1.6 → -2.5`
  - `FollowPath.rotate_to_heading_angular_vel: 0.8 → 1.2`
  - `FollowPath.max_angular_accel: 1.6 → 2.5`
- Restarted `ugv-tools-api.service` and `ugv-waveshare.service`.
- Verified live params:
  - `goal_checker.yaw_goal_tolerance = 0.35` ✓
  - `FollowPath.max_vel_theta = 1.2` ✓
  - `FollowPath.acc_lim_theta = 2.5` ✓
  - `FollowPath.rotate_to_heading_angular_vel = 1.2` ✓
  - `waypoints_bridge.orient_intermediate = True` ✓
  - `waypoints_bridge.final_orient_mode = face_last_segment` ✓
- Standalone unit test of reorient logic on test path (0,0)→(1,0)→(1,1)→(2,1) produced expected yaws [0°, 90°, 0°, 0°] ✓.

### Pending Brandon validation
- Drive a multi-waypoint mission. Robot should drive smoothly through waypoints without 360 spin at intermediate stops.
- Single nav-to-pose with simplegoal should still respect drag-direction final heading.
- Rotation should feel notably snappier (1.2 rad/s peak vs 0.8 prior).
