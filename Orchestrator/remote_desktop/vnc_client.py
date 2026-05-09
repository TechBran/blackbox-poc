"""
VNC Client — captures screenshots and sends input to remote desktops over Tailscale.

Used by Computer Use agents to control remote Linux/Windows machines.
Uses vncdotool for VNC operations.
"""
import asyncio
import tempfile
import os
import io
from typing import Tuple, Optional
from PIL import Image


class VNCClient:
    """Lightweight VNC client for CU screenshot/input over Tailscale."""

    def __init__(self, host: str, port: int = 5900, password: Optional[str] = None):
        self.host = host
        self.port = port
        self.password = password
        self._vncdotool_args = ["vncdotool", "-s", f"{host}::{port}"]
        if password:
            self._vncdotool_args.extend(["-p", password])

    async def _run(self, *args, timeout: int = 15) -> Tuple[int, str]:
        """Run a vncdotool command."""
        cmd = self._vncdotool_args + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode() + stderr.decode()
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "VNC command timed out"

    async def screenshot(self, width: int = 1280, height: int = 720) -> bytes:
        """Capture a screenshot from the remote desktop. Returns PNG bytes."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            rc, output = await self._run("capture", tmp_path)
            if rc != 0:
                raise RuntimeError(f"VNC screenshot failed: {output}")
            img = Image.open(tmp_path)
            if img.size != (width, height):
                img = img.resize((width, height), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def click(self, x: int, y: int, button: int = 1):
        """Click at coordinates on the remote desktop."""
        await self._run("move", str(x), str(y))
        await self._run("click", str(button))

    async def type_text(self, text: str):
        """Type text on the remote desktop."""
        await self._run("type", text)

    async def key(self, key: str):
        """Press a key or key combination."""
        await self._run("key", key)

    async def move(self, x: int, y: int):
        """Move mouse to coordinates."""
        await self._run("move", str(x), str(y))

    async def is_reachable(self) -> bool:
        """Check if the VNC server is reachable."""
        try:
            rc, _ = await self._run("capture", "/dev/null", timeout=5)
            return rc == 0
        except Exception:
            return False
