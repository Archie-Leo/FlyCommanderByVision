#!/usr/bin/env bash
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env_rk3576.sh"
cd "$REPO_ROOT"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$HOME/venvs/fcv/bin/python3" - <<'PY'
import sys

# ROS Jazzy's PyYAML is installed for system Python. Add that directory after
# the vision venv's site-packages so its NumPy/OpenCV remain selected.
import cv2  # noqa: F401
import numpy  # noqa: F401
sys.path.append("/usr/lib/python3/dist-packages")
import pytest

raise SystemExit(pytest.main(["-q", "stage6_closed_loop/tests/test_gate6c_evidence.py"]))
PY
