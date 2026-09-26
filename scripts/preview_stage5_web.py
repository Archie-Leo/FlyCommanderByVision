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
from stage5_v2.ownership import Settings
from stage5_v2.pipeline import Stage5PipelineV2
from stage5_v2.reid_rknn import RKNNOSNetEmbedder, VALIDATED_RK3576_SHA256
from stage5_v2.tracker import BotSortTrackerAdapter
from stage6.rotated_depth import RotatedDepthAdapter


class TrackedRotatedDepthAdapter(RotatedDepthAdapter):
    """Stage5-only tracked ROI path; keep Stage6 adapter unchanged."""

    def process_tracked(self, left_raw, right_raw, bboxes, track_ids, *,
                        frame_id, timestamp_ms, rectified_left=None):
        mapping_start = time.perf_counter()
        if self._original_left is None or rectified_left is not self._analysis_left:
            raise RuntimeError("Rotated depth frame was not prepared")
        height, width = rectified_left.shape[:2]
        # XYXY boxes have exclusive x2/y2 boundaries.
        source_boxes = [(width-x2, height-y2, width-x1, height-y1)
                        for x1, y1, x2, y2 in bboxes]
        self.last_mapping_ms = (time.perf_counter()-mapping_start)*1000.
        _, depths = self.source.process_tracked(
            left_raw, right_raw, source_boxes, track_ids, frame_id=frame_id,
            timestamp_ms=timestamp_ms, rectified_left=self._original_left)
        return rectified_left, depths

    @property
    def last_profile_ms(self):
        profile = dict(self.source.last_profile_ms)
        profile["bbox_mapping"] = getattr(self, "last_mapping_ms", 0.)
        return profile

    def __getattr__(self, name):
        if name in ("last_depth_ages_ms", "last_depth_valid", "last_depth_sources",
                    "last_updated_count", "last_roi_shapes"):
            return getattr(self.source, name)
        raise AttributeError(name)


def auto_validation_fields(record):
    """Aliases for the existing Stage5 frame record, without new decisions."""
    second = record.get("second_candidate") or {}
    auto_state = record.get("auto_reauthorize_state")
    decision = ("AUTHORIZED" if record.get("ownership_state") == "LOCKED_HIGH"
                and auto_state == "SUCCESS"
                and record.get("authorization_source") == "AUTO_REAUTHORIZE" else
                "CONFIRMING" if auto_state == "CONFIRMING" else
                "REJECTED" if record.get("ownership_state") == "OPERATOR_LOST"
                and record.get("auto_reauthorize_reject_reason") not in
                (None, "AUTO_REAUTH_NO_CANDIDATE", "AUTO_REAUTH_DISABLED") else "NONE")
    return dict(stage5_state=record.get("ownership_state"),
                operator_track_id=record.get("current_track_id"),
                lost_operator_identity=(record.get("operator_session_id")
                    if record.get("ownership_state") in
                    ("OPERATOR_LOST", "AUTO_REAUTHORIZE_CONFIRMING", "REAUTHORIZING") else None),
                candidate_track_id=record.get("auto_reauthorize_candidate_id"),
                reid_similarity=record.get("auto_reauthorize_reid_similarity"),
                gallery_best=record.get("auto_reauthorize_gallery_max"),
                gallery_second=second.get("S_reid"),
                gallery_second_similarity=second.get("S_reid"),
                margin=record.get("auto_reauthorize_margin"),
                confirm_count=record.get("auto_reauthorize_frames"),
                final_decision=decision)


