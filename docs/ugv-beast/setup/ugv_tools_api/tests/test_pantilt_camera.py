import pytest
from ugv_tools_api.nodes.pantilt_camera import PantiltCameraNode, resolve_v4l2_device

def test_resolve_v4l2_prefers_video0(monkeypatch, tmp_path):
    (tmp_path / "video0").touch()
    (tmp_path / "video1").touch()
    assert resolve_v4l2_device(search_root=str(tmp_path)) == str(tmp_path / "video0")

def test_resolve_v4l2_raises_if_none(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_v4l2_device(search_root=str(tmp_path))
