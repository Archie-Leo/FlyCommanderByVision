#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/humble/setup.bash
source "${DRONE_ROS_WS:-$HOME/ros2_px4_ws}/install/setup.bash"
source "${DRONE_VENV:-$HOME/venvs/drone_stage5_v2}/bin/activate"
set -u
export PYTHONNOUSERSITE=1
export PYTHONPATH="$ROOT/stage6_closed_loop:$ROOT/stage3_pose:$ROOT/stage4_gesture:$ROOT/stage5_operator:${PYTHONPATH:-}"
"$ROOT/scripts/check_environment.sh" --dry-run
cd "$ROOT/stage6_closed_loop"
exec python live_closed_loop.py \
  --stage3-root "$ROOT/stage3_pose" \
  --stage4-root "$ROOT/stage4_gesture" \
  --stage5-root "$ROOT/stage5_operator" \
  --stage2-root "$ROOT/stage2_stereo/depth_validation" \
  --calibration "$ROOT/configs/calibration/run_b.yaml" \
  --boxmot-lib "$ROOT/stage5_operator/build/botsort/botsort_capi.so" \
  --torchreid-root "${DRONE_REF_ROOT:-$HOME/drone_vision_refs/operator_lock}/deep-person-reid" \
  --osnet-checkpoint "$ROOT/models/reid/osnet_x0_25_msmt17.pth" \
  --auto-reauthorize --record --rosbag "$@" \
  --topic /interaction/intent_dry_run
