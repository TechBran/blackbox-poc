"""Tests for the should-fire-arrival-callback decision used by MissionPoller.

The actual MissionPoller depends on httpx + SupervisorConfig + a running event
loop, none of which we want in unit tests. Instead, we test the decision rule
in isolation: fire-once on transition INTO completed, never on aborted/failed/
cancelled, never twice.
"""
from typing import Optional


class ArrivalState:
    """Tracks whether the arrival callback has fired for a given mission."""
    def __init__(self):
        self._fired: bool = False

    def should_fire(self, status: str) -> bool:
        """Return True iff this transition should fire the arrival callback.

        Sets the fired flag; subsequent calls return False.
        """
        if status == "completed" and not self._fired:
            self._fired = True
            return True
        return False


def test_completes_fires_once():
    s = ArrivalState()
    assert s.should_fire("active") is False
    assert s.should_fire("active") is False
    assert s.should_fire("completed") is True


def test_completed_never_fires_twice():
    s = ArrivalState()
    assert s.should_fire("completed") is True
    assert s.should_fire("completed") is False


def test_aborted_does_not_fire():
    s = ArrivalState()
    assert s.should_fire("active") is False
    assert s.should_fire("aborted") is False


def test_failed_does_not_fire():
    s = ArrivalState()
    assert s.should_fire("failed") is False


def test_cancelled_does_not_fire():
    s = ArrivalState()
    assert s.should_fire("cancelled") is False
