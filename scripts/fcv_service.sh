#!/usr/bin/env bash
set -Eeuo pipefail

: "${REPO:?}" "${ROS_WS:?}" "${VISION_PYTHON:?}"
cd "$REPO"
export FCV_ROS_WS="$ROS_WS"
set +u
source "$REPO/scripts/env_rk3576.sh"
set -u
helper="$REPO/scripts/fcv_boot.py"

wait_for() {
  local label="$1"; shift
  until "$@"; do
    echo "Waiting for $label" >&2
    sleep 5
  done
}

case "${1:-}" in
  xrce)
    wait_for 'PX4 serial' /usr/bin/python3 "$helper" serial
    exec /usr/local/bin/MicroXRCEAgent serial -D "$PX4_SERIAL" -b "$PX4_BAUD"
    ;;
  telemetry)
    wait_for 'PX4 DDS' /usr/bin/python3 "$helper" wait-px4 --timeout 5
    exec /usr/bin/python3 "$REPO/scripts/px4_telemetry_readonly.py" --output "$PX4_TELEMETRY_FILE"
    ;;
  command)
    test -s "${GROUND_COMMAND_KEY:?}"
    args=(--port "${GROUND_COMMAND_PORT:-5604}" --allowed-ip "$GROUND_HOST"
          --key-file "$GROUND_COMMAND_KEY")
    [[ "${GROUND_COMMAND_ENABLED:-0}" == 1 ]] && args+=(--enable-live-commands)
    [[ "${GROUND_TAKEOFF_HEIGHT_VERIFIED:-0}" == 1 ]] && args+=(--verified-native-takeoff-height)
    exec /usr/bin/python3 "$REPO/scripts/ground_command_bridge.py" "${args[@]}"
    ;;
  runtime)
    wait_for 'Ground Station route' /usr/bin/python3 "$helper" route
    wait_for 'PX4 DDS' /usr/bin/python3 "$helper" wait-px4 --timeout 5
    wait_for 'camera' /usr/bin/python3 "$helper" camera
    test -x "$VISION_PYTHON"
    test -s "$FCV_POSE_MODEL_PATH"
    test -s "$FCV_CALIBRATION_PATH"
    gateway="$ROS_WS/install/drone_control_gateway/lib/drone_control_gateway/control_gateway_node"
    params="$ROS_WS/install/drone_control_gateway/share/drone_control_gateway/config/gateway.yaml"
    test -x "$gateway"
    test -s "$params"
    rm -f "$GATEWAY_LIVE_FILE"
    "$gateway" --ros-args --params-file "$params" -p shadow_mode:=false \
      -p "live_snapshot_path:=$GATEWAY_LIVE_FILE" &
    gateway_pid=$!
    vision_pid=''
    shutdown() {
      trap - TERM INT EXIT
      if [[ -n "$vision_pid" ]]; then
        kill -TERM "$vision_pid" 2>/dev/null || true
        for _ in {1..40}; do
          kill -0 "$vision_pid" 2>/dev/null || break
          sleep 0.25
        done
        wait "$vision_pid" 2>/dev/null || true
      fi
      kill -TERM "$gateway_pid" 2>/dev/null || true
      wait "$gateway_pid" 2>/dev/null || true
    }
    trap 'shutdown; exit 0' TERM INT
    trap shutdown EXIT
    /usr/bin/python3 "$helper" wait-gateway --timeout 10
    camera_device="$(/usr/bin/python3 "$helper" camera)"
    "$VISION_PYTHON" "$REPO/scripts/run_rk3576_fanout.py" \
      --camera "$camera_device" --camera-fps 60 --video-fps 30 \
      --host "$GROUND_HOST" --port "$VIDEO_PORT" --bitrate-kbps "$VIDEO_BITRATE_KBPS" \
      --metadata-host "$GROUND_HOST" --metadata-port "$META_PORT" \
      --px4-telemetry-file "$PX4_TELEMETRY_FILE" \
      --gateway-live-file "$GATEWAY_LIVE_FILE" \
      --auto-reauthorize --async-perception --async-depth \
      --px4-live --allow-live-output --run-forever &
    vision_pid=$!
    if wait -n "$vision_pid" "$gateway_pid"; then :; fi
    echo 'Gateway or vision exited; restarting both to revoke session' >&2
    exit 1
    ;;
  health)
    while :; do
      /usr/bin/python3 "$helper" check || true
      sleep 10
    done
    ;;
  *) echo 'usage: fcv_service.sh {xrce|telemetry|command|runtime|health}' >&2; exit 2 ;;
esac
