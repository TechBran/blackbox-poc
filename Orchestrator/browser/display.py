"""
Virtual display management using Xvfb
"""
import subprocess
import os
import time
import shutil

from Orchestrator.browser.config import (
    DISPLAY_NUMBER, DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_DEPTH,
    NATIVE_MODE, ACTIVE_DISPLAY
)


class VirtualDisplay:
    """Manages an Xvfb virtual display with openbox window manager for headless automation."""

    def __init__(self, display_number=DISPLAY_NUMBER,
                 width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, depth=DISPLAY_DEPTH):
        self.display_number = display_number
        self.width = width
        self.height = height
        self.depth = depth
        self.process = None
        self.wm_process = None  # openbox window manager
        self.vnc_process = None  # x11vnc for VNC access
        self.display_str = f":{display_number}"

    def start(self) -> bool:
        """Start the Xvfb virtual display. Returns True if started (or already running).
        In native mode, just ensure VNC is running on the real display.
        """
        if NATIVE_MODE:
            self.display_str = f":{ACTIVE_DISPLAY}"
            if not self._is_vnc_running():
                self._start_vnc_server()
            return True

        if self.is_running():
            print(f"[DISPLAY] Xvfb {self.display_str} already running")
            # Ensure window manager is also running
            if not self._is_wm_running():
                self._start_window_manager()
            # Ensure VNC server is running
            if not self._is_vnc_running():
                self._start_vnc_server()
            return True

        # Kill any stale Xvfb on this display
        subprocess.run(
            ["pkill", "-f", f"Xvfb {self.display_str}"],
            capture_output=True
        )
        time.sleep(0.5)

        cmd = [
            "Xvfb", self.display_str,
            "-screen", "0", f"{self.width}x{self.height}x{self.depth}",
            "-nolisten", "tcp",
            "-ac"  # Disable access control for local use
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)  # Give Xvfb time to start

            if self.process.poll() is not None:
                print(f"[DISPLAY] Xvfb failed to start (exit code {self.process.returncode})")
                return False

            print(f"[DISPLAY] Xvfb {self.display_str} started ({self.width}x{self.height})")

            # Start openbox window manager for proper window management
            self._start_window_manager()

            # Start x11vnc for VNC fallback access
            self._start_vnc_server()

            return True
        except FileNotFoundError:
            print("[DISPLAY] Xvfb not found. Install with: sudo apt-get install xvfb")
            return False
        except Exception as e:
            print(f"[DISPLAY] Failed to start Xvfb: {e}")
            return False

    def _start_window_manager(self):
        """Start openbox on the virtual display for window management."""
        env = self.get_env()
        try:
            # Kill any existing openbox on this display
            subprocess.run(
                ["pkill", "-f", f"openbox.*DISPLAY={self.display_str}"],
                capture_output=True, env=env
            )
            time.sleep(0.3)

            self.wm_process = subprocess.Popen(
                ["openbox", "--config-file", "/dev/null"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(0.5)

            if self.wm_process.poll() is not None:
                print(f"[DISPLAY] openbox failed to start (exit {self.wm_process.returncode})")
                self.wm_process = None
            else:
                print(f"[DISPLAY] openbox window manager started on {self.display_str}")
        except FileNotFoundError:
            print("[DISPLAY] openbox not found — no window manager (install: sudo apt-get install openbox)")
        except Exception as e:
            print(f"[DISPLAY] Failed to start openbox: {e}")

    def _start_vnc_server(self):
        """Start x11vnc on the virtual display for remote viewing."""
        if not shutil.which("x11vnc"):
            print("[DISPLAY] x11vnc not installed — VNC access unavailable. Install with: sudo apt-get install x11vnc")
            return

        try:
            # Kill any existing x11vnc
            subprocess.run(["pkill", "-f", "x11vnc"], capture_output=True)
            time.sleep(0.3)

            self.vnc_process = subprocess.Popen(
                [
                    "x11vnc",
                    "-display", self.display_str,
                    "-forever",
                    "-shared",
                    "-nopw",
                    "-listen", "127.0.0.1",
                    "-rfbport", "5900",
                    "-noxdamage",
                    "-quiet",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
            if self.vnc_process.poll() is not None:
                print(f"[DISPLAY] x11vnc failed to start (exit {self.vnc_process.returncode})")
                self.vnc_process = None
            else:
                print(f"[DISPLAY] x11vnc started on localhost:5900 (PID {self.vnc_process.pid})")
        except Exception as e:
            print(f"[DISPLAY] Failed to start x11vnc: {e}")

    def _is_vnc_running(self) -> bool:
        """Check if x11vnc is running."""
        if self.vnc_process and self.vnc_process.poll() is None:
            return True
        result = subprocess.run(
            ["pgrep", "-f", "x11vnc"],
            capture_output=True, text=True
        )
        return result.returncode == 0

    def _is_wm_running(self) -> bool:
        """Check if openbox window manager is running on this display."""
        if self.wm_process and self.wm_process.poll() is None:
            return True
        result = subprocess.run(
            ["pgrep", "-f", "openbox"],
            capture_output=True, text=True
        )
        return result.returncode == 0

    def stop(self):
        """Stop VNC, window manager, and Xvfb virtual display.
        In native mode, only stops VNC (don't touch the real desktop).
        """
        if NATIVE_MODE:
            if self.vnc_process:
                self.vnc_process.terminate()
                try:
                    self.vnc_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.vnc_process.kill()
                self.vnc_process = None
                print("[DISPLAY] x11vnc stopped (native mode)")
            return

        if self.vnc_process:
            self.vnc_process.terminate()
            try:
                self.vnc_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.vnc_process.kill()
            self.vnc_process = None
            print("[DISPLAY] x11vnc stopped")

        if self.wm_process:
            self.wm_process.terminate()
            try:
                self.wm_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.wm_process.kill()
            self.wm_process = None
            print(f"[DISPLAY] openbox stopped")

        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            print(f"[DISPLAY] Xvfb {self.display_str} stopped")

    def is_running(self) -> bool:
        """Check if the virtual display is active. Native mode is always 'running'."""
        if NATIVE_MODE:
            return True
        # Check our process
        if self.process and self.process.poll() is None:
            return True
        # Check if any Xvfb is on our display
        result = subprocess.run(
            ["pgrep", "-f", f"Xvfb {self.display_str}"],
            capture_output=True, text=True
        )
        return result.returncode == 0

    def get_env(self) -> dict:
        """Return environment dict with DISPLAY set."""
        env = os.environ.copy()
        env["DISPLAY"] = f":{ACTIVE_DISPLAY}" if NATIVE_MODE else self.display_str
        return env

    def health_check(self) -> bool:
        """Verify display is functional by attempting a dummy screenshot."""
        if NATIVE_MODE:
            return True  # Real desktop is always functional
        if not self.is_running():
            return False
        try:
            result = subprocess.run(
                ["scrot", "--overwrite", "/tmp/xvfb_health_check.png"],
                env=self.get_env(), capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False


# Singleton display instance for the application
_display = None

def get_display() -> VirtualDisplay:
    """Get or create the singleton VirtualDisplay."""
    global _display
    if _display is None:
        _display = VirtualDisplay()
    return _display

def ensure_display_running() -> bool:
    """Ensure the virtual display is running. Returns True on success."""
    display = get_display()
    if not display.is_running():
        return display.start()
    return True
