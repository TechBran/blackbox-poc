"""Gather a multimodal observation snapshot for the ER model."""
from __future__ import annotations

import asyncio
import io
import json
import math
import time
from typing import Any, Optional

import httpx
import numpy as np
from PIL import Image, ImageDraw

from google.genai import types as genai_types

from . import config

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None  # depth colormap becomes a grayscale fallback when unavailable


_depth_cache: dict[str, Any] = {
    "source_stamp_ns": None,
    "rendered_at": 0.0,
    "jpeg_bytes": None,
}

_costmap_cache: dict[str, Any] = {
    "source_stamp_ns": None,
    "pose_key": None,
    "rendered_at": 0.0,
    "png_bytes": None,
}

_local_costmap_cache: dict[str, Any] = {
    "source_stamp_ns": None,
    "pose_key": None,
    "rendered_at": 0.0,
    "png_bytes": None,
}

_slam_map_cache: dict[str, Any] = {
    "source_stamp_ns": None,
    "pose_key": None,
    "rendered_at": 0.0,
    "png_bytes": None,
}


def _ros_bridge_or_none():
    try:
        from ..ros_bridge import RosBridge  # type: ignore
        b = RosBridge.instance()
        if not b.is_running():
            return None
        return b
    except Exception:
        return None


def _get_latest(topic: str):
    b = _ros_bridge_or_none()
    if b is None:
        return None
    try:
        return b.node.get_latest(topic)
    except Exception:
        return None


def _fetch_nav_status_blocking() -> dict[str, Any]:
    # tools_api owns the Nav2 action-client state; query it over loopback HTTP
    # rather than reaching into its private module globals (would be empty in
    # this process).
    try:
        with httpx.Client(timeout=2.0) as c:
            r = c.post(f"{config.TOOLS_API_URL}/tool/nav_status", json={})
        if r.status_code != 200:
            return {"status": "unknown", "distance_remaining_m": None}
        body = r.json()
        data = body.get("result", body) if isinstance(body, dict) else {}
        return {
            "status": data.get("status", "unknown"),
            "distance_remaining_m": data.get("distance_remaining_m"),
        }
    except Exception:
        return {"status": "unknown", "distance_remaining_m": None}


def _yaw_from_quat(q) -> float:
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


