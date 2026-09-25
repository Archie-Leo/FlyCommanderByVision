#!/usr/bin/env python3
"""Local, read-only Stage3/Stage4 browser preview for RK3576."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time

import cv2

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


PAGE = b"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage3 + Stage4 preview</title>
<style>body{margin:0;background:#17191d;color:#f5f5f5;font:16px sans-serif}
main{padding:12px}img{display:block;max-width:100%;height:auto;background:#111}</style>
<main><h2>Stage3 + Stage4 visual preview</h2>
<p>Rectified LEFT image; pose and gesture decisions use the original image.</p>
<img src="/stream.mjpg" alt="Live Stage3 and Stage4 preview"></main></html>"""


class PreviewState:
    def __init__(self):
        self.condition = threading.Condition()
        self.jpeg = None
        self.sequence = 0
        self.stopped = False
        self.error = None
        self.status = {"frames_processed": 0, "fps": 0.0}

    def publish(self, jpeg: bytes, status: dict):
        with self.condition:
            self.jpeg = jpeg
            self.sequence += 1
            self.status = status
            self.condition.notify_all()

    def stop(self, error=None):
        with self.condition:
            self.stopped = True
            if error is not None:
                self.error = str(error)
            self.condition.notify_all()


def draw_panel(frame, pose, quality, raw, stable, fps, pose_ms, total_ms,
               dropped, display_mirror, display_width):
    # draw_overlay only adds the existing canonical skeleton, bbox and Stage3
    # status. The top status band is replaced with Stage4 preview information.
    canvas = draw_overlay(frame, pose, quality, fps, pose_ms, total_ms)
    if display_mirror:
        canvas = cv2.flip(canvas, 1)  # Display only; inference has already ended.
    if display_width < canvas.shape[1]:
        display_height = round(canvas.shape[0] * display_width / canvas.shape[1])
        canvas = cv2.resize(canvas, (display_width, display_height),
                            interpolation=cv2.INTER_AREA)
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 194), (0, 0, 0), -1)
    confidence = quality.confidence_summary
    minimum = confidence.get("min")
    median = confidence.get("median")
    confidence_text = (f"min={minimum:.3f} median={median:.3f}"
                       if minimum is not None and median is not None else "N/A")
    lines = [
        f"POSE: {'FOUND' if pose else 'NONE'}  QUALITY: {'PASS' if quality.valid else 'FAIL'}",
        f"REASON: {', '.join(quality.reasons) if quality.reasons else 'NONE'}",
        f"RAW: {raw.label.value} ({raw.score:.3f})",
        f"STABLE: {stable.label.value}  confirmed={stable.stable}  for={stable.stable_for_ms}ms",
        f"FPS: {fps:.1f}  Pose: {pose_ms:.1f}ms  Loop: {total_ms:.1f}ms  Dropped: {dropped}",
        f"Required joint confidence: {confidence_text}",
    ]
    for index, line in enumerate(lines):
        y = 28 + index * 30
        color = (0, 220, 0) if index == 0 and quality.valid else (255, 255, 255)
        cv2.putText(canvas, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.58, color, 1, cv2.LINE_AA)
    return canvas


def make_handler(state: PreviewState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            print("HTTP " + format % args, flush=True)

        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAGE)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(PAGE)
            elif self.path == "/status":
                with state.condition:
                    payload = dict(state.status, sequence=state.sequence,
                                   stopped=state.stopped, error=state.error)
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.connection.settimeout(5)
                last_sequence = 0
                try:
                    while True:
                        with state.condition:
                            state.condition.wait_for(
                                lambda: state.sequence != last_sequence or state.stopped,
                                timeout=5)
                            if state.sequence == last_sequence:
                                if state.stopped:
                                    break
                                continue
                            jpeg = state.jpeg
                            last_sequence = state.sequence
                        # A slow viewer skips intermediate frames; it never
                        # queues images or holds the producer's lock while writing.
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                                         + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    pass
            else:
                self.send_error(404)

    return Handler


def run_camera(args, state: PreviewState, server: ThreadingHTTPServer):
    config = AppConfig(camera=replace(CameraConfig(), device=args.camera),
                       backend=BackendConfig(),
                       calibration_path=args.calibration)
    rectifier = LeftRectifier(config.calibration_path)
    quality_evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    recognizer = GeometryGestureRecognizer()
    stabilizer = TemporalStabilizer()
    intervals = deque(maxlen=30)
    previous_end = None
    processed = 0
    error = None
    try:
        with create_pose_backend(config.backend, "rknn", args.model) as backend, \
                open_stereo_source(config.camera) as camera:
            while not state.stopped:
                started = time.perf_counter()
                camera_frame = camera.read()
                rectified = rectifier.rectify(camera_frame.left_raw)
                pose_frame = backend.infer(rectified, camera_frame.timestamp_ns // 1_000_000,
                                           camera_frame.frame_id)
                pose = pose_frame.poses[0] if pose_frame.poses else None
                quality = quality_evaluator.evaluate(pose)
                skeleton = normalizer.normalize(pose, quality) if pose else None
                raw = recognizer.recognize(skeleton)
                stable = stabilizer.update(raw)
                now = time.perf_counter()
                if previous_end is not None:
                    intervals.append(now - previous_end)
                previous_end = now
                fps = 1.0 / statistics.fmean(intervals) if intervals else 0.0
                total_ms = (now - started) * 1000
                dropped = getattr(camera, "dropped_frames", 0)
                overlay = draw_panel(rectified, pose, quality, raw, stable, fps,
                                     pose_frame.inference_latency_ms, total_ms,
                                     dropped, args.display_mirror, args.display_width)
                ok, encoded = cv2.imencode(".jpg", overlay,
                                           [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                processed += 1
                state.publish(encoded.tobytes(), {
                    "frames_processed": processed, "frame_id": pose_frame.frame_id,
                    "fps": round(fps, 2), "pose_count": len(pose_frame.poses),
                    "quality_valid": quality.valid, "raw_gesture": raw.label.value,
                    "stable_gesture": stable.label.value,
                    "required_joint_confidence": quality.confidence_summary,
                    "pose_inference_ms": round(pose_frame.inference_latency_ms, 2),
                    "camera_dropped_frames": dropped,
                })
                if args.max_frames and processed >= args.max_frames:
                    break
    except Exception as exc:
        error = exc
        print(f"Preview camera error: {exc}", file=sys.stderr, flush=True)
    finally:
        state.stop(error)
        if args.max_frames or error:
            server.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default=os.environ.get("FCV_CAMERA_DEVICE", "/dev/video73"))
    parser.add_argument("--calibration", type=Path,
                        default=AppConfig().calibration_path)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--display-mirror", action="store_true")
    parser.add_argument("--display-width", type=int, default=960,
                        help="Final display width only; inference remains at calibrated resolution")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Optional finite camera run for diagnostics")
    args = parser.parse_args()
    if args.display_width < 480:
        parser.error("--display-width must be at least 480")
    state = PreviewState()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    server.daemon_threads = True
    worker = threading.Thread(target=run_camera, args=(args, state, server),
                              name="stage4-preview-camera", daemon=True)
    worker.start()
    print(f"Preview: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        state.stop()
        server.server_close()
        worker.join(timeout=7)
    return 1 if state.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
