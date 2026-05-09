#!/usr/bin/env bash
# start_voice.sh - runs inside ugv_waveshare container
# Launches the TTS speak_server (FastAPI) bound to UGV_VOICE_PORT.
# All TTS goes through BlackBox Orchestrator — Jetson holds NO API keys.
set -eo pipefail

export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:${PYTHONPATH:-}

# Container has stale HTTP(S)_PROXY env vars pointing at an unreachable host.
# httpx auto-uses them and breaks BlackBox calls. Strip them so the
# BlackBox URL is reached directly via the Jetson's normal route.
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy

# Defaults (overridable by env from systemd)
export UGV_VOICE_PORT="${UGV_VOICE_PORT:-8081}"
# Pin USB PnP audio adapter (JBL on 3.5mm) by ALSA card name, not index,
# so it survives card-enumeration drift. `Device` is the kernel's name for
# the Solid State System USB PnP Audio Device.
export UGV_ALSA_DEVICE="${UGV_ALSA_DEVICE:-plughw:CARD=Device}"
export UGV_VOICE="${UGV_VOICE:-onyx}"
# BLACKBOX_URL must come from voice.env — no default, fail-loud if unset.

exec python3 -m ugv_tools_api.voice.speak_server
