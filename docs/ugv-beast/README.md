# UGV Beast PT Jetson Orin ROS2 Kit - Documentation

Local documentation for the Waveshare UGV Beast PT, saved for offline reference.

## Quick Links

- [Tool Schema API](setup/ugv_tools_api/README.md) - LLM-callable HTTP tool server (port 8080) for Claude/GPT/Gemini control

### Setup
- [Getting Started](setup/getting-started.md) - First boot, WiFi config, sensor testing
- [Jetson Flashing](setup/jetson-flashing.md) - Flash/reinstall the Jetson Orin Nano

### Architecture
- [System Architecture](architecture/system-architecture.md) - Hardware diagram, sensors, protocols
- [ROS2 Packages](architecture/ros2-packages.md) - All packages, topics, launch commands

### Reference
- [Wiki Reference](wiki/full-wiki-reference.md) - All wiki pages, downloads, repos, tutorials
- [BlackBox Integration Plan](blackbox-integration-plan.md) - Future BlackBox integration roadmap

## Quick Start

```bash
# 1. Power on, connect to AccessPopup WiFi (pass: 1234567890)
# 2. Open http://192.168.50.5:5000 in browser
# 3. Test movement via web UI

# For ROS2:
ssh jetson@192.168.50.5        # password: jetson
top && kill -9 <python_PID>    # stop auto-start app
cd /home/jetson/ugv_ws
./ros2_humble.sh               # enter 1 for Docker SSH
ssh root@192.168.50.5 -p 23    # password: jetson (Docker)
ros2 launch ugv_bringup bringup_lidar.launch.py use_rviz:=true
```

## GitHub Repos

| Repo | Purpose |
|------|---------|
| [ugv_jetson](https://github.com/waveshareteam/ugv_jetson) | Jetson host app |
| [ugv_ws](https://github.com/waveshareteam/ugv_ws) | ROS2 workspace |
| [ugv_base_general](https://github.com/waveshareteam/ugv_base_general) | ESP32 firmware |
| [ugv_base_ros](https://github.com/waveshareteam/ugv_base_ros) | ESP32 ROS firmware |
