# AI BlackBox Phone Integration Setup Guide

This guide walks you through setting up phone calling for the AI BlackBox system.

## Overview

The phone integration allows:
- **Inbound calls**: Users call a phone number and select an AI assistant via IVR
- **Outbound calls**: The BlackBox can initiate calls to users
- **IVR Menu**: Press 1-4 to select Claude, Gemini, GPT, or Grok
- **Full logging**: All calls are saved to the BlackBox immutable ledger

## Architecture

```
Phone Network → 3CX Cloud PBX → FreeSwitch → Orchestrator → AI Backends
                    ↓              ↓
              SIP Trunk       WebSocket
                              Audio Bridge
```

## Prerequisites

1. **3CX Cloud PBX Account** (Free tier available)
   - Sign up at https://www.3cx.com/phone-system/get-3cx-free/
   - Provides a free DID number

2. **Docker and Docker Compose**
   - For running FreeSwitch and Drachtio containers

3. **Network Requirements**
   - Port 5060 (UDP/TCP) for SIP signaling
   - Ports 16384-32768 (UDP) for RTP media
   - Port 9022 (TCP) for Drachtio admin

## Setup Steps

### Step 1: Configure 3CX

1. Log into your 3CX Management Console
2. Create a new extension for AI BlackBox
3. Note down:
   - Extension number
   - Extension password
   - 3CX URL (e.g., yourcompany.3cx.us)
   - DID number assigned

### Step 2: Configure Environment

1. Copy the example environment file:
   ```bash
   cp Docker/phone.env.example Docker/.env
   ```

2. Edit `Docker/.env` with your 3CX credentials:
   ```
   PBX_3CX_URL=yourcompany.3cx.us
   PBX_3CX_EXTENSION=1001
   PBX_3CX_PASSWORD=your-extension-password
   PBX_3CX_DID=+15551234567
   PBX_OUTBOUND_CALLER_ID=+15551234567
   PHONE_ENABLED=true
   ```

3. If running on a cloud server, set HOST_IP:
   ```
   HOST_IP=your.public.ip.address
   ```

### Step 3: Start Docker Containers

```bash
cd Docker
docker-compose -f docker-compose.phone.yml up -d
```

Verify containers are running:
```bash
docker ps | grep blackbox
```

### Step 4: Enable Phone in Orchestrator

Add to your `.env` or environment:
```
PHONE_ENABLED=true
FREESWITCH_HOST=localhost
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=ClueCon
```

Restart the Orchestrator:
```bash
sudo systemctl restart blackbox.service
```

### Step 5: Verify Setup

Check phone system status:
```bash
curl http://localhost:9091/phone/status
```

Expected response:
```json
{
  "enabled": true,
  "freeswitch_connected": true,
  "greenswitch_available": true,
  "active_sessions": 0,
  "config": {...}
}
```

## Usage

### Making an Inbound Call

1. Call your 3CX DID number
2. Listen to the IVR menu:
   - Press 1 for Claude Code
   - Press 2 for Gemini
   - Press 3 for GPT
   - Press 4 for Grok
3. Start talking to your AI assistant

### Making an Outbound Call

```bash
curl -X POST http://localhost:9091/phone/call \
  -H "Content-Type: application/json" \
  -d '{
    "to": "+15559876543",
    "operator": "Brandon",
    "backend": "openai_realtime"
  }'
```

### Listing Active Calls

```bash
curl http://localhost:9091/phone/sessions
```

### Hanging Up a Call

```bash
curl -X POST http://localhost:9091/phone/hangup/{session_id}
```

## IVR Menu Reference

| Key | AI Backend | Description |
|-----|------------|-------------|
| 1 | Claude Code | Uses STT→Claude→TTS pipeline |
| 2 | Gemini Live | Native Gemini audio streaming |
| 3 | GPT (Default) | OpenAI Realtime native audio |
| 4 | Grok | xAI Grok Voice Agent |

## Troubleshooting

### FreeSwitch Not Connecting

1. Check container logs:
   ```bash
   docker logs blackbox-freeswitch
   ```

2. Verify ESL port is open:
   ```bash
   nc -zv localhost 8021
   ```

3. Test ESL connection:
   ```bash
   docker exec -it blackbox-freeswitch fs_cli -x "status"
   ```

### No Audio

1. Check RTP port range is open (16384-32768 UDP)
2. Verify HOST_IP is set correctly for cloud deployments
3. Check 3CX gateway registration:
   ```bash
   docker exec -it blackbox-freeswitch fs_cli -x "sofia status gateway 3cx"
   ```

### IVR Not Playing

1. Verify OpenAI API key is set (for TTS)
2. Check Orchestrator logs for TTS errors
3. Ensure audio path is correct in FreeSwitch config

### Call Drops After IVR

1. Verify AI API keys are configured (OpenAI, Google, xAI)
2. Check Orchestrator WebSocket endpoint is accessible
3. Review logs for bridge connection errors

## Security Notes

1. **Firewall Rules**: Only expose SIP ports to your 3CX provider
2. **ESL Access**: Keep port 8021 internal (localhost only)
3. **API Keys**: Store securely, never commit to git
4. **Call Logging**: All conversations are logged to BlackBox - inform callers

## Cost Considerations

- **3CX Free Tier**: Limited concurrent calls, sufficient for personal use
- **AI API Costs**: Usage billed by respective providers
  - OpenAI Realtime: ~$0.06/min input + $0.24/min output
  - Gemini Live: Included in Gemini API pricing
  - Grok: Per xAI pricing
  - Claude Code: Uses Anthropic + OpenAI TTS pricing

## Optional: Install greenswitch

For Python-based ESL control (alternative to WebSocket):
```bash
pip install greenswitch
```

This enables direct FreeSwitch control from the Orchestrator.
