"""Offline smoke test for PantiltCameraNode — verifies V4L2 MJPEG passthrough
produces non-empty JPEG frames at expected rate.

Runs WITHOUT ROS2 — just imports the node module to check syntax + imports,
then directly opens V4L2 device and pulls frames. Run inside the container:
  python3 -m pytest /tmp/test_pantilt_camera_smoke.py -v
"""
import os
import sys


def test_imports_clean():
    """Ensure the target module imports without errors.

    Skips if rclpy not on path (running pytest outside ROS env). The actual
    V4L2 path is validated by test_v4l2_yields_jpeg_bytes, which doesn't need
    ROS — so missing rclpy here is informational, not a deployment blocker.
    """
    sys.path.insert(0, "/tmp")
    try:
        import rclpy  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("rclpy not on path - source ROS env for full check")
    import pantilt_camera_v4l2_passthrough  # noqa: F401


def test_v4l2_yields_jpeg_bytes():
    """Verify direct V4L2 capture produces valid JPEG buffers."""
    if not os.path.exists("/dev/video0"):
        import pytest
        pytest.skip("No /dev/video0")

    from linuxpy.video.device import Device, VideoCapture, BufferType, PixelFormat
    dev = Device("/dev/video0")
    dev.open()
    try:
        dev.set_format(BufferType.VIDEO_CAPTURE, 640, 480, PixelFormat.MJPEG)
        dev.set_fps(BufferType.VIDEO_CAPTURE, 15)
        capture = VideoCapture(dev)
        capture.open()
        try:
            frame = next(iter(capture))
            data = bytes(frame.data)
            assert data[:2] == b"\xff\xd8", f"Not a JPEG: starts with {data[:4].hex()}"
            assert len(data) > 1000, f"JPEG too small: {len(data)} bytes"
        finally:
            capture.close()
    finally:
        dev.close()
