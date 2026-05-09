#!/usr/bin/env python3
"""
Asterisk PBX integration for AI BlackBox telephony.

Provides:
- ARI (Asterisk REST Interface) client for call control + events
- AudioSocket TCP server for bidirectional 16kHz PCM16 audio
- Gateway management (discover, add, configure TG200 units)

Architecture:
  Caller → Cell Tower → TG200 (G.722 SIP) → Asterisk (slin16@16kHz)
  → AudioSocket TCP → Orchestrator → PhoneAIBridge → AI Backend
"""

import os

# Quick config check without circular import
ASTERISK_ENABLED = os.getenv("ASTERISK_ENABLED", "false").lower() == "true"
