#!/usr/bin/env python3
"""Measure integrated RK3576 PoseFrame inference; optional OSNet coexistence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "stage3_pose"), str(ROOT / "stage4_gesture"),
                str(ROOT / "stage5_operator")]
from config import BackendConfig, NormalizationConfig, QualityConfig
from pose.factory import create_pose_backend
from pose.quality import PoseQualityEvaluator
from pose.normalize import SkeletonNormalizer
from gesture.geometry import GeometryGestureRecognizer


def summary(values):
    return {"mean": float(np.mean(values)), "p50": float(np.median(values)),
            "p95": float(np.percentile(values, 95))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, default=Path.home() /
        "fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/bus.jpg")
    parser.add_argument("--iterations", type=int, default=220)
    parser.add_argument("--coexist-osnet", action="store_true")
    parser.add_argument("--osnet-every", type=int, default=10)
    args = parser.parse_args()
    image = cv2.imread(str(args.image))
    if image is None:
        raise FileNotFoundError(args.image)
    # Stage3's real rectified-left input geometry; this resize is a timing
    # fixture, not a coordinate or detection-accuracy reference.
    image = cv2.resize(image, (1280, 960))
    os.environ["FCV_POSE_BACKEND"] = "rknn"
    evaluator = PoseQualityEvaluator(QualityConfig())
    normalizer = SkeletonNormalizer(NormalizationConfig())
    recognizer = GeometryGestureRecognizer()
    timings = {name: [] for name in ("preprocess_ms", "inference_ms", "decode_ms",
                                  "bridge_ms", "poseframe_ms", "total_infer_ms")}
    reid_times = []
    detections = []
    quality_pass = 0
    gestures = {}
    required_scores = []
    osnet = None
    if args.coexist_osnet:
        from stage5_v2.reid_rknn import RKNNOSNetEmbedder
        osnet = RKNNOSNetEmbedder(ROOT / "models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn")
    backend = create_pose_backend(BackendConfig(num_poses=4))
    try:
        for i in range(args.iterations + 5):
            frame = backend.infer(image, 1000+i*33, i)
            if i < 5:
                continue
            detections.append(len(frame.poses))
            for key in timings:
                timings[key].append(backend.last_timings[key])
            if frame.poses:
                person = frame.poses[0]
                quality = evaluator.evaluate(person)
                quality_pass += int(quality.valid)
                skeleton = normalizer.normalize(person, quality)
                raw = recognizer.recognize(skeleton)
                gestures[raw.label.value] = gestures.get(raw.label.value, 0) + 1
                required_scores += [person.joints[name].confidence for name in
                    ("left_shoulder", "right_shoulder", "left_hip", "right_hip",
                     "left_elbow", "right_elbow", "left_wrist", "right_wrist")]
                if osnet is not None and i % args.osnet_every == 0:
                    vector, _ = osnet.extract(image, person.bbox_xyxy)
                    if vector is None:
                        raise RuntimeError("OSNet coexistence crop rejected")
                    reid_times.append(osnet.last_latency_ms)
        output = {"source": str(args.image), "fixture_size": [1280, 960],
                  "iterations": args.iterations,
                  "pose_count": {"min": min(detections), "max": max(detections),
                                 "mean": statistics.mean(detections)},
                  "stage3_timing_ms": {key: summary(values) for key, values in timings.items()},
                  "effective_pose_fps": 1000 / statistics.mean(timings["total_infer_ms"]),
                  "first_person_quality_pass_rate": quality_pass / args.iterations,
                  "first_person_raw_gestures": gestures,
                  "required_joint_confidence": summary(required_scores),
                  "osnet_calls": len(reid_times),
                  "osnet_total_ms": summary(reid_times) if reid_times else None}
        print(json.dumps(output, indent=2))
    finally:
        backend.close()
        if osnet is not None:
            osnet.close()


if __name__ == "__main__":
    main()