PAGE = b"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage5 Operator Lock</title>
<style>body{margin:0;background:#17191d;color:#eee;font:16px sans-serif}
main{padding:12px}canvas{display:block;max-width:100%;height:auto;background:#111}</style>
<main><h2>Stage5 Operator Lock</h2><p>Visual-only. No PX4 output.</p>
<canvas id="preview" width="640" height="480"></canvas>
<p id="status">Waiting for camera...</p><p id="reauth"></p>
<p id="evidence"></p><p id="age"></p></main>
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
document.getElementById('status').textContent=`${s.ownership_state||'WAITING'} | UI ${s.ui_state||'-'} | Reject ${s.reject_reason||'-'} | Session ${s.operator_session_id||'-'} | Track ${s.current_track_id??'-'} | Pose ${s.pose_count??0} | People ${s.people_count??0} | T-Pose ${c.tpose_matched??'-'} | Pose valid ${c.pose_valid??'-'} | Crop ${c.crop_quality??'-'} | Pose ${s.pose_latency_ms?.toFixed(0)??'-'} ms | Stage5 ${s.latency_ms?.stage5_total?.toFixed(0)??'-'} ms | Loop ${s.analysis_fps??'-'} FPS | Depth ${s.latency_ms?.stereo_depth?.toFixed(0)??'-'} ms | Person depth ${s.operator_depth?.depth_m?.toFixed(2)??'-'} m | Age ${s.operator_depth?.age_ms?.toFixed(0)??'-'} ms | Valid ${s.operator_depth?.valid??'-'} | ROI ${JSON.stringify(s.depth_roi_shapes||[])}`;
document.getElementById('reauth').textContent=`Auto Reauthorize ${s.auto_reauthorize_enabled?'ENABLED':'DISABLED'} | ${s.auto_reauthorize_state||'-'} | Lost session ${s.lost_operator_identity||'-'} | Old track ${s.current_track_id??'-'} | Candidate track ${s.auto_reauthorize_candidate_id??'-'} | Frames ${s.auto_reauthorize_frames??0} | Time ${s.auto_reauthorize_elapsed_ms??0} ms | Reason ${s.auto_reauthorize_reject_reason||'-'} | Decision ${s.final_decision||'-'}`;
document.getElementById('evidence').textContent=`Gallery ${s.gallery_size??0} | Candidate ReID ${s.auto_reauthorize_reid_similarity?.toFixed(3)??'-'} | Best ${s.auto_reauthorize_gallery_max?.toFixed(3)??'-'} | TopK ${s.auto_reauthorize_gallery_topk_mean?.toFixed(3)??'-'} | Matches ${s.auto_reauthorize_gallery_match_count??'-'} | Second ${s.gallery_second_similarity?.toFixed(3)??'-'} | Margin ${s.auto_reauthorize_margin?.toFixed(3)??'-'} | Score ${s.auto_reauthorize_identity_score?.toFixed(3)??'-'} | Old/New session ${s.old_operator_session_id||'-'} / ${s.new_operator_session_id||'-'}`;
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

    def publish_analysis(self, image, people, authorized, record, capture_ns, fps,
                         pose_count, pose_latency_ms):
        observations = record.get("depth_observations", [])
        operator_depth = next((item for item in observations
                               if item["track_id"] == record["current_track_id"]),
                              observations[0] if observations else None)
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
                pose_latency_ms=pose_latency_ms,
                rectify_ms=record.get("rectify_ms"),
                depth_age_ms=record.get("depth_age_ms", []),
                depth_observations=observations,
                operator_depth=operator_depth,
                depth_valid=record.get("depth_valid", []),
                depth_source_frame_ids=record.get("depth_source_frame_ids", []),
                depth_updated_count=record.get("depth_updated_count", 0),
                depth_roi_shapes=record.get("depth_roi_shapes", []),
                depth_profile_ms=record.get("depth_profile_ms", {}),
                acquisition_checks=record.get("acquisition_checks", []),
                best_candidate=record.get("best_candidate"),
                timestamp_ms=record.get("timestamp_ms"),
                gallery_size=record.get("gallery_size"),
                lost_operator_identity=record.get("lost_operator_identity"),
                gallery_second_similarity=record.get("gallery_second_similarity"),
                final_decision=record.get("final_decision"),
                **{key: record.get(key) for key in (
                    "auto_reauthorize_enabled", "auto_reauthorize_state",
                    "auto_reauthorize_candidate_id", "auto_reauthorize_reid_similarity",
                    "auto_reauthorize_gallery_max", "auto_reauthorize_gallery_topk_mean",
                    "auto_reauthorize_gallery_match_count", "auto_reauthorize_identity_score",
                    "auto_reauthorize_margin", "auto_reauthorize_frames",
                    "auto_reauthorize_elapsed_ms", "auto_reauthorize_deadline_ms",
                    "auto_reauthorize_reject_reason", "authorization_source",
                    "old_operator_session_id", "new_operator_session_id")},
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
    log_handle = None
    error = None
    try:
        if args.log_jsonl is not None:
            log_path = args.log_jsonl.expanduser().resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("x", encoding="utf-8")
        embedder = RKNNOSNetEmbedder(args.reid_model,
                                    expected_sha256=VALIDATED_RK3576_SHA256)
        depth = StereoPersonDepthAdapter(
            args.calibration, args.stage2_root,
            depth_roi_margin_ratio=args.depth_roi_margin_ratio,
            depth_rate_hz=args.depth_rate_hz,
            depth_max_age_ms=args.depth_max_age_ms)
        analysis_depth = TrackedRotatedDepthAdapter(depth)
        tracker = BotSortTrackerAdapter(args.boxmot_lib)
        pipeline = Stage5PipelineV2(
            tracker, embedder, analysis_depth, roi_depth=True,
            ownership_settings=Settings(auto_reauthorize_enabled=args.auto_reauthorize))
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
                rectify_start = time.perf_counter()
                original_left = cv2.remap(frame.left_raw, *depth.maps[0], cv2.INTER_LINEAR)
                rectify_ms = (time.perf_counter()-rectify_start)*1000.
                analysis_left = cv2.rotate(original_left, cv2.ROTATE_180)
                analysis_depth.prepare(original_left, analysis_left)
                pose_frame = backend.infer(analysis_left, frame.timestamp_ns//1_000_000,
                                           frame.frame_id)
                image, people, authorized, record = pipeline.process(
                    frame.left_raw, right, pose_frame, evaluator, normalizer,
                    rectified_left=analysis_left)
                record["rectify_ms"] = rectify_ms
                record["pose_inference_latency_ms"] = pose_frame.inference_latency_ms
                record["capture_monotonic_ns"] = frame.timestamp_ns
                record.update(auto_validation_fields(record))
                if log_handle is not None:
                    log_handle.write(json.dumps(record, allow_nan=False) + "\n")
                    log_handle.flush()
                processed += 1
                fps = processed/max(1e-6, time.monotonic()-started)
                state.publish_analysis(image, people, authorized, record,
                                       frame.timestamp_ns, fps, len(pose_frame.poses),
                                       pose_frame.inference_latency_ms)
                if args.max_frames and processed >= args.max_frames:
                    break
    except Exception as exc:
        error = exc
        print(f"Stage5 camera error: {exc}", file=sys.stderr, flush=True)
    finally:
        if log_handle is not None:
            log_handle.close()
        if tracker is not None:
            tracker.close()
        if embedder is not None:
            embedder.close()
        state.stop(error)
        if error or args.max_frames:
            server.shutdown()


def parse_args(argv=None):
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
    parser.add_argument("--depth-roi-margin-ratio", type=float, default=.10)
    parser.add_argument("--depth-rate-hz", type=float, default=5.)
    parser.add_argument("--depth-max-age-ms", type=int, default=500)
    parser.add_argument("--auto-reauthorize", "--enable-auto-reauthorize",
                        dest="auto_reauthorize", action="store_true",
                        help="Enable the existing Stage5 experimental auto reauthorization")
    parser.add_argument("--log-jsonl", type=Path,
                        help="Write existing Stage5 frame records as JSONL (must be a new path)")
    args = parser.parse_args(argv)
    if not 320 <= args.preview_width <= 1280:
        parser.error("--preview-width must be within 320..1280")
    if not 30 <= args.jpeg_quality <= 90:
        parser.error("--jpeg-quality must be within 30..90")
    if not 1 <= args.preview_fps <= 30:
        parser.error("--preview-fps must be within 1..30")
    return args


def main():
    args = parse_args()
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
