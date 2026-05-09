#!/bin/bash
# AI BlackBox Flight Recorder - Ubuntu Setup Script
# For mini ITX deployment with AM5 7600, 32GB DDR5, Samsung 990 Evo NVMe

set -e  # Exit on any error

echo "=========================================="
echo "AI BlackBox Flight Recorder Setup"
echo "=========================================="
echo ""

# Check Ubuntu version
if ! grep -q "Ubuntu" /etc/os-release 2>/dev/null; then
    echo "❌ Error: This script requires Ubuntu"
    exit 1
fi

echo "✅ Ubuntu detected"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
   echo "⚠️  Please do not run as root. Run as the user who will run the service."
   exit 1
fi

# Get current directory
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "📁 Install directory: $INSTALL_DIR"
echo ""

# Install Python 3.11+ if not present
echo "📦 Checking Python installation..."
if ! command -v python3.11 &> /dev/null && ! command -v python3.12 &> /dev/null; then
    echo "Installing Python 3.11..."
    sudo apt update
    sudo apt install -y python3.11 python3.11-venv python3-pip
else
    echo "✅ Python 3.11+ is installed"
fi
echo ""

# Determine Python command
if command -v python3.11 &> /dev/null; then
    PYTHON_CMD=python3.11
elif command -v python3.12 &> /dev/null; then
    PYTHON_CMD=python3.12
else
    PYTHON_CMD=python3
fi

echo "🐍 Using Python: $PYTHON_CMD"
echo ""

# Create directory structure
echo "📂 Creating directory structure..."
mkdir -p Volumes Archive Manifest Portal/uploads media_files
echo "✅ Directories created"
echo ""

# Create virtual environment
echo "🔧 Creating virtual environment..."
if [ -d "Orchestrator/venv" ]; then
    echo "   Virtual environment already exists, skipping..."
else
    $PYTHON_CMD -m venv Orchestrator/venv
    echo "✅ Virtual environment created"
fi
echo ""

# Activate virtual environment and install dependencies
echo "📦 Installing Python dependencies..."
source Orchestrator/venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo "✅ Dependencies installed from requirements.txt"
else
    echo "⚠️  Warning: requirements.txt not found, installing base dependencies..."
    pip install fastapi==0.118.0 uvicorn[standard]==0.30.0 pydantic==2.9.0 \
                python-dotenv==1.0.1 python-multipart==0.0.9 requests==2.32.3
    echo "✅ Base dependencies installed"
fi
deactivate
echo ""

# Check for .env file
if [ ! -f ".env" ]; then
    if [ -f ".env.template" ]; then
        echo "📝 Copying .env.template to .env..."
        cp .env.template .env
        echo "⚠️  IMPORTANT: Edit .env with your API keys!"
        echo "   Run: nano .env"
    else
        echo "⚠️  Warning: No .env file found. You'll need to create one with:"
        echo "   OPENAI_API_KEY=your_key_here"
        echo "   ANTHROPIC_API_KEY=your_key_here"
        echo "   GOOGLE_API_KEY=your_key_here"
    fi
else
    echo "✅ .env file exists"
fi
echo ""

# Create systemd service
echo "🔧 Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/blackbox.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=AI BlackBox Flight Recorder
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/Orchestrator/venv/bin/python -m uvicorn Orchestrator.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable blackbox.service
echo "✅ Systemd service created and enabled"
echo ""

echo "=========================================="
echo "✅ Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Edit API keys: nano .env"
echo "2. Start service:  sudo systemctl start blackbox.service"
echo "3. Check status:   sudo systemctl status blackbox.service"
echo "4. View logs:      sudo journalctl -u blackbox.service -f"
echo ""
echo "Access the portal at:"
echo "  http://localhost:8000/ui/index.html"
echo "  OR via Tailscale hostname"
echo ""
