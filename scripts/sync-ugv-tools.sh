#!/usr/bin/env bash
# scripts/sync-ugv-tools.sh - rsync local code to Jetson host
#
# Hardening (post-reboot lesson, 2026-04-16):
#   - Pre-sync guard: bail if any deploy/*.sh lacks +x locally (so rsync cannot
#     propagate a non-executable script to the Jetson).
#   - --chmod flag normalizes perms on transfer (Fu=rw is intentional for non-
#     script files; shell scripts get +x re-applied post-sync).
#   - Post-sync: explicitly chmod +x every *.sh under the destination. Belt
#     and suspenders so a bad local perm state cannot reach production.
set -euo pipefail

JETSON_IP="${JETSON_IP:-192.168.1.155}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASS="${JETSON_PASS:-jetson}"

command -v sshpass >/dev/null || { echo "sshpass not installed (apt install sshpass)"; exit 1; }

LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)/docs/ugv-beast/setup/ugv_tools_api/"
REMOTE_DIR="/home/jetson/ugv_ws_waveshare/ugv_tools_api/"

# ---------------------------------------------------------------------------
# Pre-sync: refuse to sync if any deploy script is missing +x locally.
# This is the failure mode that bit us post-reboot -- start_tools_api.sh lost
# its +x in the repo, rsync faithfully copied the bad perms, service exited
# 126. Catch it here before it reaches the Jetson.
# ---------------------------------------------------------------------------
missing_x=()
for sh in "${LOCAL_DIR}deploy/"*.sh; do
  [ -e "$sh" ] || continue
  if [ ! -x "$sh" ]; then
    missing_x+=("$sh")
  fi
done
if [ "${#missing_x[@]}" -gt 0 ]; then
  echo "ERROR: the following deploy scripts are not +x locally:" >&2
  for f in "${missing_x[@]}"; do echo "  $f" >&2; done
  echo "Fix with: chmod +x ${missing_x[*]}" >&2
  exit 2
fi

# --chmod normalizes perms during transfer (dirs 755, files 644 — we restore
# +x on scripts below). This stops asymmetric local-vs-remote perm drift.
#
# We tolerate rsync exit 23 (partial transfer) because pre-existing root-owned
# cache dirs on the Jetson can fail to delete; that must NOT block the chmod
# fix-up below. Hard-fail only on truly fatal rsync exit codes.
# Secrets: service-account JSON deployed out-of-band via scp; env files created directly on target.
set +e
sshpass -p "$JETSON_PASS" rsync -avz --delete \
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  -e "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8" \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.egg-info' \
  --exclude '.cache' \
  --exclude 'deploy/voice.env' \
  --exclude 'deploy/ears.env' \
  --exclude 'deploy/er.env' \
  --exclude 'credentials/*' \
  "$LOCAL_DIR" "${JETSON_USER}@${JETSON_IP}:${REMOTE_DIR}"
rsync_rc=$?
set -e
if [ "$rsync_rc" -ne 0 ] && [ "$rsync_rc" -ne 23 ] && [ "$rsync_rc" -ne 24 ]; then
  echo "rsync failed with rc=$rsync_rc (fatal)" >&2
  exit "$rsync_rc"
fi
if [ "$rsync_rc" -ne 0 ]; then
  echo "rsync exit $rsync_rc is tolerable (partial transfer, likely stale root-owned files); continuing with chmod fix-up" >&2
fi

# ---------------------------------------------------------------------------
# Post-sync: force +x on every *.sh under the destination. rsync's --chmod
# can (and does) strip +x to match the "files 644" baseline; we re-apply it
# so systemd can always ExecStart the script. This is redundant with the
# /bin/bash wrapper in the unit files, but fixes the root cause.
# ---------------------------------------------------------------------------
sshpass -p "$JETSON_PASS" ssh -o StrictHostKeyChecking=accept-new \
  "${JETSON_USER}@${JETSON_IP}" \
  "find ${REMOTE_DIR} -name '*.sh' -exec chmod +x {} \;"

echo "Synced to ${JETSON_USER}@${JETSON_IP}:${REMOTE_DIR}"
echo "All *.sh under ${REMOTE_DIR} are +x."
