import asyncio
from unittest.mock import MagicMock, patch
from ugv_tools_api.tools import motion as tools_motion  # noqa: F401 - triggers registration
from ugv_tools_api.registry import registry


def test_motion_tools_registered():
    names = registry.names()
    for t in ["motion_move_forward", "motion_move_backward",
              "motion_rotate_left", "motion_rotate_right", "motion_stop"]:
        assert t in names


def test_motion_stop_publishes_zero_twist():
    fake_pub = MagicMock()
    with patch("ugv_tools_api.tools.motion._cmd_vel_pub", return_value=fake_pub):
        asyncio.run(registry.dispatch("motion_stop", {}))
    assert fake_pub.publish.called
    twist = fake_pub.publish.call_args.args[0]
    assert twist.linear.x == 0 and twist.angular.z == 0


def test_forward_clamps_speed():
    # speed 5.0 > MAX_LIN=0.15, should clamp to 0.15
    speeds = []

    class CapturingPub:
        def publish(self, twist):
            speeds.append(twist.linear.x)

    fake_pub = CapturingPub()
    with patch("ugv_tools_api.tools.motion._cmd_vel_pub", return_value=fake_pub):
        asyncio.run(registry.dispatch("motion_move_forward",
                                      {"duration_s": 0.25, "speed_m_s": 5.0}))
    # All non-final twists should be clamped to 0.15; final 2 should be 0 (stop)
    nonzero = [s for s in speeds if s != 0]
    assert all(s == 0.15 for s in nonzero), f"expected all 0.15 or 0, got {speeds}"


def test_duration_clamps_at_10s_forward():
    # Verify the schema's maximum=10.0 and speed maximum=0.15 are present.
    td = next(td for td in registry.descriptors() if td.name == "motion_move_forward")
    assert td.parameters["duration_s"].maximum == 10.0
    assert td.parameters["speed_m_s"].maximum == 0.15
