# SmacPlanner2D + TEB Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace NavfnPlanner (Dijkstra point-and-shoot) + DWB (random trajectory sampling) with SmacPlanner2D (cost-aware A*) + TEB (time-elastic band trajectory optimization). Both are designed for differential drive robots and will produce smoother, more predictable navigation.

**Architecture:** SmacPlanner2D generates cost-aware global paths that center in corridors. TEB optimizes a local trajectory along that path, respecting kinematic constraints (forward preference, angular limits) and avoiding obstacles in real-time. RotationShimController wraps TEB for clean in-place rotation when heading error is large.

**Tech Stack:** ROS2 Humble, Nav2, Waveshare ugv_ws (ros2-humble-develop), Waveshare Docker image `dudulrx0601/ugv_jetson_ros_humble:v1028`

**SSH (container):** `sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@192.168.1.155 -p 23`

---

## What Changes

| Component | Current | New |
|-----------|---------|-----|
| Global planner | NavfnPlanner (Dijkstra, no cost awareness) | SmacPlanner2D (cost-aware A*, built-in smoother) |
| Local controller | DWB (sample trajectories, score with critics) | TEB (optimize elastic band trajectory) |
| Rotation handling | RotationShimController + DWB | RotationShimController + TEB (keep shim) |
| Config file | `slam_nav.yaml` (planner_server + controller_server sections) | Same file, updated sections |

## What Stays Unchanged

| Component | Why |
|-----------|-----|
| slam_toolbox (SLAM) | Working perfectly — provides map + map→odom TF |
| bringup_lidar (odometry) | rf2o "radar as IMU" is proven stable |
| base_node (track width 0.150m) | Calibrated and accurate |
| ESP32 PID (Kp=80) | Calibrated for Beast track width |
| Costmap configuration | Local + global costmaps working correctly |
| Gentle recovery BT | No global costmap clearing — proven better |
| Vizanti (5100) + rosbridge (5001) | Web UI working |
| velocity_smoother | Speeds already tuned (0.15 m/s max) |

---

### Task 1: Install SmacPlanner2D Package

SmacPlanner2D is a separate apt package — NOT included in the base Nav2 install.

**Step 1: Install the package**

```bash
apt-get install -y ros-humble-nav2-smac-planner
```

**Step 2: Verify the plugin is loadable**

```bash
source /opt/ros/humble/setup.bash
ros2 pkg list | grep smac
```

Expected: `nav2_smac_planner`

---

### Task 2: Verify TEB is Already Built

TEB is included in Waveshare's workspace as a colcon dependency (`ugv_else/teb_local_planner`). It should already be compiled from `build_first.sh`.

**Step 1: Check TEB exists in install**

```bash
ls /home/ws/ugv_ws/install/teb_local_planner/
```

Expected: `lib/`, `share/`, etc.

**Step 2: Verify the plugin is loadable**

```bash
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
ros2 pkg list | grep teb
```

Expected: `teb_local_planner`

---

### Task 3: Update slam_nav.yaml — Global Planner

Replace the NavfnPlanner section with SmacPlanner2D.

**Current (NavfnPlanner):**
```yaml
planner_server:
  ros__parameters:
    expected_planner_frequency: 5.0
    use_sim_time: false
    planner_plugins: ["GridBased"]
    GridBased:
      plugin: "nav2_navfn_planner/NavfnPlanner"
      tolerance: 0.5
      use_astar: false
      allow_unknown: true
      use_final_approach_orientation: True
```

**New (SmacPlanner2D):**
```yaml
planner_server:
  ros__parameters:
    expected_planner_frequency: 5.0
    use_sim_time: false
    planner_plugins: ["GridBased"]
    GridBased:
      plugin: "nav2_smac_planner/SmacPlanner2D"
      tolerance: 0.125
      downsample_costmap: false
      downsampling_factor: 1
      allow_unknown: true
      max_iterations: 1000000
      max_on_approach_iterations: 1000
      terminal_checking_interval: 5000
      max_planning_time: 2.0
      cost_travel_multiplier: 2.0
      use_final_approach_orientation: false
      smoother:
        max_iterations: 1000
        w_smooth: 0.3
        w_data: 0.2
        tolerance: 1.0e-10
        do_refinement: true
        refinement_num: 2
```

