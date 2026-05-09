"""Streams ER mission progress back into a NON_BLOCKING tool call.

Spawned by the supervisor right after a successful dispatch_er_mission HTTP
call. Polls ER's /mission/{id} and tools_api's status_get_pose on a 1 Hz
tick, sending FunctionResponse parts under the original tool_call id:

  * 1 Hz SILENT pose ticks while the mission is active
  * WHEN_IDLE on Nav2 state transitions (any change, including
    pending->active and active->near-goal events emitted by ER)
  * Terminal (will_continue=False) WHEN_IDLE with the full result on
    completed / failed / aborted / cancelled

Cancel paths:
  * cancel_er_mission tool fires -> session calls poller.cancel() which
    sends a terminal "cancelled" and exits
  * emergency_stop tool fires -> same as cancel (the supervisor will also
    issue cancel_er_mission separately if it wants to end the task)
  * session ends/reconnects -> the inner reconnect loop calls cancel(); a
    new session can re-spawn a poller from the active mission_id if
    self._tracker.active_id is still set.

Regression guards honored:
  * #11 — pose comes from status_get_pose (EKF-fused /odom), not raw encoders
  * #2  — runs on the main asyncio loop, NOT the mic's dedicated executor
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

from .config import SupervisorConfig


_TERMINAL = {"completed", "failed", "aborted", "cancelled"}


async def _fetch_status(cfg: SupervisorConfig, mission_id: str) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
        r = await c.get(f"{cfg.er_url}/mission/{mission_id}")
        if r.status_code >= 400:
            return {"status": "failed", "error": f"http {r.status_code}", "events": []}
        return r.json()


async def _fetch_pose(cfg: SupervisorConfig) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
        r = await c.post(f"{cfg.tools_api_url}/tool/status_get_pose", json={})
        if r.status_code >= 400:
            return None
        try:
            return r.json().get("result", {}) or None
        except ValueError:
            return None


@dataclass
class PollerCallbacks:
    """The session injects three coroutines so the poller doesn't need to
    know how to talk to the Gemini Live session directly. Each takes a
    JSON-serializable body dict; the session wraps it in a FunctionResponse
    with the appropriate scheduling and id.
    """
    send_silent: Callable[[dict], Awaitable[None]]
    send_when_idle: Callable[[dict], Awaitable[None]]
    send_terminal: Callable[[dict], Awaitable[None]]


class MissionPoller:
    def __init__(
        self,
        cfg: SupervisorConfig,
        *,
        fc_id: str,
        fc_name: str,
        mission_id: str,
        callbacks: PollerCallbacks,
        tick_s: float = 1.0,
    ) -> None:
        self._cfg = cfg
        self._fc_id = fc_id
        self._fc_name = fc_name
        self._mission_id = mission_id
        self._cb = callbacks
        self._tick_s = tick_s
        self._cancelled = asyncio.Event()
        self._last_status: Optional[str] = None

    @property
    def fc_id(self) -> str:
        return self._fc_id

    @property
    def mission_id(self) -> str:
        return self._mission_id

    def cancel(self) -> None:
        """Idempotent. Wakes the poller; it sends a terminal cancelled and exits."""
        self._cancelled.set()

    async def run(self) -> None:
        try:
            while not self._cancelled.is_set():
                status_doc = await _fetch_status(self._cfg, self._mission_id)
                pose = await _fetch_pose(self._cfg)
                status = status_doc.get("status", "unknown")

                if self._last_status is not None and status != self._last_status:
                    await self._cb.send_when_idle({
                        "event": "state_transition",
                        "from": self._last_status, "to": status,
                        "mission_id": self._mission_id,
                    })
                self._last_status = status

                if status in _TERMINAL:
                    await self._cb.send_terminal({
                        "mission_status": status,
                        "mission_id": self._mission_id,
                        "result": status_doc,
                    })
                    return

                tick = {
                    "mission_status": status,
                    "mission_id": self._mission_id,
                    "distance_to_goal": status_doc.get("distance_to_goal"),
                }
                if pose:
                    tick["pose"] = pose
                await self._cb.send_silent(tick)

                try:
                    await asyncio.wait_for(self._cancelled.wait(), timeout=self._tick_s)
                except asyncio.TimeoutError:
                    pass
            # Cancelled externally — emit terminal cancelled
            await self._cb.send_terminal({
                "mission_status": "cancelled",
                "mission_id": self._mission_id,
            })
        except Exception as e:
            # Never crash the supervisor over a poller failure. Best-effort
            # terminal so the model isn't left waiting forever.
            try:
                await self._cb.send_terminal({
                    "mission_status": "failed",
                    "mission_id": self._mission_id,
                    "error": f"{type(e).__name__}: {e}",
                })
            except Exception:
                pass
