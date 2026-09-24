from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace

import cv2
import numpy as np

from config import CameraConfig


@dataclass
class CameraFrame:
    frame_id: int
    timestamp_ns: int
    raw_sbs: np.ndarray | None
    left_raw: np.ndarray | None
    read_latency_ms: float
    right_raw: np.ndarray | None = None
    pixel_format: str = "bgr24"


class StereoLeftSource:
    def __init__(self, config: CameraConfig):
        self.config = config
        device = int(config.device) if config.device.isdigit() else config.device
        self._cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            self._cap.release()
            raise RuntimeError(f"Cannot open Stage 2 camera: {config.device}")
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.raw_width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.raw_height)
        self._cap.set(cv2.CAP_PROP_FPS, config.requested_fps)
        self._frame_id = 0

    def read(self) -> CameraFrame:
        started = time.perf_counter_ns()
        ok, raw = self._cap.read()
        timestamp_ns = time.monotonic_ns()
        read_ms = (time.perf_counter_ns() - started) / 1e6
        if not ok or raw is None:
            raise RuntimeError("Camera frame read failed")
        expected = (self.config.raw_height, self.config.raw_width)
        if raw.shape[:2] != expected:
            raise RuntimeError(f"Unexpected SBS size {raw.shape[1]}x{raw.shape[0]}, expected 2560x960")
        left = raw[:, : self.config.eye_width].copy()
        result = CameraFrame(self._frame_id, timestamp_ns, raw, left, read_ms)
        self._frame_id += 1
        return result

    def release(self) -> None:
        if getattr(self, "_cap", None) is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()


def open_stereo_source(config: CameraConfig):
    """Select the camera transport; preserve the original NUC default."""
    backend = os.environ.get("FCV_CAMERA_BACKEND", "opencv").lower()
    device = os.environ.get("FCV_CAMERA_DEVICE", config.device)
    config = replace(config, device=device)
    if backend == "opencv":
        return StereoLeftSource(config)
    if backend == "ffmpeg":
        from .ffmpeg_source import FFmpegStereoSource

        return FFmpegStereoSource.from_camera_config(config)
    raise ValueError(f"Unknown FCV_CAMERA_BACKEND: {backend}")
