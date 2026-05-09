"""Tests for ImuPreprocessor — composes detector + EMA + IMU pass-through.

Pure-logic class. Takes raw gyro tuple + motion signals; returns the
gyro that should be published downstream and a flag indicating whether
to publish with 'locked' (tight) covariance.
"""
import pytest
from ugv_tools_api.nodes.imu_zupt_node import ImuPreprocessor


def test_moving_state_subtracts_current_bias():
    """When moving, output = input - current_bias. Bias is unchanged."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=2,
                        alpha=0.01, seed_bias=(0.001, 0.002, 0.003))
    # Force moving state
    p.observe_motion(linear=1.0, angular=0.0)
    out, locked = p.process_gyro((0.5, 0.6, 0.7))
    assert out == pytest.approx((0.5 - 0.001, 0.6 - 0.002, 0.7 - 0.003))
    assert locked is False


def test_stationary_state_outputs_zero_and_locks():
    """When stationary, output = (0,0,0), locked=True."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=2, alpha=0.01)
    p.observe_motion(linear=0.0, angular=0.0)
    p.observe_motion(linear=0.0, angular=0.0)  # debounce satisfied
    out, locked = p.process_gyro((0.05, -0.03, 0.01))
    assert out == (0.0, 0.0, 0.0)
    assert locked is True


def test_stationary_state_updates_bias_ema():
    """When stationary, the bias EMA tracks the input gyro stream."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=1, alpha=1.0,
                        seed_bias=(0.0, 0.0, 0.0))
    p.observe_motion(linear=0.0, angular=0.0)
    p.process_gyro((0.1, 0.2, 0.3))  # alpha=1 means full replace
    p.observe_motion(linear=0.0, angular=0.0)
    p.process_gyro((0.5, 0.5, 0.5))
    # After two stationary updates with alpha=1.0, bias = last sample
    assert p.current_bias() == pytest.approx((0.5, 0.5, 0.5))


def test_moving_state_does_not_update_bias():
    """When moving, bias stays put — important so motion isn't absorbed."""
    p = ImuPreprocessor(threshold=0.02, debounce_frames=1, alpha=1.0,
                        seed_bias=(0.001, 0.002, 0.003))
    p.observe_motion(linear=1.0, angular=0.0)  # moving
    p.process_gyro((1.0, 1.0, 1.0))
    assert p.current_bias() == pytest.approx((0.001, 0.002, 0.003))
