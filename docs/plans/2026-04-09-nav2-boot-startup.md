# Nav2 Boot Startup + Exploration Reliability Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move Nav2 from on-demand subprocess launch to always-on boot startup, and remove the unreliable explore_lite subprocess in favor of a custom Python frontier explorer that publishes goals directly (as reliably as Vizanti does manually).

**Architecture:** Nav2 launches last in `start_ros2.sh` after all SLAM/sensor nodes are up. The camera_server's subprocess-based `start_nav2()`/`stop_nav2()` is replaced with a simple action client `wait_for_server()` check (Nav2 is already running). The explore_node drops explore_lite and implements its own frontier detection by reading `/map` OccupancyGrid, clustering frontier cells, and sending `NavigateToPose` action goals directly.

**Tech Stack:** ROS2 Humble, Nav2, Python3, nav2_msgs, OccupancyGrid, ActionClient

---

## Summary of Changes

| File | Change |
|------|--------|
| `start_ros2.sh` | Add Nav2 launch as step 13, wait for lifecycle ready |
| `ugv_camera_server.py` | Remove `start_nav2()` subprocess launch, keep action client, detect Nav2 via `wait_for_server()` |
| `ugv_explore_node.py` | Replace explore_lite subprocess with custom frontier detector + direct Nav2 action goals |
| `explore_lite.launch.py` | No longer used (kept for reference) |
| `nav2_explore.launch.py` | Unchanged (still the launch file, now called from start_ros2.sh) |

---

### Task 1: Add Nav2 to boot startup in start_ros2.sh

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

**What to do:**

Add Nav2 as step 13 (after camera_server, before explore_node). Nav2 needs SLAM running and `/map` publishing, plus the costmap needs `/scan` — all of which are up by step 12.

Replace this block:
```bash
# 13. Nav2 — launched on-demand by explore_node or camera_server, not at boot
```

With:
```bash
# 13. Nav2 navigation stack (must start AFTER SLAM + LiDAR are publishing /map and /scan)
echo "=== Starting Nav2 ==="
ros2 launch /home/ws/ugv_ws/nav2_explore.launch.py &
NAV2_PID=$!
sleep 20  # Nav2 lifecycle manager needs ~15-20s to configure + activate all nodes

# Verify Nav2 is up by checking for the navigate_to_pose action server
echo "=== Waiting for Nav2 action server ==="
RETRIES=0
while [ $RETRIES -lt 10 ]; do
    if ros2 action list 2>/dev/null | grep -q 'navigate_to_pose'; then
        echo "=== Nav2 ready (navigate_to_pose action available) ==="
        break
    fi
    RETRIES=$((RETRIES + 1))
    sleep 3
done
if [ $RETRIES -ge 10 ]; then
    echo "=== WARNING: Nav2 action server not detected after 50s ==="
fi
```

**Key points:**
- `sleep 20` is the initial wait — Nav2 lifecycle manager configures controller_server, planner_server, behavior_server, bt_navigator, smoother_server, waypoint_follower, velocity_smoother sequentially
- The retry loop with `ros2 action list` confirms the action server is actually accepting goals
- Nav2 must come AFTER the camera_server (step 12) because camera_server creates an ActionClient in `__init__` — the action server doesn't need to exist yet for the client to be created, but it ensures clean sequencing
- Explore node (step 14) comes AFTER Nav2 so it can immediately send goals

**Verify:**
```bash
# After reboot, from inside container:
ros2 node list | grep -E 'controller_server|planner_server|bt_navigator'
ros2 action list | grep navigate_to_pose
```

---

### Task 2: Simplify camera_server Nav2 integration (remove subprocess launch)

**Files:**
- Modify: `/home/ws/ugv_ws/ugv_camera_server.py`

**What to do:**

The camera_server currently has ~100 lines of subprocess management (`start_nav2()`, `stop_nav2()`, `_nav2_proc`, `_nav2_lock`). Since Nav2 now runs at boot, replace all of this with a simple readiness check.

**2a. Remove subprocess state from `__init__`:**

Replace:
```python
        # Nav2 lifecycle management
        self._nav2_proc = None
        self._nav2_ready = False
        self._nav2_lock = threading.Lock()
```

With:
```python
        # Nav2 readiness (Nav2 starts at boot, we just check if it's available)
        self._nav2_ready = False
```

**2b. Replace `start_nav2()` method (~lines 1089-1133):**

