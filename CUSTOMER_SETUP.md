# AI BlackBox Flight Recorder - Setup Guide

## Hardware Specifications

Your AI BlackBox system is powered by:
- **CPU**: AMD Ryzen 5 7600 (6-core, 12-thread)
- **RAM**: 32GB DDR5
- **Storage**: Samsung 990 Evo 2TB NVMe SSD
- **OS**: Ubuntu 22.04 LTS or newer

## Initial Setup

### 1. First Boot

1. Connect the mini ITX PC to power and network (ethernet recommended)
2. Connect monitor and keyboard (first-time setup only)
3. System will boot to Ubuntu
4. Default credentials will be provided separately

### 2. Install the AI BlackBox Software

Open a terminal and navigate to the installation directory:

```bash
cd /path/to/blackbox_poc
chmod +x Scripts/install.sh
./Scripts/install.sh
```

The setup script will:
- Install Python 3.11+ if needed
- Create the directory structure
- Set up a Python virtual environment
- Install all dependencies
- Create and enable the systemd service

### 3. Configure API Keys

Edit the `.env` file with your API keys:

```bash
nano .env
```

Add your keys:
```
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-api03-...
GOOGLE_API_KEY=AIzaSy...
```

**Where to get API keys:**
- OpenAI: https://platform.openai.com/api-keys
- Anthropic: https://console.anthropic.com/settings/keys
- Google: https://aistudio.google.com/app/apikey

Save and exit (Ctrl+X, then Y, then Enter).

### 4. Start the Service

```bash
sudo systemctl start blackbox.service
```

Check that it's running:

```bash
sudo systemctl status blackbox.service
```

You should see **"active (running)"** in green.

### 5. Access the Portal

#### Local Access (on the same machine):
```
http://localhost:8000/ui/index.html
```

#### Tailscale Access (recommended):
1. Install Tailscale on the mini ITX:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```

2. Install Tailscale on your devices (laptop, phone, etc.)

3. Access the portal using the Tailscale hostname:
   ```
   http://blackbox-hostname.tailnet-name.ts.net/ui/index.html
   ```

## Daily Use

### Starting/Stopping the Service

**Start:**
```bash
sudo systemctl start blackbox.service
```

**Stop:**
```bash
sudo systemctl stop blackbox.service
```

**Restart:**
```bash
sudo systemctl restart blackbox.service
```

**Auto-start on boot** (already enabled by setup):
```bash
sudo systemctl enable blackbox.service
```

### Viewing Logs

**Real-time logs:**
```bash
sudo journalctl -u blackbox.service -f
```

**Recent logs:**
```bash
sudo journalctl -u blackbox.service -n 100
```

### Checking System Health

Open the portal and click **☰ Menu → Health** to see:
- Volume size
- Active tasks
- Token usage
- System status

## Backups

Your conversation data is stored in:
- `Volumes/` - Active conversation ledger
- `Archive/` - Timestamped backups with SHA-256 hashes
- `Portal/uploads/` - Uploaded media files
- `media_files/` - Generated images/videos

### Manual Backup

Create a backup:

```bash
cd /path/to/blackbox_poc
tar -czf backup_$(date +%Y%m%d_%H%M%S).tar.gz Volumes/ Archive/ Manifest/ Portal/uploads/ media_files/ config.ini
```

### Restore from Backup

```bash
sudo systemctl stop blackbox.service
tar -xzf backup_YYYYMMDD_HHMMSS.tar.gz
sudo systemctl start blackbox.service
```

## Troubleshooting

### Service Won't Start

1. Check the logs:
   ```bash
   sudo journalctl -u blackbox.service --no-pager
   ```

2. Common issues:
   - **Missing API keys**: Edit `.env` and add your keys
   - **Port 8000 in use**: Check with `sudo netstat -tulpn | grep 8000`
   - **Python environment issues**: Re-run `./Scripts/install.sh`

### Portal Not Loading

1. Verify service is running:
   ```bash
   sudo systemctl status blackbox.service
   ```

2. Check Tailscale connection:
   ```bash
   tailscale status
   ```

3. Test local access first:
   ```bash
   curl http://localhost:8000/health
   ```

### Slow Performance

With your hardware specs (7600 + 32GB + NVMe), the system should be very fast. If you notice slowdowns:

1. Check disk space:
   ```bash
   df -h
   ```

2. Check system resources:
   ```bash
   htop
   ```

3. Check volume size:
   ```bash
   ls -lh Volumes/SNAPSHOT_VOLUME.txt
   ```

   If over 50MB, contact support for optimization guidance.

## Updates

Software updates will be provided as needed. To update:

```bash
sudo systemctl stop blackbox.service
cd /path/to/blackbox_poc
# Apply updates (instructions will be provided)
sudo systemctl start blackbox.service
```

## Support

For technical support:
- **Email**: support@yourcompany.com
- **Documentation**: Include error messages and logs when requesting help

## Privacy & Security

- All data stays on your local device
- No data is sent to external servers except AI provider APIs
- Tailscale provides end-to-end encrypted access
- API keys are stored locally in `.env` (never share this file)

---

**System Version**: 7.1.0
**Last Updated**: 2025-01-15
