#!/usr/bin/env python3
"""EKF odometry fusion for UGV Beast."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=['/home/ws/ugv_ws/ekf_params.yaml'],
            remappings=[
                ('odometry/filtered', '/odom'),
            ],
        ),
    ])