def _jpeg_from_pil(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue()


async def _render_rgb() -> Optional[genai_types.Part]:
    cached = _get_latest(config.RGB_TOPIC)
    if cached is None:
        return None
    _, msg = cached
    raw = bytes(msg.data)

    def _resize() -> bytes:
        img = Image.open(io.BytesIO(raw))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = img.resize((config.RGB_WIDTH, config.RGB_HEIGHT), Image.LANCZOS)
        return _jpeg_from_pil(img, config.RGB_JPEG_QUALITY)

    try:
        jpeg = await asyncio.to_thread(_resize)
    except Exception:
        return None
    return genai_types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")


def _depth_image_to_array(msg) -> Optional[np.ndarray]:
    enc = getattr(msg, "encoding", "") or ""
    width = int(getattr(msg, "width", 0) or 0)
    height = int(getattr(msg, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return None
    raw = bytes(msg.data)
    if enc in ("16UC1", "mono16"):
        arr = np.frombuffer(raw, dtype=np.uint16).copy()
    elif enc == "32FC1":
        arr = np.frombuffer(raw, dtype=np.float32).copy()
    else:
        return None
    try:
        arr = arr.reshape((height, width))
    except Exception:
        return None
    if arr.dtype == np.uint16:
        arr = arr.astype(np.float32) / 1000.0
    return arr


def _colorize_depth(depth_m: np.ndarray) -> np.ndarray:
    clipped = np.clip(depth_m, config.DEPTH_CLIP_MIN_M, config.DEPTH_CLIP_MAX_M)
    invalid = ~np.isfinite(depth_m) | (depth_m <= 0)
    span = config.DEPTH_CLIP_MAX_M - config.DEPTH_CLIP_MIN_M
    norm = ((clipped - config.DEPTH_CLIP_MIN_M) / span * 255.0).astype(np.uint8)
    norm[invalid] = 0
    if cv2 is not None:
        colored = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
        colored = cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)
    else:
        colored = np.stack([norm, norm, norm], axis=-1)
    return colored


async def _render_depth() -> Optional[genai_types.Part]:
    cached = _get_latest(config.DEPTH_TOPIC)
    if cached is None:
        return None
    ts, msg = cached
    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    stamp_ns = None
    if stamp is not None:
        stamp_ns = int(getattr(stamp, "sec", 0)) * 1_000_000_000 + int(getattr(stamp, "nanosec", 0))

    now = time.time()
    last_ns = _depth_cache.get("source_stamp_ns")
    last_render = _depth_cache.get("rendered_at", 0.0)
    cached_bytes = _depth_cache.get("jpeg_bytes")
    age_ms = (now - last_render) * 1000.0
    if (
        cached_bytes is not None
        and stamp_ns is not None
        and last_ns == stamp_ns
        and age_ms < config.DEPTH_RERENDER_MIN_AGE_MS
    ):
        return genai_types.Part.from_bytes(data=cached_bytes, mime_type="image/jpeg")

    def _do_render() -> Optional[bytes]:
        arr = _depth_image_to_array(msg)
        if arr is None:
            return None
        colored = _colorize_depth(arr)
        img = Image.fromarray(colored, mode="RGB")
        img = img.resize((config.DEPTH_WIDTH, config.DEPTH_HEIGHT), Image.BILINEAR)
        return _jpeg_from_pil(img, config.DEPTH_JPEG_QUALITY)

    try:
        jpeg = await asyncio.to_thread(_do_render)
    except Exception:
        jpeg = None
    if jpeg is None:
        return None

    _depth_cache["source_stamp_ns"] = stamp_ns
    _depth_cache["rendered_at"] = now
    _depth_cache["jpeg_bytes"] = jpeg
    return genai_types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")


def _rasterize_lidar(scan) -> bytes:
    size = config.LIDAR_IMAGE_SIZE
    meters_per_cell = config.LIDAR_METERS_PER_CELL
    img = Image.new("RGB", (size, size), (10, 10, 14))
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2

    for r in range(1, 5):
        radius_px = int(r / meters_per_cell)
        if radius_px <= 0 or radius_px > size:
            continue
        draw.ellipse(
            (cx - radius_px, cy - radius_px, cx + radius_px, cy + radius_px),
            outline=(40, 40, 52),
        )

    ranges = list(getattr(scan, "ranges", []) or [])
    if ranges:
        angle = float(getattr(scan, "angle_min", 0.0))
        step = float(getattr(scan, "angle_increment", 0.0))
        rmin = float(getattr(scan, "range_min", 0.05))
        rmax = float(getattr(scan, "range_max", 12.0))
        for r in ranges:
            if not (rmin < r < rmax) or not math.isfinite(r):
                angle += step
                continue
            x_m = r * math.cos(angle)
            y_m = r * math.sin(angle)
            px = cx + int(x_m / meters_per_cell)
            py = cy - int(y_m / meters_per_cell)
            if 0 <= px < size and 0 <= py < size:
                draw.point((px, py), fill=(255, 180, 80))
            angle += step

    draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(120, 220, 255))
    draw.line((cx, cy, cx + 10, cy), fill=(120, 220, 255), width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _render_lidar() -> Optional[genai_types.Part]:
    cached = _get_latest(config.SCAN_TOPIC)
    if cached is None:
        return None
    _, scan = cached
    try:
        png = await asyncio.to_thread(_rasterize_lidar, scan)
    except Exception:
        return None
    return genai_types.Part.from_bytes(data=png, mime_type="image/png")


def _rasterize_costmap(
    msg,
    robot_x: float,
    robot_y: float,
    robot_yaw: float,
    size_px: Optional[int] = None,
    meters_per_cell: Optional[float] = None,
) -> bytes:
    size = int(size_px if size_px is not None else config.COSTMAP_IMAGE_SIZE)
    mpc = float(meters_per_cell if meters_per_cell is not None else config.COSTMAP_METERS_PER_CELL)

    info = msg.info
    width = int(info.width)
    height = int(info.height)
    resolution = float(info.resolution)
    origin_x = float(info.origin.position.x)
    origin_y = float(info.origin.position.y)

    # Decode OccupancyGrid.data (flat int8 array) into a 2D costmap.
    raw = np.frombuffer(bytes(msg.data), dtype=np.int8)
    if raw.size < width * height:
        # Defensive: if data length doesn't match metadata, bail out.
        raise ValueError(f"costmap data size {raw.size} != {width}*{height}")
    grid = raw[: width * height].reshape((height, width)).astype(np.int16)

    # Build a meshgrid of output-pixel coordinates in the robot's body frame.
    # Image-y goes down; world-y goes up, so flip the y axis.
    half = size // 2
    xs = (np.arange(size) - half).astype(np.float32) * mpc            # (W,)
    ys = (half - np.arange(size)).astype(np.float32) * mpc            # (H,)
    dx, dy = np.meshgrid(xs, ys)                                       # (H, W)

    # Rotate body-frame offsets by robot yaw into map-frame offsets,
    # then translate by robot position.
    cos_y = math.cos(robot_yaw)
    sin_y = math.sin(robot_yaw)
    map_x = robot_x + dx * cos_y - dy * sin_y
    map_y = robot_y + dx * sin_y + dy * cos_y

    # Convert map-frame coords -> costmap cell indices.
    cx = np.floor((map_x - origin_x) / resolution).astype(np.int32)
    cy = np.floor((map_y - origin_y) / resolution).astype(np.int32)

    in_bounds = (cx >= 0) & (cx < width) & (cy >= 0) & (cy < height)
    safe_cx = np.clip(cx, 0, width - 1)
    safe_cy = np.clip(cy, 0, height - 1)
    cost = grid[safe_cy, safe_cx]

    # Mark out-of-bounds so the color pass can paint them dark blue.
    OOB = np.int16(-128)
    cost = np.where(in_bounds, cost, OOB)

    # Allocate output RGB canvas, then colorize by cost band.
    out = np.zeros((size, size, 3), dtype=np.uint8)

    oob_mask = (cost == OOB)
    unknown_mask = (cost == -1) & ~oob_mask
    free_mask = (cost >= 0) & (cost <= 50) & ~oob_mask
    inflation_mask = (cost >= 51) & (cost <= 98) & ~oob_mask
    lethal_mask = (cost >= 99) & ~oob_mask

    out[oob_mask] = (30, 40, 80)
    out[unknown_mask] = (150, 200, 255)
    out[free_mask] = (220, 220, 220)
    out[lethal_mask] = (180, 30, 30)

    # Inflation gradient: interpolate between light (low cost) and dark (high cost) orange.
    if np.any(inflation_mask):
        t = ((cost[inflation_mask].astype(np.float32) - 51.0) / (98.0 - 51.0))
        t = np.clip(t, 0.0, 1.0)
        low = np.array([255, 220, 120], dtype=np.float32)
        high = np.array([255, 140, 60], dtype=np.float32)
        infl = (low * (1.0 - t[:, None]) + high * t[:, None]).astype(np.uint8)
        out[inflation_mask] = infl

    # Render overlays with PIL.
    img = Image.fromarray(out, mode="RGB")
    draw = ImageDraw.Draw(img)
    center = size // 2

    # Range rings every 2 m.
    ring_step_m = 2.0
    max_radius_px = center
    r_m = ring_step_m
    while True:
        radius_px = int(r_m / mpc)
        if radius_px <= 0 or radius_px > max_radius_px:
            break
        draw.ellipse(
            (center - radius_px, center - radius_px,
             center + radius_px, center + radius_px),
            outline=(90, 90, 90),
        )
        r_m += ring_step_m

    # Robot marker: cyan filled circle + short heading line toward image-top
    # (body frame is rotated so "forward" is always up).
    draw.ellipse((center - 4, center - 4, center + 4, center + 4), fill=(120, 220, 255))
    draw.line((center, center, center, center - 12), fill=(120, 220, 255), width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _render_costmap() -> Optional[genai_types.Part]:
    cached = _get_latest(config.COSTMAP_TOPIC)
    if cached is None:
        return None
    _, msg = cached

    # Pull current robot pose from the bridge; fall back to origin when missing
    # so the costmap still renders (it will just be map-origin-centered).
    robot_x = robot_y = 0.0
    robot_yaw = 0.0
    pose_cached = _get_latest("/robot_pose")
    if pose_cached is not None:
        _, pose_msg = pose_cached
        try:
            p = pose_msg.pose.position
            robot_x = float(p.x)
            robot_y = float(p.y)
            robot_yaw = _yaw_from_quat(pose_msg.pose.orientation)
        except Exception:
            pass

    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    stamp_ns = None
    if stamp is not None:
        stamp_ns = int(getattr(stamp, "sec", 0)) * 1_000_000_000 + int(getattr(stamp, "nanosec", 0))

    # Pose key: quantize to ~5 cm / ~1 deg so tiny pose jitter doesn't trash the cache.
    pose_key = (
        round(robot_x, 2),
        round(robot_y, 2),
        round(math.degrees(robot_yaw), 0),
    )

    now = time.time()
    last_ns = _costmap_cache.get("source_stamp_ns")
    last_pose = _costmap_cache.get("pose_key")
    last_render = _costmap_cache.get("rendered_at", 0.0)
    cached_bytes = _costmap_cache.get("png_bytes")
    age_ms = (now - last_render) * 1000.0
    if (
        cached_bytes is not None
        and stamp_ns is not None
        and last_ns == stamp_ns
        and last_pose == pose_key
        and age_ms < config.COSTMAP_RERENDER_MIN_AGE_MS
    ):
        return genai_types.Part.from_bytes(data=cached_bytes, mime_type="image/png")

    def _do_render() -> Optional[bytes]:
        try:
            return _rasterize_costmap(msg, robot_x, robot_y, robot_yaw)
        except Exception:
            return None

    try:
        png = await asyncio.to_thread(_do_render)
    except Exception:
        png = None
    if png is None:
        return None

    _costmap_cache["source_stamp_ns"] = stamp_ns
    _costmap_cache["pose_key"] = pose_key
    _costmap_cache["rendered_at"] = now
    _costmap_cache["png_bytes"] = png
    return genai_types.Part.from_bytes(data=png, mime_type="image/png")


async def _render_local_costmap() -> Optional[genai_types.Part]:
    cached = _get_latest(config.LOCAL_COSTMAP_TOPIC)
    if cached is None:
        return None
    _, msg = cached

    robot_x = robot_y = 0.0
    robot_yaw = 0.0
    pose_cached = _get_latest("/robot_pose")
    if pose_cached is not None:
        _, pose_msg = pose_cached
        try:
            p = pose_msg.pose.position
            robot_x = float(p.x)
            robot_y = float(p.y)
            robot_yaw = _yaw_from_quat(pose_msg.pose.orientation)
        except Exception:
            pass

    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    stamp_ns = None
    if stamp is not None:
        stamp_ns = int(getattr(stamp, "sec", 0)) * 1_000_000_000 + int(getattr(stamp, "nanosec", 0))

    pose_key = (
        round(robot_x, 2),
        round(robot_y, 2),
        round(math.degrees(robot_yaw), 0),
    )

    now = time.time()
    last_ns = _local_costmap_cache.get("source_stamp_ns")
    last_pose = _local_costmap_cache.get("pose_key")
    last_render = _local_costmap_cache.get("rendered_at", 0.0)
    cached_bytes = _local_costmap_cache.get("png_bytes")
    age_ms = (now - last_render) * 1000.0
    if (
        cached_bytes is not None
        and stamp_ns is not None
        and last_ns == stamp_ns
        and last_pose == pose_key
        and age_ms < config.LOCAL_COSTMAP_RERENDER_MIN_AGE_MS
    ):
        return genai_types.Part.from_bytes(data=cached_bytes, mime_type="image/png")

    def _do_render() -> Optional[bytes]:
        try:
            return _rasterize_costmap(
                msg, robot_x, robot_y, robot_yaw,
                size_px=config.LOCAL_COSTMAP_IMAGE_SIZE,
                meters_per_cell=config.LOCAL_COSTMAP_METERS_PER_CELL,
            )
        except Exception:
            return None

    try:
        png = await asyncio.to_thread(_do_render)
    except Exception:
        png = None
    if png is None:
        return None

    _local_costmap_cache["source_stamp_ns"] = stamp_ns
    _local_costmap_cache["pose_key"] = pose_key
    _local_costmap_cache["rendered_at"] = now
    _local_costmap_cache["png_bytes"] = png
    return genai_types.Part.from_bytes(data=png, mime_type="image/png")


def rasterize_slam_map(
    msg,
    robot_x: float,
    robot_y: float,
    robot_yaw: float,
    max_size_px: int,
) -> bytes:
    """Rasterize a nav_msgs/OccupancyGrid SLAM map to PNG bytes.

    Public API (no leading underscore) so the supervisor's
    get_slam_map_view tool handler can call it without reaching into
    a private name. The function is pure: takes a duck-typed grid
    message + robot pose and returns PNG bytes — no ER state, no
    cache, safe to share across modules.
    """
    info = msg.info
    width = int(info.width)
    height = int(info.height)
    resolution = float(info.resolution)
    origin_x = float(info.origin.position.x)
    origin_y = float(info.origin.position.y)

    raw = np.frombuffer(bytes(msg.data), dtype=np.int8)
    if raw.size < width * height:
        raise ValueError(f"slam map data size {raw.size} != {width}*{height}")
    grid = raw[: width * height].reshape((height, width))

    # Flip rows so row=0 is the top of the image (OccupancyGrid origin is bottom-left).
    grid_img = np.flipud(grid)

    out = np.zeros((height, width, 3), dtype=np.uint8)
    unknown_mask = (grid_img == -1)
    free_mask = (grid_img >= 0) & (grid_img <= 50)
    occupied_mask = (grid_img >= 51)
    out[unknown_mask] = (50, 60, 100)
    out[free_mask] = (220, 220, 220)
    out[occupied_mask] = (140, 30, 30)

    img = Image.fromarray(out, mode="RGB")

    # Downscale to fit max_size_px on the longer side, preserve aspect ratio.
    long_side = max(width, height)
    if long_side > max_size_px and long_side > 0:
        scale = max_size_px / float(long_side)
    else:
        scale = 1.0
    out_w = max(1, int(round(width * scale)))
    out_h = max(1, int(round(height * scale)))
    if (out_w, out_h) != (width, height):
        img = img.resize((out_w, out_h), Image.NEAREST)

    # Robot marker: convert (x, y) -> source-map cell, then to image pixels (with y-flip).
    draw = ImageDraw.Draw(img)
    if width > 0 and height > 0 and resolution > 0:
        col = (robot_x - origin_x) / resolution
        row = (robot_y - origin_y) / resolution
        image_row = (height - 1) - row
        px = int(round(col * scale))
        py = int(round(image_row * scale))
        if 0 <= px < out_w and 0 <= py < out_h:
            draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(120, 220, 255))
            # Heading: yaw=0 is world +x (image +x); image y is flipped vs world y.
            dx = math.cos(robot_yaw)
            dy = -math.sin(robot_yaw)
            ex = int(round(px + 14 * dx))
            ey = int(round(py + 14 * dy))
            draw.line((px, py, ex, ey), fill=(120, 220, 255), width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _render_slam_map() -> Optional[genai_types.Part]:
    cached = _get_latest(config.SLAM_MAP_TOPIC)
    if cached is None:
        return None
    _, msg = cached

    robot_x = robot_y = 0.0
    robot_yaw = 0.0
    pose_cached = _get_latest("/robot_pose")
    if pose_cached is not None:
        _, pose_msg = pose_cached
        try:
            p = pose_msg.pose.position
            robot_x = float(p.x)
            robot_y = float(p.y)
            robot_yaw = _yaw_from_quat(pose_msg.pose.orientation)
        except Exception:
            pass

    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    stamp_ns = None
    if stamp is not None:
        stamp_ns = int(getattr(stamp, "sec", 0)) * 1_000_000_000 + int(getattr(stamp, "nanosec", 0))

    pose_key = (
        round(robot_x, 2),
        round(robot_y, 2),
        round(math.degrees(robot_yaw), 0),
    )

    now = time.time()
    last_ns = _slam_map_cache.get("source_stamp_ns")
    last_pose = _slam_map_cache.get("pose_key")
    last_render = _slam_map_cache.get("rendered_at", 0.0)
    cached_bytes = _slam_map_cache.get("png_bytes")
    age_ms = (now - last_render) * 1000.0
    if (
        cached_bytes is not None
        and stamp_ns is not None
        and last_ns == stamp_ns
        and last_pose == pose_key
        and age_ms < config.SLAM_MAP_RERENDER_MIN_AGE_MS
    ):
        return genai_types.Part.from_bytes(data=cached_bytes, mime_type="image/png")

    def _do_render() -> Optional[bytes]:
        try:
            return rasterize_slam_map(
                msg, robot_x, robot_y, robot_yaw,
                max_size_px=config.SLAM_MAP_MAX_IMAGE_SIZE,
            )
        except Exception:
            return None

    try:
        png = await asyncio.to_thread(_do_render)
    except Exception:
        png = None
    if png is None:
        return None

    _slam_map_cache["source_stamp_ns"] = stamp_ns
    _slam_map_cache["pose_key"] = pose_key
    _slam_map_cache["rendered_at"] = now
    _slam_map_cache["png_bytes"] = png
    return genai_types.Part.from_bytes(data=png, mime_type="image/png")


def _lidar_sector_summary(scan) -> dict[str, Optional[float]]:
    names = [
        "front", "front_left", "left", "back_left",
        "back", "back_right", "right", "front_right",
    ]
    ranges = list(getattr(scan, "ranges", []) or [])
    n = len(ranges)
    if n == 0:
        return {name: None for name in names}
    rmin = float(getattr(scan, "range_min", 0.05))
    rmax = float(getattr(scan, "range_max", 12.0))
    sector = n // 8
    out: dict[str, Optional[float]] = {}
    for i, name in enumerate(names):
        chunk = [r for r in ranges[i * sector:(i + 1) * sector]
                 if rmin < r < rmax and math.isfinite(r)]
        out[name] = round(min(chunk), 3) if chunk else None
    return out


def _state_dict() -> dict[str, Any]:
    state: dict[str, Any] = {"timestamp": round(time.time(), 3)}

    pose_cached = _get_latest("/robot_pose")
    if pose_cached is not None:
        _, pose_msg = pose_cached
        p = pose_msg.pose.position
        yaw = _yaw_from_quat(pose_msg.pose.orientation)
        state["pose"] = {
            "x": round(p.x, 3),
            "y": round(p.y, 3),
            "yaw_deg": round(math.degrees(yaw), 1),
        }
    else:
        state["pose"] = None

    odom_cached = _get_latest("/odom")
    if odom_cached is not None:
        _, odom = odom_cached
        state["odom"] = {
            "v_linear": round(odom.twist.twist.linear.x, 3),
            "v_angular": round(odom.twist.twist.angular.z, 3),
        }
    else:
        state["odom"] = None

    state["nav"] = _fetch_nav_status_blocking()

    gimbal_cached = _get_latest("/gimbal/state")
    if gimbal_cached is not None:
        _, gm = gimbal_cached
        state["gimbal"] = {
            "pan_deg": round(gm.point.x, 1),
            "tilt_deg": round(gm.point.y, 1),
        }
    else:
        state["gimbal"] = None

    scan_cached = _get_latest(config.SCAN_TOPIC)
    if scan_cached is not None:
        _, scan = scan_cached
        state["lidar_sectors_m"] = _lidar_sector_summary(scan)
    else:
        state["lidar_sectors_m"] = None

    return state


async def _render_state() -> genai_types.Part:
    state = await asyncio.to_thread(_state_dict)
    body = "ROBOT_STATE_JSON\n" + json.dumps(state, separators=(",", ":"))
    return genai_types.Part.from_text(text=body)


async def gather_observation() -> list[genai_types.Part]:
    rgb, depth, lidar, local_cm, slam_map, state = await asyncio.gather(
        _render_rgb(),
        _render_depth(),
        _render_lidar(),
        _render_local_costmap(),
        _render_slam_map(),
        _render_state(),
    )
    parts: list[genai_types.Part] = []
    if rgb is not None:
        parts.append(genai_types.Part.from_text(text=(
            f"RGB (OAK-D fixed body camera, {config.RGB_WIDTH}x{config.RGB_HEIGHT}). "
            "This camera is mounted forward on the chassis and does not pan or tilt — "
            "to look elsewhere, rotate the body."
        )))
        parts.append(rgb)
    if depth is not None:
        parts.append(genai_types.Part.from_text(text=(
            "Depth (OAK-D body cam, turbo colormap, 0.3-5.0 m. Black = no return / out of range. "
            "Spatially aligned with the RGB above (same optical frame), so a pixel's depth value "
            "is the distance to whatever's at the same pixel coordinates in the RGB."
        )))
        parts.append(depth)
    if lidar is not None:
        parts.append(genai_types.Part.from_text(
            text=f"LiDAR top-down (robot centered, {config.LIDAR_METERS_PER_CELL} m per pixel):"
        ))
        parts.append(lidar)
    if local_cm is not None:
        local_window_m = config.LOCAL_COSTMAP_IMAGE_SIZE * config.LOCAL_COSTMAP_METERS_PER_CELL
        parts.append(genai_types.Part.from_text(text=(
            f"Local costmap (top-down, robot at center heading up, ~{local_window_m:.0f}m window). "
            "Immediate obstacles around the robot. Light gray = drivable. Orange = inflation "
            "(costly). Red = lethal obstacle. Blue = unknown or out of map. Use this to verify "
            "the next 1-2m of motion is clear before issuing nav_goto_point or motion_*."
        )))
        parts.append(local_cm)
    if slam_map is not None:
        parts.append(genai_types.Part.from_text(text=(
            "SLAM map (full room layout, persistent). Top-down view of the entire mapped space. "
            "Light gray = open floor. Red = walls. Blue = unmapped. Cyan dot + heading line = "
            "your position. Use this for spatial reasoning across rooms (which room is the "
            "target in, how do I get from here to that doorway, where might I search next)."
        )))
        parts.append(slam_map)
    parts.append(state)
    return parts
