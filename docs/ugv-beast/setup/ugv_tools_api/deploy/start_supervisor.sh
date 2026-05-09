#!/bin/bash
# start_supervisor.sh — container entrypoint for the supervisor service.
# Invoked by the host-side ugv-supervisor.service unit via `docker exec`.
set -eo pipefail

# Clear any stale proxy vars baked into the container's shell defaults.
# The ugv_waveshare container has https_proxy=http://192.168.10.185:10809
# in its login profile, which is unreachable from this Jetson and silently
# blocks ALL outbound HTTPS — including the Gemini Live websocket
# handshake. Unsetting here prevents the supervisor from inheriting the
# bad proxy. If a real proxy is ever needed, set HTTPS_PROXY in
# supervisor.env and add it to the unit's docker-exec env allowlist.
unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
unset ALL_PROXY all_proxy NO_PROXY no_proxy

# ROS setup scripts reference unbound vars (e.g. AMENT_TRACE_SETUP_FILES).
# Disable nounset only while sourcing them.
set +u
source /opt/ros/humble/setup.bash
source /home/ws/ugv_ws/install/setup.bash
set -u

export PYTHONPATH=/home/ws/ugv_ws/ugv_tools_api:${PYTHONPATH:-}
export SUPERVISOR_HOST=${SUPERVISOR_HOST:-0.0.0.0}
export SUPERVISOR_PORT=${SUPERVISOR_PORT:-8083}

# Wait up to 30s for sibling services to be reachable before the
# supervisor tries to dispatch missions at them. ugv-tools-api serves
# on :8080 (all the robot control tools); ugv-er serves on :8082
# (the mission executor). If either is down, the supervisor tool
# handlers will return error dicts gracefully — but starting fresh
# with both dead is a noisy failure mode we avoid here.
for i in $(seq 1 30); do
  tools_ok=0
  er_ok=0
  curl -sf "http://localhost:8080/health" >/dev/null 2>&1 && tools_ok=1
  curl -sf "http://localhost:8082/health" >/dev/null 2>&1 && er_ok=1
  if [ "$tools_ok" = "1" ] && [ "$er_ok" = "1" ]; then
    break
  fi
  sleep 1
done

# Create the handle-store directory if missing so the HandleStore's
# atomic writes (tmp + rename) succeed on first boot before any
# session-resumption token exists.
mkdir -p "$(dirname "${SUPERVISOR_HANDLE_STORE:-/home/ws/ugv_ws/ugv_supervisor_state/session_handle.txt}")"

exec python3 -m ugv_tools_api.supervisor
