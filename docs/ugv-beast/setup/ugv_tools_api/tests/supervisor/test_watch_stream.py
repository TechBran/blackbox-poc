import asyncio
import pytest

from ugv_tools_api.supervisor.watch_stream import WatchStream


class _FakeCam:
    def __init__(self, jpeg=b"\xff\xd8\xff\xe0fake"):
        self.jpeg = jpeg
    def get_camera_jpeg(self): return self.jpeg
    def get_costmap_png(self): return None


def _make_recording_send_video():
    """Return (send_video coroutine, calls list).

    Tests construct one via this factory and pass `send_video=` to
    WatchStream so the loop sends frames into `calls` instead of an
    SDK Blob over a fake session.
    """
    calls: list[tuple[str, int]] = []

    async def send_video(jpeg: bytes, mime_type: str) -> None:
        calls.append((mime_type, len(jpeg)))

    return send_video, calls


@pytest.mark.asyncio
async def test_aaa_warm_up_event_loop():
    """Absorb asyncio cold-start cost so subsequent cadence-sensitive
    tests in this file don't get penalized by it. The 'aaa' prefix
    ensures pytest runs this first regardless of definition order.
    """
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_no_push_when_off():
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0, send_video=send_video)
    w.set(on=False, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.25)
    w.stop()
    await task
    assert calls == []


@pytest.mark.asyncio
async def test_pushes_at_1hz_when_on():
    """Verify the watch loop pushes frames repeatedly when on=True."""
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    w = WatchStream(cam, fps=10.0, send_video=send_video)
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(1.0)
    w.stop()
    await task
    assert 2 <= len(calls) <= 15, (
        f"watch loop cadence wrong: got {len(calls)} pushes in 1.0s at fps=10 (expected 2-15)"
    )
    # Confirm mime/payload shape
    assert all(mt == "image/jpeg" for mt, _ in calls)
    assert all(n == len(b"\xff\xd8\xff\xe0fake") for _, n in calls)


@pytest.mark.asyncio
async def test_toggle_mid_run():
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0, send_video=send_video)
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.1)
    assert calls == []
    w.set(on=True, source="pantilt")
    await asyncio.sleep(0.25)
    pre_off = len(calls)
    w.set(on=False, source="pantilt")
    await asyncio.sleep(0.2)
    w.stop()
    await task
    assert len(calls) == pre_off  # no new pushes after off


@pytest.mark.asyncio
async def test_skips_when_no_jpeg_available():
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam(jpeg=None)
    w = WatchStream(cam, fps=20.0, send_video=send_video)
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.2)
    w.stop()
    await task
    assert calls == []


@pytest.mark.asyncio
async def test_run_with_callback_raises_when_no_send_video_provided():
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0)  # no send_video=
    w.set(on=True, source="pantilt")
    with pytest.raises(RuntimeError, match="requires send_video"):
        await w.run_with_callback()


@pytest.mark.asyncio
async def test_on_frame_callback_still_fires_on_each_push():
    """Verify the existing on_frame budget-charge hook continues to
    fire after the transport port — this is what budget.py uses to
    track JPEG-frame token charges (Task 6 of prior plan)."""
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    on_frame_count = [0]
    def bump():
        on_frame_count[0] += 1
    w = WatchStream(cam, fps=20.0, on_frame=bump, send_video=send_video)
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.25)
    w.stop()
    await task
    assert len(calls) >= 2
    assert on_frame_count[0] == len(calls)  # one bump per send_video call


def test_set_period_updates_period_atomically():
    """set_period(fps) must update _period to 1/fps so the next loop
    iteration picks up the new cadence. Operator-tunable knob exposed
    via set_watch_mode(fps=...) in T1 of the embodied-observer plan."""
    cam = _FakeCam()
    w = WatchStream(cam, fps=1.0)
    assert w._period == 1.0
    w.set_period(0.25)
    assert w._period == 4.0
    w.set_period(1.0)
    assert w._period == 1.0


def test_default_on_pattern_at_session_creation():
    """When config.WATCH_DEFAULT_ON=True, the session creation path
    calls watch.set(on=True, source='pantilt'), so is_on becomes True
    before the loop starts. Mirrors the session.py wire-up."""
    from ugv_tools_api.supervisor import config as cfg_mod
    cam = _FakeCam()
    w = WatchStream(cam, fps=cfg_mod.WATCH_FPS)
    assert cfg_mod.WATCH_DEFAULT_ON is True
    w.set(on=cfg_mod.WATCH_DEFAULT_ON, source="pantilt")
    assert w.is_on is True
