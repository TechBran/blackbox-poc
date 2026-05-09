"""HTTP control plane for the supervisor service.

The Supervisor process is always running; its Gemini Live session is
NOT. The session is wake-gated: idle state holds no WebSocket and no
mic, so ugv-ears owns the USB mic for wake-word detection. This file
exposes the endpoints that drive that state machine.

  - GET /health          liveness probe. Surfaces model id, service
                         running flag, active ER mission id, and whether
                         a Gemini Live session is currently live.
  - POST /open_session    called by ugv-ears when the wake phrase
                         fires. Triggers the supervisor to claim the
                         mic (via MUTE_FLAG handoff) and open a Live
                         session. Idempotent: extra calls while a
                         session is already live are no-ops.
  - POST /stop_session    end the current Live session and return to
                         wake-wait. Service stays up; next wake word
                         opens a fresh session. This is the "operator
                         said 'goodbye'" exit.

The Supervisor runs as an asyncio background task started at FastAPI
app startup and canceled at shutdown. FastAPI's lifespan hooks own
the task's lifetime. A module-level _running flag lets the endpoints
refuse requests that hit outside the serving window (e.g., during
the shutdown-then-restart gap).
"""
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException

from .config import SupervisorConfig, load
from .session import Supervisor


_cfg: SupervisorConfig = load()
_supervisor: Supervisor = Supervisor(_cfg)
_task: Optional[asyncio.Task] = None
_running: bool = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the Supervisor on app startup; cancel it on shutdown.

    Uses FastAPI's lifespan context (the modern replacement for
    on_event("startup") / on_event("shutdown")). The _running flag
    frames the "serving window" so endpoints can refuse outside it.
    """
    global _task, _running
    _task = asyncio.create_task(_supervisor.run())
    _running = True
    try:
        yield
    finally:
        _running = False
        _supervisor.request_stop()
        if _task is not None:
            try:
                # Give the supervisor up to 5s to finish its current
                # session gracefully. If it doesn't, cancel so systemd
                # sees a clean stop.
                await asyncio.wait_for(_task, timeout=5.0)
            except asyncio.TimeoutError:
                _task.cancel()
                try:
                    await _task
                except asyncio.CancelledError:
                    # Expected: we just cancelled it. Let shutdown proceed.
                    pass
                except Exception:
                    # Unexpected tail-end error from Supervisor.run()
                    # cleanup. Don't block shutdown on it.
                    pass
            except Exception:
                pass


app = FastAPI(
    title="UGV Supervisor",
    description=(
        "HTTP control plane for the UGV Beast supervisor daemon. The "
        "supervisor itself runs continuously; this API surface is for "
        "liveness probes and wake-word triggered session opens."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok" if _running else "stopped",
        "running": _running,
        "model": _cfg.model,
        "active_mission_id": _supervisor.tracker.active_id,
        # session_active is True while the Gemini Live WebSocket and the
        # USB mic are held by the supervisor. Lets ops/ears diagnose
        # who currently owns the audio pipe.
        "session_active": _supervisor.active,
    }


@app.post("/open_session")
async def open_session() -> dict:
    """Wake-word-triggered session open.

    Called by ugv-ears when the wake phrase fires. Sets the supervisor's
    session_requested event, which the run loop consumes on its next
    idle cycle: it writes MUTE_FLAG (ears releases the mic), opens
    arecord + aplay, and connects to Gemini Live.

    Idempotent: if a session is already live, the event is set and
    consumed again harmlessly. We return session_active so ears can
    log which path it hit.

    503 during the serving gap prevents ears from quietly logging "OK"
    against a dead supervisor.
    """
    if not _running:
        raise HTTPException(status_code=503, detail="supervisor not running")
    _supervisor.request_session()
    return {"ok": True, "session_active": _supervisor.active}


@app.post("/stop_session")
async def stop_session() -> dict:
    """End the current Gemini Live session; return to wake-wait.

    Sets close_session. The run loop's watchdog task picks it up,
    cancels the mic/response pumps, closes the WebSocket, stops
    arecord + aplay, clears MUTE_FLAG so ears reacquires the mic,
    and loops back to wait for the next wake word.

    The supervisor process STAYS UP. This is the "goodbye" exit —
    not a shutdown. Use systemctl stop ugv-supervisor (which invokes
    the lifespan shutdown path) if you actually want the service
    down.

    Returns immediately; the close happens asynchronously.
    """
    if not _running:
        raise HTTPException(status_code=503, detail="supervisor not running")
    _supervisor.request_close_session()
    return {"ok": True, "note": "current session will close; supervisor returning to wake-wait"}
