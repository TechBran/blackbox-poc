# Multi-Provider Computer Use + ADB + Tailscale Device Registry

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable any BlackBox AI model to control any device on the Tailscale mesh network — Android devices via ADB (Gemini CU), desktops via VNC/xdotool (Anthropic CU), and browsers via any provider — all routed through a unified device registry.

**Architecture:** Three independent CU provider modules (Anthropic existing, Gemini new, OpenAI future) share a common device registry and action translation layer. Android devices are controlled via ADB over Tailscale. Remote desktops are controlled via VNC over Tailscale. The device registry maps friendly names to Tailscale IPs, device types, and protocols. Tool calls route to the correct provider based on device type.

**Tech Stack:** Python 3.13, FastAPI, google-generativeai SDK, ADB (android-tools-adb), Tailscale mesh networking, existing Anthropic CU infrastructure (browser/ module), httpx for OpenAI Responses API.

**Existing Infrastructure:**
- `Orchestrator/browser/` — Working Anthropic CU with session manager, agent loop, actions, screenshots
- `Orchestrator/config.py` — Has `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
- `Orchestrator/browser/config.py` — CU display config, coordinate scaling, Chrome settings
- Tool registration: 4-part pattern (blackbox_tools.py, chat_routes.py x3 formats, bridge.py, MCP)
- Native desktop mode: X11 display :0, scrot screenshots, xdotool input, 1920x1080 → 1280x720 scaling

---

## Phase 1: Device Registry (Foundation)

Everything depends on this — the registry maps device names to Tailscale IPs, types, and protocols.

### Task 1: Device Registry Data Model + Config

**Files:**
- Create: `Orchestrator/device_registry/__init__.py`
- Create: `Orchestrator/device_registry/registry.py`
- Create: `Orchestrator/device_registry/models.py`
- Create: `Orchestrator/device_registry/devices.json`

**Step 1: Create the package directory**

```bash
mkdir -p /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/device_registry
```

**Step 2: Create models.py with device dataclasses**

```python
"""Device registry data models."""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any
import json


class DeviceType(str, Enum):
    ANDROID = "android"
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"


class DeviceProtocol(str, Enum):
    ADB = "adb"           # Android Debug Bridge (for Android devices)
    LOCAL = "local"       # Local display (xdotool/scrot on this machine)
    VNC = "vnc"           # VNC remote desktop
    RDP = "rdp"           # Windows Remote Desktop


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"         # Currently being controlled by a CU session
    UNKNOWN = "unknown"


@dataclass
class Device:
    """A device on the Tailscale mesh network."""
    id: str                              # Unique slug: "brandon-phone", "work-laptop"
    name: str                            # Human-friendly: "Z Fold 6", "Work Laptop"
    tailscale_ip: str                    # 100.x.x.x (or 127.0.0.1 for local)
    device_type: DeviceType
    protocol: DeviceProtocol
    owner: str                           # Operator who owns this device: "Brandon"
    description: str = ""                # "GPU box with StoryBox", etc.
    adb_port: int = 5555                 # ADB wireless debugging port (Android only)
    vnc_port: int = 5900                 # VNC port (desktop only)
    rdp_port: int = 3389                 # RDP port (Windows only)
    status: DeviceStatus = DeviceStatus.UNKNOWN
    last_seen: Optional[str] = None      # ISO timestamp
    metadata: Dict[str, Any] = field(default_factory=dict)

    def connection_string(self) -> str:
        """Get the connection string for this device."""
        if self.protocol == DeviceProtocol.ADB:
            return f"{self.tailscale_ip}:{self.adb_port}"
        elif self.protocol == DeviceProtocol.VNC:
            return f"{self.tailscale_ip}:{self.vnc_port}"
        elif self.protocol == DeviceProtocol.RDP:
            return f"{self.tailscale_ip}:{self.rdp_port}"
        elif self.protocol == DeviceProtocol.LOCAL:
            return "localhost"
        return self.tailscale_ip

    def to_dict(self) -> dict:
        d = asdict(self)
        d["device_type"] = self.device_type.value
        d["protocol"] = self.protocol.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        data = data.copy()
        data["device_type"] = DeviceType(data["device_type"])
        data["protocol"] = DeviceProtocol(data["protocol"])
        data["status"] = DeviceStatus(data.get("status", "unknown"))
        return cls(**data)
```

**Step 3: Create devices.json with initial device config**

```json
{
  "devices": [
    {
      "id": "blackbox",
      "name": "Black Box (Local)",
      "tailscale_ip": "127.0.0.1",
      "device_type": "linux",
      "protocol": "local",
      "owner": "Brandon",
      "description": "Mini-ITX running BlackBox Orchestrator"
    }
  ]
}
```

Note: Brandon will add his real Tailscale IPs later. The registry supports runtime additions via API.

**Step 4: Create registry.py with CRUD + health checking**

```python
"""
Device Registry — manages all devices on the Tailscale mesh.

Usage:
    from Orchestrator.device_registry import get_registry
    registry = get_registry()
    device = registry.get_device("brandon-phone")
    android_devices = registry.get_devices_by_type(DeviceType.ANDROID)
"""
import json
import asyncio
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Dict
from .models import Device, DeviceType, DeviceProtocol, DeviceStatus

DEVICES_FILE = Path(__file__).parent / "devices.json"


