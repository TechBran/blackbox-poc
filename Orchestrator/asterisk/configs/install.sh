#!/bin/bash
# =============================================================================
# install.sh — Install Asterisk configs for AI BlackBox TG200 integration
#
# Run with: sudo bash Orchestrator/asterisk/configs/install.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASTERISK_DIR="/etc/asterisk"

echo "=== AI BlackBox Asterisk Configuration Installer ==="

# Backup existing configs
echo "[1/5] Backing up existing configs..."
for f in pjsip.conf extensions.conf ari.conf http.conf; do
    if [ -f "$ASTERISK_DIR/$f" ]; then
        cp "$ASTERISK_DIR/$f" "$ASTERISK_DIR/$f.bak.$(date +%Y%m%d%H%M%S)"
        echo "  Backed up: $f"
    fi
done

# Copy new configs
echo "[2/5] Installing new configs..."
cp "$SCRIPT_DIR/pjsip.conf" "$ASTERISK_DIR/pjsip.conf"
cp "$SCRIPT_DIR/extensions.conf" "$ASTERISK_DIR/extensions.conf"
cp "$SCRIPT_DIR/ari.conf" "$ASTERISK_DIR/ari.conf"
cp "$SCRIPT_DIR/http.conf" "$ASTERISK_DIR/http.conf"

# Fix ownership
echo "[3/5] Setting permissions..."
chown asterisk:asterisk "$ASTERISK_DIR/pjsip.conf" "$ASTERISK_DIR/extensions.conf" "$ASTERISK_DIR/ari.conf" "$ASTERISK_DIR/http.conf"
chmod 640 "$ASTERISK_DIR/pjsip.conf" "$ASTERISK_DIR/extensions.conf" "$ASTERISK_DIR/ari.conf" "$ASTERISK_DIR/http.conf"

# Enable and start Asterisk
echo "[4/5] Enabling Asterisk service..."
systemctl enable asterisk
systemctl restart asterisk
sleep 2

# Verify
echo "[5/5] Verifying..."
if systemctl is-active --quiet asterisk; then
    echo "  Asterisk is running"
    asterisk -rx "core show version"
    echo ""
    echo "  Checking key modules:"
    asterisk -rx "module show like audiosocket" 2>/dev/null | grep -v "^$" || echo "  WARNING: app_audiosocket not loaded"
    asterisk -rx "module show like res_ari" 2>/dev/null | grep -c "res_ari" | xargs -I{} echo "  ARI modules loaded: {}"
    asterisk -rx "module show like codec_g722" 2>/dev/null | grep -v "^$" || echo "  WARNING: codec_g722 not loaded"
    echo ""
    echo "  Testing ARI..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -u blackbox:blackbox-ari-secret-2026 http://127.0.0.1:8088/ari/asterisk/info)
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  ARI is responding (HTTP 200)"
    else
        echo "  WARNING: ARI returned HTTP $HTTP_CODE"
    fi
else
    echo "  ERROR: Asterisk failed to start!"
    journalctl -u asterisk --no-pager -n 20
fi

echo ""
echo "=== Installation complete ==="
echo "Next: Run 'sudo bash Orchestrator/asterisk/configs/install.sh' if you haven't already"
echo "Then: Test with 'curl -u blackbox:blackbox-ari-secret-2026 http://127.0.0.1:8088/ari/asterisk/info'"
