from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import cv2
import numpy as np

from .config import CalibrationConfig
from .dataset import PairPaths, read_pair


SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "SUSPICIOUS": 2}


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    threshold_type: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "threshold_type": self.threshold_type,
        }


def overall_status(diagnostics: Sequence[Diagnostic]) -> str:
    return max((item.severity for item in diagnostics), key=lambda value: SEVERITY_RANK[value], default="INFO")


def compute_epipolar_errors(
    left_points: Sequence[np.ndarray],
    right_points: Sequence[np.ndarray],
    K_left: np.ndarray,
    D_left: np.ndarray,
    K_right: np.ndarray,
    D_right: np.ndarray,
    R1: np.ndarray,
    R2: np.ndarray,
    P1: np.ndarray,
    P2: np.ndarray,
) -> Tuple[np.ndarray, Dict[str, float]]:
    all_errors: List[np.ndarray] = []
    for left, right in zip(left_points, right_points):
        left_rect = cv2.undistortPoints(np.asarray(left, np.float32), K_left, D_left, R=R1, P=P1)
        right_rect = cv2.undistortPoints(np.asarray(right, np.float32), K_right, D_right, R=R2, P=P2)
        errors = np.abs(left_rect.reshape(-1, 2)[:, 1] - right_rect.reshape(-1, 2)[:, 1])
        all_errors.append(errors.astype(np.float64))
    values = np.concatenate(all_errors) if all_errors else np.empty((0,), dtype=np.float64)
    if values.size == 0:
        raise ValueError("Cannot compute epipolar statistics without corresponding points.")
    stats = {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }
    return values, stats


