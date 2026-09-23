from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CALIBRATION = Path.home() / (
    "drone_stage2/stereo_calibration/stereo_calibration_output/"
    "20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml"
)
DEFAULT_MODEL = Path(__file__).resolve().parent / "models/pose_landmarker_full.task"


@dataclass(frozen=True)
class CameraConfig:
    device: str = "/dev/video0"
    raw_width: int = 2560
    raw_height: int = 960
    eye_width: int = 1280
    eye_height: int = 960
    requested_fps: float = 60.0


@dataclass(frozen=True)
class BackendConfig:
    model_path: Path = DEFAULT_MODEL
    num_poses: int = 1
    min_pose_detection_confidence: float = 0.5
    min_pose_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5


@dataclass(frozen=True)
class QualityConfig:
    joint_confidence_min: float = 0.50
    min_key_joint_coverage: float = 0.75
    min_bbox_height_ratio: float = 0.20
    min_bbox_area_ratio: float = 0.025
    max_out_of_frame_key_joints: int = 2
    require_both_arms: bool = True


@dataclass(frozen=True)
class NormalizationConfig:
    min_scale_px: float = 8.0
    epsilon: float = 1e-6


@dataclass(frozen=True)
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)
    calibration_path: Path = DEFAULT_CALIBRATION

