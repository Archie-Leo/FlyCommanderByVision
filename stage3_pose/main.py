from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import cv2

from camera.rectification import LeftRectifier
from camera.stereo_left_source import open_stereo_source
from config import AppConfig, BackendConfig, CameraConfig
from countdown import CaptureCountdown
from metrics import RuntimeMetrics
from pose.mediapipe_backend import MediaPipePoseBackend
from pose.normalize import SkeletonNormalizer
from pose.quality import PoseQualityEvaluator
from ui import draw_overlay


def parse_args():
    parser = argparse.ArgumentParser(description="Stage 3 rectified LEFT pose and normalization")
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--calibration", type=Path, default=AppConfig().calibration_path)
    parser.add_argument("--model", type=Path, default=BackendConfig().model_path)
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--max-frames", type=int, default=0, help="0 means run until Q/ESC")
    parser.add_argument("--no-display", action="store_true", help="Headless smoke/performance run")
    parser.add_argument("--display-mirror", action="store_true", help="Mirror display copy only; inference is unchanged")
    return parser.parse_args()


def atomic_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def save_snapshot(output_dir, frame_id, rectified, overlay, pose_frame, quality, skeleton, calibration_id):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
    prefix = output_dir / "snapshots" / f"frame_{frame_id:08d}_{stamp}"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(prefix) + "_input.png", rectified)
    cv2.imwrite(str(prefix) + "_overlay.png", overlay)
    atomic_json(Path(str(prefix) + ".json"), {
        "calibration_id": calibration_id,
        "pose_frame": pose_frame.to_dict(),
        "quality": quality.to_dict(),
        "normalized_skeleton": skeleton.to_dict() if skeleton else None,
    })
    return prefix


def main() -> int:
    args = parse_args()
    config = AppConfig(
        camera=replace(CameraConfig(), device=args.camera),
        backend=replace(BackendConfig(), model_path=args.model),
        calibration_path=args.calibration,
    )
    output_dir = args.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rectifier = LeftRectifier(config.calibration_path)
    quality_evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    metrics = RuntimeMetrics()
    countdown = CaptureCountdown(3.0)
    verbose = False
    processed = 0
    frames_with_pose = 0
    quality_valid_frames = 0
    started_utc = datetime.now(timezone.utc).isoformat()

    try:
        with MediaPipePoseBackend(config.backend) as backend, open_stereo_source(config.camera) as camera:
            while True:
                loop_started = time.perf_counter_ns()
                camera_frame = camera.read()
                metrics.add_frame_timestamp(camera_frame.timestamp_ns)
                metrics.add("camera_read_ms", camera_frame.read_latency_ms)
                rectified = rectifier.rectify(camera_frame.left_raw)
                pose_frame = backend.infer(
                    rectified, camera_frame.timestamp_ns // 1_000_000, camera_frame.frame_id
                )
                pose = pose_frame.poses[0] if pose_frame.poses else None
                quality = quality_evaluator.evaluate(pose)
                frames_with_pose += int(pose is not None)
                quality_valid_frames += int(quality.valid)
                norm_started = time.perf_counter_ns()
                skeleton = normalizer.normalize(pose, quality) if pose else None
                normalization_ms = (time.perf_counter_ns() - norm_started) / 1e6
                total_ms = (time.perf_counter_ns() - loop_started) / 1e6
                metrics.add("pose_inference_ms", pose_frame.inference_latency_ms)
                metrics.add("normalization_ms", normalization_ms)
                metrics.add("total_processing_ms", total_ms)
                processed += 1
                intervals = metrics.values["frame_interval_ms"]
                fps = 1000.0 / statistics.fmean(intervals[-30:]) if intervals else 0.0
                countdown_state = countdown.update(time.monotonic_ns())
                overlay = draw_overlay(
                    rectified, pose, quality, fps, pose_frame.inference_latency_ms, total_ms,
                    verbose, countdown_state.display_seconds
                )
                display = cv2.flip(overlay, 1) if args.display_mirror else overlay

                if countdown_state.capture_due:
                    prefix = save_snapshot(
                        output_dir, camera_frame.frame_id, rectified, overlay,
                        pose_frame, quality, skeleton, rectifier.calibration_id
                    )
                    print(f"COUNTDOWN SNAPSHOT SAVED: {prefix}")

                key = -1
                if not args.no_display:
                    cv2.imshow("Stage 3 Pose + Quality + Normalization", display)
                    key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("l"):
                    verbose = not verbose
                if key == ord("s"):
                    prefix = save_snapshot(
                        output_dir, camera_frame.frame_id, rectified, overlay,
                        pose_frame, quality, skeleton, rectifier.calibration_id
                    )
                    print(f"SNAPSHOT SAVED: {prefix}")
                if key == ord(" "):
                    if countdown.start(time.monotonic_ns()):
                        print("COUNTDOWN STARTED: 3 seconds")
                    else:
                        print("COUNTDOWN ALREADY ACTIVE")
                if args.max_frames and processed >= args.max_frames:
                    break
    finally:
        cv2.destroyAllWindows()

    report = {
        "status": "SOFTWARE_RUN_COMPLETE",
        "manual_validation": "PENDING",
        "started_utc": started_utc,
        "ended_utc": datetime.now(timezone.utc).isoformat(),
        "frames_processed": processed,
        "frames_with_pose": frames_with_pose,
        "quality_valid_frames": quality_valid_frames,
        "camera_device": config.camera.device,
        "calibration_path": str(rectifier.path),
        "calibration_id": rectifier.calibration_id,
        "model_path": str(config.backend.model_path.expanduser().resolve()),
        "metrics": metrics.report(),
        "timestamp_note": "host monotonic timestamp; not sensor exposure timestamp",
    }
    report_path = output_dir / f"runtime_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_UTC')}.json"
    atomic_json(report_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"RUNTIME REPORT: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        cv2.destroyAllWindows()
        print(f"STAGE 3 ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
