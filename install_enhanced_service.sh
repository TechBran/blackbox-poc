#!/bin/bash
# Installation script for enhanced BlackBox service with self-healing

set -e

echo "========================================"
echo "Installing Enhanced BlackBox Service"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
   echo "Please do not run as root. Run as the user who will run the service."
   exit 1
fi

# Get current directory
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER=$(whoami)

echo "Install directory: $INSTALL_DIR"
echo "Service user: $CURRENT_USER"
echo ""

# Stop existing service if running
echo "Stopping existing service if running..."
sudo systemctl stop blackbox.service 2>/dev/null || true
echo ""

# Create service file from template
echo "Creating enhanced service file..."
sed -e "s|%USER%|$CURRENT_USER|g" \
    -e "s|%INSTALL_DIR%|$INSTALL_DIR|g" \
    blackbox-enhanced.service > /tmp/blackbox.service

# Install the service
sudo cp /tmp/blackbox.service /etc/systemd/system/blackbox.service
rm /tmp/blackbox.service

# Make cleanup script executable
chmod +x "$INSTALL_DIR/cleanup_crash.sh"

# Create systemd override directory for easy customization
sudo mkdir -p /etc/systemd/system/blackbox.service.d

# Create override file for port customization
sudo tee /etc/systemd/system/blackbox.service.d/override.conf > /dev/null <<EOF
# Override file for BlackBox service
# You can customize settings here without modifying the main service file

[Service]
# Change port if needed (default is 9091)
# ExecStart=
# ExecStart=$INSTALL_DIR/Orchestrator/venv/bin/python -m uvicorn Orchestrator.app:app --host 0.0.0.0 --port 8000

# Adjust memory limits if needed
# MemoryMax=8G
# MemoryHigh=6G

# Adjust CPU quota if needed
# CPUQuota=90%
EOF

# Create a watchdog timer service for additional monitoring
sudo tee /etc/systemd/system/blackbox-watchdog.service > /dev/null <<EOF
[Unit]
Description=BlackBox Watchdog Monitor
After=blackbox.service
Requires=blackbox.service

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'curl -f http://localhost:9091/watchdog || (echo "Watchdog check failed, restarting service" | systemd-cat -t blackbox-watchdog -p err && systemctl restart blackbox.service)'

[Install]
WantedBy=multi-user.target
EOF

# Create watchdog timer
sudo tee /etc/systemd/system/blackbox-watchdog.timer > /dev/null <<EOF
[Unit]
Description=Run BlackBox Watchdog every 2 minutes
Requires=blackbox-watchdog.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

# Create a log rotation config
sudo tee /etc/logrotate.d/blackbox > /dev/null <<EOF
/var/log/blackbox/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0640 $CURRENT_USER $CURRENT_USER
    sharedscripts
    postrotate
        systemctl reload blackbox.service > /dev/null 2>&1 || true
    endscript
}
EOF

# Reload systemd
echo "Reloading systemd configuration..."
sudo systemctl daemon-reload

# Enable services
echo "Enabling services..."
sudo systemctl enable blackbox.service
sudo systemctl enable blackbox-watchdog.timer

# Create helper scripts
echo "Creating helper scripts..."

# Create status script
cat > "$INSTALL_DIR/blackbox-status.sh" <<'EOF'
#!/bin/bash
echo "=== BlackBox Service Status ==="
systemctl status blackbox.service --no-pager
echo ""
echo "=== Recent Logs ==="
journalctl -u blackbox.service -n 20 --no-pager
echo ""
echo "=== Disk Usage ==="
df -h /
echo ""
echo "=== Archive Status ==="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/Archive" ]; then
    COUNT=$(find "$SCRIPT_DIR/Archive" -name "Volume_*.txt" 2>/dev/null | wc -l)
    SIZE=$(du -sh "$SCRIPT_DIR/Archive" 2>/dev/null | cut -f1)
    echo "Archive files: $COUNT"
    echo "Archive size: $SIZE"
fi
echo ""
echo "=== System Health ==="
curl -s http://localhost:9091/dashboard | python3 -m json.tool 2>/dev/null | head -20 || echo "Dashboard not accessible"
EOF
chmod +x "$INSTALL_DIR/blackbox-status.sh"

# Create manual cleanup script
cat > "$INSTALL_DIR/blackbox-cleanup.sh" <<'EOF'
#!/bin/bash
echo "Running manual archive cleanup..."
curl -X POST "http://localhost:9091/cleanup/archives?days_to_keep=14&max_size_gb=3&max_files=100" \
     -H "Content-Type: application/json" | python3 -m json.tool
EOF
chmod +x "$INSTALL_DIR/blackbox-cleanup.sh"

echo ""
echo "========================================"
echo "Enhanced Service Installation Complete!"
echo "========================================"
echo ""
echo "Available commands:"
echo "  Start service:    sudo systemctl start blackbox.service"
echo "  Stop service:     sudo systemctl stop blackbox.service"
echo "  Restart service:  sudo systemctl restart blackbox.service"
echo "  Check status:     ./blackbox-status.sh"
echo "  View logs:        sudo journalctl -u blackbox.service -f"
echo "  Manual cleanup:   ./blackbox-cleanup.sh"
echo "  Check dashboard:  curl http://localhost:9091/dashboard"
echo ""
echo "The service will:"
echo "  - Auto-restart on crashes (max 5 times in 10 minutes)"
echo "  - Clean up disk space automatically"
echo "  - Monitor system health every 2 minutes"
echo "  - Limit resource usage (4GB RAM, 75% CPU)"
echo "  - Run archive cleanup on startup"
echo ""
echo "To start the service now, run:"
echo "  sudo systemctl start blackbox.service"
echo ""