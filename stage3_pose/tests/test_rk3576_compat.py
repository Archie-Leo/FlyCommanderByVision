from pathlib import Path
from unittest.mock import patch

import pytest

from camera.ffmpeg_source import FFmpegCameraConfig, FFmpegStereoSource
from config import BackendConfig
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
