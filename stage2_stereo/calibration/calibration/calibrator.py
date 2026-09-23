from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .checkerboard import detect_stereo_pair, make_object_points, pose_descriptor
from .config import CalibrationConfig, output_root
from .dataset import PairPaths, SessionPaths, discover_pairs, load_metadata, read_pair
from .io_utils import (
    assert_yaml_npz_consistent,
    read_calibration_npz,
    read_calibration_yaml,
    write_calibration_npz,
    write_calibration_yaml,
)
from .validation import (
    Diagnostic,
    compute_epipolar_errors,
    format_report,
    generate_rectified_previews,
    overall_status,
    run_sanity_checks,
)


class CalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectedDataset:
    pairs: List[PairPaths]
    object_points: List[np.ndarray]
    left_points: List[np.ndarray]
    right_points: List[np.ndarray]
    rejected: List[Dict[str, object]]
    preflight_errors: List[str]
    duplicate_warnings: List[Tuple[int, int, float]]
    excluded_pair_indices: List[int]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_preflight(output_dir: Path, session: SessionPaths, dataset: DetectedDataset, config: CalibrationConfig) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "Stereo Calibration Preflight",
        "============================",
        f"Session: {session.root}",
        f"Usable pairs: {len(dataset.pairs)}",
        "Excluded pair indices: "
        + (", ".join(f"pair_{index:04d}" for index in dataset.excluded_pair_indices) or "none"),
        f"Engineering minimum: {config.min_valid_pairs} (not an OpenCV official threshold)",
        "",
    ]
    if dataset.preflight_errors:
        lines.append("Dataset integrity errors:")
        lines.extend(f"- {item}" for item in dataset.preflight_errors)
        lines.append("")
    if dataset.rejected:
        lines.append("Rejected pairs:")
        lines.extend(f"- {json.dumps(item, ensure_ascii=False, sort_keys=True)}" for item in dataset.rejected)
        lines.append("")
    if dataset.duplicate_warnings:
        lines.append("Near-duplicate pose warnings (engineering heuristic; not removed):")
        lines.extend(f"- pair_{a:04d} ~ pair_{b:04d}, descriptor_distance={distance:.6f}" for a, b, distance in dataset.duplicate_warnings)
    (output_dir / "preflight_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_detected_dataset(
    session: SessionPaths,
    config: CalibrationConfig,
    excluded_pair_indices: Sequence[int] = (),
) -> DetectedDataset:
    discovered, integrity_errors = discover_pairs(session)
    excluded = sorted(set(int(index) for index in excluded_pair_indices))
    if any(index <= 0 for index in excluded):
        integrity_errors.append(f"excluded pair indices must be positive: {excluded}")
    discovered_indices = {pair.index for pair in discovered}
    missing_exclusions = sorted(set(excluded) - discovered_indices)
    if missing_exclusions:
        integrity_errors.append(f"excluded pair indices do not exist on disk: {missing_exclusions}")
    try:
        metadata = load_metadata(session)
        metadata_indices = [int(item["index"]) for item in metadata.get("pairs", [])]
        if len(metadata_indices) != len(set(metadata_indices)):
            integrity_errors.append("duplicate pair index in metadata.json")
        disk_indices = [pair.index for pair in discovered]
        if sorted(metadata_indices) != sorted(disk_indices):
            integrity_errors.append(
                f"metadata/disk pair index mismatch: metadata={sorted(metadata_indices)}, disk={sorted(disk_indices)}"
            )
        expected = {
            "pattern_cols": config.pattern_cols,
            "pattern_rows": config.pattern_rows,
            "square_size_mm": config.square_size_mm,
            "eye_image_size": [config.eye_width, config.eye_height],
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                integrity_errors.append(f"metadata {key} mismatch: expected {value}, got {metadata.get(key)}")
    except (ValueError, KeyError, TypeError) as exc:
        integrity_errors.append(str(exc))
    valid_pairs: List[PairPaths] = []
    object_points: List[np.ndarray] = []
    left_points: List[np.ndarray] = []
    right_points: List[np.ndarray] = []
    rejected: List[Dict[str, object]] = []
    descriptors: List[Tuple[int, np.ndarray]] = []
    template = make_object_points(config)

    for pair in discovered:
        if pair.index in excluded:
            continue
        left, right = read_pair(pair)
        if left is None or right is None:
            rejected.append({"index": pair.index, "reason": "damaged or unreadable image"})
            continue
        if left.shape[:2] != (config.eye_height, config.eye_width) or right.shape[:2] != (config.eye_height, config.eye_width):
            rejected.append({"index": pair.index, "reason": f"wrong image size: left={left.shape}, right={right.shape}"})
            continue
        detection = detect_stereo_pair(left, right, config, exhaustive=True)
        if not detection.valid:
            rejected.append({
                "index": pair.index,
                "reason": detection.ordering_reason,
                "left_found": detection.left.found,
                "right_found": detection.right.found,
            })
            continue
        valid_pairs.append(pair)
        object_points.append(template.copy())
        left_points.append(detection.left.corners)
        right_points.append(detection.right.corners)
        left_desc = pose_descriptor(detection.left.corners, config.eye_size, config)
        right_desc = pose_descriptor(detection.right.corners, config.eye_size, config)
        descriptors.append((pair.index, np.concatenate((left_desc, right_desc))))

    duplicate_warnings: List[Tuple[int, int, float]] = []
    # Descriptor-distance threshold is deliberately only a warning heuristic.
    for i in range(len(descriptors)):
        for j in range(i):
            distance = float(np.linalg.norm(descriptors[i][1] - descriptors[j][1]))
            if distance < 0.025:
                duplicate_warnings.append((descriptors[j][0], descriptors[i][0], distance))
                break
    return DetectedDataset(
        valid_pairs,
        object_points,
        left_points,
        right_points,
        rejected,
        integrity_errors,
        duplicate_warnings,
        excluded,
    )


def calibrate_from_points(
    object_points: Sequence[np.ndarray],
    left_points: Sequence[np.ndarray],
    right_points: Sequence[np.ndarray],
    image_size: Tuple[int, int],
    config: CalibrationConfig,
) -> Dict[str, object]:
    if not (len(object_points) == len(left_points) == len(right_points)):
        raise CalibrationError("Object/left/right view counts differ.")
    if len(object_points) < config.min_valid_pairs:
        raise CalibrationError(
            f"Only {len(object_points)} valid pair(s); engineering minimum is {config.min_valid_pairs}. "
            "Collect more varied views (20-30 is guidance, not an OpenCV official requirement)."
        )

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 100, 1e-9)
    try:
        left_result = cv2.calibrateCameraExtended(
            list(object_points), list(left_points), image_size, None, None, flags=0, criteria=criteria
        )
        right_result = cv2.calibrateCameraExtended(
            list(object_points), list(right_points), image_size, None, None, flags=0, criteria=criteria
        )
    except cv2.error as exc:
        raise CalibrationError(f"Monocular calibration failed: {exc}") from exc

    left_rms, K_left, D_left, left_rvecs, left_tvecs, left_std_i, left_std_e, left_per_view = left_result
    right_rms, K_right, D_right, right_rvecs, right_tvecs, right_std_i, right_std_e, right_per_view = right_result

    initial_R = np.eye(3, dtype=np.float64)
    initial_T = np.zeros((3, 1), dtype=np.float64)
    try:
        stereo_result = cv2.stereoCalibrateExtended(
            list(object_points),
            list(left_points),
            list(right_points),
            K_left.copy(),
            D_left.copy(),
            K_right.copy(),
            D_right.copy(),
            image_size,
            initial_R,
            initial_T,
            flags=cv2.CALIB_FIX_INTRINSIC,
            criteria=criteria,
        )
    except cv2.error as exc:
        raise CalibrationError(f"Stereo calibration failed: {exc}") from exc

    (
        stereo_rms,
        K_left_fixed,
        D_left_fixed,
        K_right_fixed,
        D_right_fixed,
        R,
        T,
        E,
        F,
        stereo_rvecs,
        stereo_tvecs,
        stereo_per_view,
    ) = stereo_result
    R1, R2, P1, P2, Q, roi1, roi2 = cv2.stereoRectify(
        K_left_fixed,
        D_left_fixed,
        K_right_fixed,
        D_right_fixed,
        image_size,
        R,
        T,
        flags=cv2.CALIB_ZERO_DISPARITY,
        alpha=config.rectify_alpha,
        newImageSize=image_size,
    )
    _, epipolar = compute_epipolar_errors(
        left_points, right_points, K_left_fixed, D_left_fixed, K_right_fixed, D_right_fixed, R1, R2, P1, P2
    )
    return {
        "K_left": K_left_fixed,
        "D_left": D_left_fixed,
        "K_right": K_right_fixed,
        "D_right": D_right_fixed,
        "R": R,
        "T": T,
        "E": E,
        "F": F,
        "R1": R1,
        "R2": R2,
        "P1": P1,
        "P2": P2,
        "Q": Q,
        "validPixROI1": np.asarray(roi1, dtype=np.int32).reshape(1, 4),
        "validPixROI2": np.asarray(roi2, dtype=np.int32).reshape(1, 4),
        "left_rms": float(left_rms),
        "right_rms": float(right_rms),
        "stereo_rms": float(stereo_rms),
        "baseline_mm": float(np.linalg.norm(T)),
        "left_per_view_errors": np.asarray(left_per_view, dtype=np.float64).reshape(-1, 1),
        "right_per_view_errors": np.asarray(right_per_view, dtype=np.float64).reshape(-1, 1),
        "stereo_per_view_errors": np.asarray(stereo_per_view, dtype=np.float64),
        "left_intrinsic_stddev": np.asarray(left_std_i, dtype=np.float64),
        "right_intrinsic_stddev": np.asarray(right_std_i, dtype=np.float64),
        "epipolar_mean_px": epipolar["mean"],
        "epipolar_median_px": epipolar["median"],
        "epipolar_p95_px": epipolar["p95"],
        "epipolar_max_px": epipolar["max"],
    }


