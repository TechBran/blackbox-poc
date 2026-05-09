"""Tests for BiasEma — exponential-moving-average gyro bias estimator.

Updates only when stationary. Tracks 3-axis bias slowly. Caller passes
the raw (already-bias-corrected by boot cal) gyro tuple; estimator's
current() returns the residual bias to be subtracted from outgoing samples.
"""
import pytest
from ugv_tools_api.nodes.imu_zupt_node import BiasEma


def test_initial_bias_is_zero():
    """Initial EMA starts at (0,0,0) — boot cal already absorbed the gross bias."""
    ema = BiasEma(alpha=0.01)
    assert ema.current() == (0.0, 0.0, 0.0)


def test_seed_initializes_state():
    """Constructor accepts a seed value (e.g., from boot cal output)."""
    ema = BiasEma(alpha=0.01, seed=(0.001, 0.002, -0.003))
    assert ema.current() == pytest.approx((0.001, 0.002, -0.003))


def test_update_moves_toward_target():
    """One update with alpha=0.5 moves halfway from current to target."""
    ema = BiasEma(alpha=0.5)
    ema.update((0.1, 0.2, -0.3))
    assert ema.current() == pytest.approx((0.05, 0.1, -0.15))


def test_repeated_updates_converge():
    """100 updates with alpha=0.1 to constant target converges to ~target."""
    ema = BiasEma(alpha=0.1)
    target = (0.05, -0.02, 0.01)
    for _ in range(200):
        ema.update(target)
    cur = ema.current()
    for c, t in zip(cur, target):
        assert abs(c - t) < 1e-4, f"got {cur}, want {target}"


def test_alpha_zero_freezes_state():
    """alpha=0.0 means no update ever applied (test edge case)."""
    ema = BiasEma(alpha=0.0, seed=(1.0, 2.0, 3.0))
    ema.update((10.0, 10.0, 10.0))
    assert ema.current() == pytest.approx((1.0, 2.0, 3.0))


def test_alpha_one_replaces_state():
    """alpha=1.0 means each update fully replaces the state."""
    ema = BiasEma(alpha=1.0)
    ema.update((0.5, 0.5, 0.5))
    assert ema.current() == pytest.approx((0.5, 0.5, 0.5))
    ema.update((0.1, 0.1, 0.1))
    assert ema.current() == pytest.approx((0.1, 0.1, 0.1))
