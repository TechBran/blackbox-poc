#!/bin/bash
# Quick SSH into UGV Beast ROS2 Docker container
# Usage: ./ugv-ssh.sh [command]
# No args = interactive shell | With args = run command and return

JETSON_IP="192.168.1.155"
CONTAINER="ugv_jetson_ros_humble"

if [ -z "$1" ]; then
    # Interactive - SSH into container directly
    sshpass -p 'jetson' ssh -t -o StrictHostKeyChecking=no root@${JETSON_IP} -p 23
else
    # Run a command inside the container
    sshpass -p 'jetson' ssh -o StrictHostKeyChecking=no root@${JETSON_IP} -p 23 "source /root/ros2env.sh && $*"
fi
