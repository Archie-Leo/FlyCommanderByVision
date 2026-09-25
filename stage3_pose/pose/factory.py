"""Select a PoseBackend without changing the frozen PoseFrame consumers."""
from __future__ import annotations

import os
from pathlib import Path

from config import BackendConfig
from .backend import PoseBackend


def create_pose_backend(config: BackendConfig, backend_name: str | None = None,
                        model_path: Path | None = None) -> PoseBackend:
    name = (backend_name or os.environ.get("FCV_POSE_BACKEND", "mediapipe")).lower()
    if name == "mediapipe":
        from dataclasses import replace
        from .mediapipe_backend import MediaPipePoseBackend
        return MediaPipePoseBackend(replace(config, model_path=model_path) if model_path else config)
    if name == "rknn":
        from .rknn_backend import RKNNPoseBackend
        repo = Path(__file__).resolve().parents[2]
        model = model_path or Path(os.environ.get("FCV_POSE_MODEL_PATH",
            str(Path.home() / "fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/yolov8n-pose-rk3576-int8.rknn")))
        library = Path(os.environ.get("FCV_POSE_NATIVE_LIB",
            str(repo / "stage3_pose/build/rknn_pose/libfcv_rknn_pose.so")))
        return RKNNPoseBackend(config, model, library)
    raise ValueError(f"Unknown FCV_POSE_BACKEND: {name}")
