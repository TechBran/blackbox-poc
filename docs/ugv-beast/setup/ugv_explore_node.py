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
  7. On goal reached/aborted -> re-detect frontiers -> repeat
  8. When no frontiers remain -> return home -> save maps -> IDLE
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from std_msgs.msg import String
from nav_msgs.msg import OccupancyGrid, Odometry
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
        self.declare_parameter('min_frontier_size', 0.3)
        self.declare_parameter('min_frontier_cells', 5)
        self.declare_parameter('distance_weight', 1.0)
        self.declare_parameter('size_weight', 2.0)
        self.declare_parameter('blacklist_radius', 0.5)
        self.declare_parameter('no_frontier_timeout', 30.0)
        self.declare_parameter('goal_timeout', 120.0)
        self.declare_parameter('replan_interval', 3.0)
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
        self._goal_result_ready = threading.Event()
        self._goal_final_status = None
        self._blacklisted = []
        self._no_frontier_since = None
        self._explore_thread = None

        # Nav2 action client
        if HAS_NAV2:
            self._nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        else:
            self._nav_client = None
            self.get_logger().error('nav2_msgs not installed — exploration disabled')

        # Subscribers
        self.create_subscription(OccupancyGrid, '/map', self._map_cb, 10)
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

        self._goal_result_ready.clear()
        self._goal_final_status = None

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

            # Start background result monitor
            threading.Thread(
                target=self._result_monitor, args=(goal_handle,), daemon=True
            ).start()
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

    def _result_monitor(self, goal_handle):
        """Background thread: wait for Nav2 goal result and signal completion."""
        try:
            result_future = goal_handle.get_result_async()
            timeout = self.get_parameter('goal_timeout').value
            start = time.time()
            while not result_future.done() and (time.time() - start) < timeout:
                time.sleep(0.5)

            if result_future.done():
                result = result_future.result()
                status = result.status
                if status == GoalStatus.STATUS_SUCCEEDED:
                    self._goal_final_status = 'succeeded'
                    self.goals_reached += 1
                    self.get_logger().info('Goal reached!')
                elif status == GoalStatus.STATUS_CANCELED:
                    self._goal_final_status = 'canceled'
                else:
                    self._goal_final_status = 'aborted'
                    self.goals_aborted += 1
                    self.get_logger().warn(f'Goal ended with status {status}')
            else:
                self._goal_final_status = 'timeout'
                self.goals_aborted += 1
                self.get_logger().warn(f'Goal timed out after {timeout:.0f}s')
                self._cancel_goal()
        except Exception as e:
            self._goal_final_status = 'error'
            self.goals_aborted += 1
            self.get_logger().error(f'Result monitor error: {e}')
        finally:
            self._goal_handle = None
            self._goal_active = False
            self._goal_result_ready.set()

    def _wait_for_goal_done(self, timeout=None):
        """Block until the current goal completes or times out.
        Returns the final status string."""
        if timeout is None:
            timeout = self.get_parameter('goal_timeout').value + 5.0
        self._goal_result_ready.wait(timeout=timeout)
        return self._goal_final_status or 'timeout'

    def _feedback_cb(self, feedback_msg):
        """Nav2 feedback — distance remaining."""
        pass

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

                if not self._goal_active:
                    # Pick best frontier and send goal
                    target = frontiers[0]
                    self.get_logger().info(
                        f'Frontier: ({target["x"]:.2f}, {target["y"]:.2f}) '
                        f'size={target["size_m"]}m dist={target["distance"]}m '
                        f'[{len(frontiers)} total]')

                    if not self._send_goal(target['x'], target['y']):
                        # Goal rejected — try alternatives
                        sent = False
                        for alt in frontiers[1:4]:
                            if self._send_goal(alt['x'], alt['y']):
                                sent = True
                                break
                        if not sent:
                            self.get_logger().warn('All top frontiers rejected — waiting')
                            time.sleep(5.0)
                            continue

                    # Wait for this goal to complete before picking next
                    status = self._wait_for_goal_done()
                    self.get_logger().info(f'Goal result: {status}')

                    if status == 'aborted':
                        # Blacklist this location
                        self._blacklisted.append((target['x'], target['y']))

                    # Immediately re-detect frontiers (no sleep)
                    continue

                # Goal still active — sleep before re-checking
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
        if self._send_goal(home_x, home_y):
            status = self._wait_for_goal_done(timeout=120.0)
            self.get_logger().info(f'Return result: {status}')
        else:
            self.get_logger().warn('Could not send return-home goal')

        self.get_logger().info('Saving maps...')
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