def per_view_outliers(errors: np.ndarray) -> Tuple[List[int], float, float]:
    values = np.asarray(errors, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return [], float("nan"), float("nan")
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    robust_sigma = 1.4826 * mad
    threshold = median + 3.0 * robust_sigma
    if robust_sigma <= 1e-12:
        return [], median, threshold
    return np.flatnonzero(values > threshold).astype(int).tolist(), median, threshold


def _max_normalized_radius(K: np.ndarray, image_size: Tuple[int, int]) -> float:
    width, height = image_size
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    corners = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    return max(np.hypot((x - cx) / fx, (y - cy) / fy) for x, y in corners)


def radial_monotonic(K: np.ndarray, D: np.ndarray, image_size: Tuple[int, int]) -> Tuple[bool, float]:
    coeffs = np.asarray(D, dtype=np.float64).reshape(-1)
    k1 = coeffs[0] if coeffs.size > 0 else 0.0
    k2 = coeffs[1] if coeffs.size > 1 else 0.0
    k3 = coeffs[4] if coeffs.size > 4 else 0.0
    max_radius = _max_normalized_radius(K, image_size)
    radius = np.linspace(0.0, max_radius, 2048)
    r2 = radius * radius
    derivative = 1.0 + 3.0 * k1 * r2 + 5.0 * k2 * r2**2 + 7.0 * k3 * r2**3
    return bool(np.all(derivative > 0.0)), float(np.min(derivative))


def run_sanity_checks(
    data: Dict[str, object],
    config: CalibrationConfig,
    rejected_pairs: Sequence[Dict[str, object]],
) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    R = np.asarray(data["R"], dtype=np.float64)
    orthogonality = float(np.linalg.norm(R @ R.T - np.eye(3), ord="fro"))
    determinant = float(np.linalg.det(R))
    if orthogonality > 1e-3 or abs(determinant - 1.0) > 1e-3:
        diagnostics.append(Diagnostic("SUSPICIOUS", "ROTATION_MATRIX", f"R is not sufficiently orthonormal: fro_error={orthogonality:.6g}, det={determinant:.6g}", "mathematical property; 1e-3 tolerance is engineering"))
    else:
        diagnostics.append(Diagnostic("INFO", "ROTATION_MATRIX", f"R sanity: fro_error={orthogonality:.6g}, det={determinant:.6g}", "mathematical property; numerical tolerance is engineering"))

    baseline = float(data["baseline_mm"])
    relative = abs(baseline - config.nominal_baseline_mm) / config.nominal_baseline_mm
    baseline_severity = "SUSPICIOUS" if relative > 0.50 else "WARNING" if relative > 0.30 else "INFO"
    diagnostics.append(Diagnostic(baseline_severity, "BASELINE", f"Estimated {baseline:.3f} mm vs nominal ~{config.nominal_baseline_mm:.1f} mm ({relative * 100:.1f}% difference)", "engineering sanity check; nominal value is not an optimization constraint"))

    for side in ("left", "right"):
        K = np.asarray(data[f"K_{side}"], dtype=np.float64)
        D = np.asarray(data[f"D_{side}"], dtype=np.float64)
        fx, fy, cx, cy = float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])
        width, height = config.eye_size
        plausible = (
            np.isfinite(K).all()
            and np.isfinite(D).all()
            and 0.1 * width < fx < 10.0 * width
            and 0.1 * width < fy < 10.0 * width
            and 0.0 <= cx < width
            and 0.0 <= cy < height
        )
        diagnostics.append(Diagnostic("INFO" if plausible else "SUSPICIOUS", f"INTRINSICS_{side.upper()}", f"fx={fx:.3f}, fy={fy:.3f}, cx={cx:.3f}, cy={cy:.3f}, D={D.reshape(-1).tolist()}", "positive focal length is mathematical; numeric bounds are engineering"))
        coeffs = D.reshape(-1)
        extreme_distortion = bool(np.any(np.abs(coeffs) > 5.0))
        if coeffs.size >= 4 and (abs(float(coeffs[2])) > 0.1 or abs(float(coeffs[3])) > 0.1):
            extreme_distortion = True
        if extreme_distortion:
            diagnostics.append(Diagnostic("WARNING", f"DISTORTION_MAGNITUDE_{side.upper()}", f"large distortion coefficient(s): {coeffs.tolist()}", "engineering magnitude check; inspect edge residuals and rectified previews"))
        monotonic, minimum_derivative = radial_monotonic(K, D, config.eye_size)
        diagnostics.append(Diagnostic("INFO" if monotonic else "SUSPICIOUS", f"RADIAL_MONOTONIC_{side.upper()}", f"minimum sampled radial derivative={minimum_derivative:.6g}", "OpenCV states real-lens radial distortion should be monotonic; sampling range/tolerance is engineering"))

    p95 = float(data["epipolar_p95_px"])
    epi_severity = "SUSPICIOUS" if p95 > 2.0 else "WARNING" if p95 > 1.0 else "INFO"
    diagnostics.append(Diagnostic(epi_severity, "EPIPOLAR_VERTICAL", f"rectified vertical error p95={p95:.4f} px, max={float(data['epipolar_max_px']):.4f} px", "engineering thresholds; OpenCV defines no universal pass line"))

    for side in ("left", "right", "stereo"):
        key = f"{side}_per_view_errors"
        view_errors = np.asarray(data[key], dtype=np.float64)
        if view_errors.ndim == 2 and view_errors.shape[1] > 1:
            view_errors = np.sqrt(np.mean(np.square(view_errors), axis=1))
        outliers, median, threshold = per_view_outliers(view_errors)
        if outliers:
            valid_ids = np.asarray(data["valid_pair_indices"]).reshape(-1).astype(int)
            pair_ids = [int(valid_ids[index]) for index in outliers]
            diagnostics.append(Diagnostic("WARNING", f"PER_VIEW_OUTLIER_{side.upper()}", f"pairs={pair_ids}, robust threshold={threshold:.4f}, median={median:.4f}", "median + 3*1.4826*MAD engineering outlier rule; views are not auto-deleted"))

    if rejected_pairs:
        diagnostics.append(Diagnostic("WARNING", "REJECTED_PAIRS", f"{len(rejected_pairs)} saved pair(s) rejected during disk revalidation; see rejected_pairs_json", "data integrity warning"))
    return diagnostics


def build_rectify_maps(data: Dict[str, object], config: CalibrationConfig):
    size = config.eye_size
    map_lx, map_ly = cv2.initUndistortRectifyMap(
        np.asarray(data["K_left"]), np.asarray(data["D_left"]), np.asarray(data["R1"]), np.asarray(data["P1"]), size, cv2.CV_32FC1
    )
    map_rx, map_ry = cv2.initUndistortRectifyMap(
        np.asarray(data["K_right"]), np.asarray(data["D_right"]), np.asarray(data["R2"]), np.asarray(data["P2"]), size, cv2.CV_32FC1
    )
    return map_lx, map_ly, map_rx, map_ry


