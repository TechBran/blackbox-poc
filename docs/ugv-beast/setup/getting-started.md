# UGV Beast PT - Getting Started Guide

## Prerequisites

- UGV Beast PT Jetson Orin ROS2 Kit (assembled)
- 3x 18650 batteries (2200mAh+, 4C discharge rate)
- 12V 5A power adapter (included)
- WiFi-capable computer with browser
- SSH client (MobaXterm recommended on Windows)

## Step 1: Battery Installation

**CRITICAL SAFETY CHECK**: Insert 3x 18650 batteries into the 3S UPS module.
- If the LED on the battery module illuminates immediately = **REVERSED POLARITY**
- Do NOT charge if reversed -- explosion risk
- Remove and reinsert correctly

## Step 2: First Power On

1. Connect 12V 5A power cable (required for first boot)
2. Power on via switch
3. Wait for OLED to display boot info (~30-60 seconds)
4. OLED shows:
   - **E:** Ethernet IP
   - **W:** WiFi IP (default: 192.168.50.5 in AP mode)
   - **F/J:** 5000/8888 (web UI / JupyterLab)
   - Mode: AP, uptime, signal

## Step 3: Connect to Robot

### Option A: WiFi (AP Mode - Default)
1. Connect to WiFi: **AccessPopup** (password: `1234567890`)
2. Open browser: `http://192.168.50.5:5000`
3. JupyterLab: `http://192.168.50.5:8888`

### Option B: USB
1. Connect Micro USB from Jetson to your PC
2. SSH: `ssh jetson@192.168.55.1` (password: `jetson`)

### Option C: Ethernet
1. Connect Ethernet cable
2. Check OLED for assigned IP (E: line)

## Step 4: Switch to Home WiFi (Recommended)

The AP mode is useful for initial setup but you'll want the robot on your home network:

```bash
# SSH into the Jetson first
ssh jetson@192.168.50.5  # password: jetson

# Disable AP mode
cd ugv_jetson/AccessPopup/
sudo chmod +x installconfig.sh
sudo ./installconfig.sh
# Enter 7 to uninstall AP mode

# Connect to your WiFi
sudo nmcli r wifi on
sudo nmcli d wifi list
sudo nmcli d wifi connect "YourSSID" password "YourPassword"

# New IP will show on OLED after reboot
sudo reboot
```

### Switch Back to AP Mode (if needed)
```bash
sudo accesspopup -a    # Switch to AP mode
sudo accesspopup       # Switch back to STA mode
```

## Step 5: Verify Web UI

1. Open browser to `http://<jetson-ip>:5000`
2. You should see the Waveshare web control interface
3. Test video feed (WebRTC)
4. Test basic movement controls

## Step 6: Enter ROS2 Docker Container

The main program auto-starts on boot and occupies the serial port + camera.
You must stop it before using ROS2 directly.

### 6a. Stop the auto-start program
```bash
ssh jetson@<jetson-ip>  # password: jetson
top                      # Find the python PID
kill -9 <PID>            # Kill it
```

### 6b. (Optional) Disable auto-start permanently
```bash
crontab -e
# Comment out this line with #:
# @reboot sleep 3 && whoami && pulseaudio --start && sleep 2 && XDG_RUNTIME_DIR=/run/user/1000 ~/ugv_jetson/ugv-env/bin/python ~/ugv_jetson/app.py >> ~/ugv.log 2>&1
sudo reboot
```

### 6c. Start Docker SSH service
```bash
cd /home/jetson/ugv_ws
sudo chmod +x ros2_humble.sh remotessh.sh
./ros2_humble.sh
# Enter 1 to start Docker container SSH service
```

### 6d. SSH into Docker container
```bash
ssh root@<jetson-ip> -p 23  # password: jetson
```
- Or use MobaXterm: connect to Jetson IP, **Port 23**, user `root`, password `jetson`

## Step 7: Test ROS2 Basics

