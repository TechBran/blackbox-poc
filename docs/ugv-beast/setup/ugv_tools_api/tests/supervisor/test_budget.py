"""TokenBudget rotation-trigger tests."""
from ugv_tools_api.supervisor.budget import TokenBudget


def test_no_rotation_below_threshold():
    b = TokenBudget(window_tokens=10000, threshold=0.8,
                    audio_tokens_per_s=25.0, jpeg_tokens_per_frame=250.0)
    b.audio_seconds(60)        # 1500 tokens
    b.jpeg_frames(10)          # 2500 tokens
    assert not b.should_rotate
    assert b.usage_pct < 0.8


def test_rotation_at_threshold():
    b = TokenBudget(window_tokens=10000, threshold=0.8,
                    audio_tokens_per_s=25.0, jpeg_tokens_per_frame=250.0)
    b.audio_seconds(200)       # 5000 tokens
    b.jpeg_frames(15)          # 3750 tokens
    # 8750 / 10000 = 87.5% > 80%
    assert b.should_rotate


def test_reset_after_rotation():
    b = TokenBudget(window_tokens=10000, threshold=0.8)
    b.audio_seconds(200); b.jpeg_frames(15)
    assert b.should_rotate
    b.reset()
    assert not b.should_rotate
    assert b.usage_pct == 0.0


def test_zero_window_doesnt_divide_by_zero():
    b = TokenBudget(window_tokens=0, threshold=0.8)
    # Defensive: usage_pct is well-defined even at zero window
    assert b.usage_pct == 0.0
    assert not b.should_rotate
