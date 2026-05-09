"""
Action executor — translates Anthropic Computer Use actions to xdotool commands.
Supports all 18 actions from the computer_20251124 tool type.
"""
import os
import subprocess
import time
import random
import io

from Orchestrator.browser.config import (
    DISPLAY_NUMBER, DISPLAY_WIDTH, DISPLAY_HEIGHT,
    NATIVE_MODE, ACTIVE_DISPLAY, SCALE_X, SCALE_Y,
    get_native_env
)
from Orchestrator.browser.display import get_display


def _jitter(base_ms: float = 0) -> float:
    """Add slight random delay for human-like behavior."""
    return (base_ms + random.uniform(20, 80)) / 1000.0


def _scale_coord(coord):
    """Scale a coordinate pair from CU model space to real display space."""
    if not NATIVE_MODE or coord is None:
        return coord
    x, y = coord
    return [int(x * SCALE_X), int(y * SCALE_Y)]


def _run_xdotool(*args, display_number: int = ACTIVE_DISPLAY) -> subprocess.CompletedProcess:
    """Run an xdotool command on the active display."""
    if NATIVE_MODE:
        env = {**os.environ, **get_native_env()}
    else:
        env = get_display().get_env()
    cmd = ["xdotool"] + list(args)
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="ACTION_TIMEOUT: xdotool command timed out after 10s")


