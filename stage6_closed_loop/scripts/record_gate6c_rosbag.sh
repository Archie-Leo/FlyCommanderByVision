#!/usr/bin/env bash
# Record only topics actually present with the expected message type.
set -euo pipefail

if [[ $# -ne 2 || ! -d "$1" || ( "$2" != dry-run && "$2" != live ) ]]; then
  echo "usage: $0 EXISTING_RUN_DIR dry-run|live" >&2
  exit 2
fi
run_dir=$(realpath "$1")
mode=$2
if [[ -e "$run_dir/rosbag" ]]; then
  echo "Refusing to overwrite $run_dir/rosbag" >&2
  exit 2
fi
topics=()
add_topic() {
  local topic=$1 expected=$2 actual
  actual=$(ros2 topic type "$topic" 2>/dev/null || true)
  if [[ "$actual" == "$expected" ]]; then
    topics+=("$topic")
    return 0
  fi
  return 1
}

if [[ "$mode" == live ]]; then
  add_topic /interaction/intent drone_control_gateway/msg/Intent || {
    echo "Live Intent topic unavailable or wrong type" >&2; exit 2;
  }
else
  add_topic /interaction/intent_dry_run drone_control_gateway/msg/Intent || {
    echo "Dry-run Intent topic unavailable or wrong type" >&2; exit 2;
  }
fi

if [[ "$mode" == live ]]; then
  add_topic /fmu/in/trajectory_setpoint px4_msgs/msg/TrajectorySetpoint || {
    echo "Gateway trajectory setpoint topic unavailable" >&2; exit 2;
  }
  add_topic /fmu/in/offboard_control_mode px4_msgs/msg/OffboardControlMode || {
    echo "Gateway OffboardControlMode topic unavailable" >&2; exit 2;
  }
else
  add_topic /fmu/in/trajectory_setpoint px4_msgs/msg/TrajectorySetpoint || true
  add_topic /fmu/in/offboard_control_mode px4_msgs/msg/OffboardControlMode || true
fi

if ! add_topic /fmu/out/vehicle_local_position px4_msgs/msg/VehicleLocalPosition; then
  add_topic /fmu/out/vehicle_local_position_v1 px4_msgs/msg/VehicleLocalPosition || {
    [[ "$mode" == dry-run ]] || { echo "PX4 local position topic unavailable" >&2; exit 2; }
  }
fi
if ! add_topic /fmu/out/vehicle_status_v1 px4_msgs/msg/VehicleStatus; then
  add_topic /fmu/out/vehicle_status px4_msgs/msg/VehicleStatus || {
    [[ "$mode" == dry-run ]] || { echo "PX4 vehicle status topic unavailable" >&2; exit 2; }
  }
fi

printf '%s\n' "${topics[@]}" > "$run_dir/rosbag_topics.txt"
echo "Recording: ${topics[*]}"
exec ros2 bag record -o "$run_dir/rosbag" "${topics[@]}"
