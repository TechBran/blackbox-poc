#!/usr/bin/env bash
# start_ears.sh - runs inside ugv_waveshare container
# Mic loop: PyAudio -> openWakeWord -> OpenAI Whisper (direct) -> local /mission.
# All cloud calls are direct from the Jetson; no BlackBox hops.
#
# Container one-time deps (survive restart, lost only on container rebuild):
#   apt install -y alsa-utils        # for aplay -- wake/done chimes
# OPENAI_API_KEY and ER_URL come from er.env via systemd EnvironmentFile.
set -eo pipefail

export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:${PYTHONPATH:-}

# Container has stale HTTP(S)_PROXY env vars pointing at an unreachable host.
# httpx auto-uses them and breaks Whisper/speak calls. Strip them.
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy

# Defaults (overridable by systemd env / er.env)
export MIC_DEVICE_HINT="${MIC_DEVICE_HINT:-USB Camera}"
export WAKEWORD_THRESHOLD="${WAKEWORD_THRESHOLD:-0.5}"
export VAD_AGGRESSIVENESS="${VAD_AGGRESSIVENESS:-2}"
export WAKEWORD_MODEL="${WAKEWORD_MODEL:-/home/ws/ugv_ws/ugv_tools_api/voice_models/black_box_flight_recorder.onnx}"
export OPERATOR="${OPERATOR:-Brandon}"
export UGV_ALSA_DEVICE="${UGV_ALSA_DEVICE:-plughw:CARD=Device}"

exec python3 -m ugv_tools_api.voice.ears
