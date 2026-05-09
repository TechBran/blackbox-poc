import math
import numpy as np
import pytest
from projection_math import (
    pixel_to_camera_frame,
    sample_depth_at_pixel,
    transform_point_to_frame,
)


# --- pixel_to_camera_frame (4 tests) ---

def test_principal_point_back_projects_to_optical_axis():
    fx, fy, cx, cy = 600.0, 600.0, 320.0, 240.0
    x, y, z = pixel_to_camera_frame(u=320, v=240, depth=2.0, fx=fx, fy=fy, cx=cx, cy=cy)
    assert abs(x) < 1e-9
    assert abs(y) < 1e-9
    assert z == pytest.approx(2.0)


def test_pixel_offset_produces_lateral_offset():
    fx, fy, cx, cy = 600.0, 600.0, 320.0, 240.0
    x, y, z = pixel_to_camera_frame(u=380, v=240, depth=2.0, fx=fx, fy=fy, cx=cx, cy=cy)
    assert x == pytest.approx(0.20)  # 60 * 2 / 600
    assert abs(y) < 1e-9
    assert z == pytest.approx(2.0)


def test_pixel_above_principal_produces_negative_y():
    fx, fy, cx, cy = 600.0, 600.0, 320.0, 240.0
    _, y, _ = pixel_to_camera_frame(u=320, v=180, depth=2.0, fx=fx, fy=fy, cx=cx, cy=cy)
    assert y == pytest.approx((180 - 240) * 2.0 / 600.0)
    assert y < 0


def test_zero_depth_returns_origin():
    x, y, z = pixel_to_camera_frame(u=320, v=240, depth=0.0, fx=600.0, fy=600.0, cx=320.0, cy=240.0)
    assert (x, y, z) == (0.0, 0.0, 0.0)


# --- sample_depth_at_pixel (4 tests) ---

def test_sample_depth_returns_value_in_meters():
    depth_mm = np.array([[1000, 2000], [3000, 4000]], dtype=np.uint16)
    val = sample_depth_at_pixel(depth_mm, u=1, v=0, encoding="16UC1")
    assert val == pytest.approx(2.0)


def test_sample_depth_meters_encoding():
    depth_m = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    val = sample_depth_at_pixel(depth_m, u=1, v=1, encoding="32FC1")
    assert val == pytest.approx(4.0)


def test_sample_depth_out_of_bounds_returns_zero():
    depth = np.zeros((2, 2), dtype=np.uint16)
    assert sample_depth_at_pixel(depth, u=5, v=5, encoding="16UC1") == 0.0
    assert sample_depth_at_pixel(depth, u=-1, v=0, encoding="16UC1") == 0.0


def test_sample_depth_handles_3x3_neighborhood_average():
    # A 3x3 with a zero in the middle: median ignoring zeros = median of [100..900 minus 0] = 500
    depth = np.array([[100, 200, 300],
                      [400, 0, 600],
                      [700, 800, 900]], dtype=np.uint16)
    val = sample_depth_at_pixel(depth, u=1, v=1, encoding="16UC1", window=3)
    assert val == pytest.approx(0.500)


# --- transform_point_to_frame (3 tests) ---

def test_identity_transform_is_noop():
    T = np.eye(4)
    out = transform_point_to_frame(point=(1.0, 2.0, 3.0), transform=T)
    assert out == pytest.approx((1.0, 2.0, 3.0))


def test_translation_only_adds_translation():
    T = np.eye(4)
    T[0:3, 3] = [10.0, 20.0, 30.0]
    out = transform_point_to_frame(point=(1.0, 2.0, 3.0), transform=T)
    assert out == pytest.approx((11.0, 22.0, 33.0))


def test_90deg_yaw_rotates_point():
    T = np.eye(4)
    T[0:3, 0:3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    out = transform_point_to_frame(point=(1.0, 0.0, 0.0), transform=T)
    assert out[0] == pytest.approx(0.0, abs=1e-9)
    assert out[1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(0.0)


def test_sample_depth_rejects_3d_array():
    depth_3d = np.zeros((4, 4, 1), dtype=np.uint16)
    with pytest.raises(ValueError, match="2D"):
        sample_depth_at_pixel(depth_3d, u=1, v=1, encoding="16UC1")


def test_sample_depth_rejects_even_window():
    depth = np.zeros((4, 4), dtype=np.uint16)
    with pytest.raises(ValueError, match="odd"):
        sample_depth_at_pixel(depth, u=1, v=1, encoding="16UC1", window=2)


def test_transform_rejects_non_4x4():
    bad = np.eye(3)
    with pytest.raises(ValueError, match="4x4"):
        transform_point_to_frame(point=(1.0, 2.0, 3.0), transform=bad)
