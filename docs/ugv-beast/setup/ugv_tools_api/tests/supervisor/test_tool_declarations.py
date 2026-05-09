from ugv_tools_api.supervisor.tool_declarations import ALL_TOOLS, tool_names


EXPECTED = {
    "get_robot_state", "get_camera_view", "get_slam_map_view",
    "get_costmap_view",
    "dispatch_er_mission", "cancel_er_mission", "get_er_mission_status",
    "emergency_stop", "lights_on", "lights_off", "gimbal_look_at",
    "set_watch_mode",  # added in Task 4
}


def test_all_tools_present():
    assert set(tool_names()) == EXPECTED


def test_tool_names_ordering_matches_all_tools():
    assert tool_names() == tuple(t.name for t in ALL_TOOLS)


def test_dispatch_er_mission_schema_has_required_mission_param():
    d = next(t for t in ALL_TOOLS if t.name == "dispatch_er_mission")
    props = d.parameters.properties
    assert "mission" in props
    assert "mission" in (d.parameters.required or [])


def test_dispatch_er_mission_replace_current_defaults_false():
    d = next(t for t in ALL_TOOLS if t.name == "dispatch_er_mission")
    rc = d.parameters.properties["replace_current"]
    assert rc.default is False


def test_cancel_er_mission_reason_is_required():
    d = next(t for t in ALL_TOOLS if t.name == "cancel_er_mission")
    assert "reason" in (d.parameters.required or [])


def test_lights_on_which_defaults_both():
    d = next(t for t in ALL_TOOLS if t.name == "lights_on")
    which = d.parameters.properties["which"]
    assert which.default == "both"


def test_lights_off_which_defaults_both():
    d = next(t for t in ALL_TOOLS if t.name == "lights_off")
    which = d.parameters.properties["which"]
    assert which.default == "both"


def test_set_watch_mode_fps_param_clamped_in_schema():
    """T1 of embodied-observer plan: set_watch_mode advertises an
    optional fps parameter clamped to [0.1, 1.0]. Schema-enforced (not
    just prose) so Gemini rejects out-of-range values at the model
    boundary instead of letting the handler clamp silently."""
    d = next(t for t in ALL_TOOLS if t.name == "set_watch_mode")
    props = d.parameters.properties
    assert "fps" in props
    fps = props["fps"]
    assert fps.minimum == 0.1
    assert fps.maximum == 1.0
    # fps stays optional — only `on` is required.
    assert "fps" not in (d.parameters.required or [])


def test_gimbal_pan_tilt_have_enforced_ranges():
    d = next(t for t in ALL_TOOLS if t.name == "gimbal_look_at")
    pan = d.parameters.properties["pan_deg"]
    tilt = d.parameters.properties["tilt_deg"]
    assert pan.minimum == -180 and pan.maximum == 180
    assert tilt.minimum == -45 and tilt.maximum == 90


from google.genai import types as gtypes


def test_dispatch_er_mission_is_non_blocking():
    from ugv_tools_api.supervisor.tool_declarations import DISPATCH_ER_MISSION
    assert DISPATCH_ER_MISSION.behavior == gtypes.Behavior.NON_BLOCKING


def test_safety_and_imperative_tools_stay_blocking():
    """cancel_er_mission and emergency_stop MUST stay BLOCKING (default).
    They are imperative and must be acknowledged before the model speaks again."""
    from ugv_tools_api.supervisor.tool_declarations import (
        CANCEL_ER_MISSION, EMERGENCY_STOP,
    )
    for d in (CANCEL_ER_MISSION, EMERGENCY_STOP):
        assert d.behavior in (None, gtypes.Behavior.BLOCKING), (
            f"{d.name} must stay BLOCKING — it is imperative and must be "
            f"acknowledged before the model speaks again"
        )
