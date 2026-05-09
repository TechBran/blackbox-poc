"""Tests for StationaryDetector — debounced motion detector for ZUPT.

Pure-logic class with no ROS dependencies. Caller feeds it (lin, ang) tuples
representing combined cmd_vel + odom_wheel signals; detector returns whether
the robot is currently considered stationary (debounced) or moving.
"""
import pytest
from ugv_tools_api.nodes.imu_zupt_node import StationaryDetector


def test_detector_starts_in_moving_state():
    """Initial state is 'moving' until proven stationary (fail-safe — IMU
    contributes its full input until the detector has enough evidence)."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    assert d.is_stationary() is False


def test_single_zero_frame_does_not_trigger_stationary():
    """Stationary requires DEBOUNCE consecutive frames below threshold."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is False


def test_debounced_zero_frames_trigger_stationary():
    """After exactly debounce_frames at zero, stationary becomes True."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is True


def test_motion_immediately_clears_stationary():
    """Any frame above threshold flips back to moving with no debounce."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is True
    d.update(linear=0.5, angular=0.0)
    assert d.is_stationary() is False


def test_threshold_includes_signs():
    """Threshold is on absolute value — negative motion also triggers moving."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.0, angular=0.0)
    d.update(linear=-0.5, angular=0.0)
    assert d.is_stationary() is False


def test_below_threshold_counts_as_zero():
    """Sub-threshold values (sensor noise) shouldn't break the debounce."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(5):
        d.update(linear=0.001, angular=0.005)  # below threshold
    assert d.is_stationary() is True


def test_intermittent_motion_resets_debounce():
    """A single motion frame resets the counter; debounce restarts."""
    d = StationaryDetector(threshold=0.02, debounce_frames=5)
    for _ in range(4):
        d.update(linear=0.0, angular=0.0)
    assert d.is_stationary() is False  # 4 frames not enough yet
    d.update(linear=0.5, angular=0.0)
    d.update(linear=0.0, angular=0.0)  # only 1 zero frame again
    assert d.is_stationary() is False
