# Autonomous Exploration (Roomba Mode) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** On-command autonomous exploration that maps the entire environment using Nav2 + explore_lite, then returns to start and auto-saves the map. Triggered via service call, controllable from Foxglove + Vizanti + CLI.

**Architecture:** RTAB-Map continues as SLAM backend (already running). Nav2 provides path planning + obstacle avoidance using LiDAR costmap. explore_lite handles frontier detection (finds unexplored areas). A custom `ugv_explore_node.py` orchestrates the workflow: start → explore → detect complete → return home → save maps → idle.

**Tech Stack:** Nav2 (already installed), explore_lite (already installed), RTAB-Map (already running), nav2_simple_commander Python API

**Target Machine:** Jetson Orin Nano 8GB @ `192.168.1.155` (SSH port 23 into container)

---

## Context

The robot has clean SLAM mapping (RTAB-Map + LiDAR at 640x480, rf2o odom with filter) and YOLO detection on both cameras. The next step is autonomous navigation — "Roomba mode" where the robot explores the entire environment, builds a complete map, and returns home.

## User Requirements
- Triggered on command (NOT auto-start)
- Return to (0,0) start position when done
- Auto-save both 2D + 3D maps when complete
- LiDAR-only costmap (depth camera for safety braking only — deferred)
- YOLO keeps running independently during exploration
- Moderate 15cm inflation radius (doorways OK, avoids tight gaps)
- Slow exploration speed: 0.15 m/s
- Controllable from Foxglove + Vizanti + CLI

## Architecture

```
                       ┌─────────────────────┐
                       │  ugv_explore_node.py │
                       │  (orchestrator)       │
                       │                       │
  /explore/start ─────►│  State machine:       │
  /explore/stop  ─────►│  IDLE → EXPLORING →   │
  (Trigger services)   │  RETURNING → SAVING → │
                       │  IDLE                  │
                       └───────┬───────────────┘
                               │ starts/stops
                       ┌───────▼───────────────┐
                       │    explore_lite         │
                       │  (frontier detection)   │
                       │  → /goal_pose (Nav2)    │
                       └───────┬───────────────┘
                               │ goals
                       ┌───────▼───────────────┐
                       │    Nav2 Stack           │
                       │  planner + controller   │
                       │  + costmap (LiDAR)      │
                       │  → /cmd_vel             │
                       └────────────────────────┘
```

## Key Design Decisions

- **Nav2 params**: Based on Waveshare's existing `slam_nav.yaml` (found at `/home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml`). Tweaked for: `max_vel_x: 0.15`, `robot_base_frame: base_footprint`, `inflation_radius: 0.15` (moderate), `robot_radius: 0.15`.
- **No bringup in Nav2 launch**: Bringup already running from start_ros2.sh. Nav2 launch starts ONLY navigation nodes.
- **explore_lite** subscribes to `/map` (from RTAB-Map) and publishes goals to Nav2's `/goal_pose`.
- **On-demand**: Nav2 + explore_lite start when `/explore/start` service is called, stop when `/explore/stop` is called or exploration completes.
- **Return home**: After no frontiers detected for 30 seconds, nav2_simple_commander sends goal to (0,0,0).
- **Auto-save**: After return home, calls `/mapping/save_2d` and `/mapping/save_3d` services.

---

### Task 1: Create Custom Nav2 Params File

**File:** Create `/home/ws/ugv_ws/nav2_explore_params.yaml`

Based on Waveshare's `slam_nav.yaml` with these changes:
- `robot_base_frame`: `base_link` → `base_footprint` (matches our TF tree)
- `max_vel_x`: 0.26 → **0.15** (slow careful exploration)
- `max_speed_xy`: 0.26 → **0.15**
- `robot_radius`: 0.1 → **0.15** (15cm clearance — moderate)
- Global costmap `inflation_radius`: 0.3 → **0.15**
- Local costmap `inflation_radius`: 0.55 → **0.20**
- `odom_topic`: `/odom` (our filtered odom)
- Remove `static_layer` from global costmap plugins (no pre-loaded map, RTAB-Map builds it live)
- `track_unknown_space: true` (essential for frontier exploration)

**Reference file:** `/home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml`

---

### Task 2: Create Nav2 Navigation Launch (No Bringup)

**File:** Create `/home/ws/ugv_ws/nav2_explore.launch.py`

Launches ONLY the Nav2 navigation stack:
- bt_navigator
- controller_server (DWB)
- planner_server (NavFn)
- behavior_server (spin, backup, wait)
- smoother_server
- velocity_smoother
- costmap nodes (global + local)
- lifecycle_manager to bring them all up

Does NOT include: bringup_lidar, SLAM, robot_state_publisher (already running).

