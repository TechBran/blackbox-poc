"""
Sovereign Browser configuration constants
"""
import os
import subprocess
from pathlib import Path

# ── Native Desktop Mode ──
# When True, CU operates on the real Linux desktop
# instead of a sandboxed Xvfb virtual display.
NATIVE_MODE = True


def _detect_native_display() -> int:
    """Auto-detect the active X11 display number from the running GNOME session.
    Checks gnome-shell, then Xorg process, then falls back to DISPLAY env var.
    """
    # Method 1: Find gnome-shell's DISPLAY from /proc
    try:
        result = subprocess.run(
            ["pgrep", "-x", "gnome-shell"], capture_output=True, text=True, timeout=5
        )
        for pid in result.stdout.strip().split():
            env_file = f"/proc/{pid}/environ"
            if os.path.isfile(env_file):
                try:
                    with open(env_file, "rb") as f:
                        env_data = f.read().decode("utf-8", errors="replace")
                    for entry in env_data.split("\0"):
                        if entry.startswith("DISPLAY=:"):
                            return int(entry.split(":")[1].split(".")[0])
                except (PermissionError, ValueError):
                    continue
    except Exception:
        pass
    # Method 2: Parse Xorg command line for -displayfd or display arg
    try:
        result = subprocess.run(
            ["pgrep", "-a", "Xorg"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            for part in parts:
                if part.startswith(":") and part[1:].isdigit():
                    return int(part[1:])
    except Exception:
        pass
    # Method 3: Environment variable
    display = os.environ.get("DISPLAY", "")
    if display.startswith(":"):
        try:
            return int(display.split(":")[1].split(".")[0])
        except ValueError:
            pass
    return 0  # Last resort fallback


# Real desktop display — auto-detected from running session
NATIVE_DISPLAY_NUMBER = _detect_native_display()


def _detect_native_resolution():
    """Detect the real display resolution via xrandr."""
    try:
        env = {"DISPLAY": f":{NATIVE_DISPLAY_NUMBER}", "PATH": "/usr/bin:/bin"}
        xa = os.environ.get("XAUTHORITY", "")
        if xa:
            env["XAUTHORITY"] = xa
        result = subprocess.run(
            ["xrandr", "--current"],
            capture_output=True, text=True, timeout=5, env=env
        )
        for line in result.stdout.splitlines():
            if "current" in line.lower():
                # "Screen 0: minimum 16 x 16, current 3440 x 1440, ..."
                parts = line.split("current")[1].split(",")[0].strip()
                w, h = parts.split(" x ")
                return int(w.strip()), int(h.strip())
    except Exception:
        pass
    return 1920, 1080  # Fallback for this machine

NATIVE_WIDTH, NATIVE_HEIGHT = _detect_native_resolution()

# CU model resolution — what the model sees and clicks on.
# Fixed at 1280x720 (Anthropic's optimal range for computer_20251124).
# Scale factors are computed from actual desktop → CU resolution.
CU_DISPLAY_WIDTH = 1280
CU_DISPLAY_HEIGHT = 720

# ── Virtual display settings (used when NATIVE_MODE = False) ──
DISPLAY_NUMBER = 99
DISPLAY_WIDTH = CU_DISPLAY_WIDTH if NATIVE_MODE else 1280
DISPLAY_HEIGHT = CU_DISPLAY_HEIGHT if NATIVE_MODE else 720
DISPLAY_DEPTH = 24

# The active display number used throughout the system
ACTIVE_DISPLAY = NATIVE_DISPLAY_NUMBER if NATIVE_MODE else DISPLAY_NUMBER

# Coordinate scaling factors (CU model coords → real desktop coords)
SCALE_X = NATIVE_WIDTH / CU_DISPLAY_WIDTH if NATIVE_MODE else 1.0
SCALE_Y = NATIVE_HEIGHT / CU_DISPLAY_HEIGHT if NATIVE_MODE else 1.0

# X11 auth — needed for systemd services to access the real display
def _detect_xauthority():
    """Find the XAUTHORITY file for the native display.
    On Wayland+XWayland (GNOME/Mutter), it's /run/user/<uid>/.mutter-Xwaylandauth.*
    On plain X11, it's ~/.Xauthority
    """
    # Check if already set in environment
    xa = os.environ.get("XAUTHORITY", "")
    if xa and os.path.isfile(xa):
        return xa
    # XWayland (Mutter)
    import glob
    uid = os.getuid()
    for pattern in [f"/run/user/{uid}/.mutter-Xwaylandauth.*",
                    f"/run/user/{uid}/.Xauthority"]:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    # Classic X11
    home = os.path.expanduser("~")
    classic = os.path.join(home, ".Xauthority")
    if os.path.isfile(classic):
        return classic
    return ""

XAUTHORITY = _detect_xauthority() if NATIVE_MODE else ""


def _detect_dbus_session():
    """Find the D-Bus session bus address for the user session.
    Needed for XDG Portal access from systemd services.
    """
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if addr:
        return addr
    uid = os.getuid()
    bus_path = f"/run/user/{uid}/bus"
    if os.path.exists(bus_path):
        return f"unix:path={bus_path}"
    return ""

DBUS_SESSION_BUS_ADDRESS = _detect_dbus_session() if NATIVE_MODE else ""


def get_native_env() -> dict:
    """Get environment dict suitable for X11/Wayland commands on the native display."""
    env = {"DISPLAY": f":{ACTIVE_DISPLAY}", "PATH": "/usr/bin:/usr/local/bin:/bin"}
    if XAUTHORITY:
        env["XAUTHORITY"] = XAUTHORITY
    if DBUS_SESSION_BUS_ADDRESS:
        env["DBUS_SESSION_BUS_ADDRESS"] = DBUS_SESSION_BUS_ADDRESS
    return env

# Chrome settings
CHROME_PATH = "/opt/google/chrome/chrome"
BROWSER_PKG_DIR = Path(__file__).parent
PROFILE_BASE = BROWSER_PKG_DIR / "profiles"
CDP_PORT = 9222  # Chrome DevTools Protocol debug port

# Agent loop limits
MAX_ITERATIONS = 100
SESSION_TIMEOUT = 300  # seconds

# Anthropic Computer Use API
ANTHROPIC_BETA_HEADER = "computer-use-2025-11-24"
COMPUTER_TOOL_TYPE = "computer_20251124"
CU_MODEL = "claude-opus-4-6"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# API key — imported from main config
from Orchestrator.config import ANTHROPIC_API_KEY

# Domain security
# Always block dangerous domains. In native mode, allow localhost but block cloud metadata.
_COMMON_BLOCKLIST = [
    "169.254.",           # Link-local / cloud metadata
    "metadata.google.internal",
    "metadata.google",
    "metadata.aws",       # AWS IMDS
]

_PRIVATE_NETWORK_BLOCKLIST = [
    "10.",                # Private class A
    "192.168.",           # Private class C
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
]

if NATIVE_MODE:
    # Native mode: allow localhost (agent needs it) but block cloud metadata
    DOMAIN_BLOCKLIST = _COMMON_BLOCKLIST
else:
    # Headless mode: block all private networks and localhost
    DOMAIN_BLOCKLIST = _COMMON_BLOCKLIST + _PRIVATE_NETWORK_BLOCKLIST + [
        "localhost", "127.0.0.1", "0.0.0.0", "::1",
    ]

# None = allow all external domains. Set to list to restrict.
DOMAIN_ALLOWLIST = None


def is_domain_allowed(url: str) -> bool:
    """Check if a URL's domain is allowed for navigation (SSRF prevention)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Block internal/private addresses
        for blocked in DOMAIN_BLOCKLIST:
            if host == blocked or host.startswith(blocked):
                return False
        # If allowlist is set, only allow listed domains
        if DOMAIN_ALLOWLIST is not None:
            return any(host.endswith(d) for d in DOMAIN_ALLOWLIST)
        return True
    except Exception:
        return False
