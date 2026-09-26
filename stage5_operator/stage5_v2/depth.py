from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
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


INVALID_DEPTH = PersonDepthV1(None, 0.0, 0.0, None, None)


@dataclass(frozen=True)
class DepthCacheEntry:
    track_id: int
    bbox: tuple[float, float, float, float]
    value: PersonDepthV1
    source_frame_id: int
    source_timestamp_ms: int
    computed_timestamp_ms: int


def torso_bounds(bbox, width: int, height: int):
    x1, y1, x2, y2 = [float(v) for v in bbox]
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
        return None
    bw, bh = x2-x1, y2-y1
    xa, xb = max(0, int(x1+.32*bw)), min(width, int(x1+.68*bw))
    ya, yb = max(0, int(y1+.22*bh)), min(height, int(y1+.62*bh))
    if xb-xa < 8 or yb-ya < 12:
        return None
    return xa, ya, xb, yb


def stereo_roi_bounds(bbox, width: int, height: int, cfg: RunBSGBMConfig,
                      margin_ratio: float = .10):
    """Shared rectified crop; left padding covers SGBM's rightward search.

    OpenCV computes disparity at left x from right x - disparity. Both views
    use one global-coordinate crop so the original matcher sign is preserved.
    """
    torso = torso_bounds(bbox, width, height)
    if torso is None or not 0 <= margin_ratio <= 1:
        return None
    xa, ya, xb, yb = torso
    bw, bh = float(bbox[2])-float(bbox[0]), float(bbox[3])-float(bbox[1])
    pad_x = max(cfg.block_size, math.ceil(bw*margin_ratio))
    pad_y = max(cfg.block_size, math.ceil(bh*margin_ratio))
    search_left = max(0, cfg.min_disparity + cfg.num_disparities - 1)
    search_right = max(0, -cfg.min_disparity)
    x0 = max(0, xa-pad_x-search_left-cfg.block_size)
    x1 = min(width, xb+pad_x+search_right+cfg.block_size)
    y0, y1 = max(0, ya-pad_y), min(height, yb+pad_y)
    if x1-x0 <= cfg.num_disparities + cfg.block_size or y1-y0 < cfg.block_size:
        return None
    return x0, y0, x1, y1


def _bbox_iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0., x1-x0)*max(0., y1-y0)
    area_a = max(0., a[2]-a[0])*max(0., a[3]-a[1])
    area_b = max(0., b[2]-b[0])*max(0., b[3]-b[1])
    denominator = area_a+area_b-intersection
    return intersection/denominator if denominator > 0 else 0.


def summarize_torso_depth(points_mm, valid, bbox) -> PersonDepthV1:
    h, w = valid.shape
    bounds = torso_bounds(bbox, w, h)
    if bounds is None:
        return INVALID_DEPTH
    xa, ya, xb, yb = bounds
    return summarize_depth_points(points_mm[ya:yb, xa:xb, :], valid[ya:yb, xa:xb])