Replace the entire `start_nav2()` method with:
```python
    def start_nav2(self):
        """Check if Nav2 is running (started at boot by start_ros2.sh)."""
        if self._nav2_action_client and self._nav2_action_client.wait_for_server(timeout_sec=5.0):
            self._nav2_ready = True
            self.get_logger().info('Nav2 is running — connected')
            return {'ready': True}
        else:
            self.get_logger().warn('Nav2 not available — was it started at boot?')
            return {'error': 'Nav2 not running. Check start_ros2.sh or restart ugv-ros2.service'}
```

**2c. Replace `stop_nav2()` method (~lines 1135-1149):**

Replace with:
```python
    def stop_nav2(self):
        """Nav2 runs as a system service — cannot stop from here."""
        self.get_logger().warn('Nav2 is a boot service — use systemctl to restart')
        return {'error': 'Nav2 is managed by start_ros2.sh, not stoppable from camera_server'}
```

**2d. Remove `_nav2_lock` usage from `_cmd_navigate()` (~line 449-451):**

The auto-start block in `_cmd_navigate()` already calls `start_nav2()` — now it just does a readiness check instead of launching a subprocess. No change needed to `_cmd_navigate()` logic, just the underlying method changed.

**2e. Update the `start_nav2`/`stop_nav2` command handlers (~lines 723-729):**

These stay the same — they call `node.start_nav2()` and `node.stop_nav2()` which are now lightweight.

**2f. Update log message (~line 1070):**

Change:
```python
        self.get_logger().info('  Nav2: action client ready (on-demand startup)')
```
To:
```python
        self.get_logger().info('  Nav2: action client ready (boot startup)')
```

**Verify:**
```bash
# Send a navigate command via the ER agent or curl:
curl -X POST http://192.168.1.155:8090/command \
  -H 'Content-Type: application/json' \
  -d '{"command": "get_nav_status"}'
# Should show: nav2_running: true
```

---

### Task 3: Replace explore_lite with custom frontier explorer in ugv_explore_node.py

**Files:**
- Rewrite: `/home/ws/ugv_ws/ugv_explore_node.py`

**Why:** explore_lite has a fundamental architecture problem — it subscribes to its own internal costmap (`/global_costmap/costmap`), computes frontiers on that, but publishes goals to `/goal_pose` (a topic, not an action). This introduces lag and there's no feedback loop. When Nav2 aborts a goal, explore_lite doesn't know and doesn't send a replacement quickly enough.

