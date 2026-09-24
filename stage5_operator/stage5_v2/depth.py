from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import cv2
import numpy as np


@dataclass(frozen=True)
class RunBSGBMConfig:
    """Exact Stage 2 depth-validation SGBM defaults; avoid top-level config.py collision."""

    min_disparity: int = 0
    num_disparities: int = 128
    block_size: int = 5
    uniqueness_ratio: int = 10
    speckle_window_size: int = 100
    speckle_range: int = 2
    disp12_max_diff: int = 1
    pre_filter_cap: int = 63
    min_depth_mm: float = 100.0
    max_depth_mm: float = 10000.0


def load_run_b_calibration(path: Path) -> dict:
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise FileNotFoundError(f"Cannot open Run B calibration: {path}")
    try:
        keys = ("K_left", "D_left", "K_right", "D_right", "R1", "R2", "P1", "P2", "Q")
        cal = {}
        for key in keys:
            value = fs.getNode(key).mat()
            if value is None:
                raise ValueError(f"Run B calibration missing {key}")
            cal[key] = value
        for key in ("image_width", "image_height", "baseline_mm"):
            node = fs.getNode(key)
            if node.empty():
                raise ValueError(f"Run B calibration missing {key}")
            cal[key] = float(node.real())
    finally:
        fs.release()
    if cal["Q"].shape != (4, 4) or cal["R1"].shape != (3, 3) or cal["R2"].shape != (3, 3):
        raise ValueError("Run B calibration matrix shape invalid")
    if not 45.0 <= cal["baseline_mm"] <= 85.0:
        raise ValueError("Run B baseline outside conservative physical sanity range")
    return cal


def build_run_b_matcher(cfg: RunBSGBMConfig):
    return cv2.StereoSGBM_create(
        minDisparity=cfg.min_disparity, numDisparities=cfg.num_disparities,
        blockSize=cfg.block_size, P1=8*cfg.block_size*cfg.block_size,
        P2=32*cfg.block_size*cfg.block_size, disp12MaxDiff=cfg.disp12_max_diff,
        preFilterCap=cfg.pre_filter_cap, uniquenessRatio=cfg.uniqueness_ratio,
        speckleWindowSize=cfg.speckle_window_size, speckleRange=cfg.speckle_range,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)


@dataclass(frozen=True)
class PersonDepthV1:
    depth_m: float | None
    depth_quality: float
    valid_ratio: float
    mad_z_m: float | None
    xyz_center_m: tuple[float, float, float] | None

    @property
    def available(self):
        return self.depth_m is not None


def summarize_torso_depth(points_mm, valid, bbox) -> PersonDepthV1:
    h, w = valid.shape
    x1, y1, x2, y2 = [float(v) for v in bbox]
    bw, bh = x2-x1, y2-y1
    xa, xb = max(0, int(x1+.32*bw)), min(w, int(x1+.68*bw))
    ya, yb = max(0, int(y1+.22*bh)), min(h, int(y1+.62*bh))
    if xb-xa < 8 or yb-ya < 12:
        return PersonDepthV1(None, 0.0, 0.0, None, None)
    roi = points_mm[ya:yb, xa:xb, :]
    mask = valid[ya:yb, xa:xb] & np.isfinite(roi).all(axis=2)
    ratio = float(mask.mean())
    if ratio < .12 or int(mask.sum()) < 50:
        return PersonDepthV1(None, 0.0, ratio, None, None)
    xyz = roi[mask] / 1000.0
    med = np.median(xyz, axis=0)
    mad = float(np.median(np.abs(xyz[:, 2] - med[2])))
    if not np.all(np.isfinite(med)) or not .3 <= med[2] <= 5.0 or mad > .5:
        return PersonDepthV1(None, 0.0, ratio, None, None)
    # Stage 2 was physically checked at 0.5-2.5 m. Outside this range keep
    # evidence available, but taper its reliability (engineering heuristic).
    near = min(1.0, max(0.0, (med[2]-.3)/.2))
    far = min(1.0, max(0.0, (4.5-med[2])/2.0))
    quality = min(1.0, ratio/.65) * max(0.0, 1.0-mad/.25) * near * far
    return PersonDepthV1(float(med[2]), float(quality), ratio, mad, tuple(float(v) for v in med))


class StereoPersonDepthAdapter:
    """Run B calibration + Stage 2 SGBM; stereo invalid is unavailable evidence."""

    def __init__(self, calibration_path: Path, stage2_root: Path):
        root = Path(stage2_root).expanduser().resolve()
        if not (root / "config.py").is_file():
            raise FileNotFoundError(f"Stage 2 depth validation source missing: {root}")

        self.calibration_path = Path(calibration_path).expanduser().resolve()
        self.cal = load_run_b_calibration(self.calibration_path)
        self.size = (int(self.cal["image_width"]), int(self.cal["image_height"]))
        if self.size != (1280, 960):
            raise ValueError("Run B per-eye calibration must be 1280x960")
        self.maps = (
            cv2.initUndistortRectifyMap(self.cal["K_left"], self.cal["D_left"], self.cal["R1"], self.cal["P1"], self.size, cv2.CV_32FC1),
            cv2.initUndistortRectifyMap(self.cal["K_right"], self.cal["D_right"], self.cal["R2"], self.cal["P2"], self.size, cv2.CV_32FC1),
        )
        self.cfg = RunBSGBMConfig()
        self.matcher = build_run_b_matcher(self.cfg)
        self.last_latency_ms = 0.0

    def process(self, left_raw, right_raw, bboxes, *, rectified_left=None):
        start = time.perf_counter()
        if left_raw.shape[:2] != self.size[::-1] or right_raw.shape[:2] != self.size[::-1]:
            raise ValueError("Stereo frame does not match Run B calibration")
        left = rectified_left if rectified_left is not None else cv2.remap(left_raw, *self.maps[0], cv2.INTER_LINEAR)
        if left.shape[:2] != self.size[::-1]:
            raise ValueError("Rectified LEFT size does not match Run B calibration")
        if not bboxes:
            # No person means no depth evidence to compute; still return the
            # same rectified LEFT image used by pose and downstream display.
            self.last_latency_ms = (time.perf_counter()-start)*1000.0
            return left, []
        right = cv2.remap(right_raw, *self.maps[1], cv2.INTER_LINEAR)
        disparity_raw = self.matcher.compute(cv2.cvtColor(left, cv2.COLOR_BGR2GRAY),
                                             cv2.cvtColor(right, cv2.COLOR_BGR2GRAY))
        disparity = disparity_raw.astype(np.float32) / 16.0
        good = np.isfinite(disparity) & (disparity > self.cfg.min_disparity)
        disparity[~good] = np.nan
        points = cv2.reprojectImageTo3D(disparity, self.cal["Q"], handleMissingValues=False)
        z = points[:, :, 2]
        valid = good & np.isfinite(z) & (z >= self.cfg.min_depth_mm) & (z <= self.cfg.max_depth_mm)
        result = [summarize_torso_depth(points, valid, box) for box in bboxes]
        self.last_latency_ms = (time.perf_counter()-start)*1000.0
        return left, result