class DeviceRegistry:
    """Singleton registry of all controllable devices on the Tailscale mesh."""

    def __init__(self):
        self._devices: Dict[str, Device] = {}
        self._load_from_file()

    def _load_from_file(self):
        """Load devices from devices.json."""
        if DEVICES_FILE.exists():
            with open(DEVICES_FILE) as f:
                data = json.load(f)
            for d in data.get("devices", []):
                device = Device.from_dict(d)
                self._devices[device.id] = device
        print(f"[DEVICE REGISTRY] Loaded {len(self._devices)} devices")

    def _save_to_file(self):
        """Persist devices to devices.json."""
        data = {"devices": [d.to_dict() for d in self._devices.values()]}
        with open(DEVICES_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def get_device(self, device_id: str) -> Optional[Device]:
        return self._devices.get(device_id)

    def get_all_devices(self) -> List[Device]:
        return list(self._devices.values())

    def get_devices_by_type(self, device_type: DeviceType) -> List[Device]:
        return [d for d in self._devices.values() if d.device_type == device_type]

    def get_devices_by_protocol(self, protocol: DeviceProtocol) -> List[Device]:
        return [d for d in self._devices.values() if d.protocol == protocol]

    def get_devices_by_owner(self, owner: str) -> List[Device]:
        return [d for d in self._devices.values()
                if d.owner.lower() == owner.lower()]

    def get_android_devices(self) -> List[Device]:
        return self.get_devices_by_type(DeviceType.ANDROID)

    def get_desktop_devices(self) -> List[Device]:
        return [d for d in self._devices.values()
                if d.device_type in (DeviceType.LINUX, DeviceType.WINDOWS, DeviceType.MACOS)]

    def add_device(self, device: Device) -> Device:
        self._devices[device.id] = device
        self._save_to_file()
        print(f"[DEVICE REGISTRY] Added device: {device.id} ({device.name})")
        return device

    def remove_device(self, device_id: str) -> bool:
        if device_id in self._devices:
            del self._devices[device_id]
            self._save_to_file()
            print(f"[DEVICE REGISTRY] Removed device: {device_id}")
            return True
        return False

    def update_device(self, device_id: str, **kwargs) -> Optional[Device]:
        device = self._devices.get(device_id)
        if not device:
            return None
        for key, value in kwargs.items():
            if hasattr(device, key):
                setattr(device, key, value)
        self._save_to_file()
        return device

    async def check_device_health(self, device_id: str) -> DeviceStatus:
        """Ping a device to check if it's reachable."""
        device = self._devices.get(device_id)
        if not device:
            return DeviceStatus.UNKNOWN
        if device.protocol == DeviceProtocol.LOCAL:
            device.status = DeviceStatus.ONLINE
            device.last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return DeviceStatus.ONLINE
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", "2", device.tailscale_ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            if proc.returncode == 0:
                device.status = DeviceStatus.ONLINE
                device.last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            else:
                device.status = DeviceStatus.OFFLINE
        except Exception:
            device.status = DeviceStatus.UNKNOWN
        return device.status

    async def check_all_health(self) -> Dict[str, DeviceStatus]:
        """Check health of all devices in parallel."""
        tasks = {did: self.check_device_health(did) for did in self._devices}
        results = {}
        for did, coro in tasks.items():
            results[did] = await coro
        return results

    def to_prompt_context(self, owner: Optional[str] = None) -> str:
        """Generate a text summary for injection into AI system prompts.
        This lets the model know what devices are available."""
        devices = self.get_devices_by_owner(owner) if owner else self.get_all_devices()
        if not devices:
            return "No devices registered in the device registry."
        lines = ["Available devices on the Tailscale mesh network:"]
        for d in devices:
            status = f" [{d.status.value}]" if d.status != DeviceStatus.UNKNOWN else ""
            lines.append(
                f"  - {d.id}: {d.name} | Type: {d.device_type.value} | "
                f"Protocol: {d.protocol.value} | IP: {d.tailscale_ip}{status}"
            )
            if d.description:
                lines.append(f"    Description: {d.description}")
        return "\n".join(lines)


# ── Singleton ──
_registry: Optional[DeviceRegistry] = None

def get_registry() -> DeviceRegistry:
    global _registry
    if _registry is None:
        _registry = DeviceRegistry()
    return _registry
```

**Step 5: Create __init__.py**

```python
"""Device Registry — Tailscale mesh device management."""
from .registry import get_registry, DeviceRegistry
from .models import Device, DeviceType, DeviceProtocol, DeviceStatus

__all__ = [
    "get_registry", "DeviceRegistry",
    "Device", "DeviceType", "DeviceProtocol", "DeviceStatus",
]
```

**Step 6: Verify imports work**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
python3 -c "from Orchestrator.device_registry import get_registry; r = get_registry(); print(r.get_all_devices())"
```

Expected: Loads the single "blackbox" device from devices.json.

---

### Task 2: Device Registry REST Endpoints

**Files:**
- Create: `Orchestrator/routes/device_routes.py`
- Modify: `Orchestrator/app.py` (add router import)

**Step 1: Create device_routes.py**

```python
"""REST API routes for the Device Registry."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from Orchestrator.device_registry import (
    get_registry, Device, DeviceType, DeviceProtocol
)

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceCreate(BaseModel):
    id: str
    name: str
    tailscale_ip: str
    device_type: str          # "android", "linux", "windows", "macos"
    protocol: str             # "adb", "local", "vnc", "rdp"
    owner: str
    description: str = ""
    adb_port: int = 5555
    vnc_port: int = 5900
    rdp_port: int = 3389
    metadata: Dict[str, Any] = {}


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    tailscale_ip: Optional[str] = None
    description: Optional[str] = None
    adb_port: Optional[int] = None
    vnc_port: Optional[int] = None
    rdp_port: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@router.get("/")
async def list_devices(owner: Optional[str] = None, device_type: Optional[str] = None):
    """List all registered devices, optionally filtered."""
    registry = get_registry()
    if owner:
        devices = registry.get_devices_by_owner(owner)
    elif device_type:
        devices = registry.get_devices_by_type(DeviceType(device_type))
    else:
        devices = registry.get_all_devices()
    return {"devices": [d.to_dict() for d in devices]}


@router.get("/{device_id}")
async def get_device(device_id: str):
    """Get a specific device by ID."""
    registry = get_registry()
    device = registry.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    return device.to_dict()


@router.post("/")
async def add_device(body: DeviceCreate):
    """Register a new device."""
    registry = get_registry()
    if registry.get_device(body.id):
        raise HTTPException(status_code=409, detail=f"Device already exists: {body.id}")
    device = Device(
        id=body.id,
        name=body.name,
        tailscale_ip=body.tailscale_ip,
        device_type=DeviceType(body.device_type),
        protocol=DeviceProtocol(body.protocol),
        owner=body.owner,
        description=body.description,
        adb_port=body.adb_port,
        vnc_port=body.vnc_port,
        rdp_port=body.rdp_port,
        metadata=body.metadata,
    )
    registry.add_device(device)
    return {"status": "created", "device": device.to_dict()}


@router.put("/{device_id}")
async def update_device(device_id: str, body: DeviceUpdate):
    """Update a device's properties."""
    registry = get_registry()
    updates = {k: v for k, v in body.dict().items() if v is not None}
    device = registry.update_device(device_id, **updates)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    return {"status": "updated", "device": device.to_dict()}


@router.delete("/{device_id}")
async def remove_device(device_id: str):
    """Unregister a device."""
    registry = get_registry()
    if not registry.remove_device(device_id):
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    return {"status": "removed", "device_id": device_id}


@router.get("/{device_id}/health")
async def check_device_health(device_id: str):
    """Ping a device to check if it's reachable via Tailscale."""
    registry = get_registry()
    device = registry.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    status = await registry.check_device_health(device_id)
    return {"device_id": device_id, "status": status.value, "last_seen": device.last_seen}


@router.post("/health/all")
async def check_all_health():
    """Check health of all devices."""
    registry = get_registry()
    results = await registry.check_all_health()
    return {"results": {k: v.value for k, v in results.items()}}


@router.get("/context/prompt")
async def get_prompt_context(owner: Optional[str] = None):
    """Get device registry as text for AI system prompt injection."""
    registry = get_registry()
    return {"context": registry.to_prompt_context(owner)}
```

**Step 2: Register the router in app.py**

Find where other routers are imported in `Orchestrator/app.py` and add:

```python
from Orchestrator.routes.device_routes import router as device_router
app.include_router(device_router)
```

**Step 3: Verify endpoints**

```bash
curl http://localhost:9091/devices/
curl http://localhost:9091/devices/blackbox
curl http://localhost:9091/devices/blackbox/health
```

**Step 4: Commit Phase 1**

```bash
git add Orchestrator/device_registry/ Orchestrator/routes/device_routes.py
git commit -m "feat: add Tailscale device registry with CRUD + health checks"
```

---

## Phase 2: ADB Manager (Android Device Control)

The ADB manager provides the low-level bridge between BlackBox and Android devices over Tailscale.

### Task 3: ADB Connection Manager

**Files:**
- Create: `Orchestrator/adb/__init__.py`
- Create: `Orchestrator/adb/manager.py`
- Create: `Orchestrator/adb/commands.py`

**Step 1: Create the package directory**

```bash
mkdir -p /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/adb
```

**Step 2: Verify ADB is installed**

```bash
which adb || sudo apt-get install -y android-tools-adb
adb version
```

**Step 3: Create manager.py — ADB connection lifecycle**

```python
"""
ADB Connection Manager — manages ADB connections to Android devices over Tailscale.

Handles connect, disconnect, health checking, and connection pooling.
"""
import asyncio
import subprocess
import time
from typing import Optional, Dict, Tuple
from Orchestrator.device_registry import get_registry, Device, DeviceProtocol, DeviceStatus


class ADBManager:
    """Manages ADB connections to Android devices."""

    def __init__(self):
        self._connected: Dict[str, float] = {}  # device_id → last_connected_timestamp

    async def _run_adb(self, *args, timeout: int = 10) -> Tuple[int, str, str]:
        """Run an ADB command and return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "adb", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", "ADB command timed out"

    async def connect(self, device_id: str) -> Dict:
        """Connect to an Android device via ADB over Tailscale."""
        registry = get_registry()
        device = registry.get_device(device_id)
        if not device:
            return {"success": False, "error": f"Device not found: {device_id}"}
        if device.protocol != DeviceProtocol.ADB:
            return {"success": False, "error": f"Device {device_id} is not an ADB device"}

        conn_str = device.connection_string()
        rc, stdout, stderr = await self._run_adb("connect", conn_str, timeout=15)

        if rc == 0 and ("connected" in stdout.lower() or "already connected" in stdout.lower()):
            self._connected[device_id] = time.time()
            device.status = DeviceStatus.ONLINE
            device.last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(f"[ADB] Connected to {device_id} at {conn_str}")
            return {"success": True, "device_id": device_id, "message": stdout}

        return {"success": False, "error": stderr or stdout or "Connection failed"}

    async def disconnect(self, device_id: str) -> Dict:
        """Disconnect from an Android device."""
        registry = get_registry()
        device = registry.get_device(device_id)
        if not device:
            return {"success": False, "error": f"Device not found: {device_id}"}

        conn_str = device.connection_string()
        rc, stdout, stderr = await self._run_adb("disconnect", conn_str)
        self._connected.pop(device_id, None)
        return {"success": True, "message": stdout or "Disconnected"}

    async def is_connected(self, device_id: str) -> bool:
        """Check if a device is currently connected via ADB."""
        registry = get_registry()
        device = registry.get_device(device_id)
        if not device:
            return False
        conn_str = device.connection_string()
        rc, stdout, stderr = await self._run_adb("devices")
        return conn_str in stdout and "device" in stdout

    async def ensure_connected(self, device_id: str) -> Dict:
        """Ensure a device is connected, connecting if needed."""
        if await self.is_connected(device_id):
            return {"success": True, "message": "Already connected"}
        return await self.connect(device_id)

    async def list_connected(self) -> list:
        """List all ADB-connected devices."""
        rc, stdout, stderr = await self._run_adb("devices")
        lines = stdout.strip().split("\n")[1:]  # Skip "List of devices attached"
        devices = []
        for line in lines:
            if "\t" in line:
                serial, state = line.split("\t", 1)
                devices.append({"serial": serial.strip(), "state": state.strip()})
        return devices


# ── Singleton ──
_adb_manager: Optional[ADBManager] = None

def get_adb_manager() -> ADBManager:
    global _adb_manager
    if _adb_manager is None:
        _adb_manager = ADBManager()
    return _adb_manager
```

**Step 4: Create commands.py — ADB command wrappers for CU actions**

```python
"""
ADB Commands — translates Computer Use actions into ADB shell commands.

Maps Gemini CU normalized coordinates (0-999) to device screen coordinates,
and provides wrappers for tap, swipe, type, screenshot, etc.
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
        """Get the ADB serial (IP:port) for this device."""
        from Orchestrator.device_registry import get_registry
        device = get_registry().get_device(self.device_id)
        return device.connection_string() if device else ""

    async def _shell(self, command: str, timeout: int = 10) -> Tuple[int, str]:
        """Run an ADB shell command on the device."""
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
        """Detect the device's screen resolution via ADB."""
        rc, output = await self._shell("wm size")
        # Output: "Physical size: 1080x2400"
        if "x" in output:
            size_str = output.split(":")[-1].strip()
            w, h = size_str.split("x")
            self.screen_width = int(w)
            self.screen_height = int(h)
        return self.screen_width, self.screen_height

    async def screenshot(self) -> bytes:
        """Capture a screenshot from the device. Returns PNG bytes."""
        serial = self._get_serial()
        # Capture to device, pull to local, clean up
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
        """Capture screenshot and return as base64 string."""
        png_bytes = await self.screenshot()
        return base64.b64encode(png_bytes).decode()

    async def tap(self, x: int, y: int, normalized: bool = True) -> Dict:
        """Tap at coordinates. If normalized=True, converts from 0-999 range."""
        if normalized:
            x, y = self.denormalize_coords(x, y)
        rc, output = await self._shell(f"input tap {x} {y}")
        return {"success": rc == 0, "action": "tap", "x": x, "y": y}

    async def long_press(self, x: int, y: int, duration_ms: int = 1000,
                         normalized: bool = True) -> Dict:
        """Long press at coordinates."""
        if normalized:
            x, y = self.denormalize_coords(x, y)
        # Long press = swipe from same point to same point with duration
        rc, output = await self._shell(
            f"input swipe {x} {y} {x} {y} {duration_ms}"
        )
        return {"success": rc == 0, "action": "long_press", "x": x, "y": y}

    async def swipe(self, x1: int, y1: int, x2: int, y2: int,
                    duration_ms: int = 300, normalized: bool = True) -> Dict:
        """Swipe from (x1,y1) to (x2,y2)."""
        if normalized:
            x1, y1 = self.denormalize_coords(x1, y1)
            x2, y2 = self.denormalize_coords(x2, y2)
        rc, output = await self._shell(
            f"input swipe {x1} {y1} {x2} {y2} {duration_ms}"
        )
        return {"success": rc == 0, "action": "swipe",
                "from": [x1, y1], "to": [x2, y2]}

    async def type_text(self, text: str) -> Dict:
        """Type text on the device. Escapes special characters for ADB."""
        # ADB input text requires escaping spaces and special chars
        escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        rc, output = await self._shell(f'input text "{escaped}"')
        return {"success": rc == 0, "action": "type", "text": text}

    async def key_event(self, keycode: str) -> Dict:
        """Send a key event. Common: KEYCODE_HOME, KEYCODE_BACK, KEYCODE_ENTER."""
        rc, output = await self._shell(f"input keyevent {keycode}")
        return {"success": rc == 0, "action": "key", "keycode": keycode}

    async def go_home(self) -> Dict:
        """Press the home button."""
        return await self.key_event("KEYCODE_HOME")

    async def go_back(self) -> Dict:
        """Press the back button."""
        return await self.key_event("KEYCODE_BACK")

    async def open_app(self, package_name: str, activity: Optional[str] = None) -> Dict:
        """Launch an app by package name."""
        if activity:
            rc, output = await self._shell(
                f"am start -n {package_name}/{activity}"
            )
        else:
            # Use monkey to launch the app's main activity
            rc, output = await self._shell(
                f"monkey -p {package_name} -c android.intent.category.LAUNCHER 1"
            )
        return {"success": rc == 0, "action": "open_app", "package": package_name}

    async def get_current_app(self) -> str:
        """Get the currently focused app's package name."""
        rc, output = await self._shell(
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"
        )
        return output

    async def scroll_down(self, x: int = 500, y: int = 500,
                          normalized: bool = True) -> Dict:
        """Scroll down at coordinates."""
        if normalized:
            x, y = self.denormalize_coords(x, y)
        # Swipe up to scroll down (200px upward)
        rc, output = await self._shell(
            f"input swipe {x} {y} {x} {y - 400} 300"
        )
        return {"success": rc == 0, "action": "scroll_down"}

    async def scroll_up(self, x: int = 500, y: int = 500,
                        normalized: bool = True) -> Dict:
        """Scroll up at coordinates."""
        if normalized:
            x, y = self.denormalize_coords(x, y)
        rc, output = await self._shell(
            f"input swipe {x} {y} {x} {y + 400} 300"
        )
        return {"success": rc == 0, "action": "scroll_up"}
```

**Step 5: Create __init__.py**

```python
"""ADB Manager — Android device control over Tailscale."""
from .manager import get_adb_manager, ADBManager
from .commands import ADBCommands

__all__ = ["get_adb_manager", "ADBManager", "ADBCommands"]
```

**Step 6: Commit**

```bash
git add Orchestrator/adb/
git commit -m "feat: add ADB manager with connection lifecycle and command wrappers"
```

---

### Task 4: ADB REST Endpoints

**Files:**
- Create: `Orchestrator/routes/adb_routes.py`
- Modify: `Orchestrator/app.py` (add router)

**Step 1: Create adb_routes.py**

```python
"""REST API routes for ADB device control."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from Orchestrator.adb import get_adb_manager, ADBCommands
from Orchestrator.device_registry import get_registry

router = APIRouter(prefix="/adb", tags=["adb"])


class TapRequest(BaseModel):
    device_id: str
    x: int
    y: int
    normalized: bool = True

class SwipeRequest(BaseModel):
    device_id: str
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int = 300
    normalized: bool = True

class TypeRequest(BaseModel):
    device_id: str
    text: str

class KeyRequest(BaseModel):
    device_id: str
    keycode: str

class AppRequest(BaseModel):
    device_id: str
    package_name: str
    activity: Optional[str] = None


@router.post("/connect/{device_id}")
async def connect_device(device_id: str):
    """Connect to an Android device via ADB over Tailscale."""
    mgr = get_adb_manager()
    result = await mgr.connect(device_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/disconnect/{device_id}")
async def disconnect_device(device_id: str):
    """Disconnect from an Android device."""
    mgr = get_adb_manager()
    return await mgr.disconnect(device_id)


@router.get("/devices")
async def list_connected():
    """List all ADB-connected devices."""
    mgr = get_adb_manager()
    return {"devices": await mgr.list_connected()}


@router.get("/screenshot/{device_id}")
async def get_screenshot(device_id: str):
    """Capture a screenshot from an Android device. Returns base64 PNG."""
    mgr = get_adb_manager()
    result = await mgr.ensure_connected(device_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Connection failed"))
    cmds = ADBCommands(device_id)
    await cmds.detect_screen_size()
    b64 = await cmds.screenshot_base64()
    return {"device_id": device_id, "screenshot_base64": b64,
            "screen_size": [cmds.screen_width, cmds.screen_height]}


@router.post("/tap")
async def tap(body: TapRequest):
    cmds = ADBCommands(body.device_id)
    await cmds.detect_screen_size()
    return await cmds.tap(body.x, body.y, body.normalized)


@router.post("/swipe")
async def swipe(body: SwipeRequest):
    cmds = ADBCommands(body.device_id)
    await cmds.detect_screen_size()
    return await cmds.swipe(body.x1, body.y1, body.x2, body.y2,
                            body.duration_ms, body.normalized)


@router.post("/type")
async def type_text(body: TypeRequest):
    cmds = ADBCommands(body.device_id)
    return await cmds.type_text(body.text)


@router.post("/key")
async def key_event(body: KeyRequest):
    cmds = ADBCommands(body.device_id)
    return await cmds.key_event(body.keycode)


@router.post("/open-app")
async def open_app(body: AppRequest):
    cmds = ADBCommands(body.device_id)
    return await cmds.open_app(body.package_name, body.activity)


@router.post("/home/{device_id}")
async def go_home(device_id: str):
    cmds = ADBCommands(device_id)
    return await cmds.go_home()


@router.post("/back/{device_id}")
async def go_back(device_id: str):
    cmds = ADBCommands(device_id)
    return await cmds.go_back()
```

**Step 2: Add router to app.py**

```python
from Orchestrator.routes.adb_routes import router as adb_router
app.include_router(adb_router)
```

**Step 3: Commit**

```bash
git add Orchestrator/routes/adb_routes.py
git commit -m "feat: add ADB REST endpoints for Android device control"
```

---

## Phase 3: Gemini Computer Use Module (Browser + Android)

This is the new Gemini CU module — separate from the existing Anthropic browser/ module.

### Task 5: Gemini CU Core — Config + Agent Loop

**Files:**
- Create: `Orchestrator/gemini_cu/__init__.py`
- Create: `Orchestrator/gemini_cu/config.py`
- Create: `Orchestrator/gemini_cu/agent_loop.py`
- Create: `Orchestrator/gemini_cu/session_manager.py`

**Step 1: Create package directory**

```bash
mkdir -p /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/gemini_cu
```

**Step 2: Create config.py**

```python
"""Gemini Computer Use configuration."""
from Orchestrator.config import GOOGLE_API_KEY

# ── Models that support Computer Use ──
GEMINI_CU_MODEL = "gemini-2.5-computer-use-preview-10-2025"
GEMINI_CU_MODEL_PRO = "gemini-3-pro-preview"
GEMINI_CU_MODEL_FLASH = "gemini-3-flash-preview"

# Default model for CU tasks
DEFAULT_CU_MODEL = GEMINI_CU_MODEL

# ── Coordinate system ──
# Gemini CU uses normalized coordinates 0-999
GEMINI_COORD_MAX = 999

# ── Agent loop limits ──
MAX_ITERATIONS = 50
SESSION_TIMEOUT = 300  # seconds
MAX_WALL_CLOCK = 1800  # 30 minutes

# ── Environment types ──
ENVIRONMENT_BROWSER = "ENVIRONMENT_BROWSER"
ENVIRONMENT_ANDROID = "ENVIRONMENT_ANDROID"  # Custom — not a Google enum, we handle routing

# ── Predefined functions to exclude for Android mode ──
BROWSER_ONLY_FUNCTIONS = [
    "open_web_browser", "navigate", "go_back", "go_forward",
    "search", "scroll_document"
]

# ── Screenshot settings ──
SCREENSHOT_MIME_TYPE = "image/png"
RECOMMENDED_RESOLUTION = (1440, 900)  # Google's recommended for browser CU
```

**Step 3: Create session_manager.py**

```python
"""
Gemini CU Session Manager — manages persistent sessions for Gemini Computer Use.

Mirrors the pattern from Orchestrator/browser/session_manager.py but for Gemini.
"""
import asyncio
import time
import uuid
from typing import Optional, Dict, List, Any
from Orchestrator.gemini_cu.config import SESSION_TIMEOUT


class GeminiCUSession:
    """A persistent Gemini Computer Use session."""

    def __init__(self, operator: str, device_id: str, environment: str,
                 session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.operator = operator
        self.device_id = device_id           # Target device from registry
        self.environment = environment       # "browser" or "android"
        self.conversation_history: List[Any] = []
        self.screenshot_count: int = 0
        self.total_tokens: Dict[str, int] = {"input": 0, "output": 0}
        self.last_activity: float = time.time()

        # Background task state
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.agent_task: Optional[asyncio.Task] = None
        self.status: str = "idle"  # idle|running|complete|error|stopped
        self.final_response: str = ""
        self.error_message: str = ""
        self.current_step: int = 0
        self.total_steps: int = MAX_ITERATIONS

        # E-Stop
        self.stop_requested: bool = False
        self.prompt_queue: List[str] = []

    def touch(self):
        self.last_activity = time.time()

    def is_expired(self, timeout: int = SESSION_TIMEOUT) -> bool:
        return (time.time() - self.last_activity) > timeout

    def reset_task_state(self):
        self.status = "idle"
        self.final_response = ""
        self.error_message = ""
        self.current_step = 0
        self.stop_requested = False
        # Drain stale events
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def enqueue_prompt(self, text: str) -> int:
        self.prompt_queue.append(text)
        return len(self.prompt_queue)

    def dequeue_prompt(self) -> Optional[str]:
        if self.prompt_queue:
            return self.prompt_queue.pop(0)
        return None

    def request_stop(self):
        self.stop_requested = True
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()

    def destroy(self):
        self.request_stop()
        self.conversation_history.clear()


# ── Session Store ──
_sessions: Dict[str, GeminiCUSession] = {}
_operator_sessions: Dict[str, str] = {}  # operator → session_id

MAX_ITERATIONS = 50  # Import from config


def get_or_create_session(operator: str, device_id: str, environment: str,
                          session_id: Optional[str] = None) -> GeminiCUSession:
    """Get existing session or create a new one."""
    # Check for existing session
    if session_id and session_id in _sessions:
        session = _sessions[session_id]
        if session.operator == operator and not session.is_expired():
            session.touch()
            return session

    # Check operator's active session
    if operator in _operator_sessions:
        sid = _operator_sessions[operator]
        if sid in _sessions:
            session = _sessions[sid]
            if not session.is_expired():
                session.touch()
                return session
            else:
                # Expired — clean up
                session.destroy()
                del _sessions[sid]

    # Create new session
    session = GeminiCUSession(operator, device_id, environment, session_id)
    _sessions[session.session_id] = session
    _operator_sessions[operator] = session.session_id
    print(f"[GEMINI CU] Created session {session.session_id} for {operator} "
          f"targeting {device_id} ({environment})")
    return session


def get_session(operator: str) -> Optional[GeminiCUSession]:
    sid = _operator_sessions.get(operator)
    if sid and sid in _sessions:
        return _sessions[sid]
    return None


def destroy_session(operator: str):
    sid = _operator_sessions.pop(operator, None)
    if sid and sid in _sessions:
        _sessions[sid].destroy()
        del _sessions[sid]
```

**Step 4: Create agent_loop.py — the core Gemini CU screenshot→think→act loop**

```python
"""
Gemini Computer Use Agent Loop.

Implements the screenshot → Gemini API → action → screenshot cycle
for both browser and Android targets.

Uses google-generativeai SDK with ComputerUse tool configuration.
"""
import asyncio
import json
import time
import base64
from typing import Optional, Dict, Any, List, AsyncGenerator

import google.generativeai as genai
from google.generativeai import types

from Orchestrator.gemini_cu.config import (
    DEFAULT_CU_MODEL, GEMINI_CU_MODEL, GEMINI_CU_MODEL_PRO,
    MAX_ITERATIONS, MAX_WALL_CLOCK, ENVIRONMENT_BROWSER,
    BROWSER_ONLY_FUNCTIONS, SCREENSHOT_MIME_TYPE
)
from Orchestrator.gemini_cu.session_manager import GeminiCUSession
from Orchestrator.config import GOOGLE_API_KEY


# ── Custom Android Function Declarations ──
# These are added when targeting Android devices (excluded for browser-only)

def _get_android_function_declarations(client) -> list:
    """Build custom function declarations for Android CU."""
    # Define as callables for FunctionDeclaration.from_callable
    android_tools = []

    # We define them as dicts since we don't have the client context here
    android_tools.append(types.FunctionDeclaration(
        name="open_app",
        description="Opens an Android app by package name.",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The app package name (e.g., com.android.chrome) or friendly name"
                }
            },
            "required": ["app_name"]
        }
    ))
    android_tools.append(types.FunctionDeclaration(
        name="long_press_at",
        description="Long press at a coordinate on the Android screen.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (0-999 normalized)"},
                "y": {"type": "integer", "description": "Y coordinate (0-999 normalized)"}
            },
            "required": ["x", "y"]
        }
    ))
    android_tools.append(types.FunctionDeclaration(
        name="go_home",
        description="Navigate to the Android home screen.",
        parameters={"type": "object", "properties": {}}
    ))
    android_tools.append(types.FunctionDeclaration(
        name="go_back",
        description="Press the Android back button.",
        parameters={"type": "object", "properties": {}}
    ))
    android_tools.append(types.FunctionDeclaration(
        name="scroll_down",
        description="Scroll down on the Android screen.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (0-999)"},
                "y": {"type": "integer", "description": "Y coordinate (0-999)"}
            }
        }
    ))
    android_tools.append(types.FunctionDeclaration(
        name="scroll_up",
        description="Scroll up on the Android screen.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (0-999)"},
                "y": {"type": "integer", "description": "Y coordinate (0-999)"}
            }
        }
    ))
    return android_tools


def _build_tools(environment: str, model_name: str) -> list:
    """Build the tool configuration for Gemini CU."""
    tools = []

    if environment == "browser":
        # Browser CU — use Google's built-in ComputerUse tool
        tools.append(types.Tool(
            computer_use=types.ComputerUse(
                environment=types.Environment.ENVIRONMENT_BROWSER
            )
        ))
    elif environment == "android":
        # Android CU — ComputerUse with browser functions excluded + custom Android functions
        cu_tool = types.Tool(
            computer_use=types.ComputerUse(
                environment=types.Environment.ENVIRONMENT_BROWSER,
                excluded_predefined_functions=BROWSER_ONLY_FUNCTIONS
            )
        )
        tools.append(cu_tool)

        # Add custom Android functions
        android_fns = _get_android_function_declarations(None)
        tools.append(types.Tool(function_declarations=android_fns))

    return tools


async def _capture_screenshot(session: GeminiCUSession) -> bytes:
    """Capture a screenshot from the target device."""
    if session.environment == "browser":
        # Use existing browser screenshot infrastructure
        from Orchestrator.browser.screenshot import capture_screenshot
        return capture_screenshot()
    elif session.environment == "android":
        # Use ADB to capture screenshot from Android device
        from Orchestrator.adb.commands import ADBCommands
        cmds = ADBCommands(session.device_id)
        await cmds.detect_screen_size()
        return await cmds.screenshot()
    else:
        raise ValueError(f"Unknown environment: {session.environment}")


async def _execute_predefined_action(session: GeminiCUSession,
                                      action_name: str, args: dict) -> dict:
    """Execute a predefined Gemini CU action (click_at, type_text_at, etc.)."""
    if session.environment == "browser":
        # Map Gemini actions to existing ActionExecutor
        from Orchestrator.browser.actions import ActionExecutor
        from Orchestrator.browser.config import CU_DISPLAY_WIDTH, CU_DISPLAY_HEIGHT
        executor = ActionExecutor()

        if action_name == "click_at":
            x = int(args["x"] / 999 * CU_DISPLAY_WIDTH)
            y = int(args["y"] / 999 * CU_DISPLAY_HEIGHT)
            return executor.execute("left_click", coordinate=[x, y])

        elif action_name == "type_text_at":
            x = int(args["x"] / 999 * CU_DISPLAY_WIDTH)
            y = int(args["y"] / 999 * CU_DISPLAY_HEIGHT)
            # Click first, then type
            executor.execute("left_click", coordinate=[x, y])
            await asyncio.sleep(0.2)
            text = args.get("text", "")
            if args.get("clear_before_typing", False):
                executor.execute("key", text="ctrl+a")
                await asyncio.sleep(0.1)
            return executor.execute("type", text=text)

        elif action_name == "hover_at":
            x = int(args["x"] / 999 * CU_DISPLAY_WIDTH)
            y = int(args["y"] / 999 * CU_DISPLAY_HEIGHT)
            return executor.execute("mouse_move", coordinate=[x, y])

        elif action_name == "key_combination":
            keys = args.get("keys", "")
            return executor.execute("key", text=keys)

        elif action_name == "scroll_at":
            x = int(args["x"] / 999 * CU_DISPLAY_WIDTH)
            y = int(args["y"] / 999 * CU_DISPLAY_HEIGHT)
            direction = args.get("direction", "down")
            magnitude = args.get("magnitude", 3)
            return executor.execute("scroll", coordinate=[x, y],
                                    direction=direction, amount=magnitude)

        elif action_name == "scroll_document":
            direction = args.get("direction", "down")
            return executor.execute("scroll", direction=direction, amount=5)

        elif action_name == "navigate":
            url = args.get("url", "")
            executor.execute("key", text="ctrl+l")
            await asyncio.sleep(0.2)
            executor.execute("type", text=url)
            await asyncio.sleep(0.1)
            executor.execute("key", text="Return")
            return {"success": True, "action": "navigate", "url": url}

        elif action_name == "wait_5_seconds":
            await asyncio.sleep(5)
            return {"success": True, "action": "wait"}

        elif action_name == "drag_and_drop":
            sx = int(args["x"] / 999 * CU_DISPLAY_WIDTH)
            sy = int(args["y"] / 999 * CU_DISPLAY_HEIGHT)
            dx = int(args["destination_x"] / 999 * CU_DISPLAY_WIDTH)
            dy = int(args["destination_y"] / 999 * CU_DISPLAY_HEIGHT)
            return executor.execute("left_click_drag",
                                    start_coordinate=[sx, sy],
                                    coordinate=[dx, dy])
        else:
            return {"success": False, "error": f"Unknown action: {action_name}"}

    elif session.environment == "android":
        # Map Gemini actions to ADB commands
        from Orchestrator.adb.commands import ADBCommands
        cmds = ADBCommands(session.device_id)
        await cmds.detect_screen_size()

        if action_name == "click_at":
            return await cmds.tap(args["x"], args["y"], normalized=True)

        elif action_name == "type_text_at":
            await cmds.tap(args["x"], args["y"], normalized=True)
            await asyncio.sleep(0.3)
            if args.get("clear_before_typing", False):
                await cmds.key_event("KEYCODE_CTRL_LEFT")  # Select all
            return await cmds.type_text(args.get("text", ""))

        elif action_name == "hover_at":
            # Android has no hover — treat as no-op
            return {"success": True, "action": "hover (no-op on Android)"}

        elif action_name == "key_combination":
            keys = args.get("keys", "")
            return await cmds.key_event(keys)

        elif action_name == "scroll_at":
            direction = args.get("direction", "down")
            if direction == "down":
                return await cmds.scroll_down(args.get("x", 500), args.get("y", 500))
            else:
                return await cmds.scroll_up(args.get("x", 500), args.get("y", 500))

        elif action_name == "wait_5_seconds":
            await asyncio.sleep(5)
            return {"success": True, "action": "wait"}

        else:
            return {"success": False, "error": f"Unknown action: {action_name}"}

    return {"success": False, "error": "Unknown environment"}


async def _execute_custom_function(session: GeminiCUSession,
                                    func_name: str, args: dict) -> dict:
    """Execute a custom Android function."""
    from Orchestrator.adb.commands import ADBCommands
    cmds = ADBCommands(session.device_id)
    await cmds.detect_screen_size()

    if func_name == "open_app":
        return await cmds.open_app(args.get("app_name", ""))
    elif func_name == "long_press_at":
        return await cmds.long_press(args.get("x", 500), args.get("y", 500))
    elif func_name == "go_home":
        return await cmds.go_home()
    elif func_name == "go_back":
        return await cmds.go_back()
    elif func_name == "scroll_down":
        return await cmds.scroll_down(args.get("x", 500), args.get("y", 500))
    elif func_name == "scroll_up":
        return await cmds.scroll_up(args.get("x", 500), args.get("y", 500))
    else:
        return {"success": False, "error": f"Unknown custom function: {func_name}"}


# ── Predefined CU function names (from Google's API) ──
PREDEFINED_CU_FUNCTIONS = {
    "click_at", "hover_at", "type_text_at", "key_combination",
    "scroll_at", "scroll_document", "navigate", "open_web_browser",
    "go_back", "go_forward", "search", "wait_5_seconds", "drag_and_drop"
}

CUSTOM_ANDROID_FUNCTIONS = {
    "open_app", "long_press_at", "go_home", "go_back", "scroll_down", "scroll_up"
}


async def run_gemini_cu_loop(
    session: GeminiCUSession,
    prompt: str,
    model_name: str = DEFAULT_CU_MODEL,
    system_prompt: Optional[str] = None,
    url: Optional[str] = None
) -> AsyncGenerator[dict, None]:
    """
    Run the Gemini Computer Use agent loop.

    Yields SSE-style event dicts as the loop progresses.
    """
    start_time = time.time()
    session.status = "running"
    session.current_step = 0

    # Build tools
    tools = _build_tools(session.environment, model_name)

    # Build config
    config = types.GenerateContentConfig(
        tools=tools,
        system_instruction=system_prompt or _default_system_prompt(session),
    )

    # Initialize Gemini client
    client = genai.Client(api_key=GOOGLE_API_KEY)

    # Capture initial screenshot
    try:
        screenshot_bytes = await _capture_screenshot(session)
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
    except Exception as e:
        yield {"type": "error", "data": {"message": f"Failed to capture initial screenshot: {e}"}}
        session.status = "error"
        return

    # Save screenshot
    screenshot_url = _save_screenshot(screenshot_bytes, session)
    yield {"type": "cu_screenshot", "data": {"url": screenshot_url, "step": 0}}

    # Build initial content
    contents = [
        types.Content(role="user", parts=[
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
        ])
    ]

    if url and session.environment == "browser":
        # Navigate to URL first
        from Orchestrator.browser.actions import ActionExecutor
        executor = ActionExecutor()
        executor.execute("key", text="ctrl+l")
        await asyncio.sleep(0.2)
        executor.execute("type", text=url)
        await asyncio.sleep(0.1)
        executor.execute("key", text="Return")
        await asyncio.sleep(2)
        # Re-capture after navigation
        screenshot_bytes = await _capture_screenshot(session)
        screenshot_url = _save_screenshot(screenshot_bytes, session)
        yield {"type": "cu_screenshot", "data": {"url": screenshot_url, "step": 0}}
        contents[0] = types.Content(role="user", parts=[
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
        ])

    # ── Agent Loop ──
    for step in range(1, MAX_ITERATIONS + 1):
        if session.stop_requested:
            yield {"type": "cu_stopped", "data": {"step": step}}
            break

        elapsed = time.time() - start_time
        if elapsed > MAX_WALL_CLOCK:
            yield {"type": "error", "data": {"message": "Wall clock timeout (30 min)"}}
            break

        session.current_step = step
        yield {"type": "cu_step", "data": {"step": step, "total": MAX_ITERATIONS}}

        # Call Gemini API
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except Exception as e:
            yield {"type": "error", "data": {"message": f"Gemini API error: {e}"}}
            session.status = "error"
            return

        # Track tokens
        if hasattr(response, "usage_metadata"):
            session.total_tokens["input"] += getattr(response.usage_metadata, "prompt_token_count", 0)
            session.total_tokens["output"] += getattr(response.usage_metadata, "candidates_token_count", 0)

        # Process response
        if not response.candidates:
            yield {"type": "error", "data": {"message": "No response candidates from Gemini"}}
            break

        candidate = response.candidates[0]
        content = candidate.content

        # Add assistant response to history
        contents.append(content)

        # Check for function calls
        function_calls = []
        text_parts = []
        for part in content.parts:
            if hasattr(part, "function_call") and part.function_call:
                function_calls.append(part.function_call)
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)

        # Emit any text response
        if text_parts:
            combined_text = "\n".join(text_parts)
            yield {"type": "content", "data": {"text": combined_text, "step": step}}

        # If no function calls, task is complete
        if not function_calls:
            session.final_response = "\n".join(text_parts) if text_parts else "Task completed."
            yield {"type": "done", "data": {"content": session.final_response}}
            break

        # Execute function calls and build responses
        function_responses = []
        for fc in function_calls:
            fname = fc.name
            fargs = dict(fc.args) if fc.args else {}

            yield {"type": "cu_action", "data": {
                "action": fname, "params": fargs, "step": step
            }}

            # Execute the action
            if fname in PREDEFINED_CU_FUNCTIONS:
                result = await _execute_predefined_action(session, fname, fargs)
            elif fname in CUSTOM_ANDROID_FUNCTIONS:
                result = await _execute_custom_function(session, fname, fargs)
            else:
                result = {"success": False, "error": f"Unknown function: {fname}"}

            # Wait for UI update
            await asyncio.sleep(0.5)

            # Capture new screenshot
            try:
                screenshot_bytes = await _capture_screenshot(session)
                screenshot_url = _save_screenshot(screenshot_bytes, session)
                session.screenshot_count += 1
                yield {"type": "cu_screenshot", "data": {
                    "url": screenshot_url, "step": step
                }}

                # Build function response with screenshot
                function_responses.append(types.Part.from_function_response(
                    name=fname,
                    response={
                        "result": json.dumps(result),
                        "screenshot": types.Part.from_bytes(
                            data=screenshot_bytes,
                            mime_type="image/png"
                        )
                    }
                ))
            except Exception as e:
                function_responses.append(types.Part.from_function_response(
                    name=fname,
                    response={"error": str(e)}
                ))

        # Add function responses to conversation
        contents.append(types.Content(
            role="user",
            parts=function_responses
        ))

    # Session complete
    session.status = "complete"
    session.conversation_history = contents
    yield {"type": "usage", "data": session.total_tokens}


def _default_system_prompt(session: GeminiCUSession) -> str:
    """Generate a default system prompt based on environment."""
    if session.environment == "android":
        return (
            "You are a Computer Use agent controlling an Android device. "
            "You can see the screen through screenshots and interact via tap, swipe, "
            "type, and other actions. Use the custom functions (open_app, go_home, etc.) "
            "for Android-specific actions. Make sure to scroll down to see everything "
            "before deciding something isn't available. Use the type tool instead of "
            "the onscreen keyboard when possible. Complete the user's task step by step."
        )
    else:
        return (
            "You are a Computer Use agent controlling a web browser. "
            "You can see the screen through screenshots and interact via click, type, "
            "scroll, and navigation actions. Complete the user's task step by step. "
            "If a page is loading, use wait_5_seconds before retrying."
        )


def _save_screenshot(png_bytes: bytes, session: GeminiCUSession) -> str:
    """Save screenshot to uploads directory and return URL."""
    import os
    uploads_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "Portal", "uploads"
    )
    os.makedirs(uploads_dir, exist_ok=True)
    filename = f"gemini_cu_{session.operator}_{session.screenshot_count:03d}.png"
    filepath = os.path.join(uploads_dir, filename)
    with open(filepath, "wb") as f:
        f.write(png_bytes)
    session.screenshot_count += 1
    return f"/ui/uploads/{filename}"
```

**Step 5: Create __init__.py**

```python
"""Gemini Computer Use — browser and Android CU via Google's API."""
from .config import DEFAULT_CU_MODEL, GEMINI_CU_MODEL
from .session_manager import (
    GeminiCUSession, get_or_create_session, get_session, destroy_session
)
from .agent_loop import run_gemini_cu_loop

__all__ = [
    "DEFAULT_CU_MODEL", "GEMINI_CU_MODEL",
    "GeminiCUSession", "get_or_create_session", "get_session", "destroy_session",
    "run_gemini_cu_loop",
]
```

**Step 6: Commit**

```bash
git add Orchestrator/gemini_cu/
git commit -m "feat: add Gemini Computer Use module with browser + Android agent loop"
```

---

### Task 6: Gemini CU REST Endpoints + SSE Streaming

**Files:**
- Create: `Orchestrator/routes/gemini_cu_routes.py`
- Modify: `Orchestrator/app.py` (add router)

**Step 1: Create gemini_cu_routes.py**

```python
"""REST API routes for Gemini Computer Use."""
import asyncio
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from Orchestrator.gemini_cu import (
    get_or_create_session, get_session, destroy_session, run_gemini_cu_loop
)
from Orchestrator.gemini_cu.config import DEFAULT_CU_MODEL
from Orchestrator.device_registry import get_registry, DeviceProtocol
from Orchestrator.tasks import create_task, TaskType

router = APIRouter(prefix="/gemini-cu", tags=["gemini-cu"])


class GeminiCURequest(BaseModel):
    prompt: str
    operator: str
    device_id: str = "blackbox"    # Default to local machine
    model: str = DEFAULT_CU_MODEL
    url: Optional[str] = None      # Starting URL for browser mode
    system_prompt: Optional[str] = None


@router.post("/run")
async def run_gemini_cu(body: GeminiCURequest):
    """Start a Gemini Computer Use task. Returns a task_id for status polling."""
    registry = get_registry()
    device = registry.get_device(body.device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {body.device_id}")

    # Determine environment from device protocol
    if device.protocol == DeviceProtocol.ADB:
        environment = "android"
        # Ensure ADB connection
        from Orchestrator.adb import get_adb_manager
        result = await get_adb_manager().ensure_connected(body.device_id)
        if not result["success"]:
            raise HTTPException(status_code=400,
                                detail=f"Cannot connect to device: {result.get('error')}")
    else:
        environment = "browser"

    # Create async task
    task = create_task(
        TaskType.GEMINI_CU,
        operator=body.operator,
        prompt=body.prompt,
        result_data={
            "device_id": body.device_id,
            "environment": environment,
            "model": body.model,
            "url": body.url,
        }
    )

    # Launch background agent loop
    asyncio.create_task(_run_task(
        task.task_id, body.operator, body.device_id, environment,
        body.prompt, body.model, body.system_prompt, body.url
    ))

    return {
        "task_id": task.task_id,
        "status": "pending",
        "device_id": body.device_id,
        "environment": environment
    }


async def _run_task(task_id, operator, device_id, environment,
                    prompt, model, system_prompt, url):
    """Background task that runs the Gemini CU loop and updates the task."""
    from Orchestrator.tasks import get_task, update_task
    task = get_task(task_id)
    if not task:
        return

    session = get_or_create_session(operator, device_id, environment)
    screenshots = []
    final_text = ""

    try:
        task.status = "running"
        async for event in run_gemini_cu_loop(
            session, prompt, model, system_prompt, url
        ):
            event_type = event.get("type")
            if event_type == "cu_screenshot":
                screenshots.append(event["data"]["url"])
            elif event_type == "done":
                final_text = event["data"].get("content", "")
            elif event_type == "error":
                task.status = "failed"
                task.result_data["error"] = event["data"]["message"]
                return

        task.status = "completed"
        task.result_data.update({
            "result_text": final_text,
            "screenshots": screenshots,
            "final_screenshot": screenshots[-1] if screenshots else None,
            "steps": session.current_step,
            "tokens": session.total_tokens,
        })
    except Exception as e:
        task.status = "failed"
        task.result_data["error"] = str(e)


@router.post("/stream")
async def stream_gemini_cu(body: GeminiCURequest):
    """Stream Gemini CU events via SSE (for Portal real-time updates)."""
    registry = get_registry()
    device = registry.get_device(body.device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {body.device_id}")

    if device.protocol == DeviceProtocol.ADB:
        environment = "android"
        from Orchestrator.adb import get_adb_manager
        result = await get_adb_manager().ensure_connected(body.device_id)
        if not result["success"]:
            raise HTTPException(status_code=400,
                                detail=f"Cannot connect: {result.get('error')}")
    else:
        environment = "browser"

    session = get_or_create_session(body.operator, body.device_id, environment)

    async def event_stream():
        async for event in run_gemini_cu_loop(
            session, body.prompt, body.model, body.system_prompt, body.url
        ):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/session/{operator}")
async def get_session_info(operator: str):
    """Get the current Gemini CU session for an operator."""
    session = get_session(operator)
    if not session:
        return {"active": False}
    return {
        "active": True,
        "session_id": session.session_id,
        "device_id": session.device_id,
        "environment": session.environment,
        "status": session.status,
        "current_step": session.current_step,
        "screenshot_count": session.screenshot_count,
        "tokens": session.total_tokens,
    }


@router.delete("/session/{operator}")
async def end_session(operator: str):
    """End a Gemini CU session."""
    destroy_session(operator)
    return {"status": "destroyed"}
```

**Step 2: Add TaskType.GEMINI_CU to the tasks system**

In `Orchestrator/tasks.py`, add `GEMINI_CU = "gemini_cu"` to the `TaskType` enum.

**Step 3: Register router in app.py**

```python
from Orchestrator.routes.gemini_cu_routes import router as gemini_cu_router
app.include_router(gemini_cu_router)
```

**Step 4: Commit**

```bash
git add Orchestrator/routes/gemini_cu_routes.py
git commit -m "feat: add Gemini CU REST endpoints with SSE streaming"
```

---

## Phase 4: Tool Registration (All Providers Can Invoke CU + ADB)

### Task 7: Register `control_android_device` Tool

This tool lets any model (Claude, GPT, Gemini) control an Android device by dispatching to the Gemini CU pipeline.

**Files:**
- Modify: `Orchestrator/tools/blackbox_tools.py`
- Modify: `Orchestrator/routes/chat_routes.py`
- Modify: `Orchestrator/phone/bridge.py`
- Modify: `MCP/blackbox_mcp_server.py`

**Step 1: Add tool definition to blackbox_tools.py**

Add to `BLACKBOX_TOOLS_ANTHROPIC` list:

```python
{
    "name": "control_android_device",
    "description": "Control an Android device on the Tailscale mesh network using Gemini Computer Use. Can tap, type, swipe, open apps, and navigate the device UI autonomously. The AI agent will see the screen and perform actions to complete your task. Returns a task ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to do on the Android device (e.g., 'Open the Play Store and check StoryBox reviews')"
            },
            "device_id": {
                "type": "string",
                "description": "Device ID from the device registry (e.g., 'brandon-phone'). Use 'list' to see available devices."
            }
        },
        "required": ["prompt", "device_id"]
    }
}
```

**Step 2: Add execution handler to blackbox_tools.py**

```python
async def _execute_control_android_device(self, params: Dict[str, Any]) -> ToolResult:
    """Control an Android device via Gemini Computer Use."""
    prompt = params.get("prompt", "")
    device_id = params.get("device_id", "")

    if not prompt:
        return ToolResult(False, "Prompt is required")
    if device_id == "list":
        from Orchestrator.device_registry import get_registry, DeviceType
        devices = get_registry().get_android_devices()
        if not devices:
            return ToolResult(True, "No Android devices registered. Use /devices to add one.")
        listing = "\n".join(f"  - {d.id}: {d.name} ({d.tailscale_ip})" for d in devices)
        return ToolResult(True, f"Available Android devices:\n{listing}")

    try:
        import requests
        resp = requests.post(
            "http://localhost:9091/gemini-cu/run",
            json={"prompt": prompt, "device_id": device_id, "operator": self.operator},
            timeout=30
        )
        result = resp.json()
        task_id = result.get("task_id")
        if task_id:
            return ToolResult(
                True,
                f"Android CU task started on device '{device_id}'. Task ID: {task_id}. "
                f"Use get_task_status to check progress.",
                data={"task_id": task_id}
            )
        return ToolResult(False, f"Failed: {result}")
    except Exception as e:
        return ToolResult(False, f"Error: {str(e)}")
```

**Step 3: Add 3-format tool definitions to chat_routes.py**

Anthropic format:
```python
ANTHROPIC_CONTROL_ANDROID_TOOL = {
    "name": "control_android_device",
    "description": "Control an Android device on the Tailscale network using Gemini Computer Use. Returns a task ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to do on the device"},
            "device_id": {"type": "string", "description": "Device ID (e.g., 'brandon-phone')"}
        },
        "required": ["prompt", "device_id"]
    }
}
```

OpenAI format:
```python
OPENAI_CONTROL_ANDROID_TOOL = {
    "type": "function",
    "name": "control_android_device",
    "description": "Control an Android device on the Tailscale network using Gemini Computer Use. Returns a task ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to do on the device"},
            "device_id": {"type": "string", "description": "Device ID (e.g., 'brandon-phone')"}
        },
        "required": ["prompt", "device_id"]
    }
}
```

Gemini format:
```python
GEMINI_CONTROL_ANDROID_TOOL = {
    "function_declarations": [{
        "name": "control_android_device",
        "description": "Control an Android device on the Tailscale network using Gemini Computer Use. Returns a task ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to do on the device"},
                "device_id": {"type": "string", "description": "Device ID (e.g., 'brandon-phone')"}
            },
            "required": ["prompt", "device_id"]
        }
    }]
}
```

**Step 4: Add to tool arrays in chat_routes.py**

Add `ANTHROPIC_CONTROL_ANDROID_TOOL` to the Anthropic tools array (~line 5316).
Add `OPENAI_CONTROL_ANDROID_TOOL` to the OpenAI tools array.
Add `GEMINI_CONTROL_ANDROID_TOOL` to the Gemini tools array.

**Step 5: Add to phone bridge unified_tool_map**

In `Orchestrator/phone/bridge.py` line ~3046:

```python
"control_android_device": "control_android_device",
```

**Step 6: Add to MCP server**

In `MCP/blackbox_mcp_server.py`:

Tool definition:
```python
Tool(
    name="control_android_device",
    description="Control an Android device on the Tailscale network using Gemini Computer Use.",
    inputSchema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to do on the device"},
            "device_id": {"type": "string", "description": "Device ID from registry"},
            "operator": {"type": "string", "description": "Operator name"}
        },
        "required": ["prompt", "device_id", "operator"]
    }
),
```

Execution handler:
```python
elif name == "control_android_device":
    response = await client.post(
        f"{BLACKBOX_URL}/gemini-cu/run",
        json={
            "prompt": arguments["prompt"],
            "device_id": arguments["device_id"],
            "operator": arguments["operator"]
        },
        timeout=30
    )
    result = response.json()
    return [TextContent(type="text", text=json.dumps(result, indent=2))]
```

**Step 7: Commit**

```bash
git add Orchestrator/tools/blackbox_tools.py Orchestrator/routes/chat_routes.py \
    Orchestrator/phone/bridge.py MCP/blackbox_mcp_server.py
git commit -m "feat: register control_android_device tool across all providers"
```

---

### Task 8: Register `list_devices` Tool

**Files:** Same as Task 7 — all 4 tool registration points.

**Tool definition (Anthropic format):**

```python
{
    "name": "list_devices",
    "description": "List all devices on the Tailscale mesh network that can be controlled. Shows device name, type (android/linux/windows), protocol (adb/vnc/local), IP, and status.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_type": {
                "type": "string",
                "description": "Filter by type: 'android', 'linux', 'windows', or omit for all"
            }
        }
    }
}
```

**Execution handler:**

```python
async def _execute_list_devices(self, params: Dict[str, Any]) -> ToolResult:
    from Orchestrator.device_registry import get_registry, DeviceType
    registry = get_registry()
    dtype = params.get("device_type")
    if dtype:
        devices = registry.get_devices_by_type(DeviceType(dtype))
    else:
        devices = registry.get_all_devices()
    if not devices:
        return ToolResult(True, "No devices registered.")
    lines = []
    for d in devices:
        lines.append(f"  - {d.id}: {d.name} | {d.device_type.value} | "
                     f"{d.protocol.value} | {d.tailscale_ip} [{d.status.value}]")
    return ToolResult(True, f"Devices ({len(devices)}):\n" + "\n".join(lines))
```

Follow the same 4-part registration pattern as Task 7 for OpenAI, Gemini, bridge, and MCP.

**Commit:**

```bash
git commit -m "feat: register list_devices tool across all providers"
```

---

## Phase 5: Inject Device Context into System Prompts

### Task 9: Add Device Registry to CU System Prompts

**Files:**
- Modify: `Orchestrator/routes/chat_routes.py` (system prompt construction)

**Step 1: In the system prompt builder for all chat providers, add device context**

Find where the system prompt is built (for each provider: Anthropic, OpenAI, Gemini) and append:

```python
# Add device registry context
from Orchestrator.device_registry import get_registry
device_context = get_registry().to_prompt_context(owner=operator)
system_prompt += f"\n\n## Available Devices\n{device_context}"
```

This ensures every model knows what devices are available when the user says "control my phone."

**Step 2: Also inject into the existing Anthropic CU system prompt**

In `Orchestrator/browser/session_manager.py` or wherever the CU system prompt is built for `stream_computer_use()`, add device context.

**Step 3: Commit**

```bash
git commit -m "feat: inject device registry context into all AI system prompts"
```

---

## Phase 6: Remote Desktop Control (Anthropic CU on Remote Machines)

### Task 10: VNC Screenshot + Input for Remote Desktops

**Files:**
- Create: `Orchestrator/remote_desktop/__init__.py`
- Create: `Orchestrator/remote_desktop/vnc_client.py`

**Step 1: Install vncdotool**

```bash
pip install vncdotool
```

**Step 2: Create vnc_client.py**

```python
"""
VNC Client — captures screenshots and sends input to remote desktops over Tailscale.

Used by the Anthropic Computer Use agent to control remote Linux/Windows machines.
"""
import asyncio
import subprocess
import tempfile
import os
from typing import Tuple, Optional
from PIL import Image


class VNCClient:
    """Lightweight VNC client for CU screenshot/input over Tailscale."""

    def __init__(self, host: str, port: int = 5900, password: Optional[str] = None):
        self.host = host
        self.port = port
        self.password = password
        self._vncdotool_args = [
            "vncdotool", "-s", f"{host}::{port}"
        ]
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
            # Resize to CU resolution
            img = Image.open(tmp_path)
            if img.size != (width, height):
                img = img.resize((width, height), Image.LANCZOS)
            import io
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
```

**Step 3: Create __init__.py**

```python
"""Remote Desktop — VNC/RDP control for remote computers over Tailscale."""
from .vnc_client import VNCClient

__all__ = ["VNCClient"]
```

**Step 4: Commit**

```bash
git add Orchestrator/remote_desktop/
git commit -m "feat: add VNC client for remote desktop control over Tailscale"
```

---

### Task 11: Extend Anthropic CU to Target Remote Desktops

**Files:**
- Modify: `Orchestrator/browser/screenshot.py` (add remote screenshot path)
- Modify: `Orchestrator/browser/actions.py` (add remote action path)
- Modify: `Orchestrator/browser/session_manager.py` (add device_id to session)

**Step 1: Add `device_id` parameter to ComputerUseSession**

In `session_manager.py`, add `device_id: str = "blackbox"` to `__init__` and `get_or_create_session`.

**Step 2: In screenshot.py, add remote screenshot capture**

```python
async def capture_remote_screenshot(device_id: str) -> bytes:
    """Capture screenshot from a remote desktop via VNC."""
    from Orchestrator.device_registry import get_registry
    from Orchestrator.remote_desktop import VNCClient
    device = get_registry().get_device(device_id)
    if not device:
        raise RuntimeError(f"Device not found: {device_id}")
    client = VNCClient(device.tailscale_ip, device.vnc_port)
    return await client.screenshot(CU_DISPLAY_WIDTH, CU_DISPLAY_HEIGHT)
```

**Step 3: In actions.py, add remote action execution**

```python
async def execute_remote_action(device_id: str, action: str, **params) -> dict:
    """Execute an action on a remote desktop via VNC."""
    from Orchestrator.device_registry import get_registry
    from Orchestrator.remote_desktop import VNCClient
    from Orchestrator.browser.config import CU_DISPLAY_WIDTH, CU_DISPLAY_HEIGHT
    device = get_registry().get_device(device_id)
    client = VNCClient(device.tailscale_ip, device.vnc_port)

    if action == "left_click":
        coord = params.get("coordinate", [0, 0])
        await client.click(coord[0], coord[1])
    elif action == "type":
        await client.type_text(params.get("text", ""))
    elif action == "key":
        await client.key(params.get("text", ""))
    elif action == "mouse_move":
        coord = params.get("coordinate", [0, 0])
        await client.move(coord[0], coord[1])
    # ... map remaining actions

    return {"success": True, "action": action, "device_id": device_id}
```

**Step 4: In the CU agent loop, branch on device_id**

When `device_id != "blackbox"`, use `capture_remote_screenshot()` instead of the local `capture_screenshot()`, and `execute_remote_action()` instead of the local `ActionExecutor.execute()`.

**Step 5: Commit**

```bash
git commit -m "feat: extend Anthropic CU to control remote desktops via VNC over Tailscale"
```

---

## Phase 7: OpenAI CUA Module (Future — Lower Priority)

### Task 12: OpenAI CUA Stub Module

**Files:**
- Create: `Orchestrator/openai_cu/__init__.py`
- Create: `Orchestrator/openai_cu/config.py`
- Create: `Orchestrator/openai_cu/agent_loop.py`

**Step 1: Create the stub module**

This is a scaffold for future implementation. The key differences from Gemini/Anthropic:
- Uses the **Responses API** (not Chat Completions)
- Returns `computer_call` items (not `function_call`)
- Requires `reasoning` items to be passed back in subsequent requests
- Supports environments: "browser", "mac", "windows", "ubuntu"
- Model: `computer-use-preview`

**config.py:**
```python
"""OpenAI Computer Use Agent configuration."""
OPENAI_CUA_MODEL = "computer-use-preview"
OPENAI_CUA_ENVIRONMENTS = ["browser", "mac", "windows", "ubuntu"]
```

**agent_loop.py (stub):**
```python
"""
OpenAI CUA Agent Loop — placeholder for future implementation.

Uses the Responses API with computer_use_preview tool type.
Key differences from Anthropic/Gemini:
- Response items include 'reasoning' that must be passed back
- Uses previous_response_id for session continuity
- Actions: click, type, scroll, keypress, wait, screenshot
"""

async def run_openai_cu_loop(session, prompt, model=None, system_prompt=None, url=None):
    """Placeholder — to be implemented when OpenAI CUA is prioritized."""
    yield {"type": "error", "data": {"message": "OpenAI CUA not yet implemented. Use Anthropic or Gemini."}}
```

**Step 2: Commit**

```bash
git add Orchestrator/openai_cu/
git commit -m "feat: add OpenAI CUA stub module for future implementation"
```

---

## Phase 8: Integration Testing + Verification

### Task 13: End-to-End Verification

**Step 1: Verify device registry**

```bash
# Add a test device
curl -X POST http://localhost:9091/devices/ \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-android",
    "name": "Test Android Device",
    "tailscale_ip": "100.64.0.1",
    "device_type": "android",
    "protocol": "adb",
    "owner": "Brandon"
  }'

# List devices
curl http://localhost:9091/devices/

# Check health
curl http://localhost:9091/devices/test-android/health

# Get prompt context
curl http://localhost:9091/devices/context/prompt
```

**Step 2: Verify Gemini CU browser mode (uses local display)**

```bash
curl -X POST http://localhost:9091/gemini-cu/run \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Go to Google and search for AI BlackBox",
    "operator": "Brandon",
    "device_id": "blackbox",
    "url": "https://google.com"
  }'
```

**Step 3: Verify ADB endpoints (requires connected Android device)**

```bash
# Connect to a device (once ADB wireless debugging is enabled)
curl -X POST http://localhost:9091/adb/connect/brandon-phone

# List connected
curl http://localhost:9091/adb/devices

# Screenshot
curl http://localhost:9091/adb/screenshot/brandon-phone
```

**Step 4: Verify tool shows up in chat**

Send a message via the Portal and verify `control_android_device` and `list_devices` appear in the model's available tools.

**Step 5: Clean up test device**

```bash
curl -X DELETE http://localhost:9091/devices/test-android
```

**Step 6: Final commit**

```bash
git commit -m "feat: multi-provider computer use + ADB + Tailscale device registry — complete"
```

---

## Summary — File Map

| Phase | New Files | Modified Files |
|-------|-----------|---------------|
| **1: Device Registry** | `device_registry/{__init__,models,registry}.py`, `devices.json`, `routes/device_routes.py` | `app.py` |
| **2: ADB Manager** | `adb/{__init__,manager,commands}.py`, `routes/adb_routes.py` | `app.py` |
| **3: Gemini CU** | `gemini_cu/{__init__,config,agent_loop,session_manager}.py`, `routes/gemini_cu_routes.py` | `app.py`, `tasks.py` |
| **4: Tool Registration** | — | `blackbox_tools.py`, `chat_routes.py`, `bridge.py`, `blackbox_mcp_server.py` |
| **5: Prompt Injection** | — | `chat_routes.py` |
| **6: Remote Desktop** | `remote_desktop/{__init__,vnc_client}.py` | `browser/{screenshot,actions,session_manager}.py` |
| **7: OpenAI Stub** | `openai_cu/{__init__,config,agent_loop}.py` | — |

**Total new files:** ~20
**Total modified files:** ~10
**Estimated implementation:** 8 phases, 13 tasks
