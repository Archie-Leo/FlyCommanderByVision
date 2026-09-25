#!/usr/bin/env bash
# Source this file from the RK3576 repository checkout.
_fcv_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_ROOT="$(cd "${_fcv_env_dir}/.." && pwd)"
export FCV_ROS_WS="${FCV_ROS_WS:-$HOME/fcv_ros_ws}"
export FCV_CAMERA_BACKEND="${FCV_CAMERA_BACKEND:-ffmpeg}"
export FCV_CAMERA_DEVICE="${FCV_CAMERA_DEVICE:-/dev/video73}"
export FCV_CALIBRATION_PATH="${FCV_CALIBRATION_PATH:-${REPO_ROOT}/configs/calibration/run_b.yaml}"
export FCV_REID_BACKEND="${FCV_REID_BACKEND:-rknn}"
export FCV_TORCHREID_ROOT="${FCV_TORCHREID_ROOT:-$HOME/fcv_third_party/deep-person-reid}"

if [[ ! -f /opt/ros/jazzy/setup.bash ]]; then
  echo 'ROS 2 Jazzy setup missing' >&2
  return 1
fi
if [[ ! -f "${FCV_ROS_WS}/install/setup.bash" ]]; then
  echo "ROS workspace setup missing: ${FCV_ROS_WS}/install/setup.bash" >&2
  return 1
fi

# A virtual environment is fine for vision runtime. Deactivate it before
# colcon/rosidl builds and verify that python3 resolves to /usr/bin/python3.
source /opt/ros/jazzy/setup.bash
source "${FCV_ROS_WS}/install/setup.bash"
export PYTHONPATH="${REPO_ROOT}/stage3_pose:${REPO_ROOT}/stage4_gesture:${REPO_ROOT}/stage5_operator:${REPO_ROOT}/stage6_closed_loop${PYTHONPATH:+:${PYTHONPATH}}"
unset _fcv_env_dir
