#!/usr/bin/env python3
"""
phone/__init__.py - AI BlackBox Phone Integration Module

This module provides phone calling capabilities for the AI BlackBox system:
- Inbound calls via 3CX Cloud PBX
- Outbound AI-initiated calls
- IVR menu for AI backend selection
- Phone-to-AI WebSocket bridging
- Full call logging to BlackBox ledger

Components:
- session.py: PhoneSession model and state management
- audio_converter.py: 8kHz ULAW <-> 24kHz PCM16 conversion
- dtmf_handler.py: IVR menu and DTMF detection
- ivr_prompts.py: TTS prompt definitions
- sip_client.py: Drachtio/FreeSwitch integration
- bridge.py: Phone-to-AI WebSocket bridge
"""

from Orchestrator.phone.session import (
    PhoneSession,
    PhoneStatus,
    AIBackend,
    CallDirection,
    PHONE_SESSIONS,
    get_session,
    get_active_sessions,
)
from Orchestrator.phone.audio_converter import AudioConverter
from Orchestrator.phone.dtmf_handler import IVRState, DTMFEvent
from Orchestrator.phone.bridge import PhoneAIBridge
from Orchestrator.phone.sip_client import FreeSwitchClient, GREENSWITCH_AVAILABLE

__all__ = [
    # Session management
    "PhoneSession",
    "PhoneStatus",
    "AIBackend",
    "CallDirection",
    "PHONE_SESSIONS",
    "get_session",
    "get_active_sessions",
    # Audio conversion
    "AudioConverter",
    # IVR
    "IVRState",
    "DTMFEvent",
    # Bridge
    "PhoneAIBridge",
    # SIP
    "FreeSwitchClient",
    "GREENSWITCH_AVAILABLE",
]
