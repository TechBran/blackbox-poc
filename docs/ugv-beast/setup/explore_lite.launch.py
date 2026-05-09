"""
explore_lite Frontier Exploration Launch for UGV Beast.
Detects unexplored frontiers on the SLAM map and sends Nav2 goals.

Usage:
  ros2 launch /home/ws/ugv_ws/explore_lite.launch.py
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='explore_lite',
            executable='explore',
            name='explore_lite',
            output='screen',
            remappings=[('/move_base_simple/goal', '/goal_pose')],
            parameters=[{
                'robot_base_frame': 'base_footprint',
                'costmap_topic': '/global_costmap/costmap',
                'visualize': True,
                'planner_frequency': 0.50,        # Re-evaluate frontiers every 2s — responsive, real-time feel
                'progress_timeout': 15.0,         # Abandon stuck frontier in 15s — move on, don't linger
                'potential_scale': 3.0,
                'orientation_scale': 0.0,         # Don't care about facing direction at goal
                'gain_scale': 1.0,
                'transform_tolerance': 0.3,
                'min_frontier_size': 0.3,         # Lowered from 0.5 — doorway frontiers are small
                'return_to_init': True,           # Auto-return to start when no frontiers
            }],
        ),
    ])
