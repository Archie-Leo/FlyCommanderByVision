#!/usr/bin/env python3
"""Controlled Pose/ReID RKNN contention benchmark using the frozen bus fixture."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'stage3_pose'), str(ROOT / 'stage5_operator')]
from pose.rknn_backend import MODEL_SHA256, NativePoseRuntime
from stage5_v2.reid_rknn import RKNNOSNetEmbedder, VALIDATED_RK3576_SHA256

POSE_MODEL = Path.home() / 'fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/yolov8n-pose-rk3576-int8.rknn'
POSE_LIB = ROOT / 'stage3_pose/build/rknn_pose/libfcv_rknn_pose.so'
REID_MODEL = ROOT / 'models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn'
FIXTURE = Path.home() / 'fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/bus.jpg'
NPU_ROOT = Path('/sys/class/devfreq/27700000.npu')


def summary(values):
    values = sorted(values)
    return {'n': len(values), 'mean_ms': round(statistics.mean(values), 3),
            'p50_ms': round(values[len(values)//2], 3),
            'p95_ms': round(values[int((len(values)-1)*.95)], 3),
            'max_ms': round(values[-1], 3)}


def monitor(stop, samples):
    while not stop.wait(.1):
        try:
            samples.append({'load': (NPU_ROOT/'load').read_text().strip(),
                            'frequency_hz': int((NPU_ROOT/'cur_freq').read_text())})
        except (OSError, ValueError):
            samples.append({'load': None, 'frequency_hz': None})


def run_phase(name, pose_mask, reid_mask, image, box, pose_count, reid_count):
    pose = NativePoseRuntime(POSE_LIB, POSE_MODEL, core_mask=pose_mask) if pose_count else None
    reid = (RKNNOSNetEmbedder(REID_MODEL, VALIDATED_RK3576_SHA256,
                              core_mask=reid_mask) if reid_count else None)
    try:
        if pose:
            for _ in range(10):
                pose.infer(image, 4)
        if reid:
            for _ in range(10):
                feature, quality = reid.extract(image, box)
                if feature is None:
                    raise RuntimeError(f'ReID crop rejected; quality={quality}')
        times = {'pose_preprocess': [], 'pose_inference': [], 'pose_decode': [],
                 'pose_total': [], 'reid_preprocess': [], 'reid_inference': [],
                 'reid_postprocess': [], 'reid_total': []}
        errors = []
        def pose_loop():
            try:
                for _ in range(pose_count):
                    _, t = pose.infer(image, 4)
                    for src, dst in [('preprocess_ms','pose_preprocess'),
                                     ('inference_ms','pose_inference'),
                                     ('decode_ms','pose_decode'),('total_ms','pose_total')]:
                        times[dst].append(t[src])
            except BaseException as exc:
                errors.append(f'Pose: {exc}')
        def reid_loop():
            try:
                for _ in range(reid_count):
                    feature, quality = reid.extract(image, box)
                    if feature is None:
                        raise RuntimeError(f'ReID crop rejected; quality={quality}')
                    for src, dst in [('last_preprocess_ms','reid_preprocess'),
                                     ('last_inference_ms','reid_inference'),
                                     ('last_postprocess_ms','reid_postprocess'),
                                     ('last_latency_ms','reid_total')]:
                        times[dst].append(getattr(reid, src))
            except BaseException as exc:
                errors.append(f'ReID: {exc}')
        stop = threading.Event()
        npu = []
        sampler = threading.Thread(target=monitor, args=(stop, npu), daemon=True)
        workers = ([threading.Thread(target=pose_loop)] if pose else []) + \
                  ([threading.Thread(target=reid_loop)] if reid else [])
        start = time.monotonic()
        sampler.start()
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        elapsed = time.monotonic()-start
        stop.set()
        sampler.join()
        if errors:
            raise RuntimeError('; '.join(errors))
        return {'phase': name, 'pose_mask': pose_mask, 'reid_mask': reid_mask,
                'elapsed_s': round(elapsed, 3),
                'throughput_ops_s': round((pose_count+reid_count)/elapsed, 2),
                'timing': {key: summary(value) for key, value in times.items() if value},
                'npu_load_raw': sorted({x['load'] for x in npu if x['load']}),
                'npu_freq_hz': sorted({x['frequency_hz'] for x in npu if x['frequency_hz']})}
    finally:
        if pose:
            pose.close()
        if reid:
            reid.close()


def main():
    if hashlib.sha256(POSE_MODEL.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError('Pose model hash changed')
    image = cv2.imread(str(FIXTURE))
    if image is None:
        raise RuntimeError('Bus/person fixture missing')
    image = cv2.resize(image, (1280, 960))
    probe = NativePoseRuntime(POSE_LIB, POSE_MODEL)
    try:
        detections, _ = probe.infer(image, 4)
    finally:
        probe.close()
    if not detections:
        raise RuntimeError('No real person in bus fixture')
    box = max((tuple(p['detector_bbox_xyxy']) for p in detections),
              key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
    results = {'fixture': str(FIXTURE), 'bbox_xyxy': box,
               'pose_sha256': MODEL_SHA256, 'reid_sha256': VALIDATED_RK3576_SHA256,
               'runtime': 'RKNN native Pose + RKNNLite ReID', 'phases': []}
    cases = [('pose_only_auto', None, None, 250, 0),
             ('reid_only_auto', None, None, 0, 250),
             ('concurrent_auto', None, None, 250, 250),
             ('concurrent_split_0_1', 1, 2, 250, 250)]
    for name, pose_mask, reid_mask, pose_count, reid_count in cases:
        try:
            result = run_phase(name, pose_mask, reid_mask, image, box,
                               pose_count, reid_count)
        except Exception as exc:
            result = {'phase': name, 'error': str(exc)}
        results['phases'].append(result)
        print(json.dumps(result), flush=True)
    path = Path(os.environ.get('FCV_NPU_BENCH_JSON', '/tmp/fcv_v2_npu_contention.json'))
    path.write_text(json.dumps(results, indent=2))
    print('results', path, flush=True)


if __name__ == '__main__':
    main()
