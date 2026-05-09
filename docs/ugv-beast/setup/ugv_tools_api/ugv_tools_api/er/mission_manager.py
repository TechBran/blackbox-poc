"""In-memory mission registry with async-safe CRUD and GC."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import config
from .mission import Mission

_missions: dict[str, Mission] = {}
_lock = asyncio.Lock()


async def create(operator: str, text: str) -> Mission:
    async with _lock:
        mid = uuid.uuid4().hex[:12]
        m = Mission(id=mid, operator=operator, text=text)
        _missions[mid] = m
        return m


async def get(mission_id: str) -> Optional[Mission]:
    async with _lock:
        return _missions.get(mission_id)


async def abort(mission_id: str) -> Optional[Mission]:
    async with _lock:
        m = _missions.get(mission_id)
        if m is None:
            return None
        if m.status == "active":
            m.status = "aborted"
            m.end_reason = "aborted_by_operator"
        task = m.task
    if task is not None and not task.done():
        task.cancel()
    return m


async def abort_active() -> list[Mission]:
    """Abort every currently-active mission. Returns the list of aborted missions
    (empty if no missions were active). Used by supervisor's emergency_stop fan-out
    where the caller does not have a specific mission_id in hand."""
    async with _lock:
        active_ids = [mid for mid, m in _missions.items() if m.status == "active"]
        aborted: list[Mission] = []
        tasks_to_cancel: list[asyncio.Task] = []
        for mid in active_ids:
            m = _missions[mid]
            m.status = "aborted"
            m.end_reason = "aborted_by_operator"
            aborted.append(m)
            if m.task is not None and not m.task.done():
                tasks_to_cancel.append(m.task)
    for t in tasks_to_cancel:
        t.cancel()
    return aborted


async def set_task(mission_id: str, task: asyncio.Task) -> None:
    async with _lock:
        m = _missions.get(mission_id)
        if m is not None:
            m.task = task


async def prune() -> int:
    now = datetime.now(timezone.utc)
    removed = 0
    async with _lock:
        for mid in list(_missions.keys()):
            m = _missions[mid]
            if m.status == "active":
                continue
            age = (now - m.created_at).total_seconds()
            if age > config.MISSION_GC_SECONDS:
                _missions.pop(mid, None)
                removed += 1
    return removed


async def list_missions() -> list[dict]:
    async with _lock:
        return [m.to_status_dict() for m in _missions.values()]
