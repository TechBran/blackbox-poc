#!/usr/bin/env python3
"""
UGV Beast Map Manager Node
Handles saving, loading, and listing maps for both 2D (GMapping) and 3D (RTAB-Map) SLAM.

Services:
  /mapping/save_2d  (Trigger) - Save current GMapping map via nav2_map_server
  /mapping/save_3d  (Trigger) - Save current RTAB-Map database
  /mapping/list     (Trigger) - List all saved maps (2D + 3D)

Map storage:
  /home/ws/ugv_ws/maps/2d/  - .pgm + .yaml files from GMapping
  /home/ws/ugv_ws/maps/3d/  - .db files from RTAB-Map
"""

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger
import subprocess
import shutil
import os
from datetime import datetime


MAPS_BASE = '/home/ws/ugv_ws/maps'
MAPS_2D = os.path.join(MAPS_BASE, '2d')
MAPS_3D = os.path.join(MAPS_BASE, '3d')
RTABMAP_DB = os.path.expanduser('~/.ros/rtabmap.db')


class MappingNode(Node):
    def __init__(self):
        super().__init__('ugv_mapping')

        # Ensure map directories exist
        os.makedirs(MAPS_2D, exist_ok=True)
        os.makedirs(MAPS_3D, exist_ok=True)

        # Services
        self.create_service(Trigger, '/mapping/save_2d', self.save_2d_srv)
        self.create_service(Trigger, '/mapping/save_3d', self.save_3d_srv)
        self.create_service(Trigger, '/mapping/list', self.list_srv)

        self.get_logger().info(f"Map manager ready (2D: {MAPS_2D}, 3D: {MAPS_3D})")

    def save_2d_srv(self, req, res):
        """Save current GMapping map using nav2_map_server's map_saver_cli."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        map_path = os.path.join(MAPS_2D, f'map_{timestamp}')

        try:
            result = subprocess.run(
                ['ros2', 'run', 'nav2_map_server', 'map_saver_cli',
                 '-f', map_path, '--ros-args', '-p', 'save_map_timeout:=10000.0'],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0 and os.path.exists(f'{map_path}.pgm'):
                res.success = True
                res.message = f"2D map saved: map_{timestamp}.pgm"
                self.get_logger().info(res.message)
            else:
                res.success = False
                err = result.stderr.strip() if result.stderr else "Map file not created"
                res.message = f"2D save failed: {err}"
                self.get_logger().error(res.message)

        except subprocess.TimeoutExpired:
            res.success = False
            res.message = "2D save timed out (30s)"
            self.get_logger().error(res.message)
        except Exception as e:
            res.success = False
            res.message = f"2D save error: {e}"
            self.get_logger().error(res.message)

        return res

    def save_3d_srv(self, req, res):
        """Save current RTAB-Map database by copying ~/.ros/rtabmap.db."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        dest = os.path.join(MAPS_3D, f'rtabmap_{timestamp}.db')

        if not os.path.exists(RTABMAP_DB):
            res.success = False
            res.message = f"No RTAB-Map database found at {RTABMAP_DB}"
            self.get_logger().error(res.message)
            return res

        try:
            shutil.copy2(RTABMAP_DB, dest)
            size_mb = os.path.getsize(dest) / (1024 * 1024)
            res.success = True
            res.message = f"3D map saved: rtabmap_{timestamp}.db ({size_mb:.1f} MB)"
            self.get_logger().info(res.message)
        except Exception as e:
            res.success = False
            res.message = f"3D save error: {e}"
            self.get_logger().error(res.message)

        return res

    def list_srv(self, req, res):
        """List all saved maps."""
        lines = []

        # 2D maps
        maps_2d = sorted([f for f in os.listdir(MAPS_2D) if f.endswith('.pgm')])
        if maps_2d:
            lines.append(f"2D maps ({len(maps_2d)}):")
            for m in maps_2d:
                size_kb = os.path.getsize(os.path.join(MAPS_2D, m)) / 1024
                lines.append(f"  {m} ({size_kb:.0f} KB)")
        else:
            lines.append("2D maps: none")

        # 3D maps
        maps_3d = sorted([f for f in os.listdir(MAPS_3D) if f.endswith('.db')])
        if maps_3d:
            lines.append(f"3D maps ({len(maps_3d)}):")
            for m in maps_3d:
                size_mb = os.path.getsize(os.path.join(MAPS_3D, m)) / (1024 * 1024)
                lines.append(f"  {m} ({size_mb:.1f} MB)")
        else:
            lines.append("3D maps: none")

        res.success = True
        res.message = "\n".join(lines)
        return res

    def destroy_node(self):
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MappingNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
