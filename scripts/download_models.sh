#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POSE="$ROOT/stage3_pose/models/pose_landmarker_full.task"
OSNET="$ROOT/models/reid/osnet_x0_25_msmt17.pth"
POSE_SHA=4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad
OSNET_SHA=cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18

verified() { [[ -f "$1" ]] && [[ "$(sha256sum "$1" | cut -d' ' -f1)" == "$2" ]]; }
fetch() {
  local target="$1" expected="$2" method="$3" temp
  if verified "$target" "$expected"; then echo "verified: $target"; return; fi
  if [[ -e "$target" ]]; then echo "Refusing mismatched existing model: $target" >&2; exit 1; fi
  mkdir -p "$(dirname "$target")"
  temp="$(mktemp "${target}.tmp.XXXXXX")"
  trap 'rm -f -- "$temp"' RETURN
  if [[ "$method" == curl ]]; then
    curl -fL --retry 3 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task' -o "$temp"
  else
    python3 -m gdown 'https://drive.google.com/uc?id=1Kkx2zW89jq_NETu4u42CFZTMVD5Hwm6e' -O "$temp"
  fi
  if [[ "$(sha256sum "$temp" | cut -d' ' -f1)" != "$expected" ]]; then
    echo "SHA-256 mismatch; refusing model: $target" >&2; exit 1
  fi
  mv -- "$temp" "$target"
  trap - RETURN
  echo "downloaded and verified: $target"
}

fetch "$POSE" "$POSE_SHA" curl
if ! python3 -m gdown --version >/dev/null 2>&1; then
  echo 'OSNet needs gdown in the active venv: python3 -m pip install gdown==6.4.0' >&2
  exit 1
fi
fetch "$OSNET" "$OSNET_SHA" gdown
