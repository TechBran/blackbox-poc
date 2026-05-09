#!/usr/bin/env python3
"""
cellular_routes.py - Cellular Module HTTP Endpoints (Internet Failover Only)

The AT command telephony routes (call, sms, hangup, at) have been removed.
Telephony now runs on Asterisk/PJSIP via the TG200 gateway.

Remaining endpoints:
- GET /cellular/status — Module status (hotplug monitor)
- GET /cellular/hotplug — Hotplug monitor status
"""

from Orchestrator.checkpoint import app


# =============================================================================
# Status & Management
# =============================================================================

@app.get("/cellular/status")
async def cellular_status():
    """Get cellular module status (hotplug monitor for internet failover)."""
    from Orchestrator.cellular.hotplug import get_hotplug_monitor

    monitor = get_hotplug_monitor()
    hotplug_info = monitor.get_status() if monitor else {"state": "no_monitor"}

    return {
        "connected": hotplug_info.get("state") == "connected",
        "mode": "internet_failover_only",
        "note": "AT command telephony removed — use Asterisk/PJSIP endpoints",
        "hotplug": hotplug_info,
    }


@app.get("/cellular/hotplug")
async def cellular_hotplug():
    """Get hotplug monitor status."""
    from Orchestrator.cellular.hotplug import get_hotplug_monitor

    monitor = get_hotplug_monitor()
    if not monitor:
        return {"error": "Hotplug monitor not running", "state": "disabled"}

    return monitor.get_status()
