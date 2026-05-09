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
    device_type: str
    protocol: str
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
    registry = get_registry()
    if owner:
        devices = registry.get_devices_by_owner(owner)
    elif device_type:
        devices = registry.get_devices_by_type(DeviceType(device_type))
    else:
        devices = registry.get_all_devices()
    return {"devices": [d.to_dict() for d in devices]}


@router.post("/sync-tailscale")
async def sync_tailscale():
    """Auto-discover devices from the Tailscale network and add/update the registry."""
    registry = get_registry()
    try:
        results = await registry.sync_from_tailscale()
        return {
            "status": "synced",
            "results": results,
            "total_devices": len(registry.get_all_devices())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{device_id}")
async def get_device(device_id: str):
    registry = get_registry()
    device = registry.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    return device.to_dict()


@router.post("/")
async def add_device(body: DeviceCreate):
    registry = get_registry()
    if registry.get_device(body.id):
        raise HTTPException(status_code=409, detail=f"Device already exists: {body.id}")
    device = Device(
        id=body.id, name=body.name, tailscale_ip=body.tailscale_ip,
        device_type=DeviceType(body.device_type),
        protocol=DeviceProtocol(body.protocol),
        owner=body.owner, description=body.description,
        adb_port=body.adb_port, vnc_port=body.vnc_port,
        rdp_port=body.rdp_port, metadata=body.metadata,
    )
    registry.add_device(device)
    return {"status": "created", "device": device.to_dict()}


@router.put("/{device_id}")
async def update_device(device_id: str, body: DeviceUpdate):
    registry = get_registry()
    updates = {k: v for k, v in body.dict().items() if v is not None}
    # Validate port ranges before persisting
    for port_field in ("adb_port", "vnc_port", "rdp_port"):
        if port_field in updates:
            port_val = updates[port_field]
            if not isinstance(port_val, int) or port_val < 1 or port_val > 65535:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid {port_field}: {port_val} (must be 1-65535)"
                )
    device = registry.update_device(device_id, **updates)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    return {"status": "updated", "device": device.to_dict()}


@router.delete("/{device_id}")
async def remove_device(device_id: str):
    registry = get_registry()
    if not registry.remove_device(device_id):
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    return {"status": "removed", "device_id": device_id}


@router.get("/{device_id}/health")
async def check_device_health(device_id: str):
    registry = get_registry()
    device = registry.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device not found: {device_id}")
    status = await registry.check_device_health(device_id)
    return {"device_id": device_id, "status": status.value, "last_seen": device.last_seen}


@router.post("/health/all")
async def check_all_health():
    registry = get_registry()
    results = await registry.check_all_health()
    return {"results": {k: v.value for k, v in results.items()}}


@router.get("/context/prompt")
async def get_prompt_context(owner: Optional[str] = None):
    registry = get_registry()
    return {"context": registry.to_prompt_context(owner)}
