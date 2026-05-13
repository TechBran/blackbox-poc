#!/usr/bin/env bash
# Pre-install validation — run before main install.sh begins
set -euo pipefail

fail() { echo "[preflight] FAIL: $*" >&2; exit 1; }
warn() { echo "[preflight] WARN: $*" >&2; }
ok()   { echo "[preflight] OK: $*"; }

# 1. Ubuntu 24.04 only
if ! grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
    VERSION="$(grep '^PRETTY_NAME' /etc/os-release | cut -d= -f2 | tr -d '"')"
    fail "Ubuntu 24.04 LTS required. Detected: ${VERSION:-unknown}"
fi
ok "Ubuntu 24.04 detected"

# 2. Disk free — 10 GB minimum on /
AVAIL_GB=$(df -BG / | awk 'NR==2 {print $4}' | tr -d 'G')
if (( AVAIL_GB < 10 )); then
    fail "Need 10 GB free on /, only ${AVAIL_GB} GB available"
fi
ok "${AVAIL_GB} GB free on /"

# 3. Memory — 4 GB minimum
MEM_GB=$(awk '/MemTotal/ {printf "%d\n", $2/1048576}' /proc/meminfo)
if (( MEM_GB < 4 )); then
    fail "Need 4 GB RAM minimum, only ${MEM_GB} GB detected"
fi
ok "${MEM_GB} GB RAM"

# 4. Network — github.com reachable
if ! curl -fsS --max-time 10 https://api.github.com/zen > /dev/null; then
    fail "Cannot reach github.com — check network"
fi
ok "github.com reachable"

# 5. Sudo access
if ! sudo -n true 2>/dev/null; then
    warn "sudo will prompt for password during install"
else
    ok "sudo passwordless"
fi

# 6. Existing BlackBox install detection
if systemctl is-active --quiet blackbox.service 2>/dev/null; then
    warn "blackbox.service is currently running — install.sh will restart it"
fi

# 7. Refuse direct root invocation (audit M6)
if [[ $EUID -eq 0 ]] && [[ -z "${SUDO_USER:-}" ]]; then
    fail "Do not run as direct root. Run as your user; sudo will be invoked when needed."
fi

echo "[preflight] All checks passed."
