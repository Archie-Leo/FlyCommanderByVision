from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import cv2

from gesture import GeometryGestureRecognizer, GestureLabel, TemporalStabilizer


LABEL_KEYS = {
    ord("0"): GestureLabel.UNKNOWN,
    ord("1"): GestureLabel.LEFT,
    ord("2"): GestureLabel.RIGHT,
    ord("3"): GestureLabel.ASCEND,
    ord("4"): GestureLabel.DESCEND,
    ord("5"): GestureLabel.HOVER,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Stage 4 live geometry gesture baseline")
    parser.add_argument("--stage3-root", type=Path, default=Path.home() / "drone_stage3_pose")
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--output", type=Path, default=Path("datasets"))
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--no-display", action="store_true")
    return parser.parse_args()


def atomic_json(path: Path, payload) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def draw_gesture_panel(image, raw, stable, selected, countdown_seconds, recording):
    canvas = image.copy()
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 118), (0, 0, 0), -1)
    stable_color = (0, 220, 0) if stable.stable else (0, 190, 255)
    lines = [
        (f"RAW: {raw.label.value}  score={raw.score:.3f}", (255, 255, 255)),
        (f"STABLE: {stable.label.value}  confirmed={stable.stable}  for={stable.stable_for_ms}ms", stable_color),
        (f"DATASET LABEL: {selected.value}   [0]UNKNOWN [1]LEFT [2]RIGHT [3]ASCEND [4]DESCEND [5]HOVER", (255, 255, 0)),
        ("[SPACE] 3s Save  [S] Save Now  [R] Record Sequence  [Q/ESC] Quit"
         + (f"   CAPTURE IN {countdown_seconds}" if countdown_seconds else "")
         + ("   RECORDING" if recording else ""), (0, 0, 255) if recording else (220, 220, 220)),
    ]
    for index, (text, color) in enumerate(lines):
        cv2.putText(canvas, text, (12, 25 + index * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 1, cv2.LINE_AA)
    return canvas


def save_sample(session, frame_id, rectified, overlay, pose_frame, quality, skeleton, raw, stable, label, calibration_id):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
    prefix = session / f"sample_{frame_id:08d}_{stamp}"
    cv2.imwrite(str(prefix) + "_input.png", rectified)
    cv2.imwrite(str(prefix) + "_overlay.png", overlay)
    payload = {
        "ground_truth_label": label.value,
        "protocol_version": "GestureV1",
        "calibration_id": calibration_id,
        "pose_frame": pose_frame.to_dict(),
        "quality": quality.to_dict(),
        "normalized_skeleton": skeleton.to_dict() if skeleton else None,
        "gesture_raw": raw.to_dict(),
        "gesture_stable": stable.to_dict(),
    }
    json_path = Path(str(prefix) + ".json")
    atomic_json(json_path, payload)
    manifest = session / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "path": str(json_path.resolve()),
            "label": label.value,
            "captured_utc": stamp,
        }, ensure_ascii=False) + "\n")
    return prefix


