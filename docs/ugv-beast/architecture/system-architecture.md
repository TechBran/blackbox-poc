# UGV Beast PT Jetson Orin ROS2 Kit - System Architecture

## System Diagram

```
+------------------------------------------------------------------+
|                    UPPER COMPUTER (Jetson Orin Nano 4GB)          |
|                    20 TOPS AI | Ubuntu 22.04 | 256GB NVMe        |
|                                                                  |
|  +--Docker Container (ROS2 Humble)--------------------------+    |
|  |  ugv_base_node  <--UART/JSON 115200--> ESP32             |    |
|  |  ugv_slam       (GMapping / Cartographer / RTAB-Map)     |    |
|  |  ugv_nav        (Nav2 + AMCL/EMCL + DWA/TEB)            |    |
|  |  ugv_vision     (Object/Face/Gesture detection)          |    |
|  |  ugv_chat_ai    (Ollama LLM -> JSON commands)            |    |
|  |  ugv_web_app    (Vizanti web interface)                  |    |
|  +-----------------------------------------------------------+   |
|                                                                  |
|  Host App: ugv_jetson (Flask on :5000, JupyterLab on :8888)     |
|  USB: OAK-D Lite (depth + RGB, Myriad X 4 TOPS)                |
|  UART: D500 LiDAR (360-deg, 12m range, 5kHz)                   |
|  USB: 5MP PT camera (160-deg ultra-wide)                        |
+------------------------------------------------------------------+
        |  GPIO UART @ 115200 baud (JSON commands)
        v
+------------------------------------------------------------------+
|              LOWER COMPUTER (ESP32-WROOM-32)                     |
|              "General Driver for Robots" Board                   |
|                                                                  |
|  Motor PID control (4x geared DC motors + encoders)              |
|  Pan-tilt servo control (2x ST3215 bus servos, 30kg.cm)          |
|    PAN: +/-180deg (360 continuous) | TILT: -45 to +90deg        |
|  9-axis IMU (accel + gyro + magnetometer + temp)                 |
|  INA219 battery monitor (voltage + current)                      |
|  OLED display (IP, battery, CPU, WiFi mode, uptime)              |
|  LED control (IO4=chassis headlight, IO5=PT headlight)           |
|  WiFi / Bluetooth / ESP-NOW                                      |
+------------------------------------------------------------------+
        |
        v
+------------------------------------------------------------------+
|                    MECHANICAL PLATFORM                            |
|  2mm aluminum alloy tracked chassis, 26mm ground clearance       |
|  4x geared motors + encoders (max 1.3 m/s)                      |
|  Continuous rubber tracks (skid-steer, zero-turn capable)        |
|  Stainless steel independent suspension                          |
|  3x 18650 3S battery UPS (charge while running)                  |
|  Dimensions: 196 x 231 x 286 mm | Weight: ~2.9 kg               |
+------------------------------------------------------------------+
```

## Dual-Controller Communication

The Jetson sends JSON commands to the ESP32 over GPIO UART at 115200 baud.
Each command has a `"T"` field indicating command type.

### Motion Control
```json
{"T":13, "X":0.25, "Z":0.3}
```
- X = linear velocity (m/s), Z = angular velocity (rad/s)
- Speed range: -0.5 to +0.5 m/s per wheel
- **Heartbeat safety**: No command for 3 seconds = auto-stop

### Pan-Tilt Control
```python
gimbal_ctrl(input_x, input_y, input_speed, input_acc)
```
- PAN (X): +/-180 degrees (360 total continuous)
- TILT (Y): -45 to +90 degrees (135 range)
- Speed 0 = max speed
- IMU-assisted stabilization on vertical axis

### LED Control
```json
{"T":...}  // IO4=chassis headlight (0-255), IO5=PT headlight (0-255)
```

### ROS2 LED Control
```bash
ros2 topic pub /ugv/led_ctrl std_msgs/msg/Float32MultiArray "{data: [255, 255]}" -1
```

## Sensor Specifications

### D500 LiDAR
- Type: DTOF (Direct Time of Flight)
- Range: 0.03m - 12m
- Frequency: 5000 Hz measurement, 6-13 Hz rotation
- Accuracy: +/-30mm
- Coverage: 360 degrees
- Interface: UART

### OAK-D Lite Depth Camera
- RGB: 13MP IMX214, 4K/30fps or 1080p/60fps
- Stereo: Dual OV7251 monochrome cameras
- Depth range: 0.4m - 8m
- VPU: Intel Movidius Myriad X (4 TOPS)
- Encoding: H.264/H.265/MJPEG
- Interface: USB 3.0 Type-C
- Power: up to 5W

### Pan-Tilt Camera
- Resolution: 5MP
- FOV: 160 degrees ultra-wide
- Servos: 2x ST3215 (30 kg.cm torque each)
- Streaming: WebRTC real-time

### IMU (on ESP32 Driver Board)
- 9-axis: 3-axis accel + 3-axis gyro + 3-axis magnetometer
- Temperature sensing included
- Published to ROS2 as `/imu` topic

### Battery Monitor (INA219)
- Measures: voltage + current
- Protocol: I2C
- Displayed on OLED and web UI

### Wheel Encoders
- Integrated with geared DC motors
- PID closed-loop speed control on ESP32
- ROS2 topics: `/motor/lvel`, `/motor/rvel` (actual), `/motor/lset`, `/motor/rset` (setpoint)

## Key ROS2 Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/cmd_vel` | geometry_msgs/Twist | Velocity commands |
| `/odom_combined` | nav_msgs/Odometry | Fused odometry (encoder + IMU via EKF) |
| `/imu` | sensor_msgs/Imu | Raw IMU data |
| `/scan` | sensor_msgs/LaserScan | LiDAR scan data |
| `/motor/lvel`, `/motor/rvel` | Float32 | Actual motor velocities |
| `/ugv/led_ctrl` | Float32MultiArray | LED brightness [IO4, IO5] |
| `/robot_pose` | geometry_msgs/PoseStamped | Current robot pose |

## TF Tree

```
odom -> base_footprint  (from robot_pose_ekf, encoder+IMU fusion)
  base_footprint -> base_imu_link  (static)
  base_footprint -> base_scan      (static, LiDAR frame)
  base_footprint -> pt_base_link   (pan-tilt base)
    pt_base_link -> pt_link1       (pan servo)
      pt_link1 -> pt_link2         (tilt servo)
        pt_link2 -> camera_link    (camera frame)
```

## Network Configuration

| Mode | SSID | Password | Default IP |
|------|------|----------|------------|
| AP (default) | AccessPopup | 1234567890 | 192.168.50.5 |
| STA | User's WiFi | User's password | DHCP assigned |
| USB | N/A | N/A | 192.168.55.1 |

### OLED Display Lines
1. **E:** Ethernet IP
2. **W:** WiFi IP (192.168.50.5 in AP, or DHCP in STA)
3. **F/J:** Port 5000 (web UI) / Port 8888 (JupyterLab)
4. AP/STA mode, uptime, RSSI signal strength

## Web Ports

| Port | Service |
|------|---------|
| 5000 | Main web control UI (Flask) |
| 5100 | Vizanti (ROS2 web tool) |
| 8888 | JupyterLab tutorials |
| 23 | Docker SSH (ROS2 container) |
| 22 | Host SSH |
