from __future__ import annotations

import math
import platform
import statistics
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

import cv2
import numpy as np

from config import BackendConfig
from .backend import PoseBackend
from .types import JointObservation, PersonPose, PoseFrame


MEDIAPIPE_TO_CANONICAL = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}


def _finite(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(float(value))


def canonical_confidence(visibility: Optional[float], presence: Optional[float]) -> Optional[float]:
    values = [float(v) for v in (visibility, presence) if _finite(v)]
    return min(values) if values else None


class MediaPipePoseBackend(PoseBackend):
    name = "mediapipe_pose_landmarker_full"

    def __init__(self, config: BackendConfig):
        if platform.machine().lower() in {"aarch64", "arm64"}:
            try:
                cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            except OSError:
                cpuinfo = ""
            feature_lines = [line.split(":", 1)[1].split() for line in cpuinfo.splitlines()
                             if line.lower().startswith("features") and ":" in line]
            if not feature_lines or not all("atomics" in features for features in feature_lines):
                raise RuntimeError(
                    "MediaPipe ARM64 wheel requires LSE atomics absent on this CPU; "
                    "pose inference needs a validated RK3576 backend"
                )
        model_path = Path(config.model_path).expanduser().resolve()
        if not model_path.is_file():
            raise FileNotFoundError(f"MediaPipe model not found: {model_path}")
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_poses=config.num_poses,
            min_pose_detection_confidence=config.min_pose_detection_confidence,
            min_pose_presence_confidence=config.min_pose_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
            output_segmentation_masks=False,
        )
        self._landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1

    def infer(self, bgr_frame: np.ndarray, timestamp_ms: int, frame_id: int) -> PoseFrame:
        if bgr_frame.ndim != 3 or bgr_frame.shape[2] != 3:
            raise ValueError(f"Expected BGR8 HxWx3 frame, got {bgr_frame.shape}")
        height, width = bgr_frame.shape[:2]
        timestamp_ms = max(int(timestamp_ms), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        started = time.perf_counter_ns()
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        latency_ms = (time.perf_counter_ns() - started) / 1e6
        poses = [
            self._convert_pose(landmarks, timestamp_ms, frame_id, index, width, height)
            for index, landmarks in enumerate(result.pose_landmarks)
        ]
        return PoseFrame(
            timestamp_ms=timestamp_ms,
            frame_id=frame_id,
            image_width=width,
            image_height=height,
            poses=poses,
            backend_name=self.name,
            inference_latency_ms=latency_ms,
        )

    def _convert_pose(self, landmarks, timestamp_ms, frame_id, local_id, width, height):
        joints: Dict[str, JointObservation] = {}
        xs, ys, scores = [], [], []
        for name, index in MEDIAPIPE_TO_CANONICAL.items():
            lm = landmarks[index]
            x = float(lm.x) if _finite(lm.x) else None
            y = float(lm.y) if _finite(lm.y) else None
            visibility = float(lm.visibility) if _finite(lm.visibility) else None
            presence = float(lm.presence) if _finite(lm.presence) else None
            confidence = canonical_confidence(visibility, presence)
            valid = x is not None and y is not None and confidence is not None
            x_px = x * width if x is not None else None
            y_px = y * height if y is not None else None
            joints[name] = JointObservation(
                name=name,
                x_px=x_px,
                y_px=y_px,
                x_norm_image=x,
                y_norm_image=y,
                confidence=confidence,
                visibility=visibility,
                presence=presence,
                valid=valid,
            )
            if valid:
                xs.append(x_px)
                ys.append(y_px)
                scores.append(confidence)
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
        pose_score = statistics.median(scores) if scores else None
        return PersonPose(
            timestamp_ms=timestamp_ms,
            frame_id=frame_id,
            local_detection_id=local_id,
            image_width=width,
            image_height=height,
            bbox_xyxy=bbox,
            pose_score=pose_score,
            backend_name=self.name,
            joints=joints,
        )

    def close(self) -> None:
        if getattr(self, "_landmarker", None) is not None:
            self._landmarker.close()
            self._landmarker = None
