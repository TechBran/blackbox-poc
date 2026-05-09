"""Spatial-grounding tool: back-project an OAK-D RGB pixel into the map frame.

Uses the OAK-D depth stream + camera intrinsics + live TF to convert a
2D image coordinate into a 3D point the navigation stack understands.
Pure-math is delegated to :mod:`ugv_tools_api.projection_math` (Phase A);
this module wires it to the live ROS state owned by ``RosBridge``.

Typical usage from the ER agent::

    {"x": ..., "y": ...} = await project_pixel_to_map(pixel_u=320, pixel_v=240)
    nav_goto_point(x=..., y=..., yaw_deg=...)

Costmap diagnostics let the agent reject pixels whose projected target
lands inside a wall before issuing a doomed nav goal.
"""
import numpy as np
import rclpy
import tf2_ros
from cv_bridge import CvBridge

from ..registry import tool
from ..ros_bridge import RosBridge
from ..schema import ParamSchema
from ..projection_math import (
    pixel_to_camera_frame,
    sample_depth_at_pixel,
    transform_point_to_frame,
)


_CV_BRIDGE = CvBridge()


def _tf_to_matrix(tx) -> np.ndarray:
    """Convert a geometry_msgs/TransformStamped to a 4x4 homogeneous matrix.

    The result is ``T_target_source`` (so multiplying a point expressed in
    the source frame yields the same point in the target frame).
    """
    t = tx.transform.translation
    q = tx.transform.rotation
    x, y, z, w = q.x, q.y, q.z, q.w
    R = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
    ])
    T = np.eye(4)
    T[0:3, 0:3] = R
    T[0:3, 3] = [t.x, t.y, t.z]
    return T


@tool(
    name="project_pixel_to_map",
    description=(
        "Back-project an OAK-D RGB pixel to a map-frame coordinate using the OAK-D "
        "depth at that pixel and the live TF chain. Use this BEFORE nav_goto_point "
        "when you've identified an object in the OAK-D image — pass the pixel "
        "(u, v) and use the returned (x, y) directly. Includes costmap diagnostics: "
        "in_lethal=True means the projected target is a wall/obstacle; pick a different pixel."
    ),
    parameters={
        "pixel_u": ParamSchema(type="integer", description="Image column 0..639", minimum=0, maximum=639),
        "pixel_v": ParamSchema(type="integer", description="Image row 0..479", minimum=0, maximum=479),
        "target_frame": ParamSchema(type="string", description="Output TF frame, defaults to 'map'", default="map"),
        "window": ParamSchema(type="integer", description="Odd median window for depth robustness, default 5", default=5, minimum=1, maximum=11),
    },
    required=["pixel_u", "pixel_v"],
)
async def project_pixel_to_map(pixel_u: int, pixel_v: int, target_frame: str = "map", window: int = 5):
    node = RosBridge.instance().node

    # 1) Camera intrinsics from /oak/stereo/camera_info (msg.k is row-major 3x3)
    cached_info = node.get_latest("/oak/stereo/camera_info")
    if cached_info is None:
        return {"ok": False, "error": "no camera_info yet"}
    _, info = cached_info
    k = info.k
    fx = float(k[0])
    fy = float(k[4])
    cx = float(k[2])
    cy = float(k[5])

    # 2) Latest depth frame
    cached_depth = node.get_latest("/oak/stereo/depth")
    if cached_depth is None:
        return {"ok": False, "error": "no depth frame yet"}
    _, depth_msg = cached_depth

    # 3) Convert depth Image -> numpy
    try:
        depth_image = _CV_BRIDGE.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
    except Exception as e:  # noqa: BLE001 - cv_bridge raises a couple of distinct types
        return {"ok": False, "error": f"cv_bridge conversion failed: {e}"}

    # 4) Sample depth at the requested pixel with a small median window
    try:
        depth_m = sample_depth_at_pixel(
            depth_image, u=int(pixel_u), v=int(pixel_v),
            encoding=depth_msg.encoding, window=int(window),
        )
    except ValueError as e:
        return {"ok": False, "error": f"depth sample failed: {e}"}
    if depth_m <= 0.0 or depth_m > 10.0:
        return {"ok": False, "error": f"invalid depth at pixel: {depth_m:.3f}m"}

    # 5) Pixel -> camera-optical-frame point
    xc, yc, zc = pixel_to_camera_frame(
        u=float(pixel_u), v=float(pixel_v), depth=depth_m,
        fx=fx, fy=fy, cx=cx, cy=cy,
    )

    # 6) Lookup TF from camera-optical-frame to target_frame
    try:
        tx = node.tf_buffer.lookup_transform(
            target_frame, "oak_rgb_camera_optical_frame", rclpy.time.Time(),
        )
    except (tf2_ros.LookupException,
            tf2_ros.ConnectivityException,
            tf2_ros.ExtrapolationException) as e:
        return {"ok": False, "error": f"TF lookup failed: {e}"}

    # 7) Camera-frame point -> target-frame point
    T = _tf_to_matrix(tx)
    wx, wy, wz = transform_point_to_frame((xc, yc, zc), T)

    # 8) Costmap diagnostics (only meaningful when target_frame=='map')
    cached_cm = node.get_latest("/global_costmap/costmap")
    if cached_cm is None:
        diag = {"in_lethal": None, "in_inflation": None, "cost": None, "outside_costmap": None}
    else:
        _, grid = cached_cm
        ox = grid.info.origin.position.x
        oy = grid.info.origin.position.y
        res = grid.info.resolution
        col = int((wx - ox) / res)
        row = int((wy - oy) / res)
        if 0 <= col < grid.info.width and 0 <= row < grid.info.height:
            idx = row * grid.info.width + col
            cost = int(grid.data[idx])
            diag = {
                "in_lethal": cost >= 99,
                "in_inflation": 50 <= cost < 99,
                "cost": cost,
                "outside_costmap": False,
            }
        else:
            diag = {
                "in_lethal": False,
                "in_inflation": False,
                "cost": None,
                "outside_costmap": True,
            }

    return {
        "ok": True,
        "frame": target_frame,
        "x": round(wx, 3),
        "y": round(wy, 3),
        "z": round(wz, 3),
        "depth_m": round(depth_m, 3),
        **diag,
    }
