import asyncio
from unittest.mock import MagicMock, patch
from ugv_tools_api.tools import gimbal as tools_gimbal  # noqa: F401 - triggers registration
from ugv_tools_api.registry import registry


def test_gimbal_look_at_clamps_ranges():
    from ugv_tools_api.tools.gimbal import _clamp
    assert _clamp(200, -180, 180) == 180
    assert _clamp(-200, -180, 180) == -180
    assert _clamp(95, -45, 90) == 90


def test_gimbal_tools_registered():
    for t in ["gimbal_look_at", "gimbal_reset", "gimbal_get_state"]:
        assert t in registry.names()


def test_gimbal_look_at_publishes_clamped_point():
    fake_pub = MagicMock()
    fake_node = MagicMock()
    fake_node.publisher.return_value = fake_pub
    with patch("ugv_tools_api.tools.gimbal.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        asyncio.run(registry.dispatch("gimbal_look_at",
            {"pan_deg": 999.0, "tilt_deg": -100.0, "speed": 500}))
    pt = fake_pub.publish.call_args.args[0]
    assert pt.x == 180.0      # clamped from 999
    assert pt.y == -45.0      # clamped from -100
    assert pt.z == 300.0      # clamped from 500
