import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from camera.ffmpeg_source import FFmpegCameraConfig, FFmpegStereoSource
from config import AppConfig, BackendConfig
from pose.mediapipe_backend import MediaPipePoseBackend


def test_ffmpeg_left_and_right_crop_without_swapping_eyes():
    left = FFmpegCameraConfig(eye="left", output_width=640, output_height=480)
    right = FFmpegCameraConfig(eye="right", output_width=640, output_height=480)
    assert "crop=1280:960:0:0,scale=640:480" in FFmpegStereoSource.command(left)
    assert "crop=1280:960:1280:0,scale=640:480" in FFmpegStereoSource.command(right)


def test_ffmpeg_stereo_keeps_calibration_geometry():
    cmd = FFmpegStereoSource.command(FFmpegCameraConfig())
    assert "-vf" not in cmd
    assert "2560x960" in cmd


def test_mediapipe_rejects_arm_cpu_without_lse_before_loading_wheel():
    with patch("pose.mediapipe_backend.platform.machine", return_value="aarch64"):
        with patch.object(Path, "read_text", return_value="Features : fp asimd crc32\n"):
            with pytest.raises(RuntimeError, match="LSE atomics"):
                MediaPipePoseBackend(BackendConfig(model_path=Path("/missing.task")))


def test_ffmpeg_early_exit_closes_process():
    config = FFmpegCameraConfig(ffmpeg_binary="/bin/false")
    with FFmpegStereoSource(config) as source:
        with pytest.raises(RuntimeError, match="FFmpeg camera stopped"):
            source.read(timeout=1)
    assert source._process.poll() is not None
    assert not source._thread.is_alive()
    source.release()  # closing twice is safe


def test_rk3576_environment_selects_repo_paths():
    if "REPO_ROOT" not in os.environ:
        pytest.skip("RK3576 environment was not sourced")
    repo = Path(os.environ["REPO_ROOT"])
    calibration = repo / "configs/calibration/run_b.yaml"
    assert AppConfig().calibration_path == calibration
    assert calibration.is_file()

    def load_runner(relative, name):
        spec = importlib.util.spec_from_file_location(name, repo / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    v1 = load_runner("stage5_operator/live_operator.py", "rk_stage5_v1_runner")
    v2 = load_runner("stage5_operator/live_operator_v2.py", "rk_stage5_v2_runner")
    stage6 = load_runner("stage6_closed_loop/live_closed_loop.py", "rk_stage6_runner")
    with patch.object(sys, "argv", ["stage5-v1"]):
        args = v1.parse_args()
    assert args.stage3_root == repo / "stage3_pose"
    assert args.stage4_root == repo / "stage4_gesture"
    with patch.object(sys, "argv", ["stage5-v2", "--osnet-checkpoint", "/missing"]):
        args = v2.args_parser()
    assert args.stage2_root == repo / "stage2_stereo/depth_validation"
    assert args.calibration == calibration
    assert args.boxmot_lib == repo / "stage5_operator/build/botsort/botsort_capi.so"
    with patch.object(sys, "argv", ["stage6", "--osnet-checkpoint", "/missing"]):
        args = stage6.parse_args()
    assert args.stage5_root == repo / "stage5_operator"
    assert args.calibration == calibration
