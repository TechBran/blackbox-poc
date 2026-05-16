"""Tailscale wizard actuator — long-running operations the onboarding UI
   triggers (authenticate, cert, etc). All sudo wrapped via the NOPASSWD
   sudoers entry from install.sh Step 4e.

   Audit refs:
   - C3 (double-click race): per-operation asyncio.Lock
   - C4 (shell injection): subprocess argv lists only, hostname sanitized
   - M5 (sudoers idempotency): handled in install.sh, not here
   - M6 (auth timeout): 15s timeout waiting for Login URL line
   - E1 (T3 skipped): Tailscale pre-installed by install.sh; no install
     stream endpoint needed.
"""
import asyncio
import re
import shutil
import socket
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# One lock per operation so install + auth can serialize independently.
_install_lock = asyncio.Lock()
_up_lock = asyncio.Lock()

# Background process handle for active `tailscale up` — needed to poll
# completion + clean up when status transitions to Running OR timeout.
_active_up_process: subprocess.Popen | None = None
_active_up_login_url: str | None = None

LOGIN_URL_PATTERN = re.compile(r"https://login\.tailscale\.com/a/[a-zA-Z0-9]+")


@asynccontextmanager
async def operation_lock(lock: asyncio.Lock, name: str):
    """Acquire lock or raise 409-equivalent."""
    if lock.locked():
        raise RuntimeError(f"{name} already in progress")
    async with lock:
        yield


def _safe_hostname() -> str:
    """Return a sanitized hostname suitable for --hostname= flag.
    Audit C4: only [a-z0-9-] allowed."""
    raw = socket.gethostname().lower()
    safe = re.sub(r"[^a-z0-9-]", "-", raw).strip("-")
    return safe or "blackbox"


async def start_up() -> str:
    """Start `tailscale up`, capture login URL, leave process running.
    Returns the login URL. Caller must hold _up_lock until poll completes."""
    global _active_up_process, _active_up_login_url

    # Pre-flight: daemon alive? (audit M6)
    try:
        check = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=3,
        )
        # rc != 0 with "not running" is OK (NeedsLogin); rc != 0 with
        # other errors means daemon is dead.
        if check.returncode != 0 and "daemon" in check.stderr.lower():
            raise RuntimeError(
                "tailscaled daemon not running — try `sudo systemctl restart tailscaled`"
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError("tailscaled status check timed out")

    hostname = _safe_hostname()
    cmd = ["sudo", "-n", "/usr/bin/tailscale", "up",
           "--accept-dns=true", f"--hostname={hostname}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Wait up to 15s for the Login URL line (audit M6)
    login_url = None
    try:
        async with asyncio.timeout(15):
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                m = LOGIN_URL_PATTERN.search(line.decode("utf-8", errors="replace"))
                if m:
                    login_url = m.group(0)
                    break
    except TimeoutError:
        proc.terminate()
        raise RuntimeError("Timeout waiting for Tailscale login URL")

    if not login_url:
        proc.terminate()
        raise RuntimeError("Tailscale did not emit a login URL")

    _active_up_process = proc
    _active_up_login_url = login_url

    # Best-effort: open in default browser (audit I1)
    try:
        subprocess.Popen(["xdg-open", login_url],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass  # UI also renders clickable link

    return login_url


async def poll_up() -> dict:
    """Check whether the active `tailscale up` has authenticated.
    Returns {state: 'running'|'pending'|'failed', detail: ...}"""
    global _active_up_process, _active_up_login_url

    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            import json as _json
            data = _json.loads(result.stdout)
            backend = data.get("BackendState", "unknown")
            if backend == "Running":
                # Clean up the up-process
                if _active_up_process is not None:
                    try:
                        _active_up_process.wait(timeout=2)
                    except Exception:
                        _active_up_process.terminate()
                    _active_up_process = None
                    _active_up_login_url = None
                return {"state": "running", "detail": data.get("Self", {})}
            return {"state": "pending", "backend_state": backend,
                    "login_url": _active_up_login_url}
    except Exception as e:
        return {"state": "failed", "detail": str(e)}

    return {"state": "pending", "login_url": _active_up_login_url}


async def cancel_up() -> None:
    global _active_up_process, _active_up_login_url
    if _active_up_process is not None:
        _active_up_process.terminate()
        try:
            _active_up_process.wait(timeout=2)
        except Exception:
            _active_up_process.kill()
        _active_up_process = None
    _active_up_login_url = None
