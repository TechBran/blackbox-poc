# UGV Beast x BlackBox Integration Plan

## Phase 1: Get Robot Moving (Current)
1. Flash/verify Jetson Orin Nano software
2. Connect to robot, verify web UI
3. Switch from AP to home WiFi (Tailscale accessible)
4. Start ROS2 Docker, verify all sensors
5. Build first SLAM map
6. Test autonomous navigation

## Phase 2: BlackBox Integration
1. Connect UGV Beast to Tailscale network
2. Create BlackBox ROS2 bridge node
   - Publish robot telemetry to BlackBox (battery, pose, sensor status)
   - Subscribe to BlackBox commands (navigate, scan, return home)
3. Add UGV as a "device" in BlackBox device registry
4. Computer Use agent targets UGV's web UI at :5000
5. Stream camera feed to BlackBox Portal

## Phase 3: Custom ROS2 Nodes
1. **blackbox_bridge node**: Bidirectional comms between BlackBox API and ROS2
   - REST/WebSocket <-> ROS2 topics/services/actions
   - Snapshot robot state periodically
2. **ai_commander node**: BlackBox AI sends high-level goals
   - "Map the living room" -> SLAM + explore_lite
   - "Go to the kitchen" -> Nav2 goal
   - "Patrol route A-B-C" -> waypoint following
3. **sensor_reporter node**: Stream sensor data to BlackBox
   - LiDAR scan visualization
   - Depth camera feed
   - IMU + odometry
   - Battery monitoring with alerts

## Phase 4: Advanced Integration
1. Voice control via BlackBox phone bridge -> ROS2 actions
2. Multi-robot fleet management (ESP-NOW between robots)
3. Shared SLAM maps stored in BlackBox Volume
4. Autonomous security patrol with alerts to Portal
5. AI vision pipeline: detect objects -> report to BlackBox -> take action

## Architecture Vision

```
BlackBox (PC)                          UGV Beast (Jetson)
+------------------+                   +------------------+
| Orchestrator API | <-- Tailscale --> | blackbox_bridge  |
| Portal UI        |     REST/WS      | (ROS2 node)      |
| Phone Bridge     |                   |    |              |
| Computer Use     | --> Web UI :5000  |    v              |
| Snapshots        |                   | ROS2 Topics      |
+------------------+                   | /cmd_vel         |
                                       | /scan            |
                                       | /odom_combined   |
                                       | /camera/image    |
                                       +------------------+
```

## Key Considerations
- Tailscale provides secure tunnel (no port forwarding needed)
- ROS2 runs in Docker -- bridge node should run on host or have network access
- WebRTC video already works -- can proxy through BlackBox
- ESP-NOW for multi-robot is independent of WiFi (100us latency)
- 20 TOPS on Jetson for local inference, BlackBox for orchestration
