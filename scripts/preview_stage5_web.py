#!/usr/bin/env python3
"""Visual-only RK3576 Stage5 Operator Lock browser preview."""
from __future__ import annotations

import argparse
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in
                ("stage3_pose", "stage4_gesture", "stage5_operator", "stage6_closed_loop")]

from camera.stereo_left_source import open_stereo_source
from config import AppConfig, BackendConfig, CameraConfig
from live_operator_v2 import draw as draw_stage5
from pose.factory import create_pose_backend
from pose.normalize import SkeletonNormalizer
from pose.quality import PoseQualityEvaluator
from stage5_v2.depth import StereoPersonDepthAdapter
from stage5_v2.pipeline import Stage5PipelineV2
from stage5_v2.reid_rknn import RKNNOSNetEmbedder, VALIDATED_RK3576_SHA256
from stage5_v2.tracker import BotSortTrackerAdapter
from stage6.rotated_depth import RotatedDepthAdapter


PAGE = b"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage5 Operator Lock</title>
<style>body{margin:0;background:#17191d;color:#eee;font:16px sans-serif}
main{padding:12px}canvas{display:block;max-width:100%;height:auto;background:#111}</style>
<main><h2>Stage5 Operator Lock</h2><p>Visual-only. No PX4 output.</p>
<canvas id="preview" width="640" height="480"></canvas>
<p id="status">Waiting for camera...</p><p id="age"></p></main>
<script>
const canvas=document.getElementById('preview'),ctx=canvas.getContext('2d');
let lastSequence=0;
async function frame(){
  const started=performance.now();
  try{
    const response=await fetch('/snapshot.jpg',{cache:'no-store',signal:AbortSignal.timeout(3000)});
    if(response.ok){
      const sequence=Number(response.headers.get('X-Preview-Sequence'));
      if(sequence!==lastSequence){
        const bitmap=await createImageBitmap(await response.blob());
        canvas.width=bitmap.width;canvas.height=bitmap.height;
        ctx.drawImage(bitmap,0,0);bitmap.close();lastSequence=sequence;
        const age=Number(response.headers.get('X-Frame-Age-Ms'))+performance.now()-started;
        document.getElementById('age').textContent=`Frame age at display: ${age.toFixed(0)} ms`;
      }
    }
  }catch(error){document.getElementById('age').textContent='Preview disconnected: '+error.message;}
  setTimeout(frame,Math.max(0,100-(performance.now()-started)));
}
frame();
setInterval(async()=>{try{const s=await(await fetch('/status',{cache:'no-store'})).json();
const c=(s.acquisition_checks||[])[0]||{};
document.getElementById('status').textContent=`${s.ownership_state||'WAITING'} | UI ${s.ui_state||'-'} | Reject ${s.reject_reason||'-'} | Session ${s.operator_session_id||'-'} | Track ${s.current_track_id??'-'} | Pose ${s.pose_count??0} | People ${s.people_count??0} | T-Pose ${c.tpose_matched??'-'} | Pose valid ${c.pose_valid??'-'} | Crop ${c.crop_quality??'-'} | Stage5 ${s.latency_ms?.stage5_total?.toFixed(0)??'-'} ms`;
}catch(_){document.getElementById('status').textContent='Preview disconnected';}},500)
</script></html>"""


class PreviewState:
    def __init__(self):
        self.condition = threading.Condition()
        self.analysis = None
        self.analysis_sequence = 0
        self.jpeg = None
        self.jpeg_sequence = 0
        self.jpeg_capture_ns = 0
        self.status = {}
        self.stopped = False
        self.error = None

    def publish_analysis(self, image, people, authorized, record, capture_ns, fps, pose_count):
        with self.condition:
            self.analysis = (image, people, authorized, record, capture_ns)
            self.analysis_sequence += 1
            self.status = dict(
                frame_id=record["frame_id"], pose_count=pose_count,
                people_count=len(people), ownership_state=record["ownership_state"],
                ui_state=record["ui_state"], reject_reason=record["reject_reason"],
                operator_session_id=record["operator_session_id"],
                current_track_id=record["current_track_id"],
                gesture=authorized.gesture, authorized=authorized.valid,
                analysis_fps=round(fps, 2), latency_ms=record["latency_ms"],
                acquisition_checks=record.get("acquisition_checks", []),
                best_candidate=record.get("best_candidate"),
                capture_age_ms=round((time.monotonic_ns()-capture_ns)/1e6, 2),
            )
            self.condition.notify_all()

    def publish_jpeg(self, jpeg, capture_ns):
        with self.condition:
            self.jpeg = jpeg
            self.jpeg_capture_ns = capture_ns
            self.jpeg_sequence += 1
            self.condition.notify_all()

    def stop(self, error=None):
        with self.condition:
            self.stopped = True
            self.error = str(error) if error else None
            self.condition.notify_all()


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        wbufsize = 0

        def log_message(self, format, *args):
            if self.path == "/":
                print("HTTP " + format % args, flush=True)

        def send_no_cache(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")

        def do_GET(self):
            if self.path == "/":
                data, content_type = PAGE, "text/html; charset=utf-8"
                extras = ()
            elif self.path == "/status":
                with state.condition:
                    payload = dict(state.status, stopped=state.stopped, error=state.error,
                                   preview_sequence=state.jpeg_sequence)
                data, content_type = json.dumps(payload).encode(), "application/json"
                extras = ()
            elif self.path == "/snapshot.jpg":
                with state.condition:
                    data = state.jpeg
                    capture_ns = state.jpeg_capture_ns
                    sequence = state.jpeg_sequence
                if data is None:
                    self.send_error(503, "No Stage5 frame yet")
                    return
                content_type = "image/jpeg"
                extras = (("X-Preview-Sequence", str(sequence)),
                          ("X-Frame-Age-Ms", f"{(time.monotonic_ns()-capture_ns)/1e6:.2f}"))
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for key, value in extras:
                self.send_header(key, value)
            self.send_no_cache()
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass

    return Handler


def render_worker(state, width, quality, fps_cap):
    last_analysis = 0
    next_encode = 0.0
    while True:
        with state.condition:
            state.condition.wait_for(
                lambda: state.analysis_sequence != last_analysis or state.stopped,
                timeout=1)
            if state.analysis_sequence == last_analysis:
                if state.stopped:
                    return
                continue
            analysis = state.analysis
            last_analysis = state.analysis_sequence
        now = time.monotonic()
        if now < next_encode:
            continue  # A later analysis frame replaces this one.
        next_encode = now + 1/fps_cap
        image, people, authorized, record, capture_ns = analysis
        try:
            annotated = draw_stage5(image, people, authorized, record, debug=True)
            if annotated.shape[1] > width:
                height = round(annotated.shape[0]*width/annotated.shape[1])
                annotated = cv2.resize(annotated, (width, height), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", annotated,
                                       [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            state.publish_jpeg(encoded.tobytes(), capture_ns)
        except Exception as exc:
            state.stop(exc)
            print(f"Stage5 render error: {exc}", file=sys.stderr, flush=True)
            return


def camera_worker(args, state, server):
    embedder = tracker = None
    error = None
    try:
        embedder = RKNNOSNetEmbedder(args.reid_model,
                                    expected_sha256=VALIDATED_RK3576_SHA256)
        depth = StereoPersonDepthAdapter(args.calibration, args.stage2_root)
        analysis_depth = RotatedDepthAdapter(depth)
        tracker = BotSortTrackerAdapter(args.boxmot_lib)
        pipeline = Stage5PipelineV2(tracker, embedder, analysis_depth)
        config = AppConfig(camera=replace(CameraConfig(), device=args.camera),
                           backend=replace(BackendConfig(), num_poses=4))
        evaluator = PoseQualityEvaluator(config.quality)
        normalizer = SkeletonNormalizer(config.normalization)
        processed = 0
        started = time.monotonic()
        with create_pose_backend(config.backend, "rknn", args.pose_model) as backend, \
                open_stereo_source(config.camera) as camera:
            while not state.stopped:
                frame = camera.read()
                right = frame.raw_sbs[:, config.camera.eye_width:].copy()
                original_left = cv2.remap(frame.left_raw, *depth.maps[0], cv2.INTER_LINEAR)
                analysis_left = cv2.rotate(original_left, cv2.ROTATE_180)
                analysis_depth.prepare(original_left, analysis_left)
                pose_frame = backend.infer(analysis_left, frame.timestamp_ns//1_000_000,
                                           frame.frame_id)
                image, people, authorized, record = pipeline.process(
                    frame.left_raw, right, pose_frame, evaluator, normalizer,
                    rectified_left=analysis_left)
                processed += 1
                fps = processed/max(1e-6, time.monotonic()-started)
                state.publish_analysis(image, people, authorized, record,
                                       frame.timestamp_ns, fps, len(pose_frame.poses))
                if args.max_frames and processed >= args.max_frames:
                    break
    except Exception as exc:
        error = exc
        print(f"Stage5 camera error: {exc}", file=sys.stderr, flush=True)
    finally:
        if tracker is not None:
            tracker.close()
        if embedder is not None:
            embedder.close()
        state.stop(error)
        if error or args.max_frames:
            server.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default=os.environ.get("FCV_CAMERA_DEVICE", "/dev/video73"))
    parser.add_argument("--calibration", type=Path,
                        default=Path(os.environ.get("FCV_CALIBRATION_PATH",
                            str(ROOT / "configs/calibration/run_b.yaml"))))
    parser.add_argument("--pose-model", type=Path,
                        default=Path(os.environ["FCV_POSE_MODEL_PATH"]) if "FCV_POSE_MODEL_PATH" in os.environ else None)
    parser.add_argument("--reid-model", type=Path,
                        default=ROOT / "models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn")
    parser.add_argument("--stage2-root", type=Path,
                        default=ROOT / "stage2_stereo/depth_validation")
    parser.add_argument("--boxmot-lib", type=Path,
                        default=ROOT / "stage5_operator/build/botsort/botsort_capi.so")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--preview-width", type=int, default=640)
    parser.add_argument("--jpeg-quality", type=int, default=55)
    parser.add_argument("--preview-fps", type=float, default=10)
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()
    if not 320 <= args.preview_width <= 1280:
        parser.error("--preview-width must be within 320..1280")
    if not 30 <= args.jpeg_quality <= 90:
        parser.error("--jpeg-quality must be within 30..90")
    if not 1 <= args.preview_fps <= 30:
        parser.error("--preview-fps must be within 1..30")
    state = PreviewState()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    server.daemon_threads = True
    renderer = threading.Thread(target=render_worker,
                                args=(state, args.preview_width, args.jpeg_quality,
                                      args.preview_fps), daemon=True)
    worker = threading.Thread(target=camera_worker, args=(args, state, server), daemon=True)
    renderer.start()
    worker.start()
    print(f"Stage5 preview: http://{args.host}:{args.port}/", flush=True)
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
