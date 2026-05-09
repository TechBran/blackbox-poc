#!/bin/bash
# Nav2 startup for Jetson ARM64.
# Ensures clean start by killing stale processes AND verifying they're dead.

source /root/ros2env.sh

echo "[Nav2] Killing stale Nav2 processes..."
# Use multiple kill strategies
pkill -9 -f controller_server 2>/dev/null
pkill -9 -f planner_server 2>/dev/null
pkill -9 -f bt_navigator 2>/dev/null
pkill -9 -f behavior_server 2>/dev/null
pkill -9 -f smoother_server 2>/dev/null
pkill -9 -f velocity_smoother 2>/dev/null
pkill -9 -f waypoint_follower 2>/dev/null
pkill -9 -f lifecycle_manager 2>/dev/null

# Verify they're actually dead
sleep 2
remaining=$(ps aux | grep -E 'controller_server|planner_server|bt_navigator|behavior_server|smoother_server|velocity_smoother|waypoint_follower|lifecycle_manager' | grep -v grep | wc -l)
if [ "$remaining" -gt 0 ]; then
  echo "[Nav2] WARNING: $remaining processes survived, force killing by PID..."
  ps aux | grep -E 'controller_server|planner_server|bt_navigator|behavior_server|smoother_server|velocity_smoother|waypoint_follower|lifecycle_manager' | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null
  sleep 2
fi

echo "[Nav2] Waiting 15s for DDS discovery to clear..."
sleep 15

echo "[Nav2] Launching Nav2 (autostart=true)..."
ros2 launch /home/ws/ugv_ws/nav2_explore.launch.py > /dev/null 2>&1 &
NAV2_PID=$!

echo "[Nav2] Waiting for lifecycle activation..."
for i in $(seq 1 20); do
  sleep 3

  # Check costmaps (the real indicator, not node state)
  local_ok=$(timeout 3 ros2 topic echo /local_costmap/costmap --once 2>/dev/null | head -1)
  if [ -n "$local_ok" ]; then
    echo "[Nav2] Local costmap publishing (${i}x3s = $((i*3))s)"

    # Now check bt_navigator
    state=$(timeout 5 ros2 lifecycle get /bt_navigator 2>/dev/null)
    if echo "$state" | grep -q "active"; then
      echo "[Nav2] bt_navigator: ACTIVE"
      break
    fi
  fi
  echo "[Nav2] Waiting... ($((i*3))s)"
done

# Final verification
sleep 2
timeout 5 ros2 topic echo /local_costmap/costmap --once > /dev/null 2>&1
if [ $? -eq 0 ]; then echo "[Nav2] Local costmap: OK"; else echo "[Nav2] WARNING: Local costmap not publishing"; fi

timeout 5 ros2 topic echo /global_costmap/costmap --once > /dev/null 2>&1
if [ $? -eq 0 ]; then echo "[Nav2] Global costmap: OK"; else echo "[Nav2] WARNING: Global costmap not publishing"; fi

# Quick goal test
echo "[Nav2] Testing goal pipeline..."
timeout 8 ros2 topic pub /goal_pose geometry_msgs/msg/PoseStamped \
  "{header: {frame_id: 'map'}, pose: {position: {x: 0.3, y: 0.0}, orientation: {w: 1.0}}}" \
  --once > /dev/null 2>&1
sleep 3
cmd_vel=$(timeout 3 ros2 topic echo /cmd_vel --once 2>/dev/null | head -1)
if [ -n "$cmd_vel" ]; then
  echo "[Nav2] Goal test: cmd_vel RECEIVED — navigation working!"
else
  echo "[Nav2] Goal test: NO cmd_vel — navigation may not be working"
fi

# Send a tiny goal to trigger RTAB-Map initial /map publish
# (explore_lite needs /global_costmap/costmap which needs /map)
echo "[Nav2] Sending initial nudge goal to trigger map publish..."
ros2 topic pub /goal_pose geometry_msgs/msg/PoseStamped \
  "{header: {frame_id: 'map'}, pose: {position: {x: 0.15, y: 0.0}, orientation: {w: 1.0}}}" \
  --once > /dev/null 2>&1
sleep 5

echo "[Nav2] === READY ==="
wait $NAV2_PID