Uses `nav2_bringup/launch/navigation_launch.py` as the included launch (this is the standard Nav2 navigation-only launch).

---

### Task 3: Create ugv_explore_node.py (Exploration Orchestrator)

**File:** Create `/home/ws/ugv_ws/ugv_explore_node.py`

**State machine:**
```
IDLE → (start service) → LAUNCHING → EXPLORING → (no frontiers 30s) → RETURNING → (at home) → SAVING → IDLE
```

**Services:**
- `/explore/start` (Trigger) — Launch Nav2 + explore_lite, begin exploration
- `/explore/stop` (Trigger) — Stop exploration, cancel current goal, shut down Nav2

**Published topics:**
- `/explore/status` (String) — JSON: {state, frontiers_remaining, elapsed_time, distance_traveled}

**Logic:**
1. On `/explore/start`:
   - Launch Nav2 via `subprocess` (ros2 launch)
   - Wait for Nav2 lifecycle nodes to activate
   - Launch explore_lite
   - Transition to EXPLORING state
2. During EXPLORING:
   - Monitor explore_lite's frontier count
   - If no frontiers for 30 seconds → transition to RETURNING
3. During RETURNING:
   - Use nav2_simple_commander to send goal to (0, 0, 0)
   - Wait for goal completion
   - Transition to SAVING
4. During SAVING:
   - Call `/mapping/save_2d` and `/mapping/save_3d` services
   - Transition to IDLE
5. On `/explore/stop`:
   - Cancel any active Nav2 goal
   - Kill Nav2 + explore_lite processes
   - Transition to IDLE

**Parameters:**
- `frontier_timeout`: 30.0 (seconds without frontiers before declaring done)
- `home_x`, `home_y`: 0.0, 0.0 (return position)
- `nav2_params_file`: path to nav2_explore_params.yaml

---

### Task 4: Create explore_lite Launch Config

**File:** Create `/home/ws/ugv_ws/explore_lite.launch.py`

```python
Node(
    package='explore_lite',
    executable='explore',
    name='explore_lite',
    parameters=[{
        'robot_base_frame': 'base_footprint',
        'costmap_topic': '/global_costmap/costmap',
        'visualize': True,
        'planner_frequency': 0.5,  # Plan new frontier every 2 seconds
        'progress_timeout': 30.0,
        'potential_scale': 3.0,
        'orientation_scale': 0.0,  # Don't care about facing direction
        'gain_scale': 1.0,
        'transform_tolerance': 0.3,
        'min_frontier_size': 0.5,  # Minimum frontier length (meters) to pursue
    }],
)
```

---

### Task 5: Add Explore Mode to start_ros2.sh

**NOT auto-starting Nav2**. The explore node starts at boot but stays in IDLE state, waiting for the `/explore/start` service call.

Add after tracker node:
```bash
# 12. Explore orchestrator (IDLE until /explore/start called)
echo "=== Starting explore orchestrator ==="
python3 /home/ws/ugv_ws/ugv_explore_node.py &
sleep 2
```

---

### Task 6: Deploy and Test

1. Deploy all files to Jetson
2. Reboot
3. Verify explore node is in IDLE state: `ros2 service list | grep explore`
4. Start exploration: `ros2 service call /explore/start std_srvs/srv/Trigger`
5. Watch in Foxglove/Vizanti — robot should start moving to unexplored areas
6. Verify costmap appears (global + local)
7. Test stop: `ros2 service call /explore/stop std_srvs/srv/Trigger`
8. Full test: let robot explore entire room, verify return to home + auto-save

---

### Task 7: Foxglove + Vizanti Integration

**Foxglove:**
- Add Publish panel for `/explore/start` and `/explore/stop` (type: std_srvs/Trigger)
- Add `/global_costmap/costmap` to 3D panel (shows inflation zones)
- `/plan` topic shows the planned path

**Vizanti:**
- Already supports Nav2 goals — click on map to send manual override goals
- Costmap visualization built-in

---

## Dependency Chain

```
Task 1 (params) → Task 2 (launch) → Task 3 (explore node) → Task 5 (startup) → Task 6 (test)
Task 4 (explore_lite config) → Task 3
Task 7 (UI) — after Task 6
```

## Verification

1. `/explore/start` triggers Nav2 + explore_lite launch
2. Robot autonomously navigates to unexplored frontiers
3. Costmap shows inflated obstacles around walls
4. Robot avoids walls with ~15cm clearance
5. Speed stays at 0.15 m/s
6. After full exploration, robot returns to (0,0)
7. Maps auto-saved (2D + 3D)
8. `/explore/stop` cleanly shuts down navigation
9. YOLO detection continues working during exploration
10. SLAM map quality maintained during autonomous driving
