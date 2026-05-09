# UGV Beast Software Installation - From Blank JetPack

## Complete Install Order (After JetPack is Flashed)

| Step | What | Time |
|------|------|------|
| 1 | `git clone ugv_jetson` + `sudo ./setup.sh` | 30-40 min |
| 2 | JupyterLab + PulseAudio + Jtop services | 5 min |
| 3 | `./autorun.sh` (crontab auto-start) | 2 min |
| 4 | AccessPopup WiFi hotspot install | 2 min |
| 5 | RTL8822CE full driver (lwfinger/rtw88) | 10 min |
| 6 | Set robot type: `s 32` (Beast + Camera PT) | 1 min |
| 7 | Reboot + verify web UI at :5000 | 2 min |
| 8 | Install Docker + build ROS2 container | 1-3 hours |
| 9 | Clone + build ugv_ws inside Docker | 30-60 min |
| **Total** | | **~3-5 hours** |

## GitHub Repositories Needed

```bash
# On the Jetson host:
git clone https://github.com/waveshareteam/ugv_jetson.git ~/ugv_jetson
git clone https://github.com/waveshareteam/ugv_ws.git ~/ugv_ws

# Inside Docker container:
git clone -b ros2-humble-develop https://github.com/waveshareteam/ugv_ws.git /home/ws/ugv_ws
```

## ESP32 Firmware

The ESP32 on the General Driver Board comes **PRE-FLASHED from the factory**.
You do NOT need to flash it.

- Firmware version shown on OLED at boot
- If update needed: Windows-only flash tool from Waveshare wiki (ESP32 flash_download_tool)
- Connect via USB-C on the center of the driver board
- Repos (for custom development only):
  - https://github.com/waveshareteam/ugv_base_general (General Driver)
  - https://github.com/waveshareteam/ugv_base_ros (ROS Driver variant)

## Serial Port Configuration

The Jetson talks to ESP32 via GPIO UART:
- **Orin Nano**: `/dev/ttyTHS0` at 115200 baud
- **Orin NX**: `/dev/ttyTHS1` at 115200 baud

setup.sh handles this, but manually:
```bash
sudo systemctl stop nvgetty       # Stop serial console
sudo systemctl disable nvgetty    # Disable on boot
sudo udevadm trigger              # Refresh device rules
sudo usermod -aG dialout $USER    # Grant serial access
```

## Boot-Time Commands to ESP32

When app.py starts, it sends these JSON commands over UART:
```python
{"T":142,"cmd":50}     # Set feedback interval
{"T":131,"cmd":1}      # Enable continuous feedback
{"T":143,"cmd":0}      # Disable echo
{"T":4,"cmd":2}        # Set module type (2=Camera PT)
{"T":300,"mode":0,"mac":"EF:EF:EF:EF:EF:EF"}  # Lock ESP-NOW
```

## config.yaml Robot Types

| Code | main_type | module_type | Model |
|------|-----------|-------------|-------|
| s 10 | 1 | 0 | RaspRover, no module |
| s 20 | 2 | 0 | UGV Rover, no module |
| s 30 | 3 | 0 | UGV Beast, no module |
| s 32 | 3 | 2 | UGV Beast + Camera PT |

## Critical Gotchas

1. **Flask app and ROS2 cannot coexist** -- both need /dev/ttyTHS0 and the camera
2. **Serial boot hang** -- ESP32 UART traffic can prevent Jetson reboot; power-cycle instead
3. **No public Docker image** -- Waveshare's ROS2 container only ships in their pre-built image
4. **RTL8822CE needs lwfinger/rtw88** for AP mode (AccessPopup won't work with stock driver)
5. **`sudo tar xpf`** -- always use `p` flag when extracting rootfs to preserve permissions
6. **Don't use Ubuntu 24.04** as flash host -- SDK Manager requires 22.04