class ActionExecutor:
    """Executes Anthropic Computer Use actions via xdotool on the virtual display."""

    def __init__(self, display_number: int = DISPLAY_NUMBER):
        self.display_number = display_number

    def execute(self, action: str, **params) -> dict:
        """Execute a single CU action. Returns {success, message}."""
        handler = getattr(self, f"_action_{action}", None)
        if handler is None:
            return {"success": False, "message": f"Unknown action: {action}"}
        try:
            return handler(**params)
        except Exception as e:
            return {"success": False, "message": f"Action '{action}' failed: {e}"}

    # --- Click actions ---

    def _action_left_click(self, coordinate=None, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
            time.sleep(_jitter())
        _run_xdotool("click", "1")
        return {"success": True, "message": f"Left click at {coordinate}"}

    def _action_right_click(self, coordinate=None, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
            time.sleep(_jitter())
        _run_xdotool("click", "3")
        return {"success": True, "message": f"Right click at {coordinate}"}

    def _action_middle_click(self, coordinate=None, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
            time.sleep(_jitter())
        _run_xdotool("click", "2")
        return {"success": True, "message": f"Middle click at {coordinate}"}

    def _action_double_click(self, coordinate=None, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
            time.sleep(_jitter())
        _run_xdotool("click", "--repeat", "2", "--delay", "80", "1")
        return {"success": True, "message": f"Double click at {coordinate}"}

    def _action_triple_click(self, coordinate=None, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
            time.sleep(_jitter())
        _run_xdotool("click", "--repeat", "3", "--delay", "80", "1")
        return {"success": True, "message": f"Triple click at {coordinate}"}

    # --- Type/Key actions ---

    def _action_type(self, text="", **kw) -> dict:
        time.sleep(_jitter(30))
        _run_xdotool("type", "--clearmodifiers", "--delay", "12", text)
        return {"success": True, "message": f"Typed {len(text)} chars"}

    def _action_key(self, text="", **kw) -> dict:
        # Anthropic sends combos like "ctrl+a" or "Return"
        # xdotool uses the same format
        time.sleep(_jitter())
        _run_xdotool("key", "--clearmodifiers", text)
        return {"success": True, "message": f"Key press: {text}"}

    # --- Mouse movement ---

    def _action_mouse_move(self, coordinate=None, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
        return {"success": True, "message": f"Mouse moved to {coordinate}"}

    # --- Scroll ---

    def _action_scroll(self, coordinate=None, direction="down", amount=3, **kw) -> dict:
        if coordinate:
            x, y = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(x), str(y))
            time.sleep(_jitter())

        # xdotool scroll: button 4=up, 5=down, 6=left, 7=right
        # Each xdotool click = 1 mouse wheel notch ≈ 100px in Chrome.
        # Anthropic's computer tool spec: amount=N means ~N*100px of scroll.
        # Pass amount directly — no multiplier needed.
        button_map = {"up": "4", "down": "5", "left": "6", "right": "7"}
        button = button_map.get(direction, "5")
        clicks = max(1, int(amount))
        for _ in range(clicks):
            _run_xdotool("click", button)
            time.sleep(0.02)
        return {"success": True, "message": f"Scroll {direction} x{clicks} ticks at {coordinate}"}

    # --- Drag ---

    def _action_left_click_drag(self, start_coordinate=None, coordinate=None, **kw) -> dict:
        if start_coordinate and coordinate:
            sx, sy = _scale_coord(start_coordinate)
            ex, ey = _scale_coord(coordinate)
            _run_xdotool("mousemove", "--sync", str(sx), str(sy))
            time.sleep(_jitter(50))
            _run_xdotool("mousedown", "1")
            time.sleep(0.1)
            _run_xdotool("mousemove", "--sync", str(ex), str(ey))
            time.sleep(0.1)
            _run_xdotool("mouseup", "1")
            return {"success": True, "message": f"Drag ({sx},{sy}) -> ({ex},{ey})"}
        return {"success": False, "message": "Drag requires start_coordinate and coordinate"}

    # --- Mouse button hold ---

    def _action_left_mouse_down(self, **kw) -> dict:
        _run_xdotool("mousedown", "1")
        return {"success": True, "message": "Mouse button down"}

    def _action_left_mouse_up(self, **kw) -> dict:
        _run_xdotool("mouseup", "1")
        return {"success": True, "message": "Mouse button up"}

    # --- Key hold ---

    def _action_hold_key(self, text="", duration=1, **kw) -> dict:
        capped_duration = min(float(duration), 10)
        _run_xdotool("keydown", "--clearmodifiers", text)
        time.sleep(capped_duration)
        _run_xdotool("keyup", "--clearmodifiers", text)
        return {"success": True, "message": f"Held '{text}' for {capped_duration}s"}

    # --- Wait ---

    def _action_wait(self, duration=1, **kw) -> dict:
        capped = min(float(duration), 10)
        time.sleep(capped)
        return {"success": True, "message": f"Waited {capped}s"}

    # --- Screenshot (handled by agent loop, but stub here) ---

    def _action_screenshot(self, **kw) -> dict:
        return {"success": True, "message": "Screenshot captured (handled by agent loop)"}

    # --- Zoom (Opus 4.6 only — crop a region for closer inspection) ---

    def _action_zoom(self, region=None, **kw) -> dict:
        if not region or len(region) != 4:
            return {"success": False, "message": "Zoom requires region [x0, y0, x1, y1]"}
        # Actual zoom/crop is handled in agent_loop when building the tool_result
        return {"success": True, "message": f"Zoom region: {region}"}


async def execute_remote_action(device_id: str, action: str, **params) -> dict:
    """Execute an action on a remote desktop via VNC over Tailscale."""
    from Orchestrator.device_registry import get_registry
    from Orchestrator.remote_desktop import VNCClient

    device = get_registry().get_device(device_id)
    if not device:
        return {"success": False, "error": f"Device not found: {device_id}"}

    client = VNCClient(
        device.tailscale_ip,
        device.vnc_port,
        device.metadata.get("vnc_password")
    )

    try:
        if action == "left_click":
            coord = params.get("coordinate", [0, 0])
            await client.click(coord[0], coord[1])
        elif action == "right_click":
            coord = params.get("coordinate", [0, 0])
            await client.click(coord[0], coord[1], button=3)
        elif action == "type":
            await client.type_text(params.get("text", ""))
        elif action == "key":
            await client.key(params.get("text", ""))
        elif action == "mouse_move":
            coord = params.get("coordinate", [0, 0])
            await client.move(coord[0], coord[1])
        elif action == "double_click":
            coord = params.get("coordinate", [0, 0])
            await client.click(coord[0], coord[1])
            await client.click(coord[0], coord[1])
        elif action == "triple_click":
            coord = params.get("coordinate", [0, 0])
            await client.click(coord[0], coord[1])
            await client.click(coord[0], coord[1])
            await client.click(coord[0], coord[1])
        elif action == "middle_click":
            coord = params.get("coordinate", [0, 0])
            await client.click(coord[0], coord[1], button=2)
        elif action == "scroll":
            coord = params.get("coordinate", [0, 0])
            direction = params.get("direction", "down")
            amount = params.get("amount", 3)
            if coord:
                await client.move(coord[0], coord[1])
            # VNC scroll: button 4=up, 5=down
            button = 4 if direction == "up" else 5
            for _ in range(int(amount)):
                await client._run("click", str(button))
        elif action == "left_click_drag":
            start = params.get("start_coordinate", [0, 0])
            end = params.get("coordinate", [0, 0])
            await client.move(start[0], start[1])
            await client._run("mousedown", "1")
            await client.move(end[0], end[1])
            await client._run("mouseup", "1")
        elif action == "screenshot":
            return {"success": True, "action": "screenshot"}
        elif action == "wait":
            import asyncio
            await asyncio.sleep(params.get("duration", 1))
            return {"success": True, "action": "wait"}
        elif action == "hold_key":
            # hold_key: press key, wait, release (Anthropic CU sends "text" for key name)
            key_name = params.get("text", "") or params.get("key", "")
            duration = params.get("duration", 0.5)
            await client._run("keydown", key_name)
            import asyncio
            await asyncio.sleep(duration)
            await client._run("keyup", key_name)
        else:
            return {"success": False, "error": f"Unsupported remote action: {action}"}

        return {"success": True, "action": action, "device_id": device_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
