#!/usr/bin/env python3
"""One RK3576 camera: MPP JPEG -> tee -> MPP H264 RTP + Stage3-6 dry-run.

The only CPU pixel boundary is videoconvert/appsink on the full SBS AI branch.
No ROS, PX4, flight gateway, or live control publisher is created here.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict, deque
from dataclasses import replace
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts")] + [str(ROOT / name) for name in
                ("stage3_pose", "stage4_gesture", "stage5_operator", "stage6_closed_loop")]
# Ubuntu's gi is installed for system Python; the vision venv deliberately omits
# system site packages. Append only the gi location, after the vision packages.
sys.path.append("/usr/lib/python3/dist-packages")
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

from camera.stereo_left_source import CameraFrame
from config import AppConfig, BackendConfig, CameraConfig
from pose.factory import create_pose_backend
from pose.normalize import SkeletonNormalizer
from pose.quality import PoseQualityEvaluator
from stage5_v2.depth import StereoPersonDepthAdapter
from stage5_v2.ownership import Settings
from stage5_v2.pipeline import Stage5PipelineV2
from stage5_v2.reid_rknn import RKNNOSNetEmbedder, VALIDATED_RK3576_SHA256
from stage5_v2.tracker import BotSortTrackerAdapter
from preview_stage5_web import Stage6DryRun, TrackedRotatedDepthAdapter


def pipeline_description(args):
    """No second camera or JPEG decoder may be added below the tee."""
    if not re.fullmatch(r"/dev/video[0-9]+", args.camera):
        raise ValueError("camera must be a /dev/videoN device")
    ipaddress.ip_address(args.host)
    if not 1 <= args.port <= 65535:
        raise ValueError("invalid UDP port")
    if args.camera_fps not in (30, 60):
        raise ValueError("camera FPS must be 30 or 60")
    if not 1 <= args.video_fps <= args.camera_fps:
        raise ValueError("video FPS must be within camera FPS")
    if not 500 <= args.bitrate_kbps <= 20000:
        raise ValueError("invalid bitrate")
    def queue(name):
        return (f"queue name={name} max-size-buffers=1 max-size-bytes=0 "
                "max-size-time=0 leaky=downstream")
    video_branch = (
        f"decoded. ! {queue('q_video')} ! fakesink sync=false async=false"
        if getattr(args, "no_video", False) else
        f"decoded. ! {queue('q_video')} ! videorate drop-only=true "
        f"! video/x-raw,framerate={args.video_fps}/1 "
        "! videocrop right=1280 ! video/x-raw,format=NV12,width=1280,height=960 "
        f"! mpph264enc name=encoder rotation=180 bps={args.bitrate_kbps * 1000} "
        "gop=30 rc-mode=cbr max-pending=1 header-mode=each-idr profile=baseline "
        "! h264parse name=video_parser config-interval=-1 "
        "! rtph264pay pt=96 config-interval=-1 mtu=1200 "
        f"! udpsink host={args.host} port={args.port} sync=false async=false"
    )
    return (
        f"v4l2src name=camera device={args.camera} io-mode=2 do-timestamp=true "
        f"! image/jpeg,width=2560,height=960,framerate={args.camera_fps}/1 "
        f"! {queue('q_source')} ! jpegparse name=jpeg_parser ! mppjpegdec name=jpeg_decoder format=NV12 "
        "dma-feature=false ! video/x-raw,format=NV12,width=2560,height=960 "
        "! tee name=decoded "
        f"{video_branch} "
        f"decoded. ! {queue('q_ai')} ! videoconvert "
        "! video/x-raw,format=BGR,width=2560,height=960 "
        "! appsink name=ai_sink max-buffers=1 drop=true sync=false "
        "emit-signals=false enable-last-sample=false wait-on-eos=false"
    )


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.source_count = 0
        self.video_count = 0
        self.compressed_count = 0
        self.compressed_stamps = OrderedDict()
        self.decoded_stamps = OrderedDict()
        self.decoded_count = 0
        self.max_pts_delta_ms = 0.0
        self.probe_error = None
        self.ai_count = 0
        self.ai_dropped = 0
        self.last_source_id = None
        self.ai_ages_ms = deque(maxlen=8192)
        self.pose_ms = deque(maxlen=8192)
        self.stage5_ms = deque(maxlen=8192)
        self.reid_ms = deque(maxlen=8192)
        self.depth_ms = deque(maxlen=8192)

    def source_probe(self, pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            with self.lock:
                self.source_count += 1
        return Gst.PadProbeReturn.OK

    def compressed_probe(self, pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            with self.lock:
                source_id = self.compressed_count
                self.compressed_count += 1
                if buffer.pts == Gst.CLOCK_TIME_NONE:
                    self.probe_error = "JPEG decoder input has no PTS"
                else:
                    self.compressed_stamps[int(buffer.pts)] = (source_id, time.monotonic_ns())
                    if len(self.compressed_stamps) > 512:
                        self.probe_error = "JPEG decoder input timestamp map overflow"
        return Gst.PadProbeReturn.OK

    def video_probe(self, pad, info):
        if info.get_buffer() is not None:
            with self.lock:
                self.video_count += 1
        return Gst.PadProbeReturn.OK

    def decoder_probe(self, pad, info):
        buffer = info.get_buffer()
        if buffer is not None and buffer.pts != Gst.CLOCK_TIME_NONE:
            pts = int(buffer.pts)
            with self.lock:
                self.decoded_count += 1
                source = self.compressed_stamps.pop(pts, None)
                if source is None:
                    self.probe_error = "JPEG decoder output PTS has no matching parser input"
                    return Gst.PadProbeReturn.OK
                self.decoded_stamps[pts] = source
                if len(self.decoded_stamps) > 512:
                    self.decoded_stamps.popitem(last=False)
        return Gst.PadProbeReturn.OK

    def source_for(self, pts):
        with self.lock:
            return self.decoded_stamps.get(int(pts))

    def add_ai(self, source_id, age_ms, pose_ms, latency):
        with self.lock:
            self.ai_count += 1
            if self.last_source_id is not None and source_id > self.last_source_id:
                self.ai_dropped += max(0, source_id-self.last_source_id-1)
            self.last_source_id = source_id
            self.ai_ages_ms.append(age_ms)
            self.pose_ms.append(pose_ms)
            self.stage5_ms.append(latency.get("stage5_total", 0.0))
            self.reid_ms.append(latency.get("osnet_total", 0.0))
            self.depth_ms.append(latency.get("stereo_depth", 0.0))


def read_frame(sink, counters, frame_id):
    sample = sink.emit("try-pull-sample", Gst.SECOND // 2)
    if sample is None:
        return None, None
    buffer = sample.get_buffer()
    caps = sample.get_caps().get_structure(0)
    if caps.get_string("format") != "BGR" or caps.get_value("width") != 2560 or caps.get_value("height") != 960:
        raise RuntimeError("AI appsink negotiated an unexpected pixel format/shape")
    mapped, memory = buffer.map(Gst.MapFlags.READ)
    if not mapped:
        raise RuntimeError("Cannot map AI Gst.Buffer")
    try:
        raw = np.frombuffer(memory.data, dtype=np.uint8).copy().reshape(960, 2560, 3)
    finally:
        buffer.unmap(memory)
    source = counters.source_for(buffer.pts)
    if source is None:
        with counters.lock:
            nearest = sorted(counters.decoded_stamps.items(), key=lambda x: abs(x[0]-int(buffer.pts)))[:5]
        raise RuntimeError(f"Missing decoded timestamp for PTS {buffer.pts}; nearest={nearest}")
    source_id, capture_ns = source
    age_ms = (time.monotonic_ns()-capture_ns)/1e6
    frame = CameraFrame(frame_id, capture_ns, raw, raw[:, :1280], age_ms)
    # The processed frame_id is contiguous for Stage4 temporal semantics;
    # source_id separately reveals dropped camera frames.
    return frame, (source_id, age_ms, int(buffer.pts))


def memory_kb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def available_kb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def temperature_c():
    values = []
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            values.append(int(path.read_text().strip())/1000)
        except (OSError, ValueError):
            pass
    return max(values) if values else None


def check_bus(bus):
    while True:
        msg = bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING)
        if msg is None:
            return
        if msg.type == Gst.MessageType.ERROR:
            error, debug = msg.parse_error()
            raise RuntimeError(f"GStreamer {msg.src.get_name()}: {error}; {debug}")
        if msg.type == Gst.MessageType.EOS:
            raise RuntimeError("GStreamer pipeline EOS")
        error, debug = msg.parse_warning()
        print(f"GStreamer WARNING {msg.src.get_name()}: {error}; {debug}", file=sys.stderr, flush=True)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera", default="/dev/video73")
    p.add_argument("--camera-fps", type=int, default=60)
    p.add_argument("--video-fps", type=int, default=30)
    p.add_argument("--host", default="192.168.1.16")
    p.add_argument("--port", type=int, default=5600)
    p.add_argument("--bitrate-kbps", type=int, default=4000)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--no-ai", action="store_true", help="Video baseline with AI appsink still bounded")
    p.add_argument("--no-video", action="store_true", help="AI baseline with video branch replaced by fakesink")
    p.add_argument("--ai-sleep-ms", type=float, default=0, help="Test-only AI slowdown")
    p.add_argument("--auto-reauthorize", action="store_true")
    p.add_argument("--calibration", type=Path, default=Path(os.environ.get("FCV_CALIBRATION_PATH", ROOT / "configs/calibration/run_b.yaml")))
    p.add_argument("--pose-model", type=Path, default=Path(os.environ.get("FCV_POSE_MODEL_PATH", "")))
    p.add_argument("--reid-model", type=Path, default=ROOT / "models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn")
    p.add_argument("--stage2-root", type=Path, default=ROOT / "stage2_stereo/depth_validation")
    p.add_argument("--boxmot-lib", type=Path, default=ROOT / "stage5_operator/build/botsort/botsort_capi.so")
    p.add_argument("--metrics-jsonl", type=Path)
    p.add_argument("--stage6-jsonl", type=Path)
    return p.parse_args()


def main():
    args = parse_args()
    description = pipeline_description(args)
    Gst.init(None)
    pipeline = Gst.parse_launch(description)
    camera = pipeline.get_by_name("camera")
    jpeg_parser = pipeline.get_by_name("jpeg_parser")
    decoder = pipeline.get_by_name("jpeg_decoder")
    parser = pipeline.get_by_name("video_parser")
    sink = pipeline.get_by_name("ai_sink")
    counters = Counters()
    camera.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, counters.source_probe)
    jpeg_parser.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, counters.compressed_probe)
    decoder.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, counters.decoder_probe)
    if parser is not None:
        parser.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, counters.video_probe)
    bus = pipeline.get_bus()
    embedder = tracker = dry_run = None
    metrics = None
    started = time.monotonic()
    last_report_at = started
    last_cpu_at = os.times()
    last_source_count = last_video_count = last_ai_count = 0
    state = "NO_OPERATOR"
    intent = "HOVER"
    failed = None
    lease_stop = threading.Event()
    lease_thread = None
    try:
        if args.metrics_jsonl:
            path = args.metrics_jsonl.expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            metrics = path.open("x", encoding="utf-8")
        if not args.no_ai:
            embedder = RKNNOSNetEmbedder(args.reid_model, expected_sha256=VALIDATED_RK3576_SHA256)
            depth = StereoPersonDepthAdapter(args.calibration, args.stage2_root)
            analysis_depth = TrackedRotatedDepthAdapter(depth)
            tracker = BotSortTrackerAdapter(args.boxmot_lib)
            stage5 = Stage5PipelineV2(tracker, embedder, analysis_depth, roi_depth=True,
                ownership_settings=Settings(auto_reauthorize_enabled=args.auto_reauthorize))
            config = AppConfig(camera=replace(CameraConfig(), device=args.camera),
                backend=replace(BackendConfig(), num_poses=4))
            evaluator = PoseQualityEvaluator(config.quality)
            normalizer = SkeletonNormalizer(config.normalization)
            dry_run = Stage6DryRun(args.stage6_jsonl)
            def lease_clock():
                while not lease_stop.wait(.05):
                    dry_run.tick()
            lease_thread = threading.Thread(target=lease_clock, daemon=True)
            lease_thread.start()
        else:
            stage5 = config = evaluator = normalizer = dry_run = depth = analysis_depth = None
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer pipeline failed to start")
        print("FAN-OUT DRY-RUN NO PX4 OUTPUT", flush=True)
        print(f"Camera {args.camera} {args.camera_fps} FPS, video {'OFF' if args.no_video else f'{args.video_fps} FPS to {args.host}:{args.port}'}, AI {'OFF' if args.no_ai else 'ON'}", flush=True)
        if args.no_ai:
            backend_context = None
        else:
            backend_context = create_pose_backend(config.backend, "rknn", args.pose_model)
        try:
            if backend_context is not None:
                backend = backend_context.__enter__()
            frame_id = 0
            while time.monotonic()-started < args.duration:
                check_bus(bus)
                if counters.probe_error:
                    raise RuntimeError(counters.probe_error)
                if args.no_ai:
                    time.sleep(.05)
                else:
                    frame, source = read_frame(sink, counters, frame_id)
                    if frame is None:
                        continue
                    source_id, age_ms, gst_pts_ns = source
                    right = frame.raw_sbs[:, 1280:].copy()
                    original_left = cv2.remap(frame.left_raw, *depth.maps[0], cv2.INTER_LINEAR)
                    analysis_left = cv2.rotate(original_left, cv2.ROTATE_180)
                    analysis_depth.prepare(original_left, analysis_left)
                    pose_frame = backend.infer(analysis_left, frame.timestamp_ns//1_000_000, frame.frame_id)
                    image, people, authorized, record = stage5.process(
                        frame.left_raw, right, pose_frame, evaluator, normalizer,
                        rectified_left=analysis_left)
                    dry_run.submit(authorized, record)
                    stage6_snapshot = dry_run.snapshot()
                    decision = stage6_snapshot["stage6_decision"]
                    state = record["ownership_state"]
                    intent = decision["intent"]
                    counters.add_ai(source_id, age_ms, pose_frame.inference_latency_ms, record["latency_ms"])
                    if metrics:
                        metrics.write(json.dumps({
                            "output_mode": "DRY-RUN NO PX4 OUTPUT", "frame_id": frame_id,
                            "source_frame_id": source_id, "capture_monotonic_ns": frame.timestamp_ns,
                            "gst_pts_ns": gst_pts_ns, "frame_age_at_ai_start_ms": age_ms,
                            "pose_ms": pose_frame.inference_latency_ms,
                            "stage5_latency_ms": record["latency_ms"],
                            "stage5_state": state, "stage4_raw": record.get("gesture_raw"),
                            "stage4_stable": record.get("gesture_stable"),
                            "stage6": decision,
                            "stage6_latency_ms": stage6_snapshot["stage6_latency_ms"],
                        }, allow_nan=False)+"\n")
                    frame_id += 1
                    if args.ai_sleep_ms:
                        time.sleep(args.ai_sleep_ms/1000)
                now = time.monotonic()
                if now-last_report_at >= 5:
                    with counters.lock:
                        src, vid, ai = counters.source_count, counters.video_count, counters.ai_count
                        drops = counters.ai_dropped
                        last_age = counters.ai_ages_ms[-1] if counters.ai_ages_ms else None
                    dt = now-last_report_at
                    cpu_now = os.times()
                    cpu_pct = 100*((cpu_now.user+cpu_now.system)
                        -(last_cpu_at.user+last_cpu_at.system))/dt
                    report = {"event": "WINDOW", "elapsed_s": round(now-started, 2),
                        "camera_fps": round((src-last_source_count)/dt, 2),
                        "video_fps": round((vid-last_video_count)/dt, 2),
                        "ai_fps": round((ai-last_ai_count)/dt, 2),
                        "ai_frame_age_ms": round(last_age, 2) if last_age is not None else None,
                        "ai_dropped_source_frames": drops, "stage5_state": state,
                        "stage6_intent": intent, "rss_kb": memory_kb(),
                        "process_cpu_pct": round(cpu_pct, 1),
                        "q_source": pipeline.get_by_name("q_source").get_property("current-level-buffers"),
                        "q_video": pipeline.get_by_name("q_video").get_property("current-level-buffers"),
                        "q_ai": pipeline.get_by_name("q_ai").get_property("current-level-buffers"),
                        "mem_available_kb": available_kb(), "max_temp_c": temperature_c(),
                        "output_mode": "DRY-RUN NO PX4 OUTPUT"}
                    print(json.dumps(report), flush=True)
                    if metrics:
                        metrics.write(json.dumps(report)+"\n")
                        metrics.flush()
                    last_report_at = now
                    last_cpu_at = cpu_now
                    last_source_count, last_video_count, last_ai_count = src, vid, ai
        finally:
            if backend_context is not None:
                backend_context.__exit__(None, None, None)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        failed = str(exc)
        print("FAN-OUT FAIL-CLOSED:", failed, file=sys.stderr, flush=True)
    finally:
        lease_stop.set()
        if lease_thread is not None:
            lease_thread.join(timeout=2)
        if dry_run is not None:
            dry_run.close()
        pipeline.set_state(Gst.State.NULL)
        if tracker is not None:
            tracker.close()
        if embedder is not None:
            embedder.close()
        if metrics is not None:
            metrics.close()
    with counters.lock:
        summary = {"status": "ERROR" if failed else "COMPLETE", "error": failed,
            "output_mode": "DRY-RUN NO PX4 OUTPUT", "camera_open_count": 1,
            "mjpeg_decoder_count": 1, "camera_source_frames": counters.source_count,
            "decoded_frames": counters.decoded_count,
            "max_source_decoder_pts_delta_ms": round(counters.max_pts_delta_ms, 3),
            "video_encoded_frames": counters.video_count, "ai_processed_frames": counters.ai_count,
            "ai_dropped_source_frames": counters.ai_dropped,
            "elapsed_s": round(time.monotonic()-started, 3), "rss_kb": memory_kb()}
    print(json.dumps(summary), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
