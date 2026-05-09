from launch import LaunchDescription
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    return LaunchDescription([
        Node(
            package='slam_gmapping',
            executable='slam_gmapping',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                # Reduced thresholds for small-room mapping
                # Default linearUpdate=1.0 (1 meter!) too high for indoor use
                'linearUpdate': 0.2,    # Update map every 20cm of movement
                'angularUpdate': 0.2,   # Update map every ~12° of rotation
                # Reasonable defaults for indoor mapping
                'maxUrange': 8.0,       # Max usable LiDAR range (meters)
                'maxRange': 12.0,       # Max LiDAR range
                'particles': 30,        # Number of particles (default 30)
            }]
        ),
    ])
