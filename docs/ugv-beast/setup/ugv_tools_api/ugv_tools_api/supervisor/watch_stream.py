"""1 FPS ambient JPEG push into the Live session.

The supervisor owns one WatchStream per Gemini Live session. When `on=True`,
the loop pulls the latest JPEG from RosCamera once per FPS-window and pushes
it via the injected `send_video(jpeg_bytes, mime_type)` async callback.
When `on=False`, the loop sleeps. Mid-run toggling is supported (see set()).

Token-cost note: 1 FPS at ~30-80 KB/frame is the documented Google ceiling.
Task 6's TokenBudget tracks bytes shipped so we rotate the session before
context fills. Do NOT raise the FPS — Google rejects > 1 FPS.

Transport agnostic: this module no longer imports anything from
google.genai. The session's transport (raw-WS RawLiveSession in production,
fake callback in tests) is decoupled via the send_video callback.
"""
from __future__ import annotations
import asyncio
from typing import Awaitable, Callable, Optional, Protocol


class _CameraLike(Protocol):
    def get_camera_jpeg(self) -> Optional[bytes]: ...


class WatchStream:
    def __init__(
        self,
        camera: _CameraLike,
        fps: float = 1.0,
        on_frame: Optional[Callable[[], None]] = None,
        send_video: Optional[Callable[[bytes, str], Awaitable[None]]] = None,
    ) -> None:
        self._cam = camera
        self._period = 1.0 / fps
        self._on = False
        self._source = "pantilt"
        self._stop = asyncio.Event()
        self._bytes_sent = 0
        self._frames_sent = 0
        # Optional notifier fired AFTER each successful frame push. Used
        # by Task 6's TokenBudget to charge a JPEG against the rotation
        # threshold. Errors inside the callback must NEVER kill the
        # watch loop — swallow them.
        self._on_frame = on_frame
        # Async callback invoked with (jpeg_bytes, mime_type) for each
        # push. Decouples this loop from any specific transport so the
        # supervisor can run over raw-WS or any future replacement
        # without touching this file.
        self._send_video = send_video

    @property
    def is_on(self) -> bool:
        return self._on

    @property
    def stats(self) -> dict:
        return {"on": self._on, "source": self._source,
                "bytes_sent": self._bytes_sent, "frames_sent": self._frames_sent}

    def set(self, *, on: bool, source: str = "pantilt") -> None:
        self._on = bool(on)
        self._source = source

    def set_period(self, fps: float) -> None:
        """Atomically retune the loop's frame period to 1/fps.

        Operator-tunable knob exposed via set_watch_mode(fps=...) so
        Gemini Live can ask to "speed up" or "slow down" the ambient
        feed when a mission needs more or less detail. The loop reads
        self._period at the top of each tick, so the change takes
        effect on the next iteration without a restart.
        """
        if fps <= 0.0:
            raise ValueError(f"fps must be positive, got {fps}")
        self._period = 1.0 / fps

    def stop(self) -> None:
        self._stop.set()

    async def run_with_callback(self) -> None:
        """Run the watch loop, pushing frames via the injected send_video
        callback. Raises RuntimeError if no send_video= was provided.
        """
        if self._send_video is None:
            raise RuntimeError("WatchStream.run_with_callback requires send_video= callback")
        while not self._stop.is_set():
            t0 = asyncio.get_running_loop().time()
            if self._on:
                jpeg = self._cam.get_camera_jpeg() if self._source == "pantilt" else None
                if jpeg:
                    try:
                        await self._send_video(jpeg, "image/jpeg")
                        self._bytes_sent += len(jpeg)
                        self._frames_sent += 1
                        if self._on_frame is not None:
                            try:
                                self._on_frame()
                            except Exception:
                                # Never let a budget bookkeeping error
                                # kill the watch loop.
                                pass
                    except Exception as e:
                        # Swallow & continue; never let a transient send
                        # error kill the watch loop.
                        print(f"[watch] send error swallowed: {type(e).__name__}: {e}")
            elapsed = asyncio.get_running_loop().time() - t0
            sleep = max(0.0, self._period - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep or 0.001)
                break
            except asyncio.TimeoutError:
                pass