def generate_rectified_previews(
    pairs: Sequence[PairPaths],
    data: Dict[str, object],
    output_dir: Path,
    config: CalibrationConfig,
) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    map_lx, map_ly, map_rx, map_ry = build_rectify_maps(data, config)
    if not pairs:
        return []
    selected = np.linspace(0, len(pairs) - 1, min(config.max_rectified_previews, len(pairs)), dtype=int)
    paths: List[Path] = []
    for position in np.unique(selected):
        pair = pairs[int(position)]
        left, right = read_pair(pair)
        if left is None or right is None:
            continue
        left_rect = cv2.remap(left, map_lx, map_ly, cv2.INTER_LINEAR)
        right_rect = cv2.remap(right, map_rx, map_ry, cv2.INTER_LINEAR)
        canvas = np.hstack((left_rect, right_rect))
        for y in range(0, canvas.shape[0], config.epipolar_line_step_px):
            cv2.line(canvas, (0, y), (canvas.shape[1] - 1, y), (0, 255, 0), 1, cv2.LINE_AA)
        cv2.line(canvas, (config.eye_width, 0), (config.eye_width, config.eye_height - 1), (0, 255, 255), 2)
        cv2.putText(canvas, f"PAIR {pair.index:04d} RECTIFIED", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
        path = output_dir / f"rectified_preview_{pair.index:04d}.png"
        if not cv2.imwrite(str(path), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise IOError(f"Failed to save rectified preview: {path}")
        paths.append(path)
    return paths


def format_report(data: Dict[str, object], diagnostics: Sequence[Diagnostic], rejected_pairs: Sequence[Dict[str, object]]) -> str:
    def matrix(name: str) -> str:
        return np.array2string(np.asarray(data[name]), precision=10, suppress_small=False)

    excluded_ids = np.asarray(data.get("excluded_pair_indices", []), dtype=np.int32).reshape(-1).tolist()
    excluded_label = ", ".join(f"pair_{index:04d}" for index in excluded_ids) or "none"
    lines = [
        "Stereo Camera Calibration Report",
        "================================",
        "",
        f"Result status        : {data['result_status']}",
        "Status semantics     : INFO/WARNING/SUSPICIOUS are engineering diagnostics, not OpenCV official pass thresholds.",
        "",
        f"Calibration UTC      : {data['calibration_datetime_utc']}",
        f"Source session       : {data['source_session']}",
        f"Camera               : {data['camera_device']}",
        f"Raw stereo           : {int(data['raw_image_width'])} x {int(data['raw_image_height'])} MJPEG Side-by-Side",
        f"Per eye              : {int(data['image_width'])} x {int(data['image_height'])}",
        f"Camera model         : {data['camera_model']}",
        f"Chessboard           : {int(data['pattern_cols'])} x {int(data['pattern_rows'])} inner corners",
        f"Square               : {float(data['square_size_mm']):.3f} mm",
        f"Valid pairs          : {int(data['valid_pair_count'])}",
        f"Excluded pairs       : {excluded_label}",
        f"Rejected disk pairs  : {len(rejected_pairs)}",
        "",
        f"LEFT RMS             : {float(data['left_rms']):.8f} px",
        f"RIGHT RMS            : {float(data['right_rms']):.8f} px",
        f"Stereo RMS           : {float(data['stereo_rms']):.8f} px",
        f"Nominal baseline     : ~{float(data['nominal_baseline_mm']):.3f} mm (sanity reference only)",
        f"Estimated baseline   : {float(data['baseline_mm']):.8f} mm",
        "",
        "Epipolar vertical error (rectified detected corners)",
        f"  mean               : {float(data['epipolar_mean_px']):.8f} px",
        f"  median             : {float(data['epipolar_median_px']):.8f} px",
        f"  p95                : {float(data['epipolar_p95_px']):.8f} px",
        f"  max                : {float(data['epipolar_max_px']):.8f} px",
        "",
        "Matrices",
        "--------",
    ]
    for name in ("K_left", "D_left", "K_right", "D_right", "R", "T", "E", "F", "R1", "R2", "P1", "P2", "Q"):
        lines.extend((f"{name}:", matrix(name), ""))
    lines.extend(("Per-view RMS", "------------"))
    ids = np.asarray(data["valid_pair_indices"]).reshape(-1).astype(int)
    left_errors = np.asarray(data["left_per_view_errors"]).reshape(-1)
    right_errors = np.asarray(data["right_per_view_errors"]).reshape(-1)
    stereo_errors = np.asarray(data["stereo_per_view_errors"])
    if stereo_errors.ndim == 2 and stereo_errors.shape[1] > 1:
        stereo_errors = np.sqrt(np.mean(np.square(stereo_errors), axis=1))
    stereo_errors = stereo_errors.reshape(-1)
    for index, left, right, stereo in zip(ids, left_errors, right_errors, stereo_errors):
        lines.append(f"pair_{index:04d}: left={left:.8f}, right={right:.8f}, stereo={stereo:.8f}")
    lines.extend(("", "Diagnostics", "-----------"))
    for item in diagnostics:
        lines.append(f"[{item.severity}] {item.code}: {item.message}")
        lines.append(f"  Basis: {item.threshold_type}")
    if rejected_pairs:
        lines.extend(("", "Rejected pairs (not silently deleted)", "-------------------------------------"))
        for item in rejected_pairs:
            lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    lines.extend(("", f"OpenCV version       : {data['opencv_version']}", f"NumPy version        : {data['numpy_version']}", ""))
    return "\n".join(lines)
