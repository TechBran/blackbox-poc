"""Status tools: read-only pose/odom/lidar/nodes/topics/health queries."""
import math
import subprocess
import time
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema  # noqa: F401 - parity with other tool modules


def _yaw_from_quat(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


@tool(name="status_get_pose",
      description="Get the robot's current (x, y, yaw) pose in the map frame.")
async def status_get_pose():
    cached = RosBridge.instance().node.get_latest("/robot_pose")
    if cached is None:
        return {"error": "no pose yet"}
    _, msg = cached
    p = msg.pose.position
    o = msg.pose.orientation
    yaw = _yaw_from_quat(o)
    return {"x": round(p.x, 3), "y": round(p.y, 3),
            "yaw_rad": round(yaw, 3), "yaw_deg": round(math.degrees(yaw), 1)}


@tool(name="status_get_odom",
      description="Get filtered odometry (position + linear/angular velocity).")
async def status_get_odom():
    cached = RosBridge.instance().node.get_latest("/odom")
    if cached is None:
        return {"error": "no odom yet"}
    _, msg = cached
    return {
        "x": round(msg.pose.pose.position.x, 3),
        "y": round(msg.pose.pose.position.y, 3),
        "yaw_deg": round(math.degrees(_yaw_from_quat(msg.pose.pose.orientation)), 1),
        "v_linear": round(msg.twist.twist.linear.x, 3),
        "v_angular": round(msg.twist.twist.angular.z, 3),
    }


@tool(name="status_get_lidar_summary",
      description="Summarize the LiDAR scan as 8 directional sectors with min distance per sector.")
async def status_get_lidar_summary():
    cached = RosBridge.instance().node.get_latest("/scan")
    if cached is None:
        return {"error": "no /scan yet"}
    _, scan = cached
    ranges = scan.ranges
    n = len(ranges)
    if n == 0:
        return {"error": "empty scan"}
    names = ["front", "front_left", "left", "back_left",
             "back", "back_right", "right", "front_right"]
    sector = n // 8
    out = {}
    for i, name in enumerate(names):
        chunk = [r for r in ranges[i*sector:(i+1)*sector]
                 if scan.range_min < r < scan.range_max]
        out[name] = round(min(chunk), 3) if chunk else None
    valid = [r for r in ranges if scan.range_min < r < scan.range_max]
    return {"sectors_m": out,
            "overall_min_m": round(min(valid), 3) if valid else None}


@tool(name="status_list_nodes", description="List all running ROS2 nodes.")
async def status_list_nodes():
    proc = subprocess.run(["ros2", "node", "list"],
                          capture_output=True, text=True, timeout=5)
    return {"nodes": sorted([n for n in proc.stdout.splitlines() if n.strip()])}


@tool(name="status_list_topics",
      description="List all active ROS2 topics with message type.")
async def status_list_topics():
    proc = subprocess.run(["ros2", "topic", "list", "-t"],
                          capture_output=True, text=True, timeout=5)
    topics = []
    for line in proc.stdout.splitlines():
        if "[" in line and "]" in line:
            name, tp = line.rsplit("[", 1)
            topics.append({"topic": name.strip(), "type": tp.rstrip("]").strip()})
    return {"topics": sorted(topics, key=lambda x: x["topic"])}


@tool(name="status_health",
      description="Overall health: bridge running, topic freshness, subscribed counts.")
async def status_health():
    b = RosBridge.instance()
    now = time.time()
    topics_seen = {}
    for t in ["/odom", "/scan", "/robot_pose", "/map",
              "/camera/image/compressed", "/oak/rgb/image_rect/compressed",
              "/oak/stereo/depth", "/gimbal/state"]:
        c = b.node.get_latest(t)
        topics_seen[t] = None if c is None else round(now - c[0], 2)
    return {
        "bridge_running": b.is_running(),
        "topic_freshness_s": topics_seen,
        "timestamp": now,
    }