**Key parameters explained:**
- `tolerance: 0.125` — 12.5cm, matches robot radius. Path endpoint within one body-width of goal.
- `cost_travel_multiplier: 2.0` — steers paths away from walls, centers in corridors. Requires inflation layer to work.
- `max_planning_time: 2.0` — generous for indoor maps on Jetson.
- `smoother` — built-in path smoother with refinement, produces controller-friendly paths.

**Step 1: Apply the change**

Use sed/python to replace the planner_server section in slam_nav.yaml.

**Step 2: Verify syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('/home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/slam_nav.yaml'))"
```

---

### Task 4: Update slam_nav.yaml — Local Controller (DWB → TEB)

Replace the entire FollowPath section under controller_server.

**New TEB configuration (optimized for UGV Beast on Jetson):**

```yaml
controller_server:
  ros__parameters:
    use_sim_time: false
    controller_frequency: 10.0
    min_x_velocity_threshold: 0.001
    min_y_velocity_threshold: 0.5
    min_theta_velocity_threshold: 0.001
    progress_checker_plugin: "progress_checker"
    goal_checker_plugin: "goal_checker"
    controller_plugins: ["FollowPath"]

    progress_checker:
      plugin: "nav2_controller::SimpleProgressChecker"
      required_movement_radius: 0.15
      movement_time_allowance: 30.0

    goal_checker:
      plugin: "nav2_controller::SimpleGoalChecker"
      xy_goal_tolerance: 0.15
      yaw_goal_tolerance: 1.57
      stateful: True

    FollowPath:
      plugin: "nav2_rotation_shim_controller::RotationShimController"
      primary_controller: "teb_local_planner::TebLocalPlannerROS"
      angular_dist_threshold: 1.0
      forward_sampling_distance: 0.3
      rotate_to_heading_angular_vel: 0.6
      max_angular_accel: 1.6
      simulate_ahead_time: 1.0

      # TEB trajectory
      teb_autosize: true
      dt_ref: 0.3
      dt_hysteresis: 0.1
      min_samples: 3
      max_samples: 100
      global_plan_overwrite_orientation: true
      allow_init_with_backwards_motion: false
      global_plan_viapoint_sep: 0.4
      via_points_ordered: false
      max_global_plan_lookahead_dist: 1.5
      global_plan_prune_distance: 1.0
      exact_arc_length: false
      force_reinit_new_goal_dist: 1.0
      force_reinit_new_goal_angular: 1.57
      feasibility_check_no_poses: 4
      publish_feedback: false
      control_look_ahead_poses: 1

      # TEB robot (UGV Beast calibrated values)
      max_vel_x: 0.15
      max_vel_x_backwards: 0.08
      max_vel_y: 0.0
      max_vel_theta: 0.8
      acc_lim_x: 1.2
      acc_lim_y: 0.0
      acc_lim_theta: 1.6
      min_turning_radius: 0.0
      wheelbase: 1.0
      cmd_angle_instead_rotvel: false
      is_footprint_dynamic: false
      use_proportional_saturation: false
      transform_tolerance: 1.0

      # TEB footprint
      footprint_model.type: "circular"
      footprint_model.radius: 0.12

      # TEB goal tolerance
      xy_goal_tolerance: 0.15
      free_goal_vel: false

      # TEB obstacles
      min_obstacle_dist: 0.05
      inflation_dist: 0.15
      dynamic_obstacle_inflation_dist: 0.3
      include_dynamic_obstacles: false
      include_costmap_obstacles: true
      costmap_obstacles_behind_robot_dist: 1.0
      obstacle_poses_affected: 15
      legacy_obstacle_association: false
      obstacle_association_force_inclusion_factor: 1.5
      obstacle_association_cutoff_factor: 5.0
      costmap_converter_plugin: ""
      costmap_converter_spin_thread: true
      costmap_converter_rate: 5

      # TEB optimization (reduced iterations for Jetson)
      no_inner_iterations: 3
      no_outer_iterations: 3
      optimization_activate: true
      optimization_verbose: false
      penalty_epsilon: 0.1
      weight_max_vel_x: 2.0
      weight_max_vel_y: 2.0
      weight_max_vel_theta: 1.0
      weight_acc_lim_x: 1.0
      weight_acc_lim_y: 1.0
      weight_acc_lim_theta: 1.0
      weight_kinematics_nh: 1000.0
      weight_kinematics_forward_drive: 100.0
      weight_kinematics_turning_radius: 0.0
      weight_optimaltime: 1.0
      weight_shortest_path: 0.0
      weight_obstacle: 100.0
      weight_inflation: 0.2
      weight_dynamic_obstacle: 50.0
      weight_dynamic_obstacle_inflation: 0.1
      weight_velocity_obstacle_ratio: 0.0
      weight_viapoint: 3.0
      weight_prefer_rotdir: 50.0
      weight_adapt_factor: 2.0
      obstacle_cost_exponent: 1.0

      # TEB homotopy (DISABLED for Jetson CPU savings)
      enable_homotopy_class_planning: false
      enable_multithreading: true
      simple_exploration: false
      max_number_classes: 2
      selection_cost_hysteresis: 1.0
      selection_prefer_initial_plan: 0.95
      selection_obst_cost_scale: 100.0
      selection_viapoint_cost_scale: 1.0
      selection_alternative_time_cost: false
      switching_blocking_period: 0.0
      roadmap_graph_no_samples: 15
      roadmap_graph_area_width: 5.0
      roadmap_graph_area_length_scale: 1.0
      h_signature_prescaler: 0.5
      h_signature_threshold: 0.1
      obstacle_heading_threshold: 0.45
      viapoints_all_candidates: true
      visualize_hc_graph: false
      delete_detours_backwards: true
      max_ratio_detours_duration_best_duration: 3.0

      # TEB recovery
      shrink_horizon_backup: true
      shrink_horizon_min_duration: 10.0
      oscillation_recovery: true
      oscillation_v_eps: 0.1
      oscillation_omega_eps: 0.1
      oscillation_recovery_min_duration: 10.0
      oscillation_filter_duration: 10.0
      divergence_detection_enable: false
      divergence_detection_max_chi_squared: 10
