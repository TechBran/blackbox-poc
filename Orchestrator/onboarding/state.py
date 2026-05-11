"""Onboarding state — first-run detection and step-completion tracking."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from Orchestrator.utils.paths import resolve

STATE_FILE = resolve(".onboarding_state.json")
COMPLETE_SENTINEL = resolve(".onboarding_complete")

StepName = Literal[
    "welcome",
    "tailscale",
    "api_keys",
    "phone",
    "optional_integrations",
    "pair_phone",
    "operator",
    "done",
]

ALL_STEPS: list[StepName] = [
    "welcome", "tailscale", "api_keys",
    "phone", "optional_integrations",
    "pair_phone", "operator", "done",
]


class OnboardingState:
    """Persistent onboarding progress state.

    Stored as JSON in {BLACKBOX_ROOT}/.onboarding_state.json.
    Marker file {BLACKBOX_ROOT}/.onboarding_complete signals 'done' to other code.
    """

    def __init__(self) -> None:
        self._data: dict = self._load()

    def _load(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except Exception:
                pass
        return {
            "started_at": time.time(),
            "completed_steps": [],
            "skipped_steps": [],
            "current_step": "welcome",
        }

    def _save(self) -> None:
        STATE_FILE.write_text(json.dumps(self._data, indent=2))

    def is_complete(self) -> bool:
        return COMPLETE_SENTINEL.exists()

    def mark_step_complete(self, step: StepName) -> None:
        if step not in self._data["completed_steps"]:
            self._data["completed_steps"].append(step)
        if step in self._data["skipped_steps"]:
            self._data["skipped_steps"].remove(step)
        self._save()

    def mark_step_skipped(self, step: StepName) -> None:
        if step not in self._data["skipped_steps"]:
            self._data["skipped_steps"].append(step)
        if step in self._data["completed_steps"]:
            self._data["completed_steps"].remove(step)
        self._save()

    def set_current(self, step: StepName) -> None:
        self._data["current_step"] = step
        self._save()

    def mark_complete(self) -> None:
        """Final marker — wizard is done."""
        COMPLETE_SENTINEL.write_text(f"completed_at={int(time.time())}\n")
        self._data["completed_at"] = time.time()
        self._save()

    def reset(self) -> None:
        """Clear onboarding state (for re-runs)."""
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        if COMPLETE_SENTINEL.exists():
            COMPLETE_SENTINEL.unlink()
        self._data = self._load()

    def snapshot(self) -> dict:
        """Return state dict for /onboarding/state response."""
        return {
            "is_complete": self.is_complete(),
            "completed_steps": self._data["completed_steps"],
            "skipped_steps": self._data["skipped_steps"],
            "current_step": self._data["current_step"],
            "all_steps": ALL_STEPS,
        }
