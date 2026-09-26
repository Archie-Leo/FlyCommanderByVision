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
main{padding:12px}canvas{display:block;max-width:100%;height:auto;background:#111}</style>
<main><h2>Stage3 + Stage4 visual preview</h2>
<p>Rectified LEFT image; pose and gesture decisions use the original image.</p>
<canvas id="preview" width="640" height="480" aria-label="Live Stage3 and Stage4 preview"></canvas>
<p id="timing">Waiting for frames...</p><p id="display-age"></p>
<p><a href="/stream.mjpg">MJPEG stream</a></p></main>
<script>
const canvas=document.getElementById('preview'),ctx=canvas.getContext('2d');
let lastSequence=0;
async function refreshFrame(){
  const started=performance.now();
  try{
    const response=await fetch('/snapshot.jpg',{cache:'no-store',signal:AbortSignal.timeout(1000)});
    if(!response.ok)throw Error('HTTP '+response.status);
    const sequence=Number(response.headers.get('X-Preview-Sequence'));
    if(sequence!==lastSequence){
      const bitmap=await createImageBitmap(await response.blob());
      canvas.width=bitmap.width;canvas.height=bitmap.height;
      ctx.drawImage(bitmap,0,0);bitmap.close();lastSequence=sequence;
      const age=Number(response.headers.get('X-Frame-Age-Ms'))+performance.now()-started;
      document.getElementById('display-age').textContent=`Estimated age at display: ${age.toFixed(0)} ms`;
    }
  }catch(error){document.getElementById('display-age').textContent='Preview disconnected: '+error.message;}
  setTimeout(refreshFrame,Math.max(0,100-(performance.now()-started)));
}
refreshFrame();
setInterval(async()=>{try{const s=await(await fetch('/status',{cache:'no-store'})).json();
document.getElementById('timing').textContent=`Analysis ${s.analysis_fps??0} FPS | Preview ${s.preview_fps??0} FPS | JPEG ${s.jpeg_encode_ms??0} ms | Frame age at HTTP send ${s.frame_age_at_send_ms??'--'} ms`;
}catch(_){document.getElementById('timing').textContent='Preview disconnected';}},500)
</script></html>"""


class PreviewState:
    def __init__(self):
        self.condition = threading.Condition()
        self.jpeg = None
        self.jpeg_capture_ns = None
        self.display_status = {}
        self.sequence = 0
        self.stopped = False
        self.error = None
        self.status = {"frames_processed": 0, "fps": 0.0}
        self.analysis = None
        self.analysis_sequence = 0
        self.render_error = None
        self.last_send_sequence = 0

    def publish_analysis(self, analysis, status: dict):
        # One overwriteable slot: rendering can never hold up inference.
        with self.condition:
            self.analysis = analysis
            self.analysis_sequence += 1
            self.status = dict(status, **self.display_status,
                               frames_displayed=self.sequence,
                               render_error=self.render_error)
            self.condition.notify_all()

    def publish(self, jpeg: bytes, status: dict):
        with self.condition:
            self.jpeg = jpeg
            self.jpeg_capture_ns = status["capture_timestamp_monotonic_ns"]
            self.sequence += 1
            self.display_status = {
                "display_capture_timestamp_monotonic_ns": self.jpeg_capture_ns,
                "display_frame_id": status["display_frame_id"],
                "frame_ready_monotonic_ns": status["frame_ready_monotonic_ns"],
                "host_receipt_age_at_publish_ms": status["host_receipt_age_at_publish_ms"],
                "preview_fps": status.get("preview_fps", 0.0),
                "jpeg_encode_ms": status.get("jpeg_encode_ms", 0.0),
                "frame_age_ms": status.get("frame_age_ms", status["host_receipt_age_at_publish_ms"]),
            }
            self.status = dict(self.status, **self.display_status,
                               frames_displayed=self.sequence)
            self.condition.notify_all()

    def note_send(self, sequence: int, frame_age_ms: float):
        with self.condition:
            if sequence >= self.last_send_sequence:
                self.last_send_sequence = sequence
                self.display_status["frame_age_at_send_ms"] = round(frame_age_ms, 2)
                self.status = dict(self.status, **self.display_status)

    def fail_render(self, error):
        with self.condition:
            self.render_error = str(error)
            self.status = dict(self.status, render_error=self.render_error)
            self.condition.notify_all()

    def stop(self, error=None):
        with self.condition:
            self.stopped = True
            if error is not None:
                self.error = str(error)
            self.condition.notify_all()


def draw_panel(frame, pose, quality, raw, stable, analysis_fps, preview_fps,
               pose_ms, total_ms, jpeg_ms, frame_age_ms, dropped,
               input_rotate_180, display_mirror, preview_width):
    # draw_overlay only adds the existing canonical skeleton, bbox and Stage3
    # status. The top status band is replaced with Stage4 preview information.
    canvas = draw_overlay(frame, pose, quality, analysis_fps, pose_ms, total_ms)
    if display_mirror:
        canvas = cv2.flip(canvas, 1)  # Display only; inference has already ended.
    if preview_width < canvas.shape[1]:
        display_height = round(canvas.shape[0] * preview_width / canvas.shape[1])
        canvas = cv2.resize(canvas, (preview_width, display_height),
                            interpolation=cv2.INTER_AREA)
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 218), (0, 0, 0), -1)
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
        f"ANALYSIS FPS: {analysis_fps:.1f}  PREVIEW FPS: {preview_fps:.1f}",
        f"POSE: {pose_ms:.1f}ms  JPEG: {jpeg_ms:.1f}ms  LOOP: {total_ms:.1f}ms",
        f"FRAME AGE (encode): {frame_age_ms:.1f}ms  DROPPED CAMERA: {dropped}",
        f"Required joint confidence: {confidence_text}",
        f"INPUT ROTATION: {180 if input_rotate_180 else 0}  DISPLAY MIRROR: {'ON' if display_mirror else 'OFF'}",
    ]
    for index, line in enumerate(lines):
        y = 20 + index * 24
        color = (0, 220, 0) if index == 0 and quality.valid else (255, 255, 255)
        cv2.putText(canvas, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.43, color, 1, cv2.LINE_AA)
    return canvas


def make_handler(state: PreviewState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        wbufsize = 0

        def send_no_cache(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")

        def log_message(self, format, *args):
            if self.path not in ("/snapshot.jpg", "/status", "/clock"):
                print("HTTP " + format % args, flush=True)

        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAGE)))
                self.send_no_cache()
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
                self.send_no_cache()
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/clock":
                data = json.dumps({"monotonic_ns": time.monotonic_ns()}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_no_cache()
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/snapshot.jpg":
                with state.condition:
                    jpeg = state.jpeg
                    capture_ns = state.jpeg_capture_ns
                    sequence = state.sequence
                if jpeg is None:
                    self.send_error(503, "No preview frame yet")
                    return
                send_age_ms = (time.monotonic_ns() - capture_ns) / 1e6
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("X-Frame-Capture-Monotonic-Ns", str(capture_ns))
                self.send_header("X-Frame-Age-Ms", f"{send_age_ms:.2f}")
                self.send_header("X-Preview-Sequence", str(sequence))
                self.send_no_cache()
                self.end_headers()
                try:
                    self.wfile.write(jpeg)
                    state.note_send(sequence, send_age_ms)
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    pass
            elif self.path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_no_cache()
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
                            capture_ns = state.jpeg_capture_ns
                            last_sequence = state.sequence
                        send_age_ms = (time.monotonic_ns() - capture_ns) / 1e6
                        # A slow viewer skips intermediate frames; it never
                        # queues images or holds the producer's lock while writing.
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         + f"X-Frame-Capture-Monotonic-Ns: {capture_ns}\r\n".encode()
                                         + f"X-Frame-Age-Ms: {send_age_ms:.2f}\r\n".encode()
                                         + f"X-Preview-Sequence: {last_sequence}\r\n".encode()
                                         + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                                         + jpeg + b"\r\n")
                        self.wfile.flush()
                        state.note_send(last_sequence, send_age_ms)
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
                analysis_frame = (cv2.rotate(rectified, cv2.ROTATE_180)
                                  if args.input_rotate_180 else rectified)
                pose_frame = backend.infer(analysis_frame, camera_frame.timestamp_ns // 1_000_000,
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
                ready_ns = time.monotonic_ns()
                processed += 1
                state.publish_analysis((analysis_frame, pose, quality, raw, stable,
                                        fps, pose_frame.inference_latency_ms,
                                        total_ms, dropped, camera_frame.timestamp_ns,
                                        pose_frame.frame_id), {
                    "frames_processed": processed, "frame_id": pose_frame.frame_id,
                    "analysis_frame_id": pose_frame.frame_id,
                    "capture_timestamp_monotonic_ns": camera_frame.timestamp_ns,
                    "vision_ready_monotonic_ns": ready_ns,
                    "host_receipt_age_at_vision_ms": round(
                        (ready_ns - camera_frame.timestamp_ns) / 1e6, 2),
                    "fps": round(fps, 2), "analysis_fps": round(fps, 2),
                    "pose_count": len(pose_frame.poses),
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


def run_render(args, state: PreviewState):
    last_analysis = 0
    preview_intervals = deque(maxlen=30)
    previous_ready_ns = None
    next_encode_ns = 0
    previous_jpeg_ms = 0.0
    preview_interval_ns = int(1e9 / getattr(args, "preview_fps", 10.0))
    try:
        while True:
            with state.condition:
                while True:
                    now_ns = time.monotonic_ns()
                    fresh = state.analysis_sequence != last_analysis
                    if fresh and now_ns >= next_encode_ns:
                        break
                    if state.stopped and not fresh:
                        return
                    wait_s = (max(0.0, (next_encode_ns-now_ns)/1e9)
                              if fresh else 1.0)
                    state.condition.wait(timeout=wait_s)
                analysis = state.analysis
                last_analysis = state.analysis_sequence
            next_encode_ns = time.monotonic_ns() + preview_interval_ns
            (frame, pose, quality, raw, stable, fps, pose_ms, total_ms,
             dropped, capture_ns) = analysis[:10]
            frame_id = analysis[10] if len(analysis) > 10 else last_analysis
            preview_fps = (1.0 / statistics.fmean(preview_intervals)
                           if preview_intervals else 0.0)
            overlay = draw_panel(frame, pose, quality, raw, stable, fps,
                                 preview_fps, pose_ms, total_ms,
                                 previous_jpeg_ms,
                                 (time.monotonic_ns()-capture_ns)/1e6,
                                 dropped, getattr(args, "input_rotate_180", False),
                                 args.display_mirror,
                                 getattr(args, "preview_width", getattr(args, "display_width", 640)))
            encode_started_ns = time.monotonic_ns()
            ok, encoded = cv2.imencode(".jpg", overlay,
                                       [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            ready_ns = time.monotonic_ns()
            previous_jpeg_ms = (ready_ns-encode_started_ns)/1e6
            if previous_ready_ns is not None:
                preview_intervals.append((ready_ns-previous_ready_ns)/1e9)
            previous_ready_ns = ready_ns
            preview_fps = (1.0 / statistics.fmean(preview_intervals)
                           if preview_intervals else 0.0)
            state.publish(encoded.tobytes(), {
                "display_frame_id": frame_id,
                "capture_timestamp_monotonic_ns": capture_ns,
                "frame_ready_monotonic_ns": ready_ns,
                "host_receipt_age_at_publish_ms": round(
                    (ready_ns - capture_ns) / 1e6, 2),
                "frame_age_ms": round((ready_ns-capture_ns)/1e6, 2),
                "preview_fps": round(preview_fps, 2),
                "jpeg_encode_ms": round(previous_jpeg_ms, 2),
            })
    except Exception as exc:
        state.fail_render(exc)
        print(f"Preview render error: {exc}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default=os.environ.get("FCV_CAMERA_DEVICE", "/dev/video73"))
    parser.add_argument("--calibration", type=Path,
                        default=AppConfig().calibration_path)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--input-rotate-180", action="store_true",
                        help="Rotate rectified analysis frame by 180 degrees before Pose inference.")
    parser.add_argument("--display-mirror", action="store_true")
    parser.add_argument("--preview-width", "--display-width", dest="preview_width",
                        type=int, default=640,
                        help="Final display width only; inference remains at calibrated resolution")
    parser.add_argument("--jpeg-quality", type=int, default=55)
    parser.add_argument("--preview-fps", type=float, default=10.0)
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Optional finite camera run for diagnostics")
    args = parser.parse_args()
    if not 480 <= args.preview_width <= 1280:
        parser.error("--preview-width must be within 480..1280")
    if not 30 <= args.jpeg_quality <= 90:
        parser.error("--jpeg-quality must be within 30..90")
    if not 1 <= args.preview_fps <= 60:
        parser.error("--preview-fps must be within 1..60")
    state = PreviewState()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    server.daemon_threads = True
    worker = threading.Thread(target=run_camera, args=(args, state, server),
                              name="stage4-preview-camera", daemon=True)
    renderer = threading.Thread(target=run_render, args=(args, state),
                                name="stage4-preview-render", daemon=True)
    renderer.start()
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
        renderer.join(timeout=7)
    return 1 if state.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
