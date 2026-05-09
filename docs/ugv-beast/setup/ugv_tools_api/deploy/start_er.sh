#!/usr/bin/env bash
# start_er.sh - runs inside ugv_waveshare container
# Launches the Gemini Robotics-ER 1.6 on-device agent (FastAPI on $ER_PORT).
# Calls Vertex AI directly (service-account JSON) and OpenAI directly (Whisper
# STT is in ears, TTS in speak_server) - no BlackBox proxy in the audio/reasoning
# loop.
set -eo pipefail

# sensors.py reaches ROS2 via rclpy (its own singleton node in this process),
# so we need the same humble + ugv_ws overlays the tools_api service uses.
# Setup scripts reference unbound vars; disable nounset only while sourcing.
set +u
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
set -u

export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:${PYTHONPATH:-}

# Container has stale HTTP(S)_PROXY env vars pointing at an unreachable host.
# httpx auto-uses them and breaks Vertex / OpenAI calls. Strip them so cloud
# endpoints are reached directly via the Jetson's normal route.
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy

cd /home/ws/ugv_ws/ugv_tools_api

exec python3 -m ugv_tools_api.er.server
