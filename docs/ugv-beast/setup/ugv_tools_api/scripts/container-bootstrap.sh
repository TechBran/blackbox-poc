#!/usr/bin/env bash
# container-bootstrap.sh — idempotent dep install for ugv_waveshare container
# Run inside the container (as root). Safe to re-run.
set -euo pipefail

echo "[bootstrap] Removing stale opencv-contrib-python artifacts (numpy 2.x ABI fix)..."
pip3 uninstall -y opencv-contrib-python 2>/dev/null || true
rm -f  /usr/local/lib/python3.10/dist-packages/cv2/config-3.10.py
rm -rf /usr/local/lib/python3.10/dist-packages/cv2/python-3.10
rm -rf /usr/local/lib/python3.10/dist-packages/cv2/__pycache__

echo "[bootstrap] Installing pinned ugv_tools_api deps..."
pip3 install \
  'fastapi==0.136.*' \
  'uvicorn[standard]==0.44.*' \
  'pydantic==2.13.*' \
  'opencv-python-headless==4.10.*' \
  'numpy>=1.24,<2' \
  'depthai>=2.30,<3' \
  'pytest>=6.2,<9' \
  'httpx==0.28.*'

echo "[bootstrap] Verifying critical imports..."
python3 -c "import cv2, numpy, depthai, fastapi, uvicorn, pydantic, pytest, httpx; \
  print('cv2', cv2.__version__, '| numpy', numpy.__version__, '| depthai', depthai.__version__)"

echo "[bootstrap] Installing mpg123 (apt) for ugv-voice MP3 playback..."
if ! command -v mpg123 >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y mpg123
fi
mpg123 --version 2>&1 | head -1 || { echo "mpg123 install failed"; exit 1; }

echo "[bootstrap] Installing portaudio19-dev (apt) for PyAudio build..."
# PyAudio needs portaudio headers to build the C extension. Idempotent.
if ! dpkg -s portaudio19-dev >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y portaudio19-dev
fi

echo "[bootstrap] Installing audio routing tools for system-pulseaudio path..."
# alsa-utils provides arecord/aplay (supervisor's audio_io.py spawns these as
# subprocesses). pulseaudio-utils provides pactl for diagnostics + the pulse
# routing tests. libasound2-plugins provides the ALSA `pulse` PCM type so
# arecord -D pulse routes through the host's system pulseaudio (which holds
# the EMEET continuously, eliminating the USB altsetting cascade trigger).
need_audio_pkgs=()
for pkg in alsa-utils pulseaudio-utils libasound2-plugins; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    need_audio_pkgs+=("$pkg")
  fi
done
if [ ${#need_audio_pkgs[@]} -gt 0 ]; then
  apt-get update -qq && apt-get install -y "${need_audio_pkgs[@]}"
fi
command -v arecord >/dev/null && command -v pactl >/dev/null || \
  { echo "audio tools install failed"; exit 1; }

echo "[bootstrap] Installing ugv-ears pip deps (pyaudio, webrtcvad, openwakeword)..."
# openwakeword is the wake-word scorer (already present but pin anyway).
# webrtcvad for endpointing. pyaudio for mic capture.
pip3 install \
  'pyaudio>=0.2.11' \
  'webrtcvad>=2.0.10' \
  'openwakeword>=0.6.0'

echo "[bootstrap] Verifying ears imports..."
python3 -c "import pyaudio, webrtcvad, openwakeword; print('ears deps OK')"

echo "[bootstrap] Installing ros-humble-robot-localization (slam_nav.launch.py EKF dep)..."
# slam_nav.launch.py imports robot_localization for the ekf_filter_node that
# fuses wheel odom + OAK-D IMU into /odom. Missing from upstream Waveshare
# image (silent-crashes Nav2 stack at launch-time). Container's USTC ROS2
# mirror has expired GPG + 404 packages, so fetch directly from official
# packages.ros.org. Auto-resolves the current version from the apt index
# so this doesn't break when Open Robotics rolls a new build.
if ! dpkg -s ros-humble-robot-localization >/dev/null 2>&1; then
  apt-get install -y libgeographic19 libgeographic-dev
  pkgs_idx=$(mktemp)
  curl -fsSL "http://packages.ros.org/ros2/ubuntu/dists/jammy/main/binary-arm64/Packages.gz" \
    | gunzip > "$pkgs_idx"
  for pkg in ros-humble-geographic-msgs ros-humble-robot-localization; do
    fn=$(awk -v p="Package: $pkg" '$0==p,/^$/' "$pkgs_idx" \
         | awk '/^Filename:/{print $2; exit}')
    [ -n "$fn" ] || { echo "could not resolve $pkg in apt index"; exit 1; }
    curl -fsSL -o "/tmp/$(basename $fn)" "http://packages.ros.org/ros2/ubuntu/$fn"
  done
  dpkg -i /tmp/ros-humble-geographic-msgs_*.deb /tmp/ros-humble-robot-localization_*.deb
  rm -f /tmp/ros-humble-geographic-msgs_*.deb /tmp/ros-humble-robot-localization_*.deb "$pkgs_idx"
fi
dpkg -s ros-humble-robot-localization >/dev/null 2>&1 || \
  { echo "robot_localization install failed"; exit 1; }

echo "[bootstrap] OK"
