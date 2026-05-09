"""Pure-math layer for OAK-D pixel -> camera-frame back-projection.

Implements the standard pinhole camera model: a depth pixel (u, v, d) maps to a
3D point (x, y, z) in the camera optical frame following ROS REP-103 conventions
(+X right, +Y down, +Z forward). Helpers also sample depth from a numpy image
buffer and apply a 4x4 homogeneous transform to a 3D point so the result can be
expressed in any TF frame.

ROS-free by design - the rclpy/tf2 wrapping happens in a separate module
(Phase B). Keep imports limited to numpy so this layer stays unit-testable on a
plain workstation.
"""

from __future__ import annotations

import numpy as np


def pixel_to_camera_frame(
    u: float,
    v: float,
    depth: float,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
) -> tuple[float, float, float]:
    """Back-project a depth pixel into the camera optical frame.

    Returns (0, 0, 0) when depth <= 0 (no-return).
    """
    if depth <= 0.0:
        return (0.0, 0.0, 0.0)
    x = (u - cx) * depth / fx
    y = (v - cy) * depth / fy
    z = depth
    return (float(x), float(y), float(z))


def sample_depth_at_pixel(
    depth_image: np.ndarray,
    u: int,
    v: int,
    encoding: str,
    window: int = 1,
) -> float:
    """Sample a depth value from a 2D image array, returning meters.

    Supported encodings:
      * "16UC1" - uint16 millimeters (OAK-D default); scaled by 1e-3.
      * "32FC1" - float32 meters; passed through.

    A `window` greater than 1 returns the median over an NxN neighborhood
    centered on (u, v), ignoring zero-valued pixels (no-return) for robustness
    against per-pixel glitches. Out-of-bounds (u, v) returns 0.0.
    """
    if depth_image.ndim != 2:
        raise ValueError(f"depth_image must be 2D, got ndim={depth_image.ndim}")
    if window < 1 or window % 2 == 0:
        raise ValueError(f"window must be a positive odd integer, got {window}")

    if encoding == "16UC1":
        scale = 0.001
    elif encoding == "32FC1":
        scale = 1.0
    else:
        raise ValueError(f"Unsupported depth encoding: {encoding!r}")

    height, width = depth_image.shape[:2]
    if u < 0 or v < 0 or u >= width or v >= height:
        return 0.0

    if window <= 1:
        return float(depth_image[v, u]) * scale

    half = window // 2
    u0 = max(0, u - half)
    u1 = min(width, u + half + 1)
    v0 = max(0, v - half)
    v1 = min(height, v + half + 1)
    patch = depth_image[v0:v1, u0:u1]
    valid = patch[patch > 0]
    if valid.size == 0:
        return 0.0
    return float(np.median(valid)) * scale


def transform_point_to_frame(
    point: tuple[float, float, float],
    transform: np.ndarray,
) -> tuple[float, float, float]:
    """Apply a 4x4 homogeneous transform to a 3D point.

    `transform` is the pose of the SOURCE frame expressed in the TARGET frame
    (as returned by `tf2_ros.Buffer.lookup_transform(target, source, ...)` once
    converted to a 4x4 matrix). The point is treated as a column vector with an
    appended homogeneous 1; the resulting (x, y, z) is in the TARGET frame.

    Equivalently: `transform = T_target_source`. Therefore
    `T_map_camera @ point_in_camera = point_in_map`.
    """
    if transform.shape != (4, 4):
        raise ValueError(f"transform must be a 4x4 matrix, got shape {transform.shape}")
    x, y, z = point
    p = np.array([x, y, z, 1.0], dtype=float)
    out = transform @ p
    return (float(out[0]), float(out[1]), float(out[2]))