def main() -> int:
    args = parse_args()
    stage3_root = args.stage3_root.expanduser().resolve()
    if not (stage3_root / "main.py").is_file():
        raise FileNotFoundError(f"Stage 3 root not found: {stage3_root}")
    sys.path.insert(0, str(stage3_root))

    from camera.rectification import LeftRectifier
    from camera.stereo_left_source import StereoLeftSource
    from config import AppConfig, BackendConfig, CameraConfig
    from countdown import CaptureCountdown
    from pose.mediapipe_backend import MediaPipePoseBackend
    from pose.normalize import SkeletonNormalizer
    from pose.quality import PoseQualityEvaluator
    from ui import draw_overlay

    config = AppConfig(camera=replace(CameraConfig(), device=args.camera), backend=BackendConfig())
    rectifier = LeftRectifier(config.calibration_path)
    quality_evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    recognizer = GeometryGestureRecognizer()
    stabilizer = TemporalStabilizer()
    countdown = CaptureCountdown(3.0)
    selected_label = GestureLabel.UNKNOWN
    session = args.output.expanduser().resolve() / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    session.mkdir(parents=True, exist_ok=False)
    processed = 0
    saved = 0
    sequence_index = 0
    recording_handle = None
    recording_path = None

    try:
        with StereoLeftSource(config.camera) as camera, MediaPipePoseBackend(config.backend) as backend:
            while True:
                started = time.perf_counter_ns()
                camera_frame = camera.read()
                rectified = rectifier.rectify(camera_frame.left_raw)
                pose_frame = backend.infer(rectified, camera_frame.timestamp_ns // 1_000_000, camera_frame.frame_id)
                pose = pose_frame.poses[0] if pose_frame.poses else None
                quality = quality_evaluator.evaluate(pose)
                skeleton = normalizer.normalize(pose, quality) if pose else None
                raw = recognizer.recognize(skeleton)
                stable = stabilizer.update(raw)
                total_ms = (time.perf_counter_ns() - started) / 1e6
                base_overlay = draw_overlay(rectified, pose, quality, 0.0, pose_frame.inference_latency_ms, total_ms, False)
                countdown_state = countdown.update(time.monotonic_ns())
                overlay = draw_gesture_panel(
                    base_overlay, raw, stable, selected_label,
                    countdown_state.display_seconds, recording_handle is not None
                )

                if recording_handle is not None:
                    recording_handle.write(json.dumps({
                        "ground_truth_label": selected_label.value,
                        "timestamp_ms": pose_frame.timestamp_ms,
                        "frame_id": pose_frame.frame_id,
                        "normalized_skeleton": skeleton.to_dict() if skeleton else None,
                        "gesture_raw": raw.to_dict(),
                        "gesture_stable": stable.to_dict(),
                    }, ensure_ascii=False, allow_nan=False) + "\n")
                    recording_handle.flush()

                if countdown_state.capture_due:
                    prefix = save_sample(session, camera_frame.frame_id, rectified, overlay, pose_frame, quality, skeleton, raw, stable, selected_label, rectifier.calibration_id)
                    saved += 1
                    print(f"GESTURE SAMPLE SAVED: {prefix} label={selected_label.value}")

                key = -1
                if not args.no_display:
                    cv2.imshow("Stage 4 Gesture Baseline", overlay)
                    key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key in LABEL_KEYS:
                    if recording_handle is None:
                        selected_label = LABEL_KEYS[key]
                        print(f"DATASET LABEL SELECTED: {selected_label.value}")
                    else:
                        print("STOP RECORDING BEFORE CHANGING DATASET LABEL")
                if key == ord(" "):
                    print("COUNTDOWN STARTED: 3 seconds" if countdown.start(time.monotonic_ns()) else "COUNTDOWN ALREADY ACTIVE")
                if key == ord("s"):
                    prefix = save_sample(session, camera_frame.frame_id, rectified, overlay, pose_frame, quality, skeleton, raw, stable, selected_label, rectifier.calibration_id)
                    saved += 1
                    print(f"GESTURE SAMPLE SAVED: {prefix} label={selected_label.value}")
                if key == ord("r"):
                    if recording_handle is None:
                        sequence_index += 1
                        recording_path = session / f"sequence_{sequence_index:04d}_{selected_label.value}.jsonl"
                        recording_handle = recording_path.open("w", encoding="utf-8")
                        stabilizer.reset()
                        print(f"SEQUENCE RECORDING STARTED: {recording_path} label={selected_label.value}")
                    else:
                        recording_handle.close()
                        recording_handle = None
                        stabilizer.reset()
                        print(f"SEQUENCE RECORDING STOPPED: {recording_path}")
                        recording_path = None
                processed += 1
                if args.max_frames and processed >= args.max_frames:
                    break
    finally:
        if recording_handle is not None:
            recording_handle.close()
        cv2.destroyAllWindows()

    summary = {
        "status": "SOFTWARE_RUN_COMPLETE",
        "protocol_version": "GestureV1",
        "frames_processed": processed,
        "samples_saved": saved,
        "session": str(session),
        "note": "No PX4/ROS2/Tracking/Operator state is present in Stage 4.",
    }
    atomic_json(session / "runtime_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        cv2.destroyAllWindows()
        print(f"STAGE 4 ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
