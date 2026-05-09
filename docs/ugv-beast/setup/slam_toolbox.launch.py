#!/usr/bin/env python3
"""slam_toolbox launch for UGV Beast — 2D LiDAR SLAM only."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='slam_toolbox',
            executable='async_slam_toolbox_node',
            name='slam_toolbox',
            output='screen',
            parameters=['/home/ws/ugv_ws/slam_toolbox_params.yaml'],
        ),
    ])
