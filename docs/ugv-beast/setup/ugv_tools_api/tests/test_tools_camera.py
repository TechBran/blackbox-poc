import asyncio
import base64
from unittest.mock import MagicMock, patch

from ugv_tools_api.tools import camera as tools_camera  # noqa: F401 - triggers registration
from ugv_tools_api.registry import registry


def test_list_cameras():
    fake_node = MagicMock()
    fake_node.get_latest.return_value = None  # no cached frames
    with patch("ugv_tools_api.tools.camera.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        out = asyncio.run(registry.dispatch("camera_list", {}))
    assert set(out["cameras"]) >= {"pantilt", "oakd"}
    # Details has entries for both
    assert set(out["details"].keys()) == {"pantilt", "oakd"}
    # With no cached frames, both are non-streaming with null age
    for name in ("pantilt", "oakd"):
        assert out["details"][name]["streaming"] is False
        assert out["details"][name]["last_frame_age_s"] is None


def test_snapshot_unknown_camera():
    out = asyncio.run(registry.dispatch("camera_snapshot", {"camera": "nope"}))
    assert "error" in out and "unknown" in out["error"].lower()


def test_snapshot_base64_with_cached_frame():
    fake_node = MagicMock()
    fake_msg = MagicMock()
    fake_msg.data = b"\xff\xd8\xff\xe0FAKEJPEG"
    fake_node.get_latest.return_value = (1000.0, fake_msg)
    with patch("ugv_tools_api.tools.camera.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        out = asyncio.run(registry.dispatch("camera_snapshot",
            {"camera": "pantilt", "as_url": False}))
    assert out["camera"] == "pantilt"
    assert out["format"] == "jpeg"
    assert base64.b64decode(out["image_b64"]) == b"\xff\xd8\xff\xe0FAKEJPEG"
    assert out["size_bytes"] == len(b"\xff\xd8\xff\xe0FAKEJPEG")


def test_snapshot_as_url():
    fake_node = MagicMock()
    fake_msg = MagicMock()
    fake_msg.data = b"xxx"
    fake_node.get_latest.return_value = (1000.0, fake_msg)
    with patch("ugv_tools_api.tools.camera.RosBridge") as RB:
        RB.instance.return_value.node = fake_node
        out = asyncio.run(registry.dispatch("camera_snapshot",
            {"camera": "oakd", "as_url": True}))
    assert out["url"] == "/snapshot/oakd"
    assert "image_b64" not in out