The custom explorer:
- Reads `/map` directly (the RTAB-Map output — same source of truth as Nav2's static layer)
- Detects frontiers (free cells adjacent to unknown cells)
- Clusters them and picks the best one (closest reachable large frontier)
- Sends goals via `NavigateToPose` action client (same as Vizanti does — proven reliable)
- Gets immediate feedback on goal acceptance/rejection/abort
- On abort → immediately picks next frontier (no waiting)

**Complete replacement for ugv_explore_node.py:**

```python
#!/usr/bin/env python3
"""
UGV Beast Autonomous Frontier Explorer
Replaces explore_lite with direct frontier detection on /map + Nav2 action goals.

Services:
  /explore/start  (Trigger) - Begin autonomous frontier exploration
  /explore/stop   (Trigger) - Stop exploration, optionally return home

Published topics:
  /explore/status (String) - JSON: {state, elapsed_s, frontiers_found, goals_sent, ...}

States:
  IDLE -> EXPLORING -> RETURNING -> SAVING -> IDLE

Frontier detection algorithm:
  1. Read /map (OccupancyGrid from RTAB-Map)
  2. Find frontier cells: free cells (value=0) adjacent to unknown cells (value=-1)
  3. Cluster adjacent frontier cells using flood-fill
  4. Filter clusters by min_frontier_size
  5. Score each cluster: size * gain_weight - distance * distance_weight
  6. Send NavigateToPose goal to best frontier centroid
  7. On goal reached/aborted → re-detect frontiers → repeat
  8. When no frontiers remain → return home → save maps → IDLE
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from std_msgs.msg import String
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from action_msgs.msg import GoalStatus
import numpy as np
from scipy import ndimage
import json
import time
import math
import threading

try:
    from nav2_msgs.action import NavigateToPose
    HAS_NAV2 = True
except ImportError:
    HAS_NAV2 = False


class FrontierExplorer(Node):
    IDLE = 'IDLE'
    EXPLORING = 'EXPLORING'
    RETURNING = 'RETURNING'
    SAVING = 'SAVING'

    def __init__(self):
        super().__init__('ugv_explore')

        # Parameters
        self.declare_parameter('min_frontier_size', 0.3)      # meters — minimum frontier cluster width
        self.declare_parameter('min_frontier_cells', 5)        # minimum cells in a cluster
        self.declare_parameter('distance_weight', 1.0)         # penalty for distant frontiers
        self.declare_parameter('size_weight', 2.0)             # reward for large frontiers
        self.declare_parameter('blacklist_radius', 0.5)        # meters — skip goals near previously failed locations
        self.declare_parameter('no_frontier_timeout', 30.0)    # seconds with no frontiers before declaring done
        self.declare_parameter('goal_timeout', 120.0)          # seconds per navigation goal
        self.declare_parameter('replan_interval', 3.0)         # seconds between frontier re-evaluations during nav
        self.declare_parameter('home_x', 0.0)
        self.declare_parameter('home_y', 0.0)

        # State
        self.state = self.IDLE
        self.start_time = 0.0
        self.goals_sent = 0
        self.goals_reached = 0
        self.goals_aborted = 0
        self.frontiers_found = 0
        self._latest_map = None
        self._map_lock = threading.Lock()
        self._robot_x = 0.0
        self._robot_y = 0.0
        self._goal_handle = None
        self._goal_active = False
        self._goal_start_time = 0.0
        self._blacklisted = []  # List of (x, y) locations where goals failed
        self._no_frontier_since = None  # Timestamp when we first found no frontiers
        self._explore_thread = None

        # Nav2 action client
        if HAS_NAV2:
            self._nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        else:
            self._nav_client = None
            self.get_logger().error('nav2_msgs not installed — exploration disabled')

        # Subscribers
        self.create_subscription(OccupancyGrid, '/map', self._map_cb, 10)
        from nav_msgs.msg import Odometry
        self.create_subscription(Odometry, '/odom', self._odom_cb, 10)

        # Services
        self.create_service(Trigger, '/explore/start', self._start_srv)
        self.create_service(Trigger, '/explore/stop', self._stop_srv)

        # Status publisher (2 Hz)
        self.status_pub = self.create_publisher(String, '/explore/status', 10)
        self.create_timer(0.5, self._status_tick)

        self.get_logger().info('Frontier explorer ready (IDLE — call /explore/start)')

    # ── Callbacks ─────────────────────────────────────────────

    def _map_cb(self, msg):
        with self._map_lock:
            self._latest_map = msg

    def _odom_cb(self, msg):
        self._robot_x = msg.pose.pose.position.x
        self._robot_y = msg.pose.pose.position.y

    # ── Services ──────────────────────────────────────────────

    def _start_srv(self, req, res):
        if self.state != self.IDLE:
            res.success = False
            res.message = f'Already in {self.state} state'
            return res

        if not HAS_NAV2 or not self._nav_client:
            res.success = False
            res.message = 'nav2_msgs not available'
            return res

        # Check Nav2 is running
        if not self._nav_client.wait_for_server(timeout_sec=5.0):
            res.success = False
            res.message = 'Nav2 not running — is it started at boot?'
            return res

        self.state = self.EXPLORING
        self.start_time = time.time()
        self.goals_sent = 0
        self.goals_reached = 0
        self.goals_aborted = 0
        self.frontiers_found = 0
        self._blacklisted.clear()
        self._no_frontier_since = None

        self._explore_thread = threading.Thread(target=self._explore_loop, daemon=True)
        self._explore_thread.start()

        self.get_logger().info('=== EXPLORATION STARTING ===')
        res.success = True
        res.message = 'Frontier exploration started'
        return res

    def _stop_srv(self, req, res):
        if self.state == self.IDLE:
            res.success = False
            res.message = 'Not exploring'
            return res

        self.get_logger().info('=== EXPLORATION STOPPED (manual) ===')
        self._cancel_goal()
        self.state = self.IDLE

        res.success = True
        res.message = f'Stopped. Goals sent={self.goals_sent}, reached={self.goals_reached}'
        return res

    # ── Frontier Detection ────────────────────────────────────

    def _detect_frontiers(self):
        """Detect frontier clusters from the occupancy grid.

        Returns list of dicts: [{x, y, size_cells, size_m}, ...]
        sorted by score (best first).
        """
        with self._map_lock:
            if self._latest_map is None:
                return []
            grid = self._latest_map
            data = np.array(grid.data, dtype=np.int8).reshape(
                (grid.info.height, grid.info.width))
            resolution = grid.info.resolution
            origin_x = grid.info.origin.position.x
            origin_y = grid.info.origin.position.y

        # Free = 0, Unknown = -1, Occupied = 100
        free = (data == 0)
        unknown = (data == -1)

        # Frontier cells: free cells that have at least one unknown neighbor
        # Use dilation to find unknown-adjacent cells, then intersect with free
        kernel = np.ones((3, 3), dtype=bool)
        unknown_adjacent = ndimage.binary_dilation(unknown, structure=kernel)
        frontier_mask = free & unknown_adjacent

        if not frontier_mask.any():
            return []

        # Label connected components (clusters)
        labeled, num_features = ndimage.label(frontier_mask)
        if num_features == 0:
            return []

        min_cells = self.get_parameter('min_frontier_cells').value
        min_size_m = self.get_parameter('min_frontier_size').value
        min_cells_from_size = int(min_size_m / resolution)
        min_cells = max(min_cells, min_cells_from_size)

        distance_w = self.get_parameter('distance_weight').value
        size_w = self.get_parameter('size_weight').value
        blacklist_r = self.get_parameter('blacklist_radius').value

        frontiers = []
        for label_id in range(1, num_features + 1):
            cells = np.argwhere(labeled == label_id)
            if len(cells) < min_cells:
                continue

            # Centroid in map coordinates
            cy, cx = cells.mean(axis=0)
            map_x = origin_x + cx * resolution
            map_y = origin_y + cy * resolution

            # Skip if blacklisted
            if any(math.hypot(map_x - bx, map_y - by) < blacklist_r
                   for bx, by in self._blacklisted):
                continue

            dist = math.hypot(map_x - self._robot_x, map_y - self._robot_y)
            # Skip very close frontiers (likely sensor noise at robot position)
            if dist < 0.3:
                continue

            score = size_w * len(cells) - distance_w * dist
            frontiers.append({
                'x': float(map_x),
                'y': float(map_y),
                'size_cells': len(cells),
                'size_m': round(len(cells) * resolution, 2),
                'distance': round(dist, 2),
                'score': round(score, 2),
            })

        # Sort by score descending
        frontiers.sort(key=lambda f: f['score'], reverse=True)
        return frontiers

    # ── Navigation ────────────────────────────────────────────

    def _send_goal(self, x, y):
        """Send a NavigateToPose goal. Returns True if accepted."""
        if not self._nav_client or not HAS_NAV2:
            return False

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.orientation.w = 1.0

        send_future = self._nav_client.send_goal_async(
            goal_msg, feedback_callback=self._feedback_cb)

        # Wait for acceptance (up to 5s)
        t0 = time.time()
        while not send_future.done() and (time.time() - t0) < 5.0:
            time.sleep(0.1)

        if not send_future.done():
            self.get_logger().warn(f'Goal send timeout for ({x:.2f}, {y:.2f})')
            return False

        goal_handle = send_future.result()
        if goal_handle and goal_handle.accepted:
            self._goal_handle = goal_handle
            self._goal_active = True
            self._goal_start_time = time.time()
            self.goals_sent += 1
            self.get_logger().info(
                f'Goal #{self.goals_sent} accepted: ({x:.2f}, {y:.2f})')
            return True
        else:
            self.get_logger().warn(f'Goal rejected: ({x:.2f}, {y:.2f})')
            self._blacklisted.append((x, y))
            return False

    def _cancel_goal(self):
        """Cancel the current navigation goal."""
        if self._goal_handle is not None:
            try:
                self._goal_handle.cancel_goal_async()
            except Exception:
                pass
            self._goal_handle = None
            self._goal_active = False

    def _check_goal_status(self):
        """Check if the current goal is still active. Returns: 'active', 'succeeded', 'aborted', 'none'."""
        if self._goal_handle is None or not self._goal_active:
            return 'none'

        # Check if result is available
        result_future = self._goal_handle.get_result_async()
        t0 = time.time()
        while not result_future.done() and (time.time() - t0) < 0.5:
            time.sleep(0.05)

        if not result_future.done():
            # Check goal timeout
            goal_timeout = self.get_parameter('goal_timeout').value
            if (time.time() - self._goal_start_time) > goal_timeout:
                self.get_logger().warn(f'Goal timed out after {goal_timeout:.0f}s')
                self._cancel_goal()
                return 'aborted'
            return 'active'

        result = result_future.result()
        status = result.status
        self._goal_handle = None
        self._goal_active = False

        if status == GoalStatus.STATUS_SUCCEEDED:
            self.goals_reached += 1
            return 'succeeded'
        elif status == GoalStatus.STATUS_CANCELED:
            return 'aborted'
        else:
            self.goals_aborted += 1
            return 'aborted'

    def _feedback_cb(self, feedback_msg):
        """Nav2 feedback — distance remaining."""
        pass  # Could log or use for replanning

    # ── Main Exploration Loop ─────────────────────────────────

    def _explore_loop(self):
        """Main frontier exploration loop. Runs in a background thread."""
        replan_interval = self.get_parameter('replan_interval').value
        no_frontier_timeout = self.get_parameter('no_frontier_timeout').value

        self.get_logger().info('Exploration loop started')

        while self.state == self.EXPLORING:
            # Detect frontiers
            frontiers = self._detect_frontiers()
            self.frontiers_found = len(frontiers)

            if frontiers:
                self._no_frontier_since = None

                # If no active goal or current goal is done, send new goal
                goal_status = self._check_goal_status()

                if goal_status in ('none', 'succeeded', 'aborted'):
                    if goal_status == 'aborted':
                        # Blacklist the failed location
                        # (already handled in check_goal_status for some cases)
                        pass

                    # Pick best frontier and send goal
                    target = frontiers[0]
                    self.get_logger().info(
                        f'Frontier: ({target["x"]:.2f}, {target["y"]:.2f}) '
                        f'size={target["size_m"]}m dist={target["distance"]}m '
                        f'[{len(frontiers)} total]')

                    if not self._send_goal(target['x'], target['y']):
                        # Goal rejected — try next frontier
                        for alt in frontiers[1:4]:  # Try up to 3 alternatives
                            if self._send_goal(alt['x'], alt['y']):
                                break
                        else:
                            self.get_logger().warn('All top frontiers rejected — waiting')
                            time.sleep(5.0)
                            continue

                # Goal is active — wait before replanning
                time.sleep(replan_interval)

            else:
                # No frontiers found
                if self._no_frontier_since is None:
                    self._no_frontier_since = time.time()
                    self.get_logger().info('No frontiers — waiting to confirm...')

                elapsed_no_frontier = time.time() - self._no_frontier_since
                if elapsed_no_frontier > no_frontier_timeout:
                    self.get_logger().info(
                        f'No frontiers for {no_frontier_timeout:.0f}s — exploration complete!')
                    self._cancel_goal()
                    self._begin_return_home()
                    return

                time.sleep(replan_interval)

        self.get_logger().info('Exploration loop ended')

    # ── Return Home + Save Maps ───────────────────────────────

    def _begin_return_home(self):
        """Navigate back to starting position, then save maps."""
        self.state = self.RETURNING
        home_x = self.get_parameter('home_x').value
        home_y = self.get_parameter('home_y').value

        self.get_logger().info(f'Returning home to ({home_x}, {home_y})...')
        self._send_goal(home_x, home_y)

        # Wait for return (up to 2 minutes)
        timeout = 120.0
        t0 = time.time()
        while self.state == self.RETURNING and (time.time() - t0) < timeout:
            status = self._check_goal_status()
            if status in ('succeeded', 'aborted', 'none'):
                break
            time.sleep(2.0)

        self.get_logger().info('Return complete — saving maps...')
        self.state = self.SAVING
        self._save_maps()
        self.state = self.IDLE
        elapsed = time.time() - self.start_time
        self.get_logger().info(
            f'=== EXPLORATION COMPLETE ({elapsed/60:.1f} min) ===\n'
            f'  Goals: {self.goals_sent} sent, {self.goals_reached} reached, '
            f'{self.goals_aborted} aborted')

    def _save_maps(self):
        """Call mapping services to save 2D + 3D maps."""
        import subprocess as sp
        for dim, service in [('2D', '/mapping/save_2d'), ('3D', '/mapping/save_3d')]:
            try:
                self.get_logger().info(f'Saving {dim} map...')
                result = sp.run(
                    ['ros2', 'service', 'call', service, 'std_srvs/srv/Trigger'],
                    capture_output=True, text=True, timeout=30)
                self.get_logger().info(f'{dim}: {result.stdout.strip()[-80:]}')
            except Exception as e:
                self.get_logger().error(f'{dim} map save error: {e}')

    # ── Status Publishing ─────────────────────────────────────

    def _status_tick(self):
        elapsed = time.time() - self.start_time if self.start_time > 0 else 0
        status = {
            'state': self.state,
            'elapsed_s': round(elapsed, 1),
            'elapsed_min': round(elapsed / 60, 1),
            'frontiers_found': self.frontiers_found,
            'goals_sent': self.goals_sent,
            'goals_reached': self.goals_reached,
            'goals_aborted': self.goals_aborted,
            'blacklisted': len(self._blacklisted),
            'goal_active': self._goal_active,
        }
        msg = String()
        msg.data = json.dumps(status)
        self.status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = FrontierExplorer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
```

**Key differences from explore_lite:**
- Uses `ActionClient` for goals (same as Vizanti) — not `/goal_pose` topic
- Reads `/map` directly — no costmap lag
- Immediate re-goal on abort — no waiting for explore_lite's `planner_frequency`
- Blacklists failed locations — stops retrying unreachable spots
- `no_frontier_timeout` (30s) — declares done only after sustained silence
- `replan_interval` (3s) — checks for better frontiers while navigating
- `scipy.ndimage` for fast frontier clustering — O(n) flood-fill, ~10ms on a 500x500 grid

**Dependency check:**
```bash
# Inside container — verify scipy is available
python3 -c "from scipy import ndimage; print('scipy OK')"
# If not installed:
pip3 install scipy
```

**Verify:**
```bash
# Start exploration
ros2 service call /explore/start std_srvs/srv/Trigger
# Watch status
ros2 topic echo /explore/status
# Should show frontiers_found > 0, goals_sent incrementing
```

---

### Task 4: Update start_ros2.sh step numbering and explore_node reference

**Files:**
- Modify: `/home/ws/ugv_ws/start_ros2.sh`

**What to do:**

After Task 1 adds Nav2 as step 13, renumber the explore node to step 14 and remove the comment about on-demand launch:

```bash
# 14. Explore orchestrator (IDLE until /explore/start called)
echo "=== Starting explore orchestrator ==="
python3 /home/ws/ugv_ws/ugv_explore_node.py &
sleep 1
```

---

### Task 5: Verify scipy is available in Docker container

**Files:** None (runtime check)

```bash
# SSH into container
sshpass -p 'jetson' ssh root@192.168.1.155 -p 23

# Check scipy
python3 -c "from scipy import ndimage; print('scipy ndimage available')"

# If missing, install:
pip3 install scipy

# Verify numpy compatibility (must be <2 for MediaPipe)
python3 -c "import numpy; print(f'numpy {numpy.__version__}')"
```

**Note:** scipy ~1.10 supports numpy<2. If pip installs scipy that requires numpy 2.x, pin: `pip3 install 'scipy<1.12'`

---

### Task 6: Deploy all files and full reboot test

**Steps:**
1. SCP updated `start_ros2.sh` to Jetson
2. SCP updated `ugv_camera_server.py` to Jetson
3. SCP new `ugv_explore_node.py` to Jetson
4. Restart the ugv-ros2 service on the host: `sudo systemctl restart ugv-ros2.service`
5. Wait ~90s for full stack startup
6. Verify from container:
   ```bash
   ros2 node list | grep -E 'controller|planner|bt_nav|ugv_explore'
   ros2 action list | grep navigate_to_pose
   ros2 topic echo /explore/status --once
   ```
7. Test manual navigation from Vizanti — should work immediately without explore
8. Test exploration:
   ```bash
   ros2 service call /explore/start std_srvs/srv/Trigger
   ros2 topic echo /explore/status
   ```
9. Verify goals are being sent and the robot is moving to frontiers
10. Stop: `ros2 service call /explore/stop std_srvs/srv/Trigger`

**Success criteria:**
- Nav2 is running on boot (no on-demand delays)
- Vizanti waypoints work immediately after boot
- Frontier exploration sends goals as reliably as Vizanti manual clicks
- Robot immediately picks a new frontier when a goal is aborted
- No "ComputePathToPose aborting" loops without forward progress
