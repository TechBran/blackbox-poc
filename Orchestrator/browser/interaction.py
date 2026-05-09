"""
interaction.py - User interaction with the display via xdotool.
Supports both native desktop (:0) and sandboxed Xvfb (:99) modes.
"""

import os
import subprocess
from Orchestrator.browser.config import (
    DISPLAY_WIDTH, DISPLAY_HEIGHT, ACTIVE_DISPLAY,
    NATIVE_MODE, SCALE_X, SCALE_Y, get_native_env
)

DISPLAY = f":{ACTIVE_DISPLAY}"

BUTTON_MAP = {
    "left": "1",
    "middle": "2",
    "right": "3",
}


def _run_xdotool(*args: str, timeout: float = 5.0) -> dict:
    if NATIVE_MODE:
        env = {**os.environ, **get_native_env()}
    else:
        env = {**os.environ, "DISPLAY": DISPLAY}
    cmd = ["xdotool"] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return {"success": result.returncode == 0, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "xdotool command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def click(x: int, y: int, button: str = "left") -> dict:
    # Clamp to CU model resolution then scale to real desktop
    x = max(0, min(x, DISPLAY_WIDTH))
    y = max(0, min(y, DISPLAY_HEIGHT))
    real_x = int(x * SCALE_X)
    real_y = int(y * SCALE_Y)
    btn = BUTTON_MAP.get(button, "1")

    move_result = _run_xdotool("mousemove", str(real_x), str(real_y))
    if not move_result["success"]:
        return move_result

    if button == "double":
        _run_xdotool("click", "--repeat", "2", "--delay", "80", "1")
        return {"success": True, "action": "double_click", "x": x, "y": y}

    result = _run_xdotool("click", btn)
    return {"success": result["success"], "action": "click", "x": x, "y": y, "button": button}


def type_text(text: str) -> dict:
    if not text:
        return {"success": False, "error": "Empty text"}
    result = _run_xdotool("type", "--delay", "12", "--clearmodifiers", text)
    return {"success": result["success"], "action": "type", "length": len(text)}


def press_key(key: str) -> dict:
    if not key:
        return {"success": False, "error": "Empty key"}
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+_")
    if not all(c in allowed_chars for c in key):
        return {"success": False, "error": f"Invalid key name: {key}"}
    result = _run_xdotool("key", "--clearmodifiers", key)
    return {"success": result["success"], "action": "key", "key": key}


def scroll(x: int, y: int, direction: str = "down", clicks: int = 3) -> dict:
    x = max(0, min(x, DISPLAY_WIDTH))
    y = max(0, min(y, DISPLAY_HEIGHT))
    real_x = int(x * SCALE_X)
    real_y = int(y * SCALE_Y)
    clicks = max(1, min(clicks, 10))
    btn = "4" if direction == "up" else "5"

    move_result = _run_xdotool("mousemove", str(real_x), str(real_y))
    if not move_result["success"]:
        return move_result

    result = _run_xdotool("click", "--repeat", str(clicks), "--delay", "50", btn)
    return {"success": result["success"], "action": "scroll", "direction": direction, "clicks": clicks}
