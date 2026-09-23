from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any, Dict

import cv2
import numpy as np


MATRIX_KEYS = (
    "K_left",
    "D_left",
    "K_right",
    "D_right",
    "R",
    "T",
    "E",
    "F",
    "R1",
    "R2",
    "P1",
    "P2",
    "Q",
    "validPixROI1",
    "validPixROI2",
    "left_per_view_errors",
    "right_per_view_errors",
    "stereo_per_view_errors",
    "valid_pair_indices",
    "left_intrinsic_stddev",
    "right_intrinsic_stddev",
)

SCALAR_KEYS = (
    "image_width",
    "image_height",
    "raw_image_width",
    "raw_image_height",
    "pattern_cols",
    "pattern_rows",
    "square_size_mm",
    "nominal_baseline_mm",
    "baseline_mm",
    "left_rms",
    "right_rms",
    "stereo_rms",
    "epipolar_mean_px",
    "epipolar_median_px",
    "epipolar_p95_px",
    "epipolar_max_px",
    "rectify_alpha",
    "valid_pair_count",
)

OPTIONAL_SCALAR_KEYS = ("excluded_pair_count",)
OPTIONAL_INDEX_LIST_KEYS = ("excluded_pair_indices",)

STRING_KEYS = (
    "schema_version",
    "camera_model",
    "corner_detector",
    "opencv_version",
    "numpy_version",
    "calibration_datetime_utc",
    "camera_device",
    "source_session",
    "result_status",
)

REQUIRED_KEYS = set(MATRIX_KEYS) | set(SCALAR_KEYS) | set(STRING_KEYS)


def result_to_dict(result: Any) -> Dict[str, Any]:
    if is_dataclass(result):
        return asdict(result)
    return dict(result)


def write_calibration_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.yaml")
    fs = cv2.FileStorage(str(temporary), cv2.FILE_STORAGE_WRITE | cv2.FILE_STORAGE_FORMAT_YAML)
    if not fs.isOpened():
        raise IOError(f"Could not open YAML for writing: {temporary}")
    try:
        for key in STRING_KEYS:
            if key in data:
                fs.write(key, str(data[key]))
        for key in SCALAR_KEYS + OPTIONAL_SCALAR_KEYS:
            if key in data:
                value = data[key]
                fs.write(key, int(value) if isinstance(value, (int, np.integer)) else float(value))
        for key in MATRIX_KEYS:
            if key in data:
                fs.write(key, np.asarray(data[key]))
        for key in OPTIONAL_INDEX_LIST_KEYS:
            values = np.asarray(data.get(key, []), dtype=np.int32).reshape(-1).tolist()
            fs.write(key, json.dumps(values))
        fs.write("diagnostics_json", str(data.get("diagnostics_json", "[]")))
        fs.write("rejected_pairs_json", str(data.get("rejected_pairs_json", "[]")))
    finally:
        fs.release()
    temporary.replace(path)


def read_calibration_yaml(path: Path) -> Dict[str, Any]:
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise ValueError(f"Could not open calibration YAML: {path}")
    data: Dict[str, Any] = {}
    try:
        for key in STRING_KEYS:
            node = fs.getNode(key)
            if not node.empty():
                data[key] = node.string()
        for key in SCALAR_KEYS + OPTIONAL_SCALAR_KEYS:
            node = fs.getNode(key)
            if not node.empty():
                data[key] = node.real()
        for key in MATRIX_KEYS:
            node = fs.getNode(key)
            if not node.empty():
                data[key] = node.mat()
        for key in OPTIONAL_INDEX_LIST_KEYS:
            node = fs.getNode(key)
            if not node.empty():
                data[key] = np.asarray(json.loads(node.string()), dtype=np.int32)
        for key in ("diagnostics_json", "rejected_pairs_json"):
            node = fs.getNode(key)
            if not node.empty():
                data[key] = node.string()
    finally:
        fs.release()
    missing = sorted(REQUIRED_KEYS - set(data))
    if missing:
        raise ValueError(f"Calibration YAML is missing required keys: {', '.join(missing)}")
    data.setdefault("excluded_pair_indices", np.asarray([], dtype=np.int32))
    data.setdefault("excluded_pair_count", 0.0)
    return data


def write_calibration_npz(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {}
    for key, value in data.items():
        if key.endswith("_json"):
            payload[key] = np.asarray(str(value))
        elif isinstance(value, str):
            payload[key] = np.asarray(value)
        else:
            payload[key] = np.asarray(value)
    temporary = path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, **payload)
    temporary.replace(path)


def read_calibration_npz(path: Path) -> Dict[str, Any]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            data = {key: archive[key].copy() for key in archive.files}
    except Exception as exc:
        raise ValueError(f"Could not read calibration NPZ: {path}: {exc}") from exc
    missing = sorted(REQUIRED_KEYS - set(data))
    if missing:
        raise ValueError(f"Calibration NPZ is missing required keys: {', '.join(missing)}")
    data.setdefault("excluded_pair_indices", np.asarray([], dtype=np.int32))
    data.setdefault("excluded_pair_count", np.asarray(0, dtype=np.int32))
    return data


def assert_yaml_npz_consistent(yaml_data: Dict[str, Any], npz_data: Dict[str, Any]) -> None:
    for key in MATRIX_KEYS:
        if not np.allclose(np.asarray(yaml_data[key]), np.asarray(npz_data[key]), rtol=1e-10, atol=1e-10):
            raise ValueError(f"YAML/NPZ mismatch for {key}")
    for key in OPTIONAL_INDEX_LIST_KEYS:
        if not np.array_equal(
            np.asarray(yaml_data.get(key, []), dtype=np.int32).reshape(-1),
            np.asarray(npz_data.get(key, []), dtype=np.int32).reshape(-1),
        ):
            raise ValueError(f"YAML/NPZ mismatch for {key}")
    for key in ("baseline_mm", "left_rms", "right_rms", "stereo_rms"):
        if not np.isclose(float(yaml_data[key]), float(npz_data[key]), rtol=1e-10, atol=1e-10):
            raise ValueError(f"YAML/NPZ mismatch for {key}")
