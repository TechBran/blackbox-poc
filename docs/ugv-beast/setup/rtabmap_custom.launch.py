"""
Custom RTAB-Map 3D SLAM launch for UGV Beast.
Uses our existing depth node's /oak/* topics — does NOT include bringup or depthai driver.
Bringup + LiDAR + depth node must already be running from start_ros2.sh.

Usage:
  ros2 launch /home/ws/ugv_ws/rtabmap_custom.launch.py
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():

    # RTAB-Map SLAM node — consumes LiDAR scan + OAK-D RGB + depth
    rtabmap_node = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        output='screen',
        parameters=[{
            'frame_id': 'base_footprint',
            'odom_frame_id': 'odom',
            'subscribe_rgb': True,
            'subscribe_depth': True,
            'subscribe_scan': True,
            'approx_sync': True,
            'publish_tf': True,
            'queue_size': 20,
            # Detection rate: how often RTAB-Map processes frames (Hz)
            'Rtabmap/DetectionRate': '3.5',
            # Memory management for Jetson
            'Mem/STMSize': '30',
            # Update map every 10cm movement or ~6° rotation
            'RGBD/LinearUpdate': '0.1',
            'RGBD/AngularUpdate': '0.1',
            # ── Ground Robot Constraints ──
            # Force 2D optimization (no roll/pitch in map→odom correction)
            'Optimizer/Strategy': '1',
            'Reg/Force3DoF': 'true',
            'RGBD/OptimizeMaxError': '0.5',
            # ── 2D Grid Map (for Vizanti) ──
            # Generate 2D occupancy grid from depth + LiDAR
            'Grid/FromDepth': 'false',  # LiDAR-only 2D grid (cleaner walls, no depth noise)
            'Grid/3D': 'false',
            'Grid/CellSize': '0.05',
            'Grid/RangeMax': '5.0',
            'Grid/RangeMin': '0.15',
            'Grid/MaxGroundHeight': '0.05',
            'Grid/MaxObstacleHeight': '0.8',
            'Grid/NormalsSegmentation': 'false',
            'Grid/RayTracing': 'true',
            'Grid/NoiseFilteringRadius': '0.2',
            'Grid/NoiseFilteringMinNeighbors': '5',
            'Grid/DepthDecimation': '4',
            # ── 3D Point Cloud (for Foxglove) ──
            # Include RGB color in point cloud
            'cloud_decimation': 4,
            'cloud_max_depth': 4.0,
            'cloud_voxel_size': 0.03,
            'cloud_output_voxelized': True,
        }],
        remappings=[
            ('rgb/image', '/oak/rgb/image_rect'),
            ('rgb/camera_info', '/oak/rgb/camera_info'),
            ('depth/image', '/oak/stereo/image_raw'),
            ('scan', '/scan'),
            ('odom', '/odom'),
            # RTAB-Map publishes grid on 'grid_map' — remap to /map for Vizanti
            ('grid_map', '/map'),
        ],
        arguments=['-d']  # Delete previous database on start
    )

    # Robot pose publisher (also used by 2D SLAM)
    robot_pose_publisher_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(
                get_package_share_directory('robot_pose_publisher'), 'launch'
            ),
            '/robot_pose_publisher_launch.py'
        ])
    )

    return LaunchDescription([
        robot_pose_publisher_launch,
        rtabmap_node,
    ])
