from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class CalibrationConfig:
    camera_device: str = "/dev/video0"
    raw_width: int = 2560
    raw_height: int = 960
    eye_width: int = 1280
    eye_height: int = 960
    requested_fps: float = 60.0
    pattern_cols: int = 11
    pattern_rows: int = 8
    square_size_mm: float = 45.0
    nominal_baseline_mm: float = 65.0
    min_valid_pairs: int = 12
    preview_scale: float = 0.5
    preview_detection_interval_s: float = 0.20
    message_duration_s: float = 2.5
    rectify_alpha: float = 0.0
    epipolar_line_step_px: int = 50
    max_rectified_previews: int = 6

    @property
    def raw_size(self) -> Tuple[int, int]:
        return self.raw_width, self.raw_height

    @property
    def eye_size(self) -> Tuple[int, int]:
        return self.eye_width, self.eye_height

    @property
    def pattern_size(self) -> Tuple[int, int]:
        return self.pattern_cols, self.pattern_rows

    @property
    def point_count(self) -> int:
        return self.pattern_cols * self.pattern_rows


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def captures_root() -> Path:
    return project_root() / "captures"


def output_root() -> Path:
    return project_root() / "stereo_calibration_output"
