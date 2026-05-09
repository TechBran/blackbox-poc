#!/usr/bin/env python3
"""nvblox launch for UGV Beast — 3D depth to costmap slice."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='nvblox_ros',
            executable='nvblox_node',
            name='nvblox_node',
            output='screen',
            parameters=['/home/ws/ugv_ws/nvblox_params.yaml'],
            remappings=[
                ('depth/image', '/oak/stereo/image_raw'),
                ('depth/camera_info', '/oak/rgb/camera_info'),
                ('color/image', '/oak/rgb/image_rect'),
                ('color/camera_info', '/oak/rgb/camera_info'),
            ],
        ),
    ])
