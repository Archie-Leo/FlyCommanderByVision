#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOXMOT="${DRONE_REF_ROOT:-$HOME/drone_vision_refs/operator_lock}/boxmot"
EXPECTED=857628343860db1ea48ea50db7a73c25b7a3be13
[[ -d "$BOXMOT/.git" ]] || { echo "BoxMOT source missing: $BOXMOT" >&2; exit 1; }
[[ "$(git -C "$BOXMOT" rev-parse HEAD)" == "$EXPECTED" ]] || {
  echo 'BoxMOT commit differs from frozen baseline; refusing build' >&2; exit 1;
}
[[ -z "$(git -C "$BOXMOT" status --porcelain)" ]] || {
  echo 'BoxMOT source is dirty; inspect before building' >&2; exit 1;
}
BUILD="$ROOT/stage5_operator/build/botsort"
cmake -S "$BOXMOT/boxmot/native/cpp/trackers/botsort" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD" --config Release --target botsort_capi -j "${JOBS:-2}"
[[ -f "$BUILD/botsort_capi.so" ]] || { echo 'Native library missing after build' >&2; exit 1; }
echo "Built $BUILD/botsort_capi.so (BoxMOT AGPL-3.0; see THIRD_PARTY_NOTICES.md)"
