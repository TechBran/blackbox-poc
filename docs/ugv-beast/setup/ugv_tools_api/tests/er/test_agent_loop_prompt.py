"""Smoke tests for ER's `_build_system_prompt()`.

Task DR3 restores depth as a primary distance sense in the ER prompt. After
DR2 landed depth back in `gather_observation()`, the prompt must advertise
it as a 6th input channel and lead the spatial-reasoning paragraph with
depth as the primary distance cue.

These tests pin the load-bearing substrings so a future edit can't silently
revert the depth restoration. Sync tests only — no pytest-asyncio.
"""


def _prompt() -> str:
    from ugv_tools_api.er.agent_loop import _build_system_prompt
    return _build_system_prompt()


# ── Senses block: depth advertised as a 6th channel ────────────────────


def test_prompt_advertises_depth_channel():
    """Depth must be listed in the senses block with its OAK-D body-cam
    provenance so the model knows the channel exists and where it comes
    from. DR2 wires the bundle; this test pins the prompt half.
    """
    assert "Depth image from the OAK-D body camera" in _prompt()


def test_prompt_describes_depth_colormap():
    """The TURBO colorization is a perceptual cue (blue=close, red=far,
    black=no return). Without this, the model can't decode the rendered
    image — it would have to guess.
    """
    assert "colorized TURBO" in _prompt()


def test_prompt_renumbers_senses_to_six_inputs():
    """Inserting depth as channel #2 makes the senses block six channels.
    The intro line must reflect the new count or the numbering won't add up.
    """
    assert "six inputs together" in _prompt()


# ── Spatial-reasoning paragraph: depth leads ──────────────────────────


def test_prompt_makes_depth_primary_distance_sense():
    """The post-`2c9112f` rewrite told ER to triangulate via costmap +
    LiDAR (no depth). DR3 restores depth as the lead distance cue, with
    costmap + LiDAR as cross-checks. Pin the lead phrase.
    """
    assert "Depth is your primary distance sense" in _prompt()
