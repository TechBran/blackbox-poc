"""
interaction.py — manual /browser/click /browser/type /browser/key /browser/scroll
endpoints used by the Portal live viewer for direct user clicks.

E18 (Brandon 2026-05-17): delegates to ActionExecutor so the Wayland-vs-X11
input routing (ydotool vs xdotool) is shared with the Computer Use agent path.
Previously this module used xdotool directly, which silently failed on native
Wayland apps.
"""

from Orchestrator.browser.config import (
    DISPLAY_WIDTH, DISPLAY_HEIGHT, ACTIVE_DISPLAY,
    NATIVE_MODE, get_scale_factors,
)
from Orchestrator.browser.actions import ActionExecutor


# Single executor instance for the live viewer endpoints. ActionExecutor's
# Wayland/ydotool detection happens once at construction, then sticks for the
# lifetime of the process. Manual clicks share the same routing decision as
# the CU agent.
_EXECUTOR = ActionExecutor()


def _scale_xy(x: int, y: int) -> tuple[int, int]:
    """Clamp to CU model resolution, then scale to real desktop coords."""
    x = max(0, min(int(x), DISPLAY_WIDTH))
    y = max(0, min(int(y), DISPLAY_HEIGHT))
    if NATIVE_MODE:
        sx, sy = get_scale_factors()
        return int(x * sx), int(y * sy)
    return x, y


def click(x: int, y: int, button: str = "left") -> dict:
    real_x, real_y = _scale_xy(x, y)
    if button == "double":
        result = _EXECUTOR.execute("double_click", coordinate=[real_x, real_y])
        return {"success": result.get("success", False), "action": "double_click", "x": x, "y": y}
    action = {"left": "left_click", "middle": "middle_click", "right": "right_click"}.get(button, "left_click")
    result = _EXECUTOR.execute(action, coordinate=[real_x, real_y])
    return {"success": result.get("success", False), "action": "click", "x": x, "y": y, "button": button}


def type_text(text: str) -> dict:
    if not text:
        return {"success": False, "error": "Empty text"}
    result = _EXECUTOR.execute("type", text=text)
    return {"success": result.get("success", False), "action": "type", "length": len(text)}


def press_key(key: str) -> dict:
    if not key:
        return {"success": False, "error": "Empty key"}
    # Keep the existing safety check — the manual /browser/key endpoint should
    # only accept simple key names from untrusted UI input.
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+_")
    if not all(c in allowed_chars for c in key):
        return {"success": False, "error": f"Invalid key name: {key}"}
    result = _EXECUTOR.execute("key", text=key)
    return {"success": result.get("success", False), "action": "key", "key": key}


def scroll(x: int, y: int, direction: str = "down", clicks: int = 3) -> dict:
    real_x, real_y = _scale_xy(x, y)
    clicks = max(1, min(int(clicks), 10))
    result = _EXECUTOR.execute("scroll", coordinate=[real_x, real_y], direction=direction, amount=clicks)
    return {"success": result.get("success", False), "action": "scroll", "direction": direction, "clicks": clicks}
