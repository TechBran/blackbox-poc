import math


def test_status_tools_registered():
    from ugv_tools_api.tools import status  # noqa: F401 - triggers registration
    from ugv_tools_api.registry import registry
    for t in ["status_get_pose", "status_get_lidar_summary",
              "status_list_nodes", "status_list_topics", "status_health"]:
        assert t in registry.names()


def test_yaw_from_quat_zero_is_zero():
    from ugv_tools_api.tools.status import _yaw_from_quat
    class Q: w=1.0; x=0.0; y=0.0; z=0.0
    assert abs(_yaw_from_quat(Q)) < 1e-9


def test_yaw_from_quat_90_deg():
    from ugv_tools_api.tools.status import _yaw_from_quat
    # rotation about Z by 90 deg: w=cos(45), z=sin(45)
    class Q: w=math.cos(math.pi/4); x=0.0; y=0.0; z=math.sin(math.pi/4)
    yaw = _yaw_from_quat(Q)
    assert abs(yaw - math.pi/2) < 1e-6
