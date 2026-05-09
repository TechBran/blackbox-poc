"""
Nav2 Navigation-Only Launch for UGV Beast Exploration.
Does NOT include bringup (already running from start_ros2.sh).
Uses custom params tuned for slow autonomous exploration.

Usage:
  ros2 launch /home/ws/ugv_ws/nav2_explore.launch.py
"""

import os
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    nav2_bringup_dir = get_package_share_directory('nav2_bringup')

    # Navigation-only launch (no SLAM, no bringup — those are already running)
    navigation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_bringup_dir, 'launch', 'navigation_launch.py')
        ),
        launch_arguments={
            'use_sim_time': 'false',
            'params_file': '/home/ws/ugv_ws/nav2_explore_params.yaml',
            'autostart': 'true',
        }.items()
    )

    return LaunchDescription([
        navigation_launch,
    ])
