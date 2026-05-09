from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="ugv_bno085",
            executable="imu_node",
            name="bno085_imu",
            output="screen",
            parameters=[{
                "frame_id": "base_imu_link",
                "i2c_address": 0x4A,
                "publish_rate": 100.0,
                "topic": "/imu/bno085/data",
            }],
        ),
    ])
