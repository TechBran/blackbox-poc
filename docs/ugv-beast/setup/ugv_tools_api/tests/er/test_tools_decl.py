"""Unit tests for ER tool declaration filtering.

ER must NOT have gimbal control — Gemini Live (supervisor) owns the
pan-tilt gimbal exclusively so the two layers don't fight for the actuator.
"""
from ugv_tools_api.er import tools_decl


def test_gimbal_tools_excluded_from_er_harness():
    names = {d.name for d in tools_decl.ALL_DECLARATIONS}
    for excluded in tools_decl.EXCLUDED_FROM_ER:
        assert excluded not in names, (
            f"{excluded} must not appear in ER tool harness — it belongs to Gemini Live"
        )


def test_core_tools_still_present_in_er_harness():
    """Sanity check: filtering didn't accidentally remove tools ER needs."""
    names = {d.name for d in tools_decl.ALL_DECLARATIONS}
    required = {
        "nav_goto_point",
        "nav_cancel",
        "motion_move_forward",
        "motion_stop",
        "camera_snapshot",
        "system_emergency_stop",
        "mission_done",
        "mission_fail",
        "ask_user",
    }
    missing = required - names
    assert not missing, f"core ER tools missing from harness: {sorted(missing)}"


def test_excluded_set_contains_only_gimbal_tools():
    """Guard against silently extending the exclusion set without thought."""
    assert tools_decl.EXCLUDED_FROM_ER == {
        "gimbal_look_at",
        "gimbal_reset",
        "gimbal_get_state",
    }
