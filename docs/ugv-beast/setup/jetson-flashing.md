# Jetson Orin Nano 8GB - Flashing Guide (From Blank NVMe)

## Your Setup
- **Jetson**: Orin Nano 8GB Developer Kit (P3767-0005 module, P3768 carrier)
- **Storage**: Samsung 990 Pro NVMe (M.2 2280, PCIe Gen 4 -- will run at Gen 3 on Orin)
- **WiFi**: RTL8822CE M.2 WiFi/BT module (from Waveshare kit)
- **State**: Blank NVMe, no OS installed

## What You Need
- Ubuntu 22.04 host PC (physical install or bootable USB -- NOT a VM, USB passthrough is unreliable)
- USB-C data cable (for flashing)
- 19V barrel jack power supply (more reliable than USB-C PD during flash)
- NVIDIA Developer account (free: https://developer.nvidia.com)
- ~60GB free disk space on host PC

## Quick Decision: Two Paths

### Path A: Waveshare Pre-Built Image (Faster, ~30 min)
- Download their image, flash it, change robot type to Beast
- **Pro**: Everything pre-configured (Docker, ROS2 container, AccessPopup, crontab)
- **Con**: Based on AI Kit (not full ROS2 kit), may need manual ROS2 Docker setup anyway
- **Image**: https://files.waveshare.com/wiki/UGV-Rover-PT-Jetson-Orin-AI-kit/jetson-image.zip

### Path B: Fresh JetPack + Manual Install (Cleaner, ~3-5 hours)
- Flash JetPack 6.1/6.2, install ugv_jetson + ugv_ws from GitHub
- **Pro**: Clean system, latest JetPack, full control
- **Con**: ROS2 Docker container must be built from scratch (Waveshare doesn't publish Dockerfile)

**Recommendation**: Try Path A first. If the image works with your 8GB Orin Nano + 990 Pro, you save hours. If not, fall back to Path B.

---

## PATH A: Waveshare Pre-Built Image

### Step 1: Download Image
```
https://files.waveshare.com/wiki/UGV-Rover-PT-Jetson-Orin-AI-kit/jetson-image.zip
```

### Step 2: Enter Recovery Mode
1. Power OFF the Jetson completely (remove power)
2. Ensure Samsung 990 Pro is seated in the M.2 Key M slot
3. Install RTL8822CE in the M.2 Key E slot (WiFi/BT slot)
4. Connect USB-C from Jetson to Ubuntu host PC
   - **Use the correct USB-C port**: the one near the 40-pin GPIO header (NOT near Ethernet)
5. Hold **Force Recovery (FC REC)** button
6. While holding FC REC, apply power (plug in barrel jack)
7. Hold FC REC for 2 more seconds, then release
8. Verify on host:
```bash
lsusb | grep -i nvidia
# Should show: 0955:7523 NVIDIA Corp. APX
```

### Step 3: Flash the Image
The exact flash method depends on how the image is packaged. If it's a raw rootfs image:
```bash
cd ~/jetson-flash/Linux_for_Tegra
# Extract the Waveshare image and place rootfs
# Then flash using initrd method (see Path B Step 6)
```

If Waveshare provides their own flash script, follow their instructions in the zip.

### Step 4: Switch to Beast Model
After first boot:
1. Connect to AccessPopup WiFi (password: `1234567890`)
2. Open `http://192.168.50.5:5000`
3. In the command input, type: `s 32` (Beast + Camera PT)
4. Reboot

---

## PATH B: Fresh JetPack (Command Line Method)

### Step 1: Download BSP and Rootfs on Host PC

Go to https://developer.nvidia.com/embedded/jetson-linux and download for JetPack 6.1 (L4T R36.4.x):

```bash
mkdir -p ~/jetson-flash && cd ~/jetson-flash

# Download L4T Driver Package (BSP) - ~1.2GB
wget <BSP_URL>/Jetson_Linux_R36.4.0_aarch64.tbz2

# Download Sample Root Filesystem - ~1.5GB
wget <BSP_URL>/Tegra_Linux_Sample-Root-Filesystem_R36.4.0_aarch64.tbz2
```

### Step 2: Extract and Prepare

```bash
cd ~/jetson-flash

# Extract BSP
sudo tar xpf Jetson_Linux_R36.4.0_aarch64.tbz2

# Extract rootfs into BSP's rootfs directory
cd Linux_for_Tegra/rootfs/
sudo tar xpf ../../Tegra_Linux_Sample-Root-Filesystem_R36.4.0_aarch64.tbz2

# Back to Linux_for_Tegra
cd ..

# Apply NVIDIA binary overlay (drivers, firmware)
sudo ./apply_binaries.sh
```

### Step 3: Install Host Dependencies

```bash
sudo apt update
sudo apt install -y \
    python3 python3-yaml abootimg sshpass \
    nfs-kernel-server qemu-user-static binutils \
    device-tree-compiler dosfstools lz4 \
    openssl uuid-runtime whois
```

### Step 4: Create Default User (Skip First-Boot Wizard)

```bash
cd ~/jetson-flash/Linux_for_Tegra

sudo ./tools/l4t_create_default_user.sh \
    -u jetson \
    -p jetson \
    --accept-license
```

### Step 5: Enter Recovery Mode

Same as Path A Step 2. Verify with:
```bash
lsusb | grep -i nvidia
# Must show: 0955:7523 NVIDIA Corp. APX
```

### Step 6: Flash to NVMe

```bash
cd ~/jetson-flash/Linux_for_Tegra

sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
    --external-device nvme0n1p1 \
    -c tools/kernel_flash/flash_l4t_t234_nvme.xml \
    -p "-c bootloader/generic/cfg/flash_t234_qspi.xml" \
    --showlogs \
    --network usb0 \
    jetson-orin-nano-devkit \
    internal
```

**What this does:**
1. Flashes QSPI bootloader to on-module flash (~2 min)
2. Boots minimal Linux via USB into Jetson RAM (~1 min)
3. Partitions and writes rootfs to Samsung 990 Pro NVMe (~10-20 min)
4. Reboots from NVMe

### Step 7: Verify Boot

```bash
# SSH via USB (if no WiFi yet)
ssh jetson@192.168.55.1  # password: jetson

# Verify NVMe boot
df -h /
# Should show /dev/nvme0n1p1

# Verify GPU
sudo tegrastats

# Check JetPack
cat /etc/nv_tegra_release
```

---

## POST-FLASH: Install UGV Beast Software

### Phase 1: Install ugv_jetson (Main Web App)

```bash
ssh jetson@<ip>  # password: jetson

# Clone host application
git clone https://github.com/waveshareteam/ugv_jetson.git
cd ugv_jetson/

# Grant permissions
sudo chmod +x setup.sh autorun.sh

# Restore full Ubuntu (minimal by default)
sudo unminimize

# Run setup (~20-40 min)
sudo ./setup.sh
```

**What setup.sh installs:**
- System packages: libopenblas, opencv, portaudio, espeak, hostapd, dnsmasq, iptables
- Disables serial console (nvgetty) -- frees /dev/ttyTHS0 for ESP32 UART
- JupyterLab + Node.js
- jetson-stats (jtop)
- Python venv with: pyserial, flask, flask_socketio, aiortc, opencv-python, mediapipe, etc.
- Adds user to `dialout` group (serial port access)

### Phase 2: JupyterLab + PulseAudio Services

```bash
cd ~/ugv_jetson/

# JupyterLab service
python3 create_jupyter_service.py
sudo mv ugv_jupyter.service /etc/systemd/system/
sudo systemctl enable ugv_jupyter
sudo systemctl start ugv_jupyter

# PulseAudio system service
sudo cp pulseaudio.service /etc/systemd/system/
sudo systemctl --system enable --now pulseaudio.service

# Jtop service
sudo systemctl enable jtop.service
```

### Phase 3: Autorun + AccessPopup

```bash
cd ~/ugv_jetson/

# Set up crontab auto-start (do NOT use sudo)
./autorun.sh

# Install WiFi hotspot manager
cd AccessPopup/
sudo chmod +x installconfig.sh
sudo ./installconfig.sh
# Enter 1 to install, then 9 to exit
```

### Phase 4: RTL8822CE WiFi Driver (for AP Mode)

The stock JetPack driver works for basic WiFi, but AccessPopup needs AP mode. Build the full driver:

```bash
# On the Jetson
sudo apt install -y build-essential git linux-headers-$(uname -r)
git clone https://github.com/lwfinger/rtw88
cd rtw88
make
sudo make install

# Blacklist the stock driver and load the new one
echo "blacklist rtl8822ce" | sudo tee /etc/modprobe.d/blacklist-rtl8822ce.conf
sudo modprobe rtw_8822ce

# Verify
dmesg | grep rtw
lsmod | grep rtw
iwconfig  # Should show wlan0
```

### Phase 5: Set Robot Type

```bash
cd ~/ugv_jetson/
# Edit config.yaml directly:
# main_type: 3  (UGV Beast)
# module_type: 2  (Camera PT)
```

Or after reboot, open `http://<ip>:5000` and type `s 32` in the command input.

### Phase 6: Reboot

```bash
sudo reboot
```

**WARNING**: The ESP32 sends continuous UART data that can hang the Jetson during reboot.
If it doesn't come back up, power-cycle (flip the switch off and on).

### Phase 7: Install Docker + ROS2 (Most Complex Step)

```bash
# Docker should come with JetPack, but verify:
sudo apt install -y docker.io docker-compose
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
# Log out and back in for group to take effect

# Clone ROS2 workspace
cd ~
git clone https://github.com/waveshareteam/ugv_ws.git
cd ugv_ws
sudo chmod +x ros2_humble.sh remotessh.sh
```

**The hard part**: Waveshare doesn't publish the Docker container image or Dockerfile.
You need to build a ROS2 Humble Docker container from scratch:

```bash
# Pull ROS2 Humble base image for arm64
docker pull ros:humble

# Create container with device access
docker run -it \
    --name ugv_jetson_ros_humble \
    --net=host \
    --privileged \
    -v /dev:/dev \
    -v /home/jetson/ugv_ws:/home/ws/ugv_ws \
    ros:humble /bin/bash

# Inside the container:
apt update && apt install -y \
    ros-humble-cartographer ros-humble-cartographer-ros \
    ros-humble-desktop \
    ros-humble-joint-state-publisher ros-humble-joint-state-publisher-gui \
    ros-humble-nav2-bringup ros-humble-nav2-* \
    ros-humble-rosbridge-server ros-humble-rosbridge-suite \
    ros-humble-rqt ros-humble-rqt-* \
    ros-humble-rtabmap ros-humble-rtabmap-ros \
    ros-humble-usb-cam \
    ros-humble-depthai-ros \
    openssh-server python3-pip

pip3 install pyserial flask mediapipe requests

# Configure SSH on port 23
echo "Port 23" >> /etc/ssh/sshd_config
echo "PermitRootLogin yes" >> /etc/ssh/sshd_config
echo "root:jetson" | chpasswd

# Build workspace
cd /home/ws/ugv_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install

# Add to bashrc
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
echo "source /home/ws/ugv_ws/install/setup.bash" >> ~/.bashrc

# Exit container
exit

# Commit the container state
docker commit ugv_jetson_ros_humble ugv_jetson_ros_humble:latest
```

### Phase 8: Test ROS2

```bash
# Start Docker SSH
cd ~/ugv_ws
./ros2_humble.sh  # Enter 1

# SSH into Docker
ssh root@localhost -p 23  # password: jetson

# Kill the main Flask app first (it holds the serial port)
# On the HOST (not Docker):
kill -9 $(pgrep -f "app.py")

# Back in Docker:
cd /home/ws/ugv_ws
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=true
```

---

## Troubleshooting

### NVMe not detected during flash
- Reseat Samsung 990 Pro in M.2 Key M slot
- Check mounting screw is secure
- Verify the slot supports 2280 form factor (it does on P3768)

### RTL8822CE not showing in lspci
- This is a PCIe link-up issue, not a driver problem
- Reseat the module in M.2 Key E slot
- Check antenna connections (IPEX4 connectors)
- Some carrier board revisions have GPIO/device-tree issues -- check NVIDIA forums

### Samsung 990 Pro random disconnects
- Disable aggressive power management:
```bash
sudo nvme set-feature /dev/nvme0 -f 0x0c -v 0
```

### Serial boot hang after reboot
- ESP32 UART traffic can confuse Jetson boot
- Power-cycle (switch off, wait 3 sec, switch on) instead of soft reboot

### Wrong USB-C port for flashing
- The Orin Nano Dev Kit has TWO USB-C ports
- Use the one near the 40-pin GPIO header (NOT near Ethernet)
- If `lsusb` shows nothing, try the other port

### Can't enter recovery mode
- Must hold FC REC BEFORE applying power
- Hold FC REC, plug in power, wait 2 sec, release FC REC
- Some boards use pin headers instead of buttons -- short FC REC to GND
