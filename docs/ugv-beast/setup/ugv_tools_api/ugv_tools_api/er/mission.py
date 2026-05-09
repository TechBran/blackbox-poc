"""Mission dataclass — one object per live mission loop."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from . import config

MissionStatus = Literal["active", "completed", "failed", "aborted"]


@dataclass
class Mission:
    id: str
    operator: str
    text: str
    status: MissionStatus = "active"
    contents: list[Any] = field(default_factory=list)
    step_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_assistant_text: str = ""
    events: deque = field(default_factory=lambda: deque(maxlen=config.EVENTS_RING_SIZE))
    task: Optional[Any] = None
    end_reason: str = ""

    def add_event(self, event: dict) -> None:
        self.events.append(event)

    def recent_events(self, n: int = 20) -> list[dict]:
        if n <= 0:
            return []
        return list(self.events)[-n:]

    def to_status_dict(self) -> dict:
        return {
            "mission_id": self.id,
            "operator": self.operator,
            "mission": self.text,
            "status": self.status,
            "step_count": self.step_count,
            "created_at": self.created_at.isoformat(),
            "last_assistant_text": self.last_assistant_text,
            "end_reason": self.end_reason,
            "events": self.recent_events(20),
        }
