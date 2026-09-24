#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
source "${DRONE_ROS_WS:-$HOME/ros2_px4_ws}/install/setup.bash"
set -u
exec ros2 launch drone_control_gateway gateway.launch.py
