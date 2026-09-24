#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS="${1:-$HOME/ros2_px4_ws}"
[[ -z "${VIRTUAL_ENV:-}" ]] || { echo 'Deactivate Python venv before colcon/rosidl build' >&2; exit 1; }
[[ -f /opt/ros/humble/setup.bash ]] || { echo 'ROS 2 Humble not installed' >&2; exit 1; }
[[ -d "$WS/src/px4_msgs/.git" ]] || {
  echo "Clone PX4/px4_msgs v1.17.0 into $WS/src/px4_msgs first" >&2; exit 1;
}
PX4_MSGS_HEAD="$(git -C "$WS/src/px4_msgs" rev-parse HEAD)"
PX4_MSGS_TAG="$(git -C "$WS/src/px4_msgs" rev-parse 'v1.17.0^{commit}')"
[[ "$PX4_MSGS_HEAD" == "$PX4_MSGS_TAG" ]] || {
  echo "px4_msgs is not v1.17.0: $PX4_MSGS_HEAD" >&2; exit 1;
}
if [[ ! -e "$WS/src/drone_control_gateway" ]]; then
  ln -s "$ROOT/control/drone_control_gateway" "$WS/src/drone_control_gateway"
elif [[ "$(realpath "$WS/src/drone_control_gateway")" != "$(realpath "$ROOT/control/drone_control_gateway")" ]]; then
  echo 'Existing Gateway source is elsewhere; compare it before building; no overwrite performed.' >&2
  exit 1
fi
source /opt/ros/humble/setup.bash
set -u
cd "$WS"
colcon build --packages-select px4_msgs drone_control_gateway
echo "Build complete. source $WS/install/setup.bash"
