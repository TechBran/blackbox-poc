import time
from ugv_tools_api.ros_bridge import RosBridge


def test_bridge_starts_and_shuts_down_cleanly():
    b = RosBridge()
    b.start()
    time.sleep(0.5)
    assert b.is_running()
    b.stop()
    assert not b.is_running()


def test_bridge_start_is_idempotent():
    b = RosBridge()
    b.start()
    b.start()  # Second call must not raise or re-init
    assert b.is_running()
    b.stop()
    assert not b.is_running()


def test_bridge_restart():
    b = RosBridge()
    b.start()
    time.sleep(0.3)
    b.stop()
    b.start()
    time.sleep(0.3)
    assert b.is_running()
    b.stop()
