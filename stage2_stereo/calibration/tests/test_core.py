from __future__ import annotations

import tempfile
import unittest
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from calibration.calibrator import CalibrationError, calibrate_from_points, run_calibration
from calibration.capture import CaptureApp, open_camera, split_frame
from calibration.checkerboard import detect_stereo_pair, make_object_points, validate_ordering
from calibration.config import CalibrationConfig
from calibration.dataset import (
    SessionPaths,
    active_pair_indices,
    create_session,
    delete_last_pair,
    discover_pairs,
    load_metadata,
    save_pair,
)
from calibration.io_utils import (
    assert_yaml_npz_consistent,
    read_calibration_npz,
    read_calibration_yaml,
    write_calibration_npz,
    write_calibration_yaml,
)


def chessboard_image(config: CalibrationConfig, square_px: int = 70, margin: int = 80) -> np.ndarray:
    square_cols = config.pattern_cols + 1
    square_rows = config.pattern_rows + 1
    image = np.full((square_rows * square_px + 2 * margin, square_cols * square_px + 2 * margin), 255, np.uint8)
    for row in range(square_rows):
        for col in range(square_cols):
            if (row + col) % 2 == 0:
                y0, x0 = margin + row * square_px, margin + col * square_px
                image[y0 : y0 + square_px, x0 : x0 + square_px] = 0
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


