#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZOO="${RKNN_ZOO_ROOT:-$HOME/fcv_third_party/rknn_model_zoo}"
TOOLKIT="${RKNN_TOOLKIT_ROOT:-$HOME/fcv_third_party/rknn-toolkit2-v2.3.2}"
EXPECTED_ZOO=bad6c7334531becaf90a561988519b7bec34d0ab
EXPECTED_RUNTIME=d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8
[[ "$(git -C "$ZOO" rev-parse HEAD)" == "$EXPECTED_ZOO" ]] || { echo 'RKNN Model Zoo commit mismatch' >&2; exit 1; }
RUNTIME="$TOOLKIT/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so"
[[ "$(sha256sum "$RUNTIME" | cut -d' ' -f1)" == "$EXPECTED_RUNTIME" ]] || { echo 'RKNN runtime SHA256 mismatch' >&2; exit 1; }
BUILD="$ROOT/stage3_pose/build/rknn_pose"
cmake -S "$ROOT/stage3_pose/native/rknn_pose" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release \
  -DRKNN_ZOO_ROOT="$ZOO" -DRKNN_TOOLKIT_ROOT="$TOOLKIT"
cmake --build "$BUILD" --config Release --target fcv_rknn_pose -j "${JOBS:-2}"
test -f "$BUILD/libfcv_rknn_pose.so"
echo "Built $BUILD/libfcv_rknn_pose.so using frozen official postprocess"
