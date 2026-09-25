#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env_rk3576.sh"
set -u
[[ -x "$HOME/venvs/fcv_stage5/bin/python3" ]] || {
  echo 'RK3576 vision Python missing' >&2; exit 2;
}
[[ -e "$FCV_CAMERA_DEVICE" && -r "$FCV_CALIBRATION_PATH" ]] || {
  echo 'Camera or Run B calibration unavailable' >&2; exit 2;
}
cd "$ROOT"
exec "$HOME/venvs/fcv_stage5/bin/python3" stage6_closed_loop/live_closed_loop.py \
  --camera "$FCV_CAMERA_DEVICE" \
  --calibration "$FCV_CALIBRATION_PATH" \
  --reid-backend rknn \
  --rknn-osnet-model "$ROOT/models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn" \
  --input-rotate-180 --no-display "$@" --topic /interaction/intent_dry_run
