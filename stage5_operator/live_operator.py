from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import cv2

from stage5.appearance import torso_histogram
from stage5.types import DetectionV1


def parse_args():
    parser = argparse.ArgumentParser(description="Stage 5 visual-only operator ownership baseline")
    repo = Path(os.environ["REPO_ROOT"]) if "REPO_ROOT" in os.environ else None
    parser.add_argument("--stage3-root", type=Path, default=repo / "stage3_pose" if repo else Path("~/drone_stage3_pose"))
    parser.add_argument("--stage4-root", type=Path, default=repo / "stage4_gesture" if repo else Path("~/drone_stage4_gesture"))
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--num-poses", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--no-display", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    return parser.parse_args()


def draw_ui(frame, tracks, authorized):
    canvas = frame.copy()
    for track in tracks:
        x1, y1, x2, y2 = (int(x) for x in track.bbox_xyxy)
        operator = authorized.authorization_state == "LOCKED_HIGH" and track.track_id == authorized.current_track_id
        color = (0, 220, 0) if operator else (0, 170, 255)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        name = "OPERATOR" if operator else "BYSTANDER"
        cv2.putText(canvas, f"{name} ID {track.track_id}", (max(4, x1), max(26, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    top = [
        f"Ownership: {authorized.authorization_state}",
        f"Gesture: {authorized.gesture}",
        f"Authorized: {'YES' if authorized.valid else 'NO'}",
        f"Reject: {','.join(authorized.reject_reasons) or '-'}",
        "Visual-only Stage 5; no ROS2/PX4 output. [Q] Quit [X] Release session",
    ]
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 145), (0, 0, 0), -1)
    for index, line in enumerate(top):
        cv2.putText(canvas, line, (10, 25 + index * 28), cv2.FONT_HERSHEY_SIMPLEX,
                    0.64 if index < 4 else 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


def main():
    args = parse_args()
    if not 1 <= args.num_poses <= 6:
        raise ValueError("num-poses must be 1..6")
    stage3 = args.stage3_root.expanduser().resolve()
    stage4 = args.stage4_root.expanduser().resolve()
    if not (stage3 / "main.py").is_file() or not (stage4 / "gesture/geometry.py").is_file():
        raise FileNotFoundError("Frozen Stage 3/4 project root missing")
    sys.path.insert(0, str(stage3))
    sys.path.insert(0, str(stage4))
    from stage5.pipeline import Stage5Pipeline
    from camera.rectification import LeftRectifier
    from camera.stereo_left_source import open_stereo_source
    from config import AppConfig, BackendConfig, CameraConfig
    from pose.mediapipe_backend import MediaPipePoseBackend
    from pose.normalize import SkeletonNormalizer
    from pose.quality import PoseQualityEvaluator

    config = AppConfig(camera=replace(CameraConfig(), device=args.camera),
                       backend=replace(BackendConfig(), num_poses=args.num_poses))
    rectifier = LeftRectifier(config.calibration_path)
    quality_evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    pipeline = Stage5Pipeline()
    session_dir = args.output.expanduser().resolve() / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    session_dir.mkdir(parents=True, exist_ok=False)
    log_path = session_dir / "ownership_frames.jsonl"
    processed = 0
    failure = None
    with log_path.open("w", encoding="utf-8") as log_handle:
        try:
            with MediaPipePoseBackend(config.backend) as backend, open_stereo_source(config.camera) as camera:
                while True:
                    camera_frame = camera.read()
                    rectified = rectifier.rectify(camera_frame.left_raw)
                    pose_frame = backend.infer(rectified, camera_frame.timestamp_ns // 1_000_000, camera_frame.frame_id)
                    detections = []
                    for pose in pose_frame.poses:
                        quality = quality_evaluator.evaluate(pose)
                        skeleton = normalizer.normalize(pose, quality)
                        embedding, crop_quality = torso_histogram(rectified, pose.bbox_xyxy)
                        detections.append(DetectionV1(
                            timestamp_ms=pose_frame.timestamp_ms, frame_id=pose_frame.frame_id,
                            bbox_xyxy=pose.bbox_xyxy, pose=pose, pose_quality=quality,
                            skeleton=skeleton, confidence=float(pose.pose_score or 0.0),
                            embedding=embedding, crop_quality=crop_quality,
                        ))
                    tracks, authorized, record = pipeline.process(detections, pose_frame.timestamp_ms, pose_frame.frame_id)
                    record["camera_device"] = args.camera
                    record["calibration_id"] = rectifier.calibration_id
                    record["pose_count"] = len(pose_frame.poses)
                    record["pose_inference_latency_ms"] = pose_frame.inference_latency_ms
                    log_handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                    log_handle.flush()
                    if not args.no_display:
                        cv2.imshow("Stage 5 Visual Operator Ownership", draw_ui(rectified, tracks, authorized))
                        key = cv2.waitKey(1) & 0xFF
                        if key in (ord("q"), 27):
                            break
                        if key == ord("x"):
                            pipeline.ownership.reset()
                            pipeline.temporal.reset()
                    processed += 1
                    if args.max_frames and processed >= args.max_frames:
                        break
        except Exception as error:
            # A camera/model fault cannot leave a stale authorized gesture in the log/state.
            failure = f"{type(error).__name__}: {error}"
            now_ms = time.monotonic_ns() // 1_000_000
            _, authorized, record = pipeline.process([], now_ms, processed)
            record["camera_or_pipeline_error"] = failure
            record["authorized_gesture"]["valid"] = False
            record["authorized_gesture"]["gesture"] = "UNKNOWN"
            record["authorized_gesture"]["reject_reasons"].append("CAMERA_OR_PIPELINE_ERROR")
            log_handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            log_handle.flush()
        finally:
            cv2.destroyAllWindows()
    summary = {"status": "CAMERA_OR_PIPELINE_ERROR_FAIL_CLOSED" if failure else "VISUAL_ONLY_RUN_COMPLETE",
               "frames_processed": processed, "log_path": str(log_path),
               "no_ros2_px4_output": True, "error": failure}
    (session_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
