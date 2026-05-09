"""
ADB Commands — translates Computer Use actions into ADB shell commands.

Maps Gemini CU normalized coordinates (0-999) to device screen coordinates.
"""
import asyncio
import base64
import tempfile
import os
from typing import Optional, Tuple, Dict, Any
from Orchestrator.adb.manager import get_adb_manager


class ADBCommands:
    """Execute actions on an Android device via ADB."""

    def __init__(self, device_id: str, screen_width: int = 1080, screen_height: int = 2400):
        self.device_id = device_id
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._adb = get_adb_manager()

    def _get_serial(self) -> str:
        from Orchestrator.device_registry import get_registry
        device = get_registry().get_device(self.device_id)
        return device.connection_string() if device else ""

    async def _shell(self, command: str, timeout: int = 10) -> Tuple[int, str]:
        serial = self._get_serial()
        rc, stdout, stderr = await self._adb._run_adb(
            "-s", serial, "shell", command, timeout=timeout
        )
        return rc, stdout

    def denormalize_coords(self, x: int, y: int) -> Tuple[int, int]:
        """Convert Gemini normalized coords (0-999) to device pixel coords."""
        real_x = int(x / 999 * self.screen_width)
        real_y = int(y / 999 * self.screen_height)
        return real_x, real_y

    async def detect_screen_size(self) -> Tuple[int, int]:
        rc, output = await self._shell("wm size")
        if "x" in output:
            size_str = output.split(":")[-1].strip()
            w, h = size_str.split("x")
            self.screen_width = int(w)
            self.screen_height = int(h)
        return self.screen_width, self.screen_height

    async def screenshot(self) -> bytes:
        """Capture a screenshot from the device. Returns PNG bytes."""
        serial = self._get_serial()
        remote_path = "/sdcard/blackbox_screenshot.png"
        await self._shell(f"screencap -p {remote_path}")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            rc, stdout, stderr = await self._adb._run_adb(
                "-s", serial, "pull", remote_path, tmp_path, timeout=15
            )
            if rc != 0:
                raise RuntimeError(f"ADB pull failed: {stderr}")
            with open(tmp_path, "rb") as f:
                png_bytes = f.read()
            return png_bytes
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            await self._shell(f"rm -f {remote_path}")

    async def screenshot_base64(self) -> str:
        png_bytes = await self.screenshot()
        return base64.b64encode(png_bytes).decode()

    async def tap(self, x: int, y: int, normalized: bool = True) -> Dict:
        orig_x, orig_y = x, y
        if normalized:
            x, y = self.denormalize_coords(x, y)
        print(f"[ADB TAP] norm({orig_x},{orig_y}) -> pixel({x},{y}) screen={self.screen_width}x{self.screen_height}")
        rc, output = await self._shell(f"input tap {x} {y}")
        return {"success": rc == 0, "action": "tap", "x": x, "y": y}

    async def long_press(self, x: int, y: int, duration_ms: int = 1000,
                         normalized: bool = True) -> Dict:
        if normalized:
            x, y = self.denormalize_coords(x, y)
        rc, output = await self._shell(f"input swipe {x} {y} {x} {y} {duration_ms}")
        return {"success": rc == 0, "action": "long_press", "x": x, "y": y}

    async def swipe(self, x1: int, y1: int, x2: int, y2: int,
                    duration_ms: int = 300, normalized: bool = True) -> Dict:
        if normalized:
            x1, y1 = self.denormalize_coords(x1, y1)
            x2, y2 = self.denormalize_coords(x2, y2)
        rc, output = await self._shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
        return {"success": rc == 0, "action": "swipe", "from": [x1, y1], "to": [x2, y2]}

    async def type_text(self, text: str) -> Dict:
        escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        rc, output = await self._shell(f'input text "{escaped}"')
        return {"success": rc == 0, "action": "type", "text": text}

    async def key_event(self, keycode: str) -> Dict:
        rc, output = await self._shell(f"input keyevent {keycode}")
        return {"success": rc == 0, "action": "key", "keycode": keycode}

    async def go_home(self) -> Dict:
        return await self.key_event("KEYCODE_HOME")

    async def go_back(self) -> Dict:
        return await self.key_event("KEYCODE_BACK")

    async def open_app(self, package_name: str, activity: Optional[str] = None) -> Dict:
        if activity:
            rc, output = await self._shell(f"am start -n {package_name}/{activity}")
        else:
            rc, output = await self._shell(
                f"monkey -p {package_name} -c android.intent.category.LAUNCHER 1"
            )
        return {"success": rc == 0, "action": "open_app", "package": package_name}

    async def get_current_app(self) -> str:
        rc, output = await self._shell(
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"
        )
        return output

    async def scroll_down(self, x: int = 500, y: int = 500,
                          normalized: bool = True) -> Dict:
        if normalized:
            x, y = self.denormalize_coords(x, y)
        # Scroll ~40% of screen height, clamped to valid range
        scroll_dist = int(self.screen_height * 0.4)
        end_y = max(0, y - scroll_dist)
        print(f"[ADB SCROLL DOWN] at ({x},{y}) -> ({x},{end_y}) dist={scroll_dist}")
        rc, output = await self._shell(f"input swipe {x} {y} {x} {end_y} 300")
        return {"success": rc == 0, "action": "scroll_down"}

    async def scroll_up(self, x: int = 500, y: int = 500,
                        normalized: bool = True) -> Dict:
        if normalized:
            x, y = self.denormalize_coords(x, y)
        scroll_dist = int(self.screen_height * 0.4)
        end_y = min(self.screen_height, y + scroll_dist)
        print(f"[ADB SCROLL UP] at ({x},{y}) -> ({x},{end_y}) dist={scroll_dist}")
        rc, output = await self._shell(f"input swipe {x} {y} {x} {end_y} 300")
        return {"success": rc == 0, "action": "scroll_up"}
