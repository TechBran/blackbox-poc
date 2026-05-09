"""Persist Gemini Live session_resumption handles across process restarts.

Google Live sessions emit SessionResumptionUpdate events carrying a handle
that lets us resume a session later. Handles are valid for 2 hr after the
session terminates. We persist the newest handle to disk so a systemd
restart (or brief crash) can resume the operator's mission conversation
without starting over.

Writes are atomic via tmp-file + rename so a crash mid-write cannot leave
a corrupt handle file. Task 8's session controller writes "every few
seconds" on every SessionResumptionUpdate event, which multiplies the
crash window enough to make non-atomic writes a real risk.
"""
from pathlib import Path
from typing import Optional, Union


class HandleStore:
    def __init__(self, path: Union[str, Path]):
        self._path = Path(path)

    def set(self, handle: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(handle)
        tmp.replace(self._path)  # atomic on POSIX

    def get(self) -> Optional[str]:
        if not self._path.exists():
            return None
        text = self._path.read_text().strip()
        return text or None

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
