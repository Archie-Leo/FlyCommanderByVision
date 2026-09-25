#!/usr/bin/env python3
"""Visual Stage 5 -> AuthorizedGestureV1 -> ROS Intent runner.

Default topic is isolated dry-run. This program never arms/takes off/lands.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import cv2


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    repo = Path(os.environ["REPO_ROOT"]) if "REPO_ROOT" in os.environ else None
    p.add_argument("--stage3-root",type=Path,default=repo / "stage3_pose" if repo else Path("~/drone_stage3_pose"))
    p.add_argument("--stage4-root",type=Path,default=repo / "stage4_gesture" if repo else Path("~/drone_stage4_gesture"))
    p.add_argument("--stage5-root",type=Path,default=repo / "stage5_operator" if repo else Path("~/drone_stage5_operator"))
    p.add_argument("--stage2-root",type=Path,default=repo / "stage2_stereo/depth_validation" if repo else Path("~/drone_stage2/stereo_depth_validation"))
    p.add_argument("--calibration",type=Path,default=Path(os.environ["FCV_CALIBRATION_PATH"]) if "FCV_CALIBRATION_PATH" in os.environ else Path("~/drone_stage2/stereo_calibration/stereo_calibration_output/20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml"))
    p.add_argument("--boxmot-lib",type=Path,default=repo / "stage5_operator/build/botsort/botsort_capi.so" if repo else Path("~/drone_stage5_operator/build/botsort/botsort_capi.so"))
    p.add_argument("--torchreid-root",type=Path,default=repo / "third_party/deep-person-reid" if repo else Path("~/drone_vision_refs/operator_lock/deep-person-reid"))
    p.add_argument("--osnet-checkpoint",type=Path,default=None)
    p.add_argument("--reid-backend",choices=("torch","rknn"),
                   default=os.environ.get("FCV_REID_BACKEND","torch"))
    p.add_argument("--rknn-osnet-model",type=Path,
                   default=repo / "models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn" if repo else None)
    p.add_argument("--rknn-osnet-sha256",default=None)
    p.add_argument("--camera",default=os.environ.get("FCV_CAMERA_DEVICE","/dev/video0"))
    p.add_argument("--input-rotate-180",action="store_true",
                   help="Rotate only the rectified analysis image; keep stereo depth in Run B coordinates")
    p.add_argument("--num-poses",type=int,default=4)
    p.add_argument("--auto-reauthorize",action="store_true")
    p.add_argument("--vision-command-timeout-ms",type=int,default=300)
    p.add_argument("--px4-status-timeout-ms",type=int,default=1500,
                   help="live-only PX4 VehicleStatus freshness lease")
    p.add_argument("--topic",choices=("/interaction/intent_dry_run","/interaction/intent"),
                   default="/interaction/intent_dry_run")
    p.add_argument("--allow-live-output",action="store_true",
                   help="required to publish to Gateway topic; only after dry-run Gate")
    p.add_argument("--output",type=Path,default=Path("runs"))
    p.add_argument("--record",action="store_true",
                   help="start annotated AVI recording with the first vision frame")
    p.add_argument("--record-playback-fps",type=float,default=10.0,
                   help="AVI playback FPS only; actual timing is in JSONL")
    p.add_argument("--rosbag",action="store_true",
                   help="record type-checked available ROS topics inside this Run")
    p.add_argument("--no-display",action="store_true")
    p.add_argument("--max-frames",type=int,default=0)
    return p.parse_args()


def main():
    args = parse_args()
    if args.topic == "/interaction/intent" and not args.allow_live_output:
        raise SystemExit("Refusing Gateway topic without --allow-live-output")
    roots = [args.stage3_root.expanduser().resolve(),args.stage4_root.expanduser().resolve(),
             args.stage5_root.expanduser().resolve()]
    for root,marker in zip(roots,("main.py","gesture/geometry.py","stage5_v2/pipeline.py")):
        if not (root/marker).is_file():
            raise FileNotFoundError(f"Frozen dependency missing: {root/marker}")
    sys.path[:0] = [str(root) for root in roots]
    import rclpy
    from rclpy.signals import SignalHandlerOptions
    from rclpy.executors import SingleThreadedExecutor
    from camera.stereo_left_source import open_stereo_source
    from config import AppConfig,BackendConfig,CameraConfig
    from pose.factory import create_pose_backend
    from pose.normalize import SkeletonNormalizer
    from pose.quality import PoseQualityEvaluator
    from stage5_v2.depth import StereoPersonDepthAdapter
    from stage5_v2.ownership import Settings
    from stage5_v2.pipeline import Stage5PipelineV2
    from stage5_v2.reid import OSNetEmbedder
    from stage5_v2.reid_rknn import RKNNOSNetEmbedder, VALIDATED_RK3576_SHA256
    from stage5_v2.tracker import BotSortTrackerAdapter
    from live_operator_v2 import draw as draw_stage5
    from stage6.ros_intent_node import AuthorizedIntentPublisher
    from stage6.gate6c_recorder import Gate6CRecorder, add_evidence_overlay
    from stage6.intent_adapter import GESTURE_TO_INTENT
    from stage6.px4_observer import Gate6CObserver
    from stage6.run_context import RunContext, write_jsonl, utc_now
    from stage6.rotated_depth import RotatedDepthAdapter

    # Fail before camera use if any authority-bearing dependency is missing.
    if args.reid_backend == "rknn":
        if args.rknn_osnet_model is None:
            raise ValueError("--rknn-osnet-model is required for RKNN ReID")
        embedder = RKNNOSNetEmbedder(args.rknn_osnet_model,
            expected_sha256=args.rknn_osnet_sha256 or VALIDATED_RK3576_SHA256)
    else:
        if args.osnet_checkpoint is None:
            raise ValueError("--osnet-checkpoint is required for Torch ReID")
        embedder = OSNetEmbedder(args.osnet_checkpoint,args.torchreid_root)
    depth = StereoPersonDepthAdapter(args.calibration,args.stage2_root)
    analysis_depth = RotatedDepthAdapter(depth) if args.input_rotate_180 else depth
    tracker = BotSortTrackerAdapter(args.boxmot_lib)
    pipeline = Stage5PipelineV2(tracker,embedder,analysis_depth,
        ownership_settings=Settings(auto_reauthorize_enabled=args.auto_reauthorize))
    config = AppConfig(camera=replace(CameraConfig(),device=args.camera),
                       backend=replace(BackendConfig(),num_poses=args.num_poses))
    evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    output = args.output.expanduser().resolve()/datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    output.mkdir(parents=True,exist_ok=False)
    run = RunContext(output)
    run.manifest(status="RUNNING", intent_topic=args.topic,
                 reid_backend=args.reid_backend,reid_model_sha256=embedder.sha256,
                 gateway_topic_explicit=args.allow_live_output,
                 camera_device=args.camera,
                 vision_command_timeout_ms=args.vision_command_timeout_ms,
                 px4_status_timeout_ms=args.px4_status_timeout_ms,
                 intent_publish_hz=20, record_requested=args.record,
                 rosbag_requested=args.rosbag,
                 video_playback_fps=args.record_playback_fps,
                 px4_time_domain="PX4-provided microseconds; domain not assumed; align by host receive monotonic")
    recorder = Gate6CRecorder(output,playback_fps=args.record_playback_fps)
    pending_record_start = args.record
    failure = None
    count = 0
    node = None
    executor = None
    spin_thread = None
    spin_errors = []
    vision_file = None
    intent_file = None
    gateway_file = None
    px4_file = None
    observer = None
    bag_process = None
    bag_log = None
    # Keep ROS alive through SIGINT so the final HOVER reaches the transport.
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    try:
        vision_file = (output/"vision_frames.jsonl").open("w",encoding="utf-8")
        intent_file = (output/"intent_events.jsonl").open("w",encoding="utf-8")
        gateway_file = (output/"gateway_events.jsonl").open("w",encoding="utf-8")
        px4_file = (output/"px4_state.jsonl").open("w",encoding="utf-8")
        node = AuthorizedIntentPublisher(topic=args.topic,
            allow_live_output=args.allow_live_output,
            timeout_ms=args.vision_command_timeout_ms,event_log=intent_file,
            run_context=run,px4_status_timeout_ms=args.px4_status_timeout_ms)
        observer = Gate6CObserver(run,gateway_file,px4_file,intent_topic=args.topic)
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        executor.add_node(observer)
        def spin_safely():
            try:
                executor.spin()
            except Exception as exc:
                spin_errors.append(f"{type(exc).__name__}: {exc}")
                node.vision_failed("ROS_THREAD_EXCEPTION")
        spin_thread = threading.Thread(target=spin_safely,daemon=True)
        spin_thread.start()
        if args.rosbag:
            # A new process group lets Ctrl+C stop the runner first; the bag is
            # then closed deliberately so metadata.yaml is finalized.
            bag_log = (output/"rosbag_process.log").open("w",encoding="utf-8")
            time.sleep(.3)
            bag_process = subprocess.Popen(
                ["bash",str(Path(__file__).parent/"scripts"/"record_gate6c_rosbag.sh"),
                 str(output),"dry-run" if args.topic.endswith("dry_run") else "live"],
                stdout=bag_log,stderr=subprocess.STDOUT,start_new_session=True)
            time.sleep(.7)
            if bag_process.poll() is not None:
                raise RuntimeError(f"rosbag startup failed; see {output/'rosbag_process.log'}")
        with create_pose_backend(config.backend) as backend, open_stereo_source(config.camera) as camera:
            while not args.max_frames or count < args.max_frames:
                if spin_errors:
                    raise RuntimeError("ROS timer failed: "+spin_errors[0])
                processing_start_ns = time.monotonic_ns()
                frame = camera.read()
                right = frame.raw_sbs[:,config.camera.eye_width:].copy()
                rectified_left = cv2.remap(frame.left_raw,*depth.maps[0],cv2.INTER_LINEAR)
                analysis_left = (cv2.rotate(rectified_left,cv2.ROTATE_180)
                                 if args.input_rotate_180 else rectified_left)
                if args.input_rotate_180:
                    analysis_depth.prepare(rectified_left,analysis_left)
                pose_frame = backend.infer(analysis_left,frame.timestamp_ns//1_000_000,frame.frame_id)
                rectified,people,authorized,log = pipeline.process(
                    frame.left_raw,right,pose_frame,evaluator,normalizer,
                    rectified_left=analysis_left)
                log["capture_monotonic_ns"] = frame.timestamp_ns
                log.update(run.stamp(frame.timestamp_ns))
                log["stage6_topic"] = args.topic
                log["pose_count"] = len(pose_frame.poses)
                log["pose_inference_ms"] = pose_frame.inference_latency_ms
                log["frame_processing_ms"] = (time.monotonic_ns()-processing_start_ns)/1_000_000
                accepted = node.submit(authorized,log)
                log["stage6_ms"] = (time.monotonic_ns()-processing_start_ns)/1_000_000-log["frame_processing_ms"]
                log["frame_age_at_intent_ms"] = (time.monotonic_ns()-frame.timestamp_ns)/1_000_000
                log.update(node.authority_snapshot())
                log["identity_authorized"] = bool(
                    authorized.authorization_state == "LOCKED_HIGH" and
                    authorized.operator_session_id and
                    authorized.current_track_id is not None)
                log["intent"] = GESTURE_TO_INTENT.get(authorized.gesture,"HOVER") if accepted else "HOVER"
                log["intent_valid"] = bool(accepted)
                log["motion_lease_active"] = bool(accepted and log["intent"] != "HOVER")
                log["control_reason"] = node.last_submit_reason
                log["authorized_gesture_valid"] = bool(authorized.valid)
                count += 1
                annotated = None
                if not args.no_display or pending_record_start or recorder.active:
                    annotated = draw_stage5(rectified,people,authorized,log)
                    position,status = observer.px4_snapshot()
                    annotated = add_evidence_overlay(
                        annotated,people,operator_track_id=authorized.current_track_id,
                        elapsed_ms=log["run_elapsed_ms"],frame_id=log["frame_id"],
                        session_id=log.get("operator_session_id"),
                        intent=log["intent"],intent_valid=log["intent_valid"],
                        control_reason=log["control_reason"],px4_position=position,
                        px4_status=status,
                        identity_authorized=log["identity_authorized"],
                        flight_authority_enabled=log["flight_authority_enabled"],
                        require_fresh_gesture=log["require_fresh_gesture"],
                        flight_authority_gate_active=log["flight_authority_gate_active"],
                        motion_lease_active=log["motion_lease_active"],
                        flight_nav_state=log.get("px4_nav_state"),
                        authority_transition_reason=log["authority_transition_reason"],
                        recording=pending_record_start or recorder.active)
                if pending_record_start:
                    recorder.start(annotated,{
                        "intent_topic":args.topic,"camera_device":args.camera,
                        "run_t0_monotonic_ns":run.t0_monotonic_ns,
                        "raw_sbs_size":[frame.raw_sbs.shape[1],frame.raw_sbs.shape[0]],
                    })
                    pending_record_start = False
                if recorder.active:
                    record_started_ns = time.monotonic_ns()
                    log["video_clip"] = recorder.clip_paths[-1]
                    log["video_frame_index"] = recorder.frame_count
                    recorder.write(annotated,frame.raw_sbs,log,frame.timestamp_ns)
                    log["record_ms"] = (time.monotonic_ns()-record_started_ns)/1_000_000
                else:
                    log["record_ms"] = 0.0
                    log["video_clip"] = None
                    log["video_frame_index"] = None
                write_jsonl(vision_file,log)
                if not args.no_display:
                    cv2.imshow("Stage 6 Authorized Gesture -> Intent",annotated)
                    key = cv2.waitKey(1)&0xff
                    if key in (ord("q"),27):
                        break
                    if key == ord("x"):
                        pipeline.ownership.reset();pipeline.temporal.reset()
                        node.vision_failed("OPERATOR_RELEASED")
                    if key == ord("r"):
                        if recorder.active:
                            print("RECORDING STOPPED:",recorder.stop("USER_STOP"))
                        else:
                            pending_record_start = True
    except KeyboardInterrupt:
        # Python handles Ctrl+C; the ROS context remains live for safe stop.
        pass
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        print("STAGE6 FAIL-CLOSED:",failure,file=sys.stderr)
    finally:
        if node is not None:
            node.stop_and_publish("VISION_STOPPED" if failure is None else "VISION_PIPELINE_EXCEPTION")
            time.sleep(.15)  # keep ROS transport alive for final safe publication
        if bag_process is not None:
            if bag_process.poll() is None:
                os.killpg(bag_process.pid,signal.SIGINT)
                try:
                    bag_process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(bag_process.pid,signal.SIGTERM)
                    bag_process.wait(timeout=5)
                    failure = failure or "ROSBAG_STOP_TIMEOUT"
            if bag_process.returncode not in (0,130,-signal.SIGINT):
                failure = failure or f"ROSBAG_EXIT_{bag_process.returncode}"
        if bag_log is not None:
            bag_log.close()
        if executor is not None:
            executor.shutdown()
        if spin_thread is not None:
            spin_thread.join(timeout=2)
        if spin_errors and failure is None:
            failure = "ROS_TIMER_EXCEPTION: "+spin_errors[0]
        recorder.stop("ERROR" if failure else "RUN_EXIT")
        if observer is not None:
            observer.destroy_node()
        if node is not None:
            node.destroy_node()
        if vision_file is not None:
            vision_file.close()
        if intent_file is not None:
            intent_file.close()
        if gateway_file is not None:
            gateway_file.close()
        if px4_file is not None:
            px4_file.close()
        rclpy.try_shutdown()
        tracker.close()
        embedder.close()
        if not args.no_display:
            cv2.destroyAllWindows()
    summary = {"status":"ERROR_FAIL_CLOSED" if failure else "VISUAL_ONLY_DRY_RUN_COMPLETE" if args.topic.endswith("dry_run") else "LIVE_INTENT_RUN_COMPLETE",
               "frames":count,"error":failure,"intent_topic":args.topic,
               "no_arm_takeoff_land":True,"gateway_topic_explicit":args.allow_live_output,
               "recordings":recorder.clips,
               "rosbag_requested":args.rosbag,
               "rosbag_metadata_present":(output/"rosbag"/"metadata.yaml").is_file(),
               "observer_event_counts":observer.event_counts if observer else {},
               "observed_topics":observer.observed_topics if observer else {},
               "publisher_sources":observer.publisher_sources if observer else {},
               "topic_errors":observer.topic_errors if observer else {}}
    (output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    run.manifest(status=summary["status"],ended_utc=utc_now(),
                 intent_topic=args.topic,gateway_topic_explicit=args.allow_live_output,
                 camera_device=args.camera,vision_command_timeout_ms=args.vision_command_timeout_ms,
                 px4_status_timeout_ms=args.px4_status_timeout_ms,
                 intent_publish_hz=20,record_requested=args.record,
                 rosbag_requested=args.rosbag,
                 recordings=recorder.clips,observed_topics=summary["observed_topics"],
                 publisher_sources=summary["publisher_sources"],
                 topic_errors=summary["topic_errors"],
                 px4_time_domain="PX4-provided microseconds; domain not assumed; align by host receive monotonic")
    try:
        from analyze_gate6c_run import analyze
        analysis = analyze(output)
    except Exception as exc:
        analysis = {"gate6c_result":"INSUFFICIENT_EVIDENCE",
                    "reasons":[f"ANALYSIS_ERROR:{type(exc).__name__}:{exc}"]}
        failure = failure or analysis["reasons"][0]
        summary["status"] = "ERROR_FAIL_CLOSED"
        summary["error"] = failure
    (output/"gate6c_analysis.json").write_text(
        json.dumps(analysis,indent=2,ensure_ascii=False,allow_nan=False)+"\n",
        encoding="utf-8")
    summary["gate6c_analysis"] = {"result":analysis["gate6c_result"],
                                  "reasons":analysis["reasons"],
                                  "per_gesture":analysis.get("per_gesture",{})}
    (output/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",
                                       encoding="utf-8")
    print(json.dumps(summary))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