def run_calibration(
    session: SessionPaths,
    config: Optional[CalibrationConfig] = None,
    output_base: Optional[Path] = None,
    excluded_pair_indices: Sequence[int] = (),
    output_name: Optional[str] = None,
) -> Path:
    config = config or CalibrationConfig()
    output_dir = (output_base or output_root()) / (output_name or session.root.name)
    if any((output_dir / name).exists() for name in ("calibration.yaml", "calibration.npz", "calibration_report.txt")):
        raise CalibrationError(f"Refusing to overwrite an existing calibration result: {output_dir}")
    dataset = collect_detected_dataset(session, config, excluded_pair_indices)
    _write_preflight(output_dir, session, dataset, config)
    if dataset.preflight_errors:
        raise CalibrationError(
            "Dataset pairing/filename integrity errors found; calibration refused: "
            + "; ".join(dataset.preflight_errors)
            + ". See "
            f"{output_dir / 'preflight_report.txt'}"
        )
    if len(dataset.pairs) < config.min_valid_pairs:
        raise CalibrationError(
            f"Only {len(dataset.pairs)} valid pair(s) after disk revalidation; need at least "
            f"the configured engineering minimum {config.min_valid_pairs}. See preflight_report.txt."
        )

    data = calibrate_from_points(dataset.object_points, dataset.left_points, dataset.right_points, config.eye_size, config)
    data.update(
        {
            "schema_version": "1",
            "camera_model": "opencv_pinhole_radial_tangential_5_coeff",
            "corner_detector": "findChessboardCornersSB",
            "image_width": config.eye_width,
            "image_height": config.eye_height,
            "raw_image_width": config.raw_width,
            "raw_image_height": config.raw_height,
            "pattern_cols": config.pattern_cols,
            "pattern_rows": config.pattern_rows,
            "square_size_mm": config.square_size_mm,
            "nominal_baseline_mm": config.nominal_baseline_mm,
            "rectify_alpha": config.rectify_alpha,
            "valid_pair_count": len(dataset.pairs),
            "valid_pair_indices": np.asarray([pair.index for pair in dataset.pairs], dtype=np.int32).reshape(-1, 1),
            "excluded_pair_indices": np.asarray(dataset.excluded_pair_indices, dtype=np.int32),
            "excluded_pair_count": len(dataset.excluded_pair_indices),
            "opencv_version": cv2.__version__,
            "numpy_version": np.__version__,
            "calibration_datetime_utc": _utc_now(),
            "camera_device": config.camera_device,
            "source_session": str(session.root.resolve()),
        }
    )
    diagnostics = run_sanity_checks(data, config, dataset.rejected)
    for a, b, distance in dataset.duplicate_warnings:
        diagnostics.append(Diagnostic("WARNING", "NEAR_DUPLICATE_POSE", f"pair_{a:04d} and pair_{b:04d}, descriptor_distance={distance:.6f}; retained", "engineering descriptor threshold"))
    data["result_status"] = overall_status(diagnostics)
    data["diagnostics_json"] = json.dumps([item.as_dict() for item in diagnostics], ensure_ascii=False)
    data["rejected_pairs_json"] = json.dumps(dataset.rejected, ensure_ascii=False)

    yaml_path = output_dir / "calibration.yaml"
    npz_path = output_dir / "calibration.npz"
    write_calibration_yaml(yaml_path, data)
    write_calibration_npz(npz_path, data)
    yaml_check = read_calibration_yaml(yaml_path)
    npz_check = read_calibration_npz(npz_path)
    assert_yaml_npz_consistent(yaml_check, npz_check)
    generate_rectified_previews(dataset.pairs, data, output_dir / "rectified_previews", config)
    report = format_report(data, diagnostics, dataset.rejected)
    (output_dir / "calibration_report.txt").write_text(report, encoding="utf-8")
    return output_dir


def print_result_summary(output_dir: Path) -> None:
    data = read_calibration_yaml(output_dir / "calibration.yaml")
    print("\nCALIBRATION RESULT:", data["result_status"])
    print(f"LEFT RMS             : {float(data['left_rms']):.6f} px")
    print(f"RIGHT RMS            : {float(data['right_rms']):.6f} px")
    print(f"Stereo RMS           : {float(data['stereo_rms']):.6f} px")
    print(f"Nominal baseline     : ~{float(data['nominal_baseline_mm']):.2f} mm")
    print(f"Estimated baseline   : {float(data['baseline_mm']):.3f} mm")
    print(f"Vertical epi p95     : {float(data['epipolar_p95_px']):.4f} px")
    print(f"Report               : {output_dir / 'calibration_report.txt'}")
    print(f"Rectified previews   : {output_dir / 'rectified_previews'}")
