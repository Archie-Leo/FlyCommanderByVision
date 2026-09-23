from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CALIBRATION = Path.home() / "drone_stage2/stereo_calibration/stereo_calibration_output/20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml"


@dataclass(frozen=True)
class DepthValidationConfig:
    calibration_path: Path = DEFAULT_CALIBRATION
    camera_device: str = "/dev/video0"
    raw_width: int = 2560
    raw_height: int = 960
    eye_width: int = 1280
    eye_height: int = 960
    requested_fps: float = 60.0
    min_disparity: int = 0
    num_disparities: int = 128
    block_size: int = 5
    uniqueness_ratio: int = 10
    speckle_window_size: int = 100
    speckle_range: int = 2
    disp12_max_diff: int = 1
    pre_filter_cap: int = 63
    click_window: int = 7
    min_valid_click_samples: int = 5
    min_depth_mm: float = 100.0
    max_depth_mm: float = 10000.0
    display_scale: float = 0.65
    epiline_step: int = 80
    output_root: Path = PROJECT_ROOT / "outputs"

    def validate(self) -> None:
        if self.num_disparities <= 0 or self.num_disparities % 16:
            raise ValueError("num_disparities must be positive and divisible by 16")
        if self.block_size < 1 or self.block_size % 2 == 0:
            raise ValueError("block_size must be an odd integer >= 1")
        if self.click_window < 1 or self.click_window % 2 == 0:
            raise ValueError("click_window must be an odd integer >= 1")

    @property
    def p1(self) -> int:
        return 8 * self.block_size * self.block_size

    @property
    def p2(self) -> int:
        return 32 * self.block_size * self.block_size
