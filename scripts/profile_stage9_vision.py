#!/usr/bin/env python3
"""Read-only RK3576 camera/Stage3/Stage4 frame-age and stage-time profiler."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
from dataclasses import replace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "stage3_pose"), str(ROOT / "stage4_gesture")]

from camera.rectification import LeftRectifier
from camera.stereo_left_source import open_stereo_source
from config import AppConfig, BackendConfig, CameraConfig
from gesture import GeometryGestureRecognizer, TemporalStabilizer
from pose.factory import create_pose_backend
from pose.normalize import SkeletonNormalizer
from pose.quality import PoseQualityEvaluator
from ui import draw_overlay


def summarize(rows, key):
    values = [row[key] for row in rows]
    return {"mean": statistics.fmean(values), "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)), "max": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default=os.environ.get("FCV_CAMERA_DEVICE", "/dev/video73"))
    parser.add_argument("--calibration", type=Path, default=AppConfig().calibration_path)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--input-rotate-180", action="store_true")
    parser.add_argument("--with-preview", action="store_true",
                        help="Include Stage3 overlay and JPEG encode cost, without HTTP clients")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/stage9_profile.json")
    args = parser.parse_args()
    if args.frames < 1 or args.warmup < 0:
        parser.error("frames must be positive and warmup nonnegative")
    config = AppConfig(camera=replace(CameraConfig(), device=args.camera),
                       backend=BackendConfig(), calibration_path=args.calibration)
    rectifier = LeftRectifier(config.calibration_path)
    evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    recognizer = GeometryGestureRecognizer()
    stabilizer = TemporalStabilizer()
    rows = []
    first_end_ns = None
    last_end_ns = None
    dropped = 0
    with create_pose_backend(config.backend, "rknn", args.model) as backend, \
            open_stereo_source(config.camera) as camera:
        for i in range(args.frames + args.warmup):
            read_start_ns = time.monotonic_ns()
            frame = camera.read()
            read_end_ns = time.monotonic_ns()
            rectified = rectifier.rectify(frame.left_raw)
            if args.input_rotate_180:
                rectified = cv2.rotate(rectified, cv2.ROTATE_180)
            rectify_end_ns = time.monotonic_ns()
            pose_frame = backend.infer(rectified, frame.timestamp_ns // 1_000_000,
                                       frame.frame_id)
            pose_end_ns = time.monotonic_ns()
            pose = pose_frame.poses[0] if pose_frame.poses else None
            quality = evaluator.evaluate(pose)
            skeleton = normalizer.normalize(pose, quality) if pose else None
            raw = recognizer.recognize(skeleton)
            stable = stabilizer.update(raw)
            gesture_end_ns = time.monotonic_ns()
            if args.with_preview:
                overlay = draw_overlay(rectified, pose, quality, 0.0,
                                       pose_frame.inference_latency_ms,
                                       (gesture_end_ns - read_start_ns) / 1e6)
                overlay = cv2.resize(overlay, (960, 720), interpolation=cv2.INTER_AREA)
                overlay_end_ns = time.monotonic_ns()
                ok, _ = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                encode_end_ns = time.monotonic_ns()
            else:
                overlay_end_ns = encode_end_ns = gesture_end_ns
            if i < args.warmup:
                continue
            if first_end_ns is None:
                first_end_ns = encode_end_ns
            last_end_ns = encode_end_ns
            dropped = camera.dropped_frames
            row = {
                "frame_id": frame.frame_id,
                "capture_timestamp_monotonic_ns": frame.timestamp_ns,
                "frame_age_at_read_ms": (read_end_ns - frame.timestamp_ns) / 1e6,
                "frame_age_at_end_ms": (encode_end_ns - frame.timestamp_ns) / 1e6,
                "capture_ms": (read_end_ns - read_start_ns) / 1e6,
                "rectify_ms": (rectify_end_ns - read_end_ns) / 1e6,
                "pose_ms": (pose_end_ns - rectify_end_ns) / 1e6,
                "gesture_ms": (gesture_end_ns - pose_end_ns) / 1e6,
                "overlay_ms": (overlay_end_ns - gesture_end_ns) / 1e6,
                "encode_ms": (encode_end_ns - overlay_end_ns) / 1e6,
                "total_processing_ms": (encode_end_ns - read_start_ns) / 1e6,
                "pose_count": len(pose_frame.poses),
                "quality_valid": quality.valid,
                "raw_gesture": raw.label.value,
                "stable_gesture": stable.label.value,
                "dropped_camera_frames_cumulative": dropped,
            }
            rows.append(row)
    elapsed_s = (last_end_ns - first_end_ns) / 1e9 if len(rows) > 1 else 0.0
    keys = ("frame_age_at_read_ms", "frame_age_at_end_ms", "capture_ms",
            "rectify_ms", "pose_ms", "gesture_ms", "overlay_ms", "encode_ms",
            "total_processing_ms")
    report = {
        "scope": "Camera + Stage3 + Stage4" + (" + preview overlay/JPEG" if args.with_preview else ""),
        "timestamp_semantics": "FFmpeg rawvideo host receipt monotonic; sensor exposure timestamp unavailable",
        "frames": len(rows), "first_frame_id": rows[0]["frame_id"],
        "last_frame_id": rows[-1]["frame_id"],
        "effective_vision_fps": (len(rows) - 1) / elapsed_s if elapsed_s else None,
        "capture_frame_id_rate_fps": (rows[-1]["frame_id"] - rows[0]["frame_id"]) / elapsed_s if elapsed_s else None,
        "camera_dropped_frames_cumulative": dropped,
        "timing_ms": {key: summarize(rows, key) for key in keys},
        "pose_frames": sum(bool(row["pose_count"]) for row in rows),
        "quality_valid_frames": sum(bool(row["quality_valid"]) for row in rows),
        "per_frame": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_frame"}, indent=2))
    print("PER_FRAME_REPORT:", args.output)


if __name__ == "__main__":
    main()