def summarize_depth_points(roi, valid) -> PersonDepthV1:
    mask = valid & np.isfinite(roi).all(axis=2)
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

    def __init__(self, calibration_path: Path, stage2_root: Path, *,
                 depth_roi_margin_ratio: float = .10, depth_rate_hz: float = 5.,
                 depth_max_age_ms: int = 500):
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
        if (not math.isfinite(depth_roi_margin_ratio) or not 0 <= depth_roi_margin_ratio <= 1 or
                not math.isfinite(depth_rate_hz) or depth_rate_hz <= 0 or
                not math.isfinite(depth_max_age_ms) or depth_max_age_ms <= 0):
            raise ValueError("Invalid ROI depth schedule configuration")
        self.depth_roi_margin_ratio = depth_roi_margin_ratio
        self.depth_rate_hz = depth_rate_hz
        self.depth_max_age_ms = depth_max_age_ms
        self._cache: dict[int, DepthCacheEntry] = {}
        self.last_profile_ms: dict[str, float] = {}
        self.last_roi_shapes: list[tuple[int, int] | None] = []
        self.last_depth_ages_ms: list[float | None] = []
        self.last_depth_sources: list[int | None] = []
        self.last_depth_valid: list[bool] = []
        self.last_updated_count = 0

    def process_roi(self, left_raw, right_raw, bboxes, *, rectified_left=None):
        start = time.perf_counter()
        if left_raw.shape[:2] != self.size[::-1] or right_raw.shape[:2] != self.size[::-1]:
            raise ValueError("Stereo frame does not match Run B calibration")
        left = rectified_left if rectified_left is not None else cv2.remap(left_raw, *self.maps[0], cv2.INTER_LINEAR)
        if left.shape[:2] != self.size[::-1]:
            raise ValueError("Rectified LEFT size does not match Run B calibration")
        profile = dict(rectify=(time.perf_counter()-start)*1000, bbox_mapping=0.,
                       right_rectify=0., roi_preparation=0., matcher=0., aggregation=0.)
        results = []
        shapes = []
        for bbox in bboxes:
            prep_start = time.perf_counter()
            bounds = stereo_roi_bounds(bbox, *self.size, self.cfg, self.depth_roi_margin_ratio)
            if bounds is None:
                results.append(INVALID_DEPTH); shapes.append(None)
                continue
            x0, y0, x1, y1 = bounds
            # Remap only the needed RIGHT pixels; LEFT was rectified for Pose.
            remap_start = time.perf_counter()
            right = cv2.remap(right_raw, self.maps[1][0][y0:y1, x0:x1],
                              self.maps[1][1][y0:y1, x0:x1], cv2.INTER_LINEAR)
            profile["right_rectify"] += (time.perf_counter()-remap_start)*1000
            left_gray = cv2.cvtColor(left[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
            right_gray = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
            profile["roi_preparation"] += (time.perf_counter()-prep_start)*1000
            shapes.append((x1-x0, y1-y0))
            try:
                match_start = time.perf_counter()
                disparity_raw = self.matcher.compute(left_gray, right_gray)
                profile["matcher"] += (time.perf_counter()-match_start)*1000
                aggregate_start = time.perf_counter()
                torso = torso_bounds(bbox, *self.size)
                if torso is None:
                    results.append(INVALID_DEPTH)
                    continue
                xa, ya, xb, yb = torso
                disparity = disparity_raw[ya-y0:yb-y0, xa-x0:xb-x0].astype(np.float32)/16.0
                good = np.isfinite(disparity) & (disparity > self.cfg.min_disparity)
                disparity[~good] = np.nan
                roi_q = self.cal["Q"].copy()
                roi_q[0, 3] += xa
                roi_q[1, 3] += ya
                points = cv2.reprojectImageTo3D(disparity, roi_q, handleMissingValues=False)
                z = points[:, :, 2]
                valid = good & np.isfinite(z) & (z >= self.cfg.min_depth_mm) & (z <= self.cfg.max_depth_mm)
                results.append(summarize_depth_points(points, valid))
                profile["aggregation"] += (time.perf_counter()-aggregate_start)*1000
            except cv2.error:
                results.append(INVALID_DEPTH)
        self.last_profile_ms = profile
        self.last_roi_shapes = shapes
        self.last_latency_ms = (time.perf_counter()-start)*1000
        self.last_profile_ms["total_depth"] = self.last_latency_ms
        return left, results

    def process_tracked(self, left_raw, right_raw, bboxes, track_ids, *,
                        frame_id: int, timestamp_ms: int, rectified_left=None):
        if len(bboxes) != len(track_ids) or len(set(track_ids)) != len(track_ids):
            raise ValueError("Depth track IDs must be unique and align with bboxes")
        now_ms = time.monotonic_ns()//1_000_000
        # A track absent in any processed frame cannot inherit its old depth.
        active_tracks = set(track_ids)
        self._cache = {key: entry for key, entry in self._cache.items()
                       if key in active_tracks}
        period_ms = 1000./self.depth_rate_hz
        update_indices = []
        results = [INVALID_DEPTH for _ in bboxes]
        ages: list[float | None] = [None for _ in bboxes]
        sources: list[int | None] = [None for _ in bboxes]
        for i, (bbox, track_id) in enumerate(zip(bboxes, track_ids)):
            entry = self._cache.get(track_id)
            age = now_ms-entry.computed_timestamp_ms if entry else math.inf
            reusable = (entry is not None and 0 <= age <= self.depth_max_age_ms and
                        _bbox_iou(entry.bbox, bbox) >= .25 and
                        timestamp_ms >= entry.source_timestamp_ms)
            if reusable and age < period_ms:
                results[i] = entry.value
                ages[i] = float(age)
                sources[i] = entry.source_frame_id
            else:
                update_indices.append(i)
        start = time.perf_counter()
        if update_indices:
            left, updates = self.process_roi(left_raw, right_raw,
                [bboxes[i] for i in update_indices], rectified_left=rectified_left)
            computed_ms = time.monotonic_ns()//1_000_000
            for i, value in zip(update_indices, updates):
                track_id = track_ids[i]
                entry = DepthCacheEntry(track_id, tuple(bboxes[i]), value,
                                        frame_id, timestamp_ms, computed_ms)
                self._cache[track_id] = entry
                results[i] = value
                ages[i] = 0.
                sources[i] = frame_id
        else:
            left = rectified_left if rectified_left is not None else cv2.remap(left_raw, *self.maps[0], cv2.INTER_LINEAR)
            self.last_profile_ms = dict(rectify=0., bbox_mapping=0., roi_preparation=0.,
                                        matcher=0., aggregation=0., total_depth=0.)
            self.last_roi_shapes = []
        # No indefinite retention, including disappeared tracks.
        self._cache = {track_id: entry for track_id, entry in self._cache.items()
                       if now_ms-entry.computed_timestamp_ms <= self.depth_max_age_ms}
        self.last_depth_ages_ms = ages
        self.last_depth_sources = sources
        self.last_depth_valid = [value.available for value in results]
        self.last_updated_count = len(update_indices)
        self.last_latency_ms = (time.perf_counter()-start)*1000
        self.last_profile_ms["total_depth"] = self.last_latency_ms
        return left, results

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