def synthetic_views(config: CalibrationConfig, count: int = 18):
    rng = np.random.default_rng(1234)
    object_template = make_object_points(config).astype(np.float64)
    K_left = np.array([[620.0, 0.0, 640.0], [0.0, 618.0, 480.0], [0.0, 0.0, 1.0]], np.float64)
    K_right = np.array([[622.0, 0.0, 638.0], [0.0, 619.0, 482.0], [0.0, 0.0, 1.0]], np.float64)
    D_left = np.array([-0.02, 0.005, 0.0005, -0.0003, -0.001], np.float64)
    D_right = np.array([-0.018, 0.004, -0.0004, 0.0002, -0.0008], np.float64)
    R_lr, _ = cv2.Rodrigues(np.array([0.002, -0.004, 0.001], np.float64))
    T_lr = np.array([[-65.0], [0.4], [-0.3]], np.float64)
    objects, lefts, rights = [], [], []
    for index in range(count):
        yaw = -0.25 + 0.5 * (index % 6) / 5.0
        pitch = -0.18 + 0.36 * ((index // 3) % 6) / 5.0
        roll = -0.12 + 0.24 * (index % 4) / 3.0
        rvec_left = np.array([pitch, yaw, roll], np.float64)
        R_board_left, _ = cv2.Rodrigues(rvec_left)
        t_left = np.array(
            [[-210.0 + 70.0 * (index % 7)], [-120.0 + 55.0 * (index % 5)], [950.0 + 55.0 * index]],
            np.float64,
        )
        R_board_right = R_lr @ R_board_left
        rvec_right, _ = cv2.Rodrigues(R_board_right)
        t_right = R_lr @ t_left + T_lr
        left, _ = cv2.projectPoints(object_template, rvec_left, t_left, K_left, D_left)
        right, _ = cv2.projectPoints(object_template, rvec_right, t_right, K_right, D_right)
        left += rng.normal(0.0, 0.04, left.shape).astype(np.float32)
        right += rng.normal(0.0, 0.04, right.shape).astype(np.float32)
        objects.append(object_template.astype(np.float32))
        lefts.append(left.astype(np.float32))
        rights.append(right.astype(np.float32))
    return objects, lefts, rights


def render_board(config: CalibrationConfig, K: np.ndarray, D: np.ndarray, rvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    image = np.full((config.eye_height, config.eye_width), 150, np.uint8)

    def project(vertices):
        points, _ = cv2.projectPoints(np.asarray(vertices, np.float64), rvec, tvec, K, D)
        return np.rint(points.reshape(-1, 2)).astype(np.int32)

    cv2.fillConvexPoly(
        image,
        project([[-67.5, -67.5, 0.0], [517.5, -67.5, 0.0], [517.5, 382.5, 0.0], [-67.5, 382.5, 0.0]]),
        255,
    )
    for row in range(config.pattern_rows + 1):
        for col in range(config.pattern_cols + 1):
            x0, y0 = (col - 1) * config.square_size_mm, (row - 1) * config.square_size_mm
            vertices = [[x0, y0, 0.0], [x0 + 45.0, y0, 0.0], [x0 + 45.0, y0 + 45.0, 0.0], [x0, y0 + 45.0, 0.0]]
            cv2.fillConvexPoly(image, project(vertices), 0 if (row + col) % 2 == 0 else 255)
    image = cv2.GaussianBlur(image, (3, 3), 0.5)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


class CheckerboardTests(unittest.TestCase):
    def setUp(self):
        self.config = CalibrationConfig()

    def test_object_points_use_mm_and_correct_order(self):
        points = make_object_points(self.config)
        self.assertEqual(points.shape, (88, 3))
        np.testing.assert_array_equal(points[0], [0.0, 0.0, 0.0])
        np.testing.assert_array_equal(points[1], [45.0, 0.0, 0.0])
        np.testing.assert_array_equal(points[11], [0.0, 45.0, 0.0])

    def test_sb_detects_both_and_rejects_one_side(self):
        board = chessboard_image(self.config)
        config = replace(self.config, eye_width=board.shape[1], eye_height=board.shape[0])
        both = detect_stereo_pair(board, board.copy(), config, exhaustive=True)
        self.assertTrue(both.valid, both.ordering_reason)
        blank = np.full_like(board, 127)
        one = detect_stereo_pair(board, blank, config, exhaustive=True)
        self.assertFalse(one.valid)
        self.assertTrue(one.left.found)
        self.assertFalse(one.right.found)

    def test_ordering_mismatch_is_rejected_without_autoflip(self):
        cols, rows = self.config.pattern_size
        grid = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2).astype(np.float32).reshape(-1, 1, 2)
        ok, _ = validate_ordering(grid, grid.copy(), self.config)
        self.assertTrue(ok)
        ok, reason = validate_ordering(grid, grid[::-1].copy(), self.config)
        self.assertFalse(ok)
        self.assertIn("ORDERING_MISMATCH", reason)


class DatasetTests(unittest.TestCase):
    def test_session_save_pair_and_recoverable_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(CalibrationConfig(), raw_width=24, raw_height=10, eye_width=12, eye_height=10)
            session = create_session(config, Path(temporary))
            full = np.zeros((10, 24, 3), np.uint8)
            left, right = full[:, :12].copy(), full[:, 12:].copy()
            save_pair(session, 1, full, left, right, config, monotonic_ns=123)
            self.assertEqual(active_pair_indices(session), [1])
            pairs, errors = discover_pairs(session)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(errors, [])
            self.assertEqual(delete_last_pair(session), 1)
            self.assertEqual(active_pair_indices(session), [])
            metadata = load_metadata(session)
            self.assertEqual(len(metadata["deleted_pairs"]), 1)
            self.assertTrue(any(session.deleted.rglob("pair_0001_left.png")))

    def test_split_requires_exact_raw_shape(self):
        config = CalibrationConfig()
        frame = np.zeros((960, 2560, 3), np.uint8)
        left, right = split_frame(frame, config)
        self.assertEqual(left.shape, (960, 1280, 3))
        self.assertEqual(right.shape, (960, 1280, 3))
        with self.assertRaises(ValueError):
            split_frame(np.zeros((480, 1280, 3), np.uint8), config)

    def test_empty_session_calibration_refuses_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = CalibrationConfig()
            session = create_session(config, base / "captures")
            with self.assertRaisesRegex(CalibrationError, "Only 0 valid pair"):
                run_calibration(session, config, base / "output")
            self.assertTrue((base / "output" / session.root.name / "preflight_report.txt").is_file())

    def test_capture_save_rejects_when_only_left_is_detected(self):
        base_config = CalibrationConfig()
        board = chessboard_image(base_config)
        height, width = board.shape[:2]
        config = replace(base_config, raw_width=2 * width, raw_height=height, eye_width=width, eye_height=height)
        blank = np.full_like(board, 127)
        full = np.hstack((board, blank))
        with tempfile.TemporaryDirectory() as temporary:
            app = CaptureApp(config)
            app.session = create_session(config, Path(temporary))
            app._save_current(full, board, blank, monotonic_ns=1)
            self.assertEqual(active_pair_indices(app.session), [])


class CalibrationTests(unittest.TestCase):
    def test_opencv_410_required_apis_exist(self):
        for name in ("findChessboardCornersSB", "calibrateCameraExtended", "stereoCalibrateExtended", "stereoRectify", "initUndistortRectifyMap"):
            self.assertTrue(hasattr(cv2, name), name)

    def test_synthetic_geometry_and_yaml_npz_roundtrip(self):
        config = CalibrationConfig(min_valid_pairs=12)
        objects, lefts, rights = synthetic_views(config)
        data = calibrate_from_points(objects, lefts, rights, config.eye_size, config)
        self.assertLess(abs(float(data["baseline_mm"]) - 65.0), 3.0)
        self.assertLess(float(data["epipolar_p95_px"]), 0.5)
        data.update(
            {
                "schema_version": "1",
                "camera_model": "opencv_pinhole_radial_tangential_5_coeff",
                "corner_detector": "findChessboardCornersSB",
                "image_width": 1280,
                "image_height": 960,
                "raw_image_width": 2560,
                "raw_image_height": 960,
                "pattern_cols": 11,
                "pattern_rows": 8,
                "square_size_mm": 45.0,
                "nominal_baseline_mm": 65.0,
                "rectify_alpha": 0.0,
                "valid_pair_count": len(objects),
                "valid_pair_indices": np.arange(1, len(objects) + 1, dtype=np.int32).reshape(-1, 1),
                "opencv_version": cv2.__version__,
                "numpy_version": np.__version__,
                "calibration_datetime_utc": "2026-09-14T00:00:00Z",
                "camera_device": "/dev/video0",
                "source_session": "/tmp/synthetic",
                "result_status": "INFO",
                "diagnostics_json": "[]",
                "rejected_pairs_json": "[]",
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            yaml_path = Path(temporary) / "calibration.yaml"
            npz_path = Path(temporary) / "calibration.npz"
            write_calibration_yaml(yaml_path, data)
            write_calibration_npz(npz_path, data)
            yaml_data = read_calibration_yaml(yaml_path)
            npz_data = read_calibration_npz(npz_path)
            assert_yaml_npz_consistent(yaml_data, npz_data)

    def test_rendered_png_session_end_to_end(self):
        config = CalibrationConfig(min_valid_pairs=12, max_rectified_previews=3)
        K_left = np.array([[620.0, 0.0, 640.0], [0.0, 618.0, 480.0], [0.0, 0.0, 1.0]], np.float64)
        K_right = np.array([[622.0, 0.0, 638.0], [0.0, 619.0, 482.0], [0.0, 0.0, 1.0]], np.float64)
        D_left = np.zeros(5, np.float64)
        D_right = np.zeros(5, np.float64)
        R_lr, _ = cv2.Rodrigues(np.array([0.002, -0.004, 0.001], np.float64))
        T_lr = np.array([[-65.0], [0.4], [-0.3]], np.float64)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            session = create_session(config, base / "captures")
            for index in range(14):
                rvec_left = np.array(
                    [-0.16 + 0.055 * (index % 6), -0.20 + 0.065 * (index % 7), -0.09 + 0.045 * (index % 5)],
                    np.float64,
                )
                R_board_left, _ = cv2.Rodrigues(rvec_left)
                t_left = np.array(
                    [[-260.0 + 58.0 * (index % 8)], [-155.0 + 52.0 * (index % 6)], [850.0 + 52.0 * index]],
                    np.float64,
                )
                R_board_right = R_lr @ R_board_left
                rvec_right, _ = cv2.Rodrigues(R_board_right)
                t_right = R_lr @ t_left + T_lr
                left = render_board(config, K_left, D_left, rvec_left, t_left)
                right = render_board(config, K_right, D_right, rvec_right, t_right)
                full = np.hstack((left, right))
                detection = detect_stereo_pair(left, right, config, exhaustive=True)
                self.assertTrue(detection.valid, f"pair {index + 1}: {detection.ordering_reason}")
                save_pair(
                    session,
                    index + 1,
                    full,
                    left,
                    right,
                    config,
                    monotonic_ns=index + 1,
                    left_descriptor=None,
                    right_descriptor=None,
                )
            output = run_calibration(session, config, base / "output")
            self.assertTrue((output / "calibration.yaml").is_file())
            self.assertTrue((output / "calibration.npz").is_file())
            self.assertTrue((output / "calibration_report.txt").is_file())
            self.assertEqual(len(list((output / "rectified_previews").glob("*.png"))), 3)
            saved = read_calibration_yaml(output / "calibration.yaml")
            self.assertLess(abs(float(saved["baseline_mm"]) - 65.0), 5.0)
            self.assertLess(float(saved["epipolar_p95_px"]), 1.0)
            validator = Path(__file__).resolve().parent.parent / "03_validate_calibration.py"
            completed = subprocess.run(
                [sys.executable, str(validator), str(output)],
                cwd=validator.parent,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((output / "validation_report.txt").is_file())
            revalidated_previews = list((output / "rectified_previews_revalidated").glob("*.png"))
            self.assertGreaterEqual(len(revalidated_previews), 1)
            self.assertLessEqual(len(revalidated_previews), CalibrationConfig().max_rectified_previews)

            comparison = run_calibration(
                session,
                config,
                base / "output",
                excluded_pair_indices=[4, 7],
                output_name="comparison_exclude_0004_0007",
            )
            comparison_yaml = read_calibration_yaml(comparison / "calibration.yaml")
            comparison_npz = read_calibration_npz(comparison / "calibration.npz")
            assert_yaml_npz_consistent(comparison_yaml, comparison_npz)
            self.assertEqual(
                np.asarray(comparison_yaml["excluded_pair_indices"]).reshape(-1).tolist(),
                [4, 7],
            )
            self.assertEqual(int(comparison_yaml["valid_pair_count"]), 12)
            self.assertEqual(active_pair_indices(session), list(range(1, 15)))
            self.assertEqual(len(list(session.left.glob("pair_*_left.png"))), 14)
            self.assertIn("pair_0004, pair_0007", (comparison / "calibration_report.txt").read_text())
            self.assertIn("pair_0004, pair_0007", (comparison / "preflight_report.txt").read_text())
            with self.assertRaisesRegex(CalibrationError, "do not exist on disk"):
                run_calibration(
                    session,
                    config,
                    base / "output",
                    excluded_pair_indices=[999],
                    output_name="invalid_exclusion",
                )

    def test_missing_camera_has_clear_error(self):
        config = replace(CalibrationConfig(), camera_device="/dev/this_camera_must_not_exist")
        with self.assertRaisesRegex(RuntimeError, "Could not open"):
            open_camera(config)


if __name__ == "__main__":
    unittest.main()
