#!/usr/bin/env bash
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env_rk3576.sh"

status=0
printf 'architecture: %s\n' "$(uname -m)"
[[ "$(uname -m)" == aarch64 ]] || status=1
printf 'python3: %s (%s)\n' "$(command -v python3)" "$(python3 --version 2>&1)"
printf 'ROS_DISTRO: %s\n' "${ROS_DISTRO:-unset}"
[[ "${ROS_DISTRO:-}" == jazzy ]] || status=1
printf 'ROS workspace: %s\n' "$FCV_ROS_WS"
if ros2 interface show px4_msgs/msg/VehicleStatus >/dev/null 2>&1; then
  echo 'px4_msgs VehicleStatus: PASS'
else
  echo 'px4_msgs VehicleStatus: FAIL'
  status=1
fi
if ros2 pkg prefix drone_control_gateway >/dev/null 2>&1; then
  echo 'drone_control_gateway: PASS'
else
  echo 'drone_control_gateway: MISSING'
  status=1
fi
if command -v MicroXRCEAgent >/dev/null; then
  printf 'MicroXRCEAgent: %s\n' "$(command -v MicroXRCEAgent)"
else
  echo 'MicroXRCEAgent: MISSING'
  status=1
fi
printf 'camera: %s\n' "$(test -e "${FCV_CAMERA_DEVICE:-/dev/video73}" && echo PRESENT || echo MISSING)"
printf 'RKNN runtime: %s\n' "$(test -r /usr/lib/librknnrt.so && echo PRESENT || echo MISSING)"
[[ -e "${FCV_CAMERA_DEVICE:-/dev/video73}" ]] || status=1
[[ -r /usr/lib/librknnrt.so ]] || status=1
if [[ -x "$HOME/venvs/fcv/bin/python3" ]]; then
  "$HOME/venvs/fcv/bin/python3" -c 'import cv2, numpy, rknnlite; print("vision Python: NumPy", numpy.__version__, "OpenCV", cv2.__version__)'
else
  echo 'vision Python: MISSING'
  status=1
fi
exit "$status"
