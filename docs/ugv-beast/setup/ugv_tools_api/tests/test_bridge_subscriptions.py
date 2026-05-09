"""Integration test: verify RosBridge caches canonical topics when the robot is live."""
import time
import pytest

from ugv_tools_api.ros_bridge import RosBridge


@pytest.mark.integration
def test_bridge_caches_expected_topics():
    """With robot running, expected non-image topics should populate within 3s.

    Image topics (/camera/image/compressed, /oak/rgb/image_rect/compressed) are
    NOT checked here because their publishers (pantilt_camera, oakd_camera) are
    only launched during camera-specific tests, not running continuously.
    """
    b = RosBridge()
    b.start()
    try:
        time.sleep(3.0)
        for t in [
            "/odom",
            "/scan",
            "/robot_pose",
            "/map",
            "/local_costmap/costmap",
        ]:
            assert b.node.get_latest(t) is not None, f"no msg on {t}"
    finally:
        b.stop()
