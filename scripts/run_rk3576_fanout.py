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
import statistics
import sys
import threading
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts")] + [str(ROOT / name) for name in
                ("stage3_pose", "stage4_gesture", "stage5_operator", "stage6_closed_loop")] + [str(ROOT)]
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
from async_depth import AsyncTrackedRotatedDepthAdapter


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
    ai_format = ("video/x-raw,format=NV12,width=2560,height=960" if
                 getattr(args, "async_perception", False) else
                 "videoconvert ! video/x-raw,format=BGR,width=2560,height=960")
    return (
        f"v4l2src name=camera device={args.camera} io-mode=2 do-timestamp=true "
        f"! image/jpeg,width=2560,height=960,framerate={args.camera_fps}/1 "
        f"! {queue('q_source')} ! jpegparse name=jpeg_parser ! mppjpegdec name=jpeg_decoder format=NV12 "
        "dma-feature=false ! video/x-raw,format=NV12,width=2560,height=960 "
        "! tee name=decoded "
        f"{video_branch} "
        f"decoded. ! {queue('q_ai')} ! {ai_format} "
        "! appsink name=ai_sink max-buffers=1 drop=true sync=false "
        "emit-signals=false enable-last-sample=false wait-on-eos=false"
    )


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.source_count = 0
        self.video_count = 0
        self.video_bytes = 0
        self.compressed_count = 0
        self.compressed_stamps = OrderedDict()
        self.decoded_stamps = OrderedDict()
        self.decoded_count = 0
        self.ai_convert_starts = OrderedDict()
        self.ai_convert_ms = OrderedDict()
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
        buffer = info.get_buffer()
        if buffer is not None:
            with self.lock:
                self.video_count += 1
                self.video_bytes += buffer.get_size()
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

    def ai_convert_start_probe(self, pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            with self.lock:
                self.ai_convert_starts[int(buffer.pts)] = time.perf_counter_ns()
                if len(self.ai_convert_starts) > 512:
                    self.ai_convert_starts.popitem(last=False)
        return Gst.PadProbeReturn.OK

    def ai_convert_end_probe(self, pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            with self.lock:
                start = self.ai_convert_starts.pop(int(buffer.pts), None)
                if start is not None:
                    self.ai_convert_ms[int(buffer.pts)] = (time.perf_counter_ns()-start)/1e6
                    if len(self.ai_convert_ms) > 512:
                        self.ai_convert_ms.popitem(last=False)
        return Gst.PadProbeReturn.OK

    def take_ai_convert_ms(self, pts):
        with self.lock:
            return self.ai_convert_ms.pop(int(pts), None)

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


class LatestFrameSlot:
    """One ref-counted Gst.Sample; replacing an unread sample drops the old one."""

    def __init__(self):
        self.condition = threading.Condition()
        self.latest = None
        self.published = 0
        self.replaced = 0
        self.closed = False
        self.callback_tid = None
        self.callback_ms = deque(maxlen=1024)

    def publish(self, sample):
        start = time.perf_counter_ns()
        with self.condition:
            if self.closed:
                return False
            if self.latest is not None:
                self.replaced += 1
            self.latest = (sample, time.monotonic_ns())
            self.published += 1
            self.callback_tid = threading.get_native_id()
            self.condition.notify()
            self.callback_ms.append((time.perf_counter_ns()-start)/1e6)
        return True

    def take(self, timeout=.1):
        with self.condition:
            if self.latest is None and not self.closed:
                self.condition.wait(timeout)
            item, self.latest = self.latest, None
            return item

    def close(self):
        with self.condition:
            self.closed = True
            self.latest = None
            self.condition.notify_all()

    def status(self):
        with self.condition:
            times = sorted(self.callback_ms)
            return {"size": int(self.latest is not None),
                    "published": self.published, "replaced": self.replaced,
                    "callback_tid": self.callback_tid,
                    "callback_mean_ms": round(statistics.mean(times), 3) if times else None,
                    "callback_p95_ms": round(times[int((len(times)-1)*.95)], 3) if times else None}


def decode_sample(sample, counters, frame_id, *, nv12=False, acquire_ms=0.0):
    buffer = sample.get_buffer()
    caps = sample.get_caps().get_structure(0)
    if caps.get_string("format") != ("NV12" if nv12 else "BGR") or \
            caps.get_value("width") != 2560 or caps.get_value("height") != 960:
        raise RuntimeError("AI appsink negotiated an unexpected pixel format/shape")
    mapped, memory = buffer.map(Gst.MapFlags.READ)
    if not mapped:
        raise RuntimeError("Cannot map AI Gst.Buffer")
    try:
        copy_start = time.perf_counter_ns()
        if nv12:
            pixels = np.frombuffer(memory.data, dtype=np.uint8).copy()
            if pixels.size != 2560 * 960 * 3 // 2:
                raise RuntimeError("Unexpected NV12 layout/padding at AI appsink")
            raw_nv12 = pixels.reshape(1440, 2560)
        else:
            raw = np.frombuffer(memory.data, dtype=np.uint8).copy().reshape(960, 2560, 3)
        copy_ms = (time.perf_counter_ns()-copy_start)/1e6
    finally:
        buffer.unmap(memory)
    convert_ms = counters.take_ai_convert_ms(buffer.pts)
    if nv12:
        convert_start = time.perf_counter_ns()
        raw = cv2.cvtColor(raw_nv12, cv2.COLOR_YUV2BGR_NV12)
        convert_ms = (time.perf_counter_ns()-convert_start)/1e6
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
    return frame, (source_id, age_ms, int(buffer.pts), {
        "buffer_acquire_ms": acquire_ms,
        "full_sbs_copy_ms": copy_ms,
        "nv12_to_bgr_ms": convert_ms,
    })


def read_frame(sink, counters, frame_id):
    acquire_start = time.perf_counter_ns()
    sample = sink.emit("try-pull-sample", Gst.SECOND // 2)
    if sample is None:
        return None, None
    acquire_ms = (time.perf_counter_ns()-acquire_start)/1e6
    return decode_sample(sample, counters, frame_id, acquire_ms=acquire_ms)


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


def run_lease_clock(dry_run, stop):
    """Keep the Stage6 300 ms lease independent of camera and AI callbacks."""
    while not stop.wait(.05):
        dry_run.tick()


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
    p.add_argument("--pause-on-right-ms", type=float, default=0,
                   help="Test-only one-time Perception stall after valid RIGHT")
    p.add_argument("--async-perception", action="store_true",
                   help="V2 bounded NV12 sample slot and perception worker")
    p.add_argument("--async-depth", action="store_true",
                   help="V2 single bounded 5 Hz tracked ROI depth worker")
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
    if args.async_depth and not args.async_perception:
        raise ValueError("--async-depth requires --async-perception")
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
    if not args.async_perception:
        pipeline.get_by_name("q_ai").get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER, counters.ai_convert_start_probe)
        sink.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, counters.ai_convert_end_probe)
    if parser is not None:
        parser.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, counters.video_probe)
    bus = pipeline.get_bus()
    embedder = tracker = dry_run = analysis_depth = None
    metrics = None
    started = time.monotonic()
    last_report_at = started
    last_cpu_at = os.times()
    last_source_count = last_video_count = last_ai_count = last_video_bytes = 0
    state = "NO_OPERATOR"
    intent = "HOVER"
    failed = None
    lease_stop = threading.Event()
    lease_thread = None
    perception_thread = None
    perception_stop = threading.Event()
    perception_error = []
    frame_slot = LatestFrameSlot() if args.async_perception and not args.no_ai else None
    metrics_lock = threading.Lock()
    if frame_slot is not None:
        sink.set_property("emit-signals", True)
        def on_ai_sample(appsink):
            sample = appsink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.EOS
            frame_slot.publish(sample)
            return Gst.FlowReturn.OK
        sink.connect("new-sample", on_ai_sample)
    try:
        if args.metrics_jsonl:
            path = args.metrics_jsonl.expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            metrics = path.open("x", encoding="utf-8")
        if not args.no_ai:
            embedder = RKNNOSNetEmbedder(args.reid_model, expected_sha256=VALIDATED_RK3576_SHA256)
            depth = StereoPersonDepthAdapter(args.calibration, args.stage2_root)
            analysis_depth = (AsyncTrackedRotatedDepthAdapter(depth) if args.async_depth
                              else TrackedRotatedDepthAdapter(depth))
            tracker = BotSortTrackerAdapter(args.boxmot_lib)
            stage5 = Stage5PipelineV2(tracker, embedder, analysis_depth, roi_depth=True,
                ownership_settings=Settings(auto_reauthorize_enabled=args.auto_reauthorize))
            config = AppConfig(camera=replace(CameraConfig(), device=args.camera),
                backend=replace(BackendConfig(), num_poses=4))
            evaluator = PoseQualityEvaluator(config.quality)
            normalizer = SkeletonNormalizer(config.normalization)
            dry_run = Stage6DryRun(args.stage6_jsonl)
            lease_thread = threading.Thread(target=run_lease_clock, args=(dry_run, lease_stop),
                                            name="stage6-lease", daemon=True)
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
            paused_on_right = False
            def process_frame(frame, source):
                nonlocal frame_id, state, intent, paused_on_right
                source_id, age_ms, gst_pts_ns, stage_times = source
                split_start = time.perf_counter_ns()
                right = frame.raw_sbs[:, 1280:].copy()
                stage_times["split_right_copy_ms"] = (time.perf_counter_ns()-split_start)/1e6
                rectify_start = time.perf_counter_ns()
                original_left = cv2.remap(frame.left_raw, *depth.maps[0], cv2.INTER_LINEAR)
                stage_times["rectify_left_ms"] = (time.perf_counter_ns()-rectify_start)/1e6
                rotate_start = time.perf_counter_ns()
                analysis_left = cv2.rotate(original_left, cv2.ROTATE_180)
                stage_times["rotate_left_ms"] = (time.perf_counter_ns()-rotate_start)/1e6
                analysis_depth.prepare(original_left, analysis_left)
                pose_frame = backend.infer(analysis_left, frame.timestamp_ns//1_000_000, frame.frame_id)
                stage_times["pose"] = backend.last_timings
                image, people, authorized, record = stage5.process(
                    frame.left_raw, right, pose_frame, evaluator, normalizer,
                    rectified_left=analysis_left)
                if args.async_depth:
                    analysis_depth.set_identity(record["operator_session_id"],
                                                record["ownership_state"])
                stage_times["stage5"] = record.get("runtime_profile_ms", {})
                stage_times["depth"] = record.get("depth_profile_ms", {})
                if args.async_depth:
                    stage_times["async_depth"] = analysis_depth.status()
                dry_run.submit(authorized, record)
                stage6_snapshot = dry_run.snapshot()
                frame_age_at_output_ms = (time.monotonic_ns()-frame.timestamp_ns)/1e6
                decision = stage6_snapshot["stage6_decision"]
                state = record["ownership_state"]
                intent = decision["intent"]
                counters.add_ai(source_id, age_ms, pose_frame.inference_latency_ms, record["latency_ms"])
                if metrics:
                    item = {"output_mode": "DRY-RUN NO PX4 OUTPUT", "frame_id": frame_id,
                            "source_frame_id": source_id, "capture_monotonic_ns": frame.timestamp_ns,
                            "gst_pts_ns": gst_pts_ns, "frame_age_at_ai_start_ms": age_ms,
                            "frame_age_at_ai_output_ms": frame_age_at_output_ms,
                            "pose_ms": pose_frame.inference_latency_ms,
                            "stage5_latency_ms": record["latency_ms"],
                            "runtime_profile_ms": stage_times,
                            "stage5_state": state, "stage4_raw": record.get("gesture_raw"),
                            "stage4_stable": record.get("gesture_stable"),
                            "stage6": decision,
                            "stage6_latency_ms": stage6_snapshot["stage6_latency_ms"]}
                    with metrics_lock:
                        metrics.write(json.dumps(item, allow_nan=False)+"\n")
                frame_id += 1
                if (args.pause_on_right_ms and not paused_on_right and decision["valid"]
                        and decision["intent"] == "MOVE_RIGHT"):
                    paused_on_right = True
                    pause_start_ms = time.monotonic_ns()//1_000_000
                    if metrics:
                        with metrics_lock:
                            metrics.write(json.dumps({"event": "PERCEPTION_PAUSE_START",
                                "frame_id": frame_id-1, "at_ms": pause_start_ms,
                                "duration_ms": args.pause_on_right_ms})+"\n")
                            metrics.flush()
                    time.sleep(args.pause_on_right_ms/1000)
                    if metrics:
                        with metrics_lock:
                            metrics.write(json.dumps({"event": "PERCEPTION_PAUSE_END",
                                "frame_id": frame_id-1, "at_ms": time.monotonic_ns()//1_000_000,
                                "stage6": dry_run.snapshot()["stage6_decision"]})+"\n")
                            metrics.flush()
                if args.ai_sleep_ms:
                    time.sleep(args.ai_sleep_ms/1000)

            if frame_slot is not None:
                def perception_loop():
                    try:
                        while not perception_stop.is_set():
                            latest = frame_slot.take()
                            if latest is None:
                                continue
                            sample, arrival_ns = latest
                            frame, source = decode_sample(sample, counters, frame_id, nv12=True)
                            source[3]["slot_wait_ms"] = (time.monotonic_ns()-arrival_ns)/1e6
                            process_frame(frame, source)
                    except BaseException as exc:
                        perception_error.append(exc)
                        perception_stop.set()
                perception_thread = threading.Thread(target=perception_loop,
                    name="perception-worker", daemon=True)
                perception_thread.start()
            while time.monotonic()-started < args.duration:
                check_bus(bus)
                if counters.probe_error:
                    raise RuntimeError(counters.probe_error)
                if args.no_ai:
                    time.sleep(.05)
                elif frame_slot is not None:
                    if perception_error:
                        raise RuntimeError("Perception worker failed") from perception_error[0]
                    time.sleep(.02)
                else:
                    frame, source = read_frame(sink, counters, frame_id)
                    if frame is None:
                        continue
                    process_frame(frame, source)
                now = time.monotonic()
                if now-last_report_at >= 5:
                    with counters.lock:
                        src, vid, ai = counters.source_count, counters.video_count, counters.ai_count
                        vid_bytes = counters.video_bytes
                        drops = counters.ai_dropped
                        last_age = counters.ai_ages_ms[-1] if counters.ai_ages_ms else None
                    dt = now-last_report_at
                    cpu_now = os.times()
                    cpu_pct = 100*((cpu_now.user+cpu_now.system)
                        -(last_cpu_at.user+last_cpu_at.system))/dt
                    report = {"event": "WINDOW", "elapsed_s": round(now-started, 2),
                        "camera_fps": round((src-last_source_count)/dt, 2),
                        "video_fps": round((vid-last_video_count)/dt, 2),
                        "video_h264_kbps": round(8*(vid_bytes-last_video_bytes)/dt/1000, 1),
                        "ai_fps": round((ai-last_ai_count)/dt, 2),
                        "ai_frame_age_ms": round(last_age, 2) if last_age is not None else None,
                        "ai_dropped_source_frames": drops, "stage5_state": state,
                        "stage6_intent": intent, "rss_kb": memory_kb(),
                        "process_cpu_pct": round(cpu_pct, 1),
                        "lease_thread_tid": lease_thread.native_id if lease_thread else None,
                        "perception_thread_tid": perception_thread.native_id if perception_thread else None,
                        "ai_callback_tid": frame_slot.callback_tid if frame_slot else None,
                        "latest_frame_slot": frame_slot.status() if frame_slot else None,
                        "async_depth": analysis_depth.status() if args.async_depth else None,
                        "q_source": pipeline.get_by_name("q_source").get_property("current-level-buffers"),
                        "q_video": pipeline.get_by_name("q_video").get_property("current-level-buffers"),
                        "q_ai": pipeline.get_by_name("q_ai").get_property("current-level-buffers"),
                        "mem_available_kb": available_kb(), "max_temp_c": temperature_c(),
                        "output_mode": "DRY-RUN NO PX4 OUTPUT"}
                    depth_samples = analysis_depth.drain_samples() if args.async_depth else []
                    if args.async_depth:
                        report["depth_effective_hz"] = round(len(depth_samples)/dt, 2)
                    print(json.dumps(report), flush=True)
                    if metrics:
                        with metrics_lock:
                            metrics.write(json.dumps(report)+"\n")
                            if depth_samples:
                                metrics.write(json.dumps({"event": "DEPTH_BATCH",
                                    "elapsed_s": round(now-started, 2),
                                    "samples": depth_samples})+"\n")
                            metrics.flush()
                    last_report_at = now
                    last_cpu_at = cpu_now
                    last_source_count, last_video_count, last_ai_count = src, vid, ai
                    last_video_bytes = vid_bytes
        finally:
            perception_stop.set()
            if frame_slot is not None:
                frame_slot.close()
            try:
                if perception_thread is not None:
                    perception_thread.join()
            finally:
                if backend_context is not None:
                    backend_context.__exit__(None, None, None)
            if perception_error:
                raise RuntimeError("Perception worker failed") from perception_error[0]
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
        if args.async_depth and analysis_depth is not None:
            analysis_depth.close()
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