```

**Key design decisions:**
- `enable_homotopy_class_planning: false` — biggest CPU saver for Jetson. Indoor corridors have one path class.
- `costmap_converter_plugin: ""` — disabled, saves CPU. Point obstacles from costmap work fine.
- `weight_kinematics_forward_drive: 100.0` — strong forward preference, avoids unnecessary backing up.
- `weight_obstacle: 100.0` — high obstacle avoidance priority for indoor safety.
- `no_inner_iterations: 3, no_outer_iterations: 3` — reduced from 5/4, ~40% CPU savings.
- `max_samples: 100` — reduced from 500, limits trajectory optimization size.
- `footprint_model.type: "circular"` — fastest geometric model, accurate for Beast's roughly round footprint.
- `transform_tolerance: 1.0` — handles Jetson TF timing jitter.

---

### Task 5: Verify Costmap Inflation

SmacPlanner2D's `cost_travel_multiplier` only works when the costmap has non-free cost values. The inflation layer must be wide enough.

**Check current inflation settings:**

```bash
grep -A 5 'inflation_layer' slam_nav.yaml
```

**Recommended (should already be set):**
```yaml
inflation_layer:
  plugin: "nav2_costmap_2d::InflationLayer"
  cost_scaling_factor: 3.0
  inflation_radius: 0.55
