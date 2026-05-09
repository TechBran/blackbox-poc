import asyncio
import json as _json
from unittest.mock import MagicMock, patch

from ugv_tools_api.tools import system as tools_system  # noqa: F401 - triggers registration
from ugv_tools_api.registry import registry


def test_system_tools_registered():
    names = registry.names()
    for t in ["system_emergency_stop", "system_servo_center", "system_servo_release"]:
        assert t in names


def _make_node_with_two_pubs():
    """Helper: returns (fake_node, fake_cmd_vel_pub, fake_json_cmd_pub).

    `fake_node.publisher(topic, msg_type)` routes to the right fake publisher
    based on whether 'json_cmd' is in the topic string. Mirrors how the real
    bridge node multiplexes publishers by topic.
    """
    fake_cmd_vel = MagicMock(name="cmd_vel_pub")
    fake_json_cmd = MagicMock(name="json_cmd_pub")
    fake_node = MagicMock(name="node")
    fake_node.publisher.side_effect = lambda topic, msg_type: (
        fake_json_cmd if "json_cmd" in topic else fake_cmd_vel
    )
    return fake_node, fake_cmd_vel, fake_json_cmd


def test_estop_publishes_zero_twist_and_t0():
    fake_node, fake_cmd_vel, fake_json_cmd = _make_node_with_two_pubs()
    with patch("ugv_tools_api.tools.system.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        # Speed up the test: shrink the cmd_vel pin window so we don't wait 1.5s
        with patch("ugv_tools_api.tools.system._ESTOP_PIN_SECONDS", 0.05), \
             patch("ugv_tools_api.tools.system._ESTOP_PIN_HZ", 100):
            out = asyncio.run(registry.dispatch("system_emergency_stop", {}))
    assert out["estopped"] is True
    # New contract: fan-out list reports which branches fired
    assert "fanout" in out

    # /cmd_vel got a zero Twist (at least once during the pin window)
    assert fake_cmd_vel.publish.called, "expected /cmd_vel.publish() call"
    twist_call = fake_cmd_vel.publish.call_args.args[0]
    assert twist_call.linear.x == 0.0
    assert twist_call.linear.y == 0.0
    assert twist_call.linear.z == 0.0
    assert twist_call.angular.x == 0.0
    assert twist_call.angular.y == 0.0
    assert twist_call.angular.z == 0.0

    # /ugv/json_cmd got a String with {"T": 0}
    assert fake_json_cmd.publish.called, "expected /ugv/json_cmd.publish() call"
    string_call = fake_json_cmd.publish.call_args.args[0]
    payload = _json.loads(string_call.data)
    assert payload["T"] == 0


def test_servo_center_publishes_t134():
    fake_node = MagicMock()
    fake_pub = MagicMock()
    fake_node.publisher.return_value = fake_pub
    with patch("ugv_tools_api.tools.system.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        out = asyncio.run(registry.dispatch("system_servo_center", {}))
    assert out == {"centered": True}
    payload = _json.loads(fake_pub.publish.call_args.args[0].data)
    assert payload["T"] == 134
    # Center implies X=Y=0
    assert payload.get("X", 0) == 0
    assert payload.get("Y", 0) == 0


def test_servo_release_publishes_t135():
    fake_node = MagicMock()
    fake_pub = MagicMock()
    fake_node.publisher.return_value = fake_pub
    with patch("ugv_tools_api.tools.system.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        out = asyncio.run(registry.dispatch("system_servo_release", {}))
    assert out == {"released": True}
    payload = _json.loads(fake_pub.publish.call_args.args[0].data)
    assert payload["T"] == 135


def test_emergency_stop_cancels_active_nav_goal():
    """When a Nav2 goal handle is active in nav._state, e-stop must cancel it."""
    from ugv_tools_api.tools import nav as nav_tools

    fake_handle = MagicMock()
    fake_bridge = MagicMock()
    fake_bridge.node.publisher.return_value = MagicMock()

    with patch.object(nav_tools, "_state", {"handle": fake_handle, "client": None,
                                              "status": "navigating", "distance_remaining": None}), \
         patch.object(nav_tools, "_lock", MagicMock()), \
         patch("ugv_tools_api.tools.system.RosBridge") as br:
        br.instance.return_value = fake_bridge
        result = asyncio.run(tools_system.system_emergency_stop())

    fake_handle.cancel_goal_async.assert_called_once()
    assert result["estopped"] is True
    assert "nav_cancel" in result.get("fanout", [])


def test_emergency_stop_calls_explore_stop_service():
    """E-stop must trigger /explore/stop service if the client is reachable."""
    fake_bridge = MagicMock()
    fake_bridge.node.publisher.return_value = MagicMock()
    fake_client = MagicMock()
    fake_client.wait_for_service.return_value = True
    fake_bridge.node.create_client.return_value = fake_client

    with patch("ugv_tools_api.tools.system.RosBridge") as br:
        br.instance.return_value = fake_bridge
        result = asyncio.run(tools_system.system_emergency_stop())

    from std_srvs.srv import Trigger
    fake_bridge.node.create_client.assert_called()
    args, _ = fake_bridge.node.create_client.call_args
    assert Trigger in args, f"Expected Trigger srv type in create_client args, got {args}"
    assert any("/explore/stop" in str(a) for a in args)
    fake_client.call_async.assert_called_once()
    assert "explore_stop" in result.get("fanout", [])


def test_emergency_stop_pins_zero_cmd_vel_multiple_times():
    """E-stop must publish zero Twist repeatedly (>=10 times) to outlive controller_server burst."""
    fake_node, fake_cmd_vel, fake_json_cmd = _make_node_with_two_pubs()
    with patch("ugv_tools_api.tools.system.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        # Speed up the test: monkeypatch the duration constant to 0.1s
        with patch.object(tools_system, "_ESTOP_PIN_SECONDS", 0.1, create=True), \
             patch.object(tools_system, "_ESTOP_PIN_HZ", 100, create=True):
            asyncio.run(tools_system.system_emergency_stop())

    pub_calls = fake_cmd_vel.publish.call_count
    assert pub_calls >= 10, f"Expected >=10 zero-Twist publishes on /cmd_vel, got {pub_calls}"


def test_emergency_stop_cancels_active_er_mission():
    """E-stop must POST to ER /mission/abort_active so ER stops issuing new actions."""
    from unittest.mock import AsyncMock
    fake_node, fake_cmd_vel, fake_json_cmd = _make_node_with_two_pubs()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_async_client = MagicMock()
    fake_async_client.__aenter__ = AsyncMock(return_value=fake_async_client)
    fake_async_client.__aexit__ = AsyncMock(return_value=None)
    fake_async_client.post = AsyncMock(return_value=fake_response)

    with patch("ugv_tools_api.tools.system.RosBridge") as RB, \
         patch("ugv_tools_api.tools.system.httpx.AsyncClient", return_value=fake_async_client):
        RB.instance.return_value.node = fake_node
        # Speed the cmd_vel pin so the test runs fast
        with patch.object(tools_system, "_ESTOP_PIN_SECONDS", 0.05, create=True), \
             patch.object(tools_system, "_ESTOP_PIN_HZ", 100, create=True):
            result = asyncio.run(tools_system.system_emergency_stop())

    fake_async_client.post.assert_called_once()
    posted_url = fake_async_client.post.call_args.args[0]
    assert posted_url.endswith("/mission/abort_active"), (
        f"expected POST to /mission/abort_active, got {posted_url}"
    )
    assert "er_cancel" in result.get("fanout", [])
