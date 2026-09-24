#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---dry-run}"
[[ "$MODE" == --dry-run || "$MODE" == --live ]] || { echo 'Use --dry-run or --live' >&2; exit 2; }
[[ -f /opt/ros/humble/setup.bash ]] || { echo 'ROS Humble missing' >&2; exit 1; }
source /opt/ros/humble/setup.bash
source "${DRONE_ROS_WS:-$HOME/ros2_px4_ws}/install/setup.bash"
set -u
ros2 pkg prefix px4_msgs >/dev/null
ros2 pkg prefix drone_control_gateway >/dev/null
[[ -e /dev/video0 ]] || { echo '/dev/video0 unavailable' >&2; exit 1; }
[[ -f "$ROOT/configs/calibration/run_b.yaml" ]] || { echo 'Run B calibration missing' >&2; exit 1; }
[[ -f "$ROOT/stage5_operator/build/botsort/botsort_capi.so" ]] || { echo 'Build BoxMOT native BoT-SORT first' >&2; exit 1; }
for spec in \
  "$ROOT/stage3_pose/models/pose_landmarker_full.task:4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad" \
  "$ROOT/models/reid/osnet_x0_25_msmt17.pth:cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18"; do
  file="${spec%%:*}"; expected="${spec##*:}"
  [[ -f "$file" && "$(sha256sum "$file" | cut -d' ' -f1)" == "$expected" ]] || {
    echo "Model missing or SHA mismatch: $file" >&2; exit 1;
  }
done
free_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
(( free_kb >= 5*1024*1024 )) || { echo 'Less than 5 GiB free for evidence' >&2; exit 1; }
if [[ "$MODE" == --live ]]; then
  pgrep -f '/PX4-Autopilot/build/px4_sitl_default/bin/px4' >/dev/null || { echo 'PX4 SITL absent' >&2; exit 1; }
  pgrep -f 'gz sim' >/dev/null || { echo 'Gazebo absent' >&2; exit 1; }
  pgrep -f 'MicroXRCEAgent udp4 -p 8888' >/dev/null || { echo 'XRCE Agent absent' >&2; exit 1; }
  ros2 topic info /interaction/intent | grep -q 'Subscription count: 1' || { echo 'Gateway Intent subscriber absent/ambiguous' >&2; exit 1; }
  ros2 topic info /fmu/in/trajectory_setpoint | grep -q 'Publisher count: 1' || { echo 'Gateway setpoint publisher absent/ambiguous' >&2; exit 1; }
  timeout 5 ros2 topic echo /fmu/out/vehicle_status_v1 px4_msgs/msg/VehicleStatus --once >/dev/null || { echo 'PX4 status unavailable' >&2; exit 1; }
  timeout 5 ros2 topic echo /fmu/out/vehicle_local_position_v1 px4_msgs/msg/VehicleLocalPosition --once >/dev/null || { echo 'PX4 position unavailable' >&2; exit 1; }
fi
echo "Environment check PASS ($MODE); this does not arm or enter Offboard."
