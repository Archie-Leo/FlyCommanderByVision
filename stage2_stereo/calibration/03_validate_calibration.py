#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from calibration.calibrator import collect_detected_dataset
from calibration.config import CalibrationConfig
from calibration.dataset import SessionPaths
from calibration.io_utils import assert_yaml_npz_consistent, read_calibration_npz, read_calibration_yaml
from calibration.validation import compute_epipolar_errors, generate_rectified_previews, run_sanity_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Reload and independently validate a saved stereo calibration.")
    parser.add_argument("output", help="Calibration output directory containing calibration.yaml and calibration.npz")
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    try:
        yaml_data = read_calibration_yaml(output / "calibration.yaml")
        npz_data = read_calibration_npz(output / "calibration.npz")
        assert_yaml_npz_consistent(yaml_data, npz_data)
        config = CalibrationConfig(
            camera_device=str(yaml_data["camera_device"]),
            raw_width=int(yaml_data["raw_image_width"]),
            raw_height=int(yaml_data["raw_image_height"]),
            eye_width=int(yaml_data["image_width"]),
            eye_height=int(yaml_data["image_height"]),
            pattern_cols=int(yaml_data["pattern_cols"]),
            pattern_rows=int(yaml_data["pattern_rows"]),
            square_size_mm=float(yaml_data["square_size_mm"]),
            nominal_baseline_mm=float(yaml_data["nominal_baseline_mm"]),
            rectify_alpha=float(yaml_data["rectify_alpha"]),
        )
        session = SessionPaths.from_root(Path(str(yaml_data["source_session"])))
        excluded_pair_indices = np.asarray(yaml_data.get("excluded_pair_indices", []), dtype=np.int32).reshape(-1).tolist()
        dataset = collect_detected_dataset(session, config, excluded_pair_indices)
        if dataset.preflight_errors or len(dataset.pairs) != int(yaml_data["valid_pair_count"]):
            raise ValueError("Source dataset integrity/count changed since calibration; refusing silent validation.")
        _, epipolar = compute_epipolar_errors(
            dataset.left_points,
            dataset.right_points,
            yaml_data["K_left"],
            yaml_data["D_left"],
            yaml_data["K_right"],
            yaml_data["D_right"],
            yaml_data["R1"],
            yaml_data["R2"],
            yaml_data["P1"],
            yaml_data["P2"],
        )
        for label, key in (("mean", "epipolar_mean_px"), ("median", "epipolar_median_px"), ("p95", "epipolar_p95_px"), ("max", "epipolar_max_px")):
            if not np.isclose(epipolar[label], float(yaml_data[key]), rtol=1e-7, atol=1e-7):
                raise ValueError(f"Recomputed {key} differs from saved value.")
        diagnostics = run_sanity_checks(yaml_data, config, dataset.rejected)
        preview_dir = output / "rectified_previews_revalidated"
        generate_rectified_previews(dataset.pairs, yaml_data, preview_dir, config)
        report_lines = [
            "Independent Calibration Validation",
            "==================================",
            "YAML/NPZ consistency: OK",
            "Source capture re-read: OK",
            f"Valid pairs: {len(dataset.pairs)}",
            "Excluded pair indices: "
            + (", ".join(f"pair_{index:04d}" for index in excluded_pair_indices) or "none"),
            f"Epipolar mean/median/p95/max: {epipolar['mean']:.8f} / {epipolar['median']:.8f} / {epipolar['p95']:.8f} / {epipolar['max']:.8f} px",
            "",
        ]
        report_lines.extend(f"[{item.severity}] {item.code}: {item.message}" for item in diagnostics)
        (output / "validation_report.txt").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"VALIDATION COMPLETE: {output / 'validation_report.txt'}")
        print(f"RECTIFIED PREVIEWS : {preview_dir}")
        return 0
    except Exception as exc:
        print(f"VALIDATION FAILED: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