```

If `inflation_radius` is less than 0.3m, increase it. SmacPlanner2D needs a smooth cost gradient to produce centered paths.

---

### Task 6: Restart and Verify SmacPlanner2D

**Step 1: Restart the service**

```bash
sudo systemctl restart ugv-waveshare.service
```

**Step 2: Verify SmacPlanner2D is loaded (after ~40s boot)**

```bash
ros2 node list | grep planner
ros2 param get /planner_server GridBased.plugin
```

Expected: `nav2_smac_planner/SmacPlanner2D`

**Step 3: Test a navigation goal**

Set a waypoint in Vizanti (http://192.168.1.155:5100). The robot should:
- Plan a path that centers in the corridor (not hugging walls)
- The path should be smoother than NavfnPlanner
- No "weird artifacts" or jagged turns

**Step 4: Check for errors**

```bash
grep -i 'error\|failed' /tmp/slam_nav.log | tail -10
```

---

### Task 7: Verify TEB Controller

**Step 1: Check TEB is loaded**

```bash
ros2 param get /controller_server FollowPath.primary_controller
```

Expected: `teb_local_planner::TebLocalPlannerROS`

**Step 2: Test corridor navigation**

Set a waypoint 3-5m down a corridor. The robot should:
- Rotate in place to face the goal (RotationShimController)
- Drive forward smoothly along the planned path
- Not back up unnecessarily (weight_kinematics_forward_drive: 100.0)
- Stay centered in the corridor
- Stop at the goal without spinning

**Step 3: Test doorway navigation**

Set a waypoint through a doorway. The robot should:
- Approach the doorway centered
- Pass through without oscillating
- Continue to the goal on the other side

**Step 4: Monitor CPU usage**

```bash
top -b -n 1 | head -15
```

TEB at 10Hz with homotopy disabled should use less than 30% of a single core.

---

### Task 8: Test Autonomous Exploration

Launch explore_lite with the new planner + controller:

```bash
ros2 launch explore_lite explore.launch.py
```

The robot should explore at least as well as with NavfnPlanner + DWB. Watch for:
- Smoother trajectories around corners
- Better corridor centering
- No false recovery triggers
- Consistent forward motion (no unnecessary backing up)

---

### Task 9: Full Reboot Test

```bash
sudo reboot
```

Wait 2 minutes, then verify:

1. SSH access: `ssh root@192.168.1.155 -p 23`
2. Vizanti: `http://192.168.1.155:5100`
3. All nodes running: `ros2 node list | wc -l`
4. SmacPlanner2D loaded: `ros2 param get /planner_server GridBased.plugin`
5. TEB loaded: controller responds to waypoints
6. Exploration works: `ros2 launch explore_lite explore.launch.py`

---

## Tuning Guide (After Integration)

If the robot needs adjustment after switching:

| Symptom | Parameter to Adjust | Direction |
|---------|---------------------|-----------|
| Robot cuts corners | `cost_travel_multiplier` | Increase (2.0 → 3.0) |
| Robot too far from walls | `inflation_radius` | Decrease |
| Robot backs up when it shouldn't | `weight_kinematics_forward_drive` | Increase (100 → 200) |
| Robot oscillates near obstacles | `weight_obstacle` | Increase (100 → 150) |
| Robot overshoots at goal | `xy_goal_tolerance` | Increase slightly |
| Path following too loose | `weight_viapoint` | Increase (3.0 → 5.0) |
| TEB too slow (CPU) | `no_inner_iterations` | Decrease (3 → 2) |
| Progress checker false alarm | `movement_time_allowance` | Increase |
| Planner TF errors | `transform_tolerance` | Increase (1.0 → 2.0) |

---

## Rollback

If SmacPlanner2D + TEB doesn't work:

1. Revert planner: change `plugin` back to `"nav2_navfn_planner/NavfnPlanner"`
2. Revert controller: change `primary_controller` back to `"dwb_core::DWBLocalPlanner"` and restore DWB params
3. Restart service

The rest of the stack (SLAM, odometry, costmaps, recovery BT) is unchanged and doesn't need rollback.
