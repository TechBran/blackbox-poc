#!/usr/bin/env bash
# AI BlackBox installer — Ubuntu 24.04
set -euo pipefail

# ── Step 0: detect sudo, resolve real user/home (audit M6) ──
if [[ $EUID -eq 0 ]]; then
    if [[ -z "${SUDO_USER:-}" ]]; then
        echo "[install] ERROR: do not run as direct root. Run as your user (sudo invoked as needed)."
        exit 1
    fi
    REAL_USER="$SUDO_USER"
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_USER="$USER"
    REAL_HOME="$HOME"
fi

# Determine BLACKBOX_ROOT: parent of the directory holding this script
BLACKBOX_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "[install] BLACKBOX_ROOT=$BLACKBOX_ROOT"
echo "[install] REAL_USER=$REAL_USER  REAL_HOME=$REAL_HOME"

# Audit: REAL_USER drives sudoers grants — defend against weird envvar injection
if ! [[ "$REAL_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "[install] ERROR: REAL_USER='$REAL_USER' contains invalid characters (POSIX usernames only)" >&2
    exit 1
fi

# ── Pre-flight (Phase 4.0) ──
"$BLACKBOX_ROOT/Scripts/install-preflight.sh"

# ── Step 1: apt deps (audit C1 — corrected pipeline) ──
echo "[install] Installing system packages..."
sudo apt update
grep -E '^[a-zA-Z0-9._+-]+\s+#\s+MUST_HAVE' \
    "$BLACKBOX_ROOT/Scripts/onboarding/system-packages.txt" \
  | awk '{print $1}' \
  | xargs sudo apt install -y

# ── Step 1b: Tailscale install (audit E1 — official installer adds apt repo + signing key + package) ──
# Pre-installs Tailscale on every BlackBox. Wizard onboarding step then only
# needs to handle authentication (not install). Idempotent on re-run.
if [[ ! -x /usr/bin/tailscale ]]; then
    echo "[install] Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "[install] Tailscale already installed (skipping)"
fi

# Audit: fail fast if tailscale binary not at the path sudoers grants
if ! [[ -x /usr/bin/tailscale ]]; then
    echo "[install] ERROR: tailscale binary not at /usr/bin/tailscale after install" >&2
    echo "[install] Found at: $(command -v tailscale 2>/dev/null || echo 'nowhere on PATH')" >&2
    exit 1
fi

# ── Step 2: Python venv (audit I1 — run as $REAL_USER so files are user-owned, not root-owned) ──
echo "[install] Creating Python venv..."
sudo -u "$REAL_USER" python3.12 -m venv "$BLACKBOX_ROOT/Orchestrator/venv"
sudo -u "$REAL_USER" "$BLACKBOX_ROOT/Orchestrator/venv/bin/pip" install --upgrade pip
sudo -u "$REAL_USER" "$BLACKBOX_ROOT/Orchestrator/venv/bin/pip" install -r "$BLACKBOX_ROOT/requirements.txt"

# ── Step 3: .env from template (audit I2 — created as $REAL_USER, mode 0600 since it holds API keys) ──
if [[ ! -f "$BLACKBOX_ROOT/.env" ]]; then
    sudo -u "$REAL_USER" cp "$BLACKBOX_ROOT/.env.template" "$BLACKBOX_ROOT/.env"
    sudo -u "$REAL_USER" bash -c "echo 'BLACKBOX_ROOT=$BLACKBOX_ROOT' >> '$BLACKBOX_ROOT/.env'"
    chmod 0600 "$BLACKBOX_ROOT/.env"
    echo "[install] Created .env from template (mode 0600)"
fi

# ── Step 3b: config.ini from template (per-customer state — operators + pairing) ──
# Customer ZIP doesn't ship config.ini (gitignored to prevent shipping the
# author's operator roster + tailnet hostname). Wizard's operator + tailscale
# steps populate [users] + [pairing] sections via /onboarding/config writes.
if [[ ! -f "$BLACKBOX_ROOT/config.ini" ]]; then
    sudo -u "$REAL_USER" cp "$BLACKBOX_ROOT/config.ini.template" "$BLACKBOX_ROOT/config.ini"
    echo "[install] Created config.ini from template"
fi

# ── Step 4: systemd unit (audit M2 + M3 + Q3 + Q4) ──
echo "[install] Installing blackbox.service..."
sudo tee /etc/systemd/system/blackbox.service > /dev/null <<EOF
[Unit]
Description=AI BlackBox Orchestrator
Documentation=https://github.com/TechBran/blackbox-poc
After=network-online.target
Wants=network-online.target
# Restart rate limiting (audit empirical fix: these belong in [Unit], not [Service]
# — systemd silently ignores them in [Service] and warns. Without them, Restart=always
# loops forever on a broken install at ~6 attempts/min instead of bounding to 5 per 600s.)
StartLimitBurst=5
StartLimitIntervalSec=600

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$BLACKBOX_ROOT
EnvironmentFile=$BLACKBOX_ROOT/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$BLACKBOX_ROOT/Orchestrator/venv/bin/python -m uvicorn Orchestrator.app:app \\
    --host 0.0.0.0 --port 9091 \\
    --timeout-keep-alive 120 --limit-max-requests 10000 --loop uvloop
Restart=always
RestartSec=10

# Memory pressure (audit Q4) — soft cap at 70 % of system RAM
MemoryHigh=70%

# Security hardening (audit M2 — preserved from existing unit)
# NOTE: ProtectHome=read-only (NOT true) because BLACKBOX_ROOT lives in /home
# (audit Q2=A install location). ProtectHome=true masks /home entirely so the
# sandboxed process cannot exec \$BLACKBOX_ROOT/Orchestrator/venv/bin/python
# → status=203/EXEC. read-only allows visibility; ReadWritePaths punches through
# for the install dir's write needs (Volume/, Manifest/, Fossils/, etc.).
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$BLACKBOX_ROOT
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=blackbox

[Install]
WantedBy=multi-user.target
EOF

# ── Step 4b: override.conf scaffold (audit M3 carry-forward) ──
sudo mkdir -p /etc/systemd/system/blackbox.service.d
sudo tee /etc/systemd/system/blackbox.service.d/override.conf > /dev/null <<EOF
# BlackBox service override — customize without modifying the main unit.
# Uncomment + edit, then run:
#   sudo systemctl daemon-reload && sudo systemctl restart blackbox

[Service]
# Change port (default 9091):
# ExecStart=
# ExecStart=$BLACKBOX_ROOT/Orchestrator/venv/bin/python -m uvicorn Orchestrator.app:app --host 0.0.0.0 --port 8000

# Override memory pressure (default 70 %):
# MemoryHigh=50%

# Override CPU priority:
# Nice=-5
EOF

# ── Step 4c: log rotation (audit M3 carry-forward) ──
sudo tee /etc/logrotate.d/blackbox > /dev/null <<EOF
/var/log/blackbox/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0640 $REAL_USER $REAL_USER
    sharedscripts
}
EOF

# ── Step 4d: helper script (audit M3 carry-forward) ──
cat > "$BLACKBOX_ROOT/blackbox-status.sh" <<'STATUSEOF'
#!/usr/bin/env bash
echo "=== BlackBox Service ==="
systemctl status blackbox.service --no-pager | head -15
echo
echo "=== Recent Logs ==="
journalctl -u blackbox.service -n 20 --no-pager
echo
echo "=== Health ==="
curl -fsS http://localhost:9091/health 2>&1 | head -5
STATUSEOF
chmod +x "$BLACKBOX_ROOT/blackbox-status.sh"

# ── Step 4e: sudoers grant for runtime tailscale operations ──
# (Tailscale wizard actuator: bounded NOPASSWD for the specific commands
# the onboarding step needs. install -m 0440 atomic-replaces existing
# file; visudo-check aborts if syntax broken.)
sed "s|REAL_USER_PLACEHOLDER|$REAL_USER|g" \
    "$BLACKBOX_ROOT/installer/templates/sudoers-blackbox-tailscale" \
    | sudo install -m 0440 -o root -g root /dev/stdin /etc/sudoers.d/blackbox-tailscale
if ! sudo visudo -c -f /etc/sudoers.d/blackbox-tailscale > /dev/null; then
    echo "[install] ERROR: sudoers file syntax check failed" >&2
    sudo rm -f /etc/sudoers.d/blackbox-tailscale
    exit 1
fi
echo "[install] Sudoers grant written for $REAL_USER (tailscale operations)"

# ── Step 5: Build + install Tauri setup app (audit C2 / Q1=A) ──
build_tauri_setup() {
    echo "[install] Building BlackBox Setup (Tauri .deb)..."
    sudo apt install -y \
        libwebkit2gtk-4.1-dev libsoup-3.0-dev librsvg2-dev libxdo-dev \
        libssl-dev libayatana-appindicator3-dev pkg-config build-essential
    if ! command -v cargo > /dev/null; then
        echo "[install] Installing Rust toolchain via rustup..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
            | sh -s -- -y --default-toolchain stable
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
    fi
    if ! cargo tauri --version 2>/dev/null | grep -q "^tauri-cli"; then
        cargo install tauri-cli --locked --version "^2.0"
    fi
    # Pre-clean bundle dir so we always install the freshly-built .deb (audit I4)
    rm -f "$BLACKBOX_ROOT/installer/src-tauri/target/release/bundle/deb/"*.deb 2>/dev/null || true
    cd "$BLACKBOX_ROOT/installer"
    npm install --no-audit --no-fund > /dev/null 2>&1 || true
    cargo tauri build --bundles deb
    DEB_FILE=$(ls "$BLACKBOX_ROOT/installer/src-tauri/target/release/bundle/deb/"*.deb | head -1)
    if [[ ! -f "$DEB_FILE" ]]; then
        echo "[install] ERROR: cargo tauri build did not produce a .deb" >&2
        exit 1
    fi
    echo "[install] Built: $DEB_FILE"
    cd "$BLACKBOX_ROOT"
}
build_tauri_setup
DEB_FILE=$(ls "$BLACKBOX_ROOT/installer/src-tauri/target/release/bundle/deb/"*.deb | head -1)
echo "[install] Installing $DEB_FILE..."
sudo apt install -y "$DEB_FILE"   # apt 1.1+ resolves deps + installs in one step (audit N7)

# ── Step 6a: autostart .desktop — first-boot wizard launch (audit M6) ──
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.config/autostart"
sudo -u "$REAL_USER" cp "$BLACKBOX_ROOT/installer/dist/blackbox-setup-autostart.desktop" \
    "$REAL_HOME/.config/autostart/blackbox-setup.desktop"

# ── Step 6b: persistent .desktop — manage-mode launcher (audit M6) ──
sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.local/share/applications"
sudo -u "$REAL_USER" cp "$BLACKBOX_ROOT/installer/dist/blackbox-setup.desktop" \
    "$REAL_HOME/.local/share/applications/blackbox-setup.desktop"
sudo -u "$REAL_USER" update-desktop-database "$REAL_HOME/.local/share/applications" 2>/dev/null || true

# ── Step 7: enable + restart (audit M5 — restart works whether running or stopped) ──
sudo systemctl daemon-reload
sudo systemctl enable blackbox.service
sudo systemctl restart blackbox.service

# ── Step 8: Final user message (audit C3 — /usr/bin not /usr/local/bin) ──
echo
echo "[install] Done. Reboot to launch BlackBox Setup, or run /usr/bin/blackbox-setup --first-run now."
echo "[install] Find 'BlackBox Setup' in your applications menu later for maintenance/manage mode."
