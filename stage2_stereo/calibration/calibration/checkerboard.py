from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import CalibrationConfig


@dataclass(frozen=True)
class CornerDetection:
    found: bool
    corners: Optional[np.ndarray]
    method: str
    reason: str = ""


@dataclass(frozen=True)
class StereoDetection:
    left: CornerDetection
    right: CornerDetection
    ordering_ok: bool
    ordering_reason: str

    @property
    def valid(self) -> bool:
        return self.left.found and self.right.found and self.ordering_ok


def make_object_points(config: CalibrationConfig) -> np.ndarray:
    """Return row-major planar points in millimetres."""
    points = np.zeros((config.point_count, 3), dtype=np.float32)
    grid = np.mgrid[0 : config.pattern_cols, 0 : config.pattern_rows].T.reshape(-1, 2)
    points[:, :2] = grid.astype(np.float32) * np.float32(config.square_size_mm)
    return points


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("Image is empty.")
    if image.dtype != np.uint8:
        raise ValueError(f"Expected uint8 image, got {image.dtype}.")
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"Unsupported image shape: {image.shape}")


def detect_corners(
    image: np.ndarray,
    config: CalibrationConfig,
    *,
    exhaustive: bool,
) -> CornerDetection:
    try:
        gray = _to_gray(image)
    except ValueError as exc:
        return CornerDetection(False, None, "findChessboardCornersSB", str(exc))

    flags = cv2.CALIB_CB_NORMALIZE_IMAGE
    if exhaustive:
        flags |= cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    try:
        found, corners = cv2.findChessboardCornersSB(gray, config.pattern_size, flags=flags)
    except cv2.error as exc:
        return CornerDetection(False, None, "findChessboardCornersSB", str(exc))

    if not found or corners is None:
        return CornerDetection(False, None, "findChessboardCornersSB", "complete 11x8 board not found")
    corners = np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2)
    if corners.shape[0] != config.point_count or not np.isfinite(corners).all():
        return CornerDetection(False, None, "findChessboardCornersSB", "invalid corner count or value")
    return CornerDetection(True, corners, "findChessboardCornersSB")


def _board_basis(corners: np.ndarray, config: CalibrationConfig) -> Tuple[np.ndarray, np.ndarray, float]:
    grid = np.asarray(corners, dtype=np.float64).reshape(config.pattern_rows, config.pattern_cols, 2)
    row_steps = grid[:, 1:, :] - grid[:, :-1, :]
    col_steps = grid[1:, :, :] - grid[:-1, :, :]
    row_basis = np.mean(row_steps.reshape(-1, 2), axis=0)
    col_basis = np.mean(col_steps.reshape(-1, 2), axis=0)
    row_norm = float(np.linalg.norm(row_basis))
    col_norm = float(np.linalg.norm(col_basis))
    if row_norm <= 1e-9 or col_norm <= 1e-9:
        raise ValueError("degenerate board basis")
    row_unit = row_basis / row_norm
    col_unit = col_basis / col_norm
    cross = float(row_unit[0] * col_unit[1] - row_unit[1] * col_unit[0])
    return row_unit, col_unit, cross


def validate_ordering(
    left_corners: np.ndarray,
    right_corners: np.ndarray,
    config: CalibrationConfig,
) -> Tuple[bool, str]:
    """Reject a left/right 180-degree ordering disagreement; never auto-flip.

    This relies on the physical stereo heads having the same image orientation.
    A standard symmetric board has no absolute marker-defined origin.
    """
    try:
        left_row, left_col, left_cross = _board_basis(left_corners, config)
        right_row, right_col, right_cross = _board_basis(right_corners, config)
    except ValueError as exc:
        return False, str(exc)

    row_cos = float(np.dot(left_row, right_row))
    col_cos = float(np.dot(left_col, right_col))
    same_handedness = left_cross * right_cross > 0.0
    if row_cos <= 0.0 or col_cos <= 0.0 or not same_handedness:
        return (
            False,
            "ORDERING_MISMATCH: left/right board axes disagree "
            f"(row_cos={row_cos:.3f}, col_cos={col_cos:.3f}, handedness={same_handedness}); "
            "pair rejected, no automatic corner reversal",
        )
    return True, f"ordering consistent (row_cos={row_cos:.3f}, col_cos={col_cos:.3f})"


def detect_stereo_pair(
    left: np.ndarray,
    right: np.ndarray,
    config: CalibrationConfig,
    *,
    exhaustive: bool,
) -> StereoDetection:
    left_result = detect_corners(left, config, exhaustive=exhaustive)
    right_result = detect_corners(right, config, exhaustive=exhaustive)
    if not left_result.found or not right_result.found:
        missing = []
        if not left_result.found:
            missing.append("LEFT NOT FOUND")
        if not right_result.found:
            missing.append("RIGHT NOT FOUND")
        return StereoDetection(left_result, right_result, False, " / ".join(missing))
    ordering_ok, reason = validate_ordering(left_result.corners, right_result.corners, config)
    return StereoDetection(left_result, right_result, ordering_ok, reason)


def draw_detection(image: np.ndarray, result: CornerDetection, config: CalibrationConfig) -> np.ndarray:
    output = image.copy()
    if result.found and result.corners is not None:
        cv2.drawChessboardCorners(output, config.pattern_size, result.corners, True)
    return output


def pose_descriptor(corners: np.ndarray, image_size: Tuple[int, int], config: CalibrationConfig) -> np.ndarray:
    """Compact normalized descriptor used only for duplicate-pose warnings."""
    width, height = image_size
    grid = np.asarray(corners, dtype=np.float64).reshape(config.pattern_rows, config.pattern_cols, 2)
    centroid = np.mean(grid.reshape(-1, 2), axis=0) / np.array([width, height], dtype=np.float64)
    top = np.mean(grid[0], axis=0)
    bottom = np.mean(grid[-1], axis=0)
    left = np.mean(grid[:, 0], axis=0)
    right = np.mean(grid[:, -1], axis=0)
    row_vec = right - left
    col_vec = bottom - top
    row_len = np.linalg.norm(row_vec) / width
    col_len = np.linalg.norm(col_vec) / height
    angle = np.arctan2(row_vec[1], row_vec[0]) / np.pi
    area = abs(row_vec[0] * col_vec[1] - row_vec[1] * col_vec[0]) / (width * height)
    return np.array([centroid[0], centroid[1], row_len, col_len, angle, area], dtype=np.float64)
