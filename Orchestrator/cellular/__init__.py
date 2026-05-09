#!/usr/bin/env python3
"""
cellular/__init__.py - Cellular Module (Internet Failover)

The AT command telephony modules (modem, sms, voice) have been removed.
Telephony now runs on Asterisk/PJSIP via the TG200 gateway.

Remaining components:
- hotplug.py: USB hot-plug monitor for SIM detection
- internet_manager.py: SIM8260G cellular internet failover
- audio_stream.py: Serial audio stream (retained for compatibility)
- ivr.py: Local IVR handling
"""

from Orchestrator.cellular.hotplug import get_hotplug_monitor, start_hotplug_monitor
from Orchestrator.cellular.internet_manager import (
    CellularInternetManager,
    get_internet_manager,
    start_internet_manager,
    stop_internet_manager,
)

__all__ = [
    "get_hotplug_monitor",
    "start_hotplug_monitor",
    "CellularInternetManager",
    "get_internet_manager",
    "start_internet_manager",
    "stop_internet_manager",
]
