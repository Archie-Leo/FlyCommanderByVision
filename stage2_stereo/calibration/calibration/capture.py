from __future__ import annotations

import json
import time
from typing import Optional, Tuple

import cv2
import numpy as np

from .calibrator import CalibrationError, print_result_summary, run_calibration
from .checkerboard import detect_stereo_pair, draw_detection, pose_descriptor
from .config import CalibrationConfig
from .dataset import SessionPaths, active_pair_indices, create_session, delete_last_pair, next_pair_index, save_pair


WINDOW_NAME = "Stage 2 Stereo Calibration"


def open_camera(config: CalibrationConfig) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(config.camera_device, cv2.CAP_V4L2)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(
            f"Could not open {config.camera_device} with CAP_V4L2. "
            "Check that the camera is connected, no other process owns it, and permissions allow access."
        )
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.raw_width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.raw_height)
    capture.set(cv2.CAP_PROP_FPS, config.requested_fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def split_frame(frame: np.ndarray, config: CalibrationConfig) -> Tuple[np.ndarray, np.ndarray]:
    if frame is None or frame.shape[:2] != (config.raw_height, config.raw_width):
        shape = None if frame is None else frame.shape
        raise ValueError(
            f"Expected raw Side-by-Side frame {config.raw_width}x{config.raw_height}, got {shape}. "
            "Calibration refuses resized or unexpected camera frames."
        )
    left = frame[:, 0 : config.eye_width].copy()
    right = frame[:, config.eye_width : config.raw_width].copy()
    if left.shape[:2] != (config.eye_height, config.eye_width) or right.shape[:2] != (config.eye_height, config.eye_width):
        raise ValueError(f"Side-by-Side split failed: left={left.shape}, right={right.shape}")
    return left, right


def _put(canvas: np.ndarray, text: str, position: Tuple[int, int], color, scale=0.65, thickness=2) -> None:
    cv2.putText(canvas, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def compose_preview(
    left: np.ndarray,
    right: np.ndarray,
    detection,
    config: CalibrationConfig,
    *,
    saved_count: int,
    fps: float,
    message: str,
    message_ok: bool,
    show_corners: bool,
) -> np.ndarray:
    left_drawn = draw_detection(left, detection.left, config) if show_corners else left.copy()
    right_drawn = draw_detection(right, detection.right, config) if show_corners else right.copy()
    display_size = (int(config.eye_width * config.preview_scale), int(config.eye_height * config.preview_scale))
    left_small = cv2.resize(left_drawn, display_size, interpolation=cv2.INTER_AREA)
    right_small = cv2.resize(right_drawn, display_size, interpolation=cv2.INTER_AREA)
    body = np.hstack((left_small, right_small))
    top_height, bottom_height = 105, 82
    canvas = np.zeros((top_height + body.shape[0] + bottom_height, body.shape[1], 3), dtype=np.uint8)
    canvas[top_height : top_height + body.shape[0]] = body
    half = body.shape[1] // 2
    green, red, white, yellow = (0, 220, 0), (0, 0, 255), (235, 235, 235), (0, 220, 255)
    _put(canvas, f"LEFT: {'FOUND' if detection.left.found else 'NOT FOUND'}", (18, 32), green if detection.left.found else red)
    _put(canvas, f"RIGHT: {'FOUND' if detection.right.found else 'NOT FOUND'}", (half + 18, 32), green if detection.right.found else red)
    pair_label = "VALID" if detection.valid else "INVALID"
    _put(canvas, f"PAIR: {pair_label}", (18, 66), green if detection.valid else red)
    _put(canvas, f"Saved: {saved_count}   FPS: {fps:.1f}   Board: 11x8   Square: 45mm", (240, 66), white, 0.62)
    _put(canvas, "Camera: /dev/video0  MJPG  2560x960 -> LEFT/RIGHT 1280x960", (18, 96), white, 0.54, 1)
    cv2.line(canvas, (half, top_height), (half, top_height + body.shape[0] - 1), yellow, 2)
    footer_y = top_height + body.shape[0]
    _put(canvas, "[S/SPACE] Save Pair   [C] Calibrate from Disk   [D] Delete Last   [Q/ESC] Quit", (18, footer_y + 31), white, 0.58)
    if message:
        _put(canvas, message[:115], (18, footer_y + 66), green if message_ok else red, 0.58)
    elif not detection.valid:
        _put(canvas, detection.ordering_reason[:115], (18, footer_y + 66), red, 0.55)
    return canvas


class CaptureApp:
    def __init__(self, config: Optional[CalibrationConfig] = None, *, allow_calibration: bool = True):
        self.config = config or CalibrationConfig()
        self.allow_calibration = allow_calibration
        self.capture: Optional[cv2.VideoCapture] = None
        self.session: Optional[SessionPaths] = None
        self.message = ""
        self.message_ok = True
        self.message_until = 0.0

    def _notify(self, text: str, ok: bool) -> None:
        self.message = text
        self.message_ok = ok
        self.message_until = time.monotonic() + self.config.message_duration_s
        print(text)

    def _save_current(self, frame: np.ndarray, left: np.ndarray, right: np.ndarray, monotonic_ns: int) -> None:
        assert self.session is not None
        print("Re-detecting current full-resolution LEFT/RIGHT images before save...")
        detection = detect_stereo_pair(left, right, self.config, exhaustive=True)
        if not detection.valid:
            detail = detection.ordering_reason
            self._notify(f"SAVE REJECTED: Chessboard must be detected in BOTH cameras. {detail}", False)
            return
        left_desc = pose_descriptor(detection.left.corners, self.config.eye_size, self.config)
        right_desc = pose_descriptor(detection.right.corners, self.config.eye_size, self.config)
        combined = np.concatenate((left_desc, right_desc))
        duplicate_distance = None
        metadata = json.loads(self.session.metadata.read_text(encoding="utf-8"))
        for record in metadata.get("pairs", []):
            if "left_pose_descriptor" not in record or "right_pose_descriptor" not in record:
                continue
            previous = np.concatenate((record["left_pose_descriptor"], record["right_pose_descriptor"]))
            distance = float(np.linalg.norm(combined - previous))
            duplicate_distance = distance if duplicate_distance is None else min(duplicate_distance, distance)
        index = next_pair_index(self.session)
        save_pair(
            self.session,
            index,
            frame,
            left,
            right,
            self.config,
            monotonic_ns=monotonic_ns,
            left_descriptor=left_desc,
            right_descriptor=right_desc,
        )
        suffix = ""
        if duplicate_distance is not None and duplicate_distance < 0.025:
            suffix = f"  WARNING: pose resembles an existing pair (distance={duplicate_distance:.4f})"
        self._notify(f"SAVED PAIR #{index:04d}{suffix}", True)

    def _calibrate(self) -> None:
        assert self.session is not None
        if not self.allow_calibration:
            self._notify("Use 02_calibrate_stereo.py to calibrate this session.", False)
            return
        self._notify("CALIBRATING FROM DISK... preview will resume when complete", True)
        try:
            output_dir = run_calibration(self.session, self.config)
            print_result_summary(output_dir)
            self._notify(f"CALIBRATION RESULT WRITTEN: {output_dir}", True)
        except CalibrationError as exc:
            self._notify(f"CALIBRATION REFUSED: {exc}", False)
        except Exception as exc:
            self._notify(f"CALIBRATION ERROR: {type(exc).__name__}: {exc}", False)

    def run(self) -> int:
        last_detection = None
        last_detection_time = 0.0
        last_frame_time = None
        display_fps = 0.0
        consecutive_failures = 0
        try:
            self.capture = open_camera(self.config)
            self.session = create_session(self.config)
            print(f"Capture session: {self.session.root}")
            print("Raw calibration images are persistent; deleting the last pair moves it to session/deleted/.")
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
            while True:
                ok, frame = self.capture.read()
                frame_ns = time.monotonic_ns()
                now = frame_ns / 1e9
                if not ok or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= 10:
                        raise RuntimeError("Camera returned 10 consecutive empty frames.")
                    continue
                consecutive_failures = 0
                left, right = split_frame(frame, self.config)
                if last_frame_time is not None:
                    instant = 1.0 / max(now - last_frame_time, 1e-6)
                    display_fps = instant if display_fps == 0.0 else 0.90 * display_fps + 0.10 * instant
                last_frame_time = now

                detection_ran = last_detection is None or now - last_detection_time >= self.config.preview_detection_interval_s
                if detection_ran:
                    last_detection = detect_stereo_pair(left, right, self.config, exhaustive=False)
                    last_detection_time = time.monotonic()
                if time.monotonic() > self.message_until:
                    self.message = ""
                canvas = compose_preview(
                    left,
                    right,
                    last_detection,
                    self.config,
                    saved_count=len(active_pair_indices(self.session)),
                    fps=display_fps,
                    message=self.message,
                    message_ok=self.message_ok,
                    # Keep the latest full-resolution detection overlay visible
                    # between throttled detector runs.  Saving is still guarded
                    # by a fresh exhaustive detection on the exact raw frame, so
                    # this UI-only hold cannot admit an invalid stereo pair.
                    show_corners=True,
                )
                cv2.imshow(WINDOW_NAME, canvas)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    return 0
                if key in (ord("s"), ord("S"), 32):
                    self._save_current(frame.copy(), left, right, frame_ns)
                elif key in (ord("d"), ord("D")):
                    deleted = delete_last_pair(self.session)
                    if deleted is None:
                        self._notify("DELETE: no saved pair", False)
                    else:
                        self._notify(f"DELETED PAIR #{deleted:04d} (recoverable under session/deleted/)", True)
                elif key in (ord("c"), ord("C")):
                    self._calibrate()
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")
            return 2
        finally:
            if self.capture is not None:
                self.capture.release()
            cv2.destroyAllWindows()


def run_capture(config: Optional[CalibrationConfig] = None, *, allow_calibration: bool = True) -> int:
    return CaptureApp(config, allow_calibration=allow_calibration).run()