Inside the Docker container:

### View robot model in RViz
```bash
cd /home/ws/ugv_ws
ros2 launch ugv_description display.launch.py use_rviz:=true
```

### Start driver + LiDAR
```bash
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=true
```

### Test keyboard control
```bash
# In a new terminal (new SSH to port 23):
ros2 run ugv_tools keyboard_ctrl
```

Keyboard layout:
| U | I | O |
|---|---|---|
| Left-fwd | Forward | Right-fwd |
| **J** | **K** | **L** |
| Turn left | **STOP** | Turn right |
| **M** | **,** | **.** |
| Left-back | Backward | Right-back |

### Test joystick
```bash
# Verify gamepad detected:
ls /dev/input/js0
# Launch joystick teleop:
ros2 launch ugv_tools teleop_twist_joy.launch.py
```

## Step 8: Test Sensors

### LiDAR scan
```bash
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=true
# In RViz: Add -> By topic -> /scan -> LaserScan
# You should see 360-degree scan data
```

### OAK-D Lite depth camera
```bash
ros2 launch ugv_vision oak_d_lite.launch.py
# In RViz: Add -> By topic -> /oak/rgb/image_rect
# And: Add -> By topic -> /oak/stereo/image_rect (depth)
```

### IMU data
```bash
ros2 topic echo /imu
```

### Motor encoders / odometry
```bash
ros2 topic echo /odom_combined
```

### LED test
```bash
# Turn on both LEDs (max brightness)
ros2 topic pub /ugv/led_ctrl std_msgs/msg/Float32MultiArray "{data: [255, 255]}" -1
# Turn off
ros2 topic pub /ugv/led_ctrl std_msgs/msg/Float32MultiArray "{data: [0, 0]}" -1
```

## Step 9: First SLAM Map

```bash
# Terminal 1: Start driver + SLAM
ros2 launch ugv_slam gmapping.launch.py use_rviz:=true

# Terminal 2: Keyboard control to drive around
ros2 run ugv_tools keyboard_ctrl

# Drive slowly around the room, watching the map build in RViz
# When satisfied, in Terminal 3:
cd /home/ws/ugv_ws
chmod +x ./save_2d_gmapping_map.sh
./save_2d_gmapping_map.sh
```

## Step 10: First Navigation

```bash
# Terminal 1: Start navigation with saved map
ros2 launch ugv_nav nav.launch.py use_localization:=amcl use_rviz:=true

# In RViz:
# 1. Click "2D Pose Estimate" -> place on map where robot is
# 2. Click "Nav2 Goal" -> click destination on map
# Robot navigates autonomously!
```

## Default Credentials

| System | Username | Password |
|--------|----------|----------|
| Jetson SSH (host) | jetson | jetson |
| Docker SSH (port 23) | root | jetson |
| WiFi AP | N/A | 1234567890 |
| USB IP | 192.168.55.1 | N/A |

## Troubleshooting

### Serial port conflict
The auto-start main program holds the UART port. Kill it before ROS2:
```bash
top  # Find python PID
kill -9 <PID>
```

### Camera not available
Same auto-start program holds the camera. Stop it first.

### Docker container not starting
```bash
cd /home/jetson/ugv_ws
sudo chmod +x ros2_humble.sh
./ros2_humble.sh
# Enter 1
```

### Can't see RViz (headless)
RViz requires X11 forwarding. Use MobaXterm (has built-in X server) or set up VNC.

### Robot not moving
1. Check battery level on OLED
2. Verify serial connection: `ls /dev/ttyTHS*` in host
3. Check if main program is still running (kill it)
4. Verify ugv_base_node is running: `ros2 node list`

### WiFi issues after switching to STA
```bash
# Check connection status
nmcli d show
# Reconnect
sudo nmcli d wifi connect "YourSSID" password "YourPassword"
# Or switch back to AP
sudo accesspopup -a
```
