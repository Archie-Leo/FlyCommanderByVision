#!/usr/bin/env python3
"""Visual-only V2 runner; startup fails closed without official OSNet weights."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import cv2


def args_parser():
    p = argparse.ArgumentParser(description="Stage 5 V2 visual-only operator ownership")
    repo = Path(os.environ["REPO_ROOT"]) if "REPO_ROOT" in os.environ else None
    p.add_argument("--stage3-root", type=Path, default=repo / "stage3_pose" if repo else Path("~/drone_stage3_pose"))
    p.add_argument("--stage4-root", type=Path, default=repo / "stage4_gesture" if repo else Path("~/drone_stage4_gesture"))
    p.add_argument("--stage2-root", type=Path, default=repo / "stage2_stereo/depth_validation" if repo else Path("~/drone_stage2/stereo_depth_validation"))
    p.add_argument("--calibration", type=Path, default=Path(os.environ["FCV_CALIBRATION_PATH"]) if "FCV_CALIBRATION_PATH" in os.environ else Path("~/drone_stage2/stereo_calibration/stereo_calibration_output/20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml"))
    p.add_argument("--boxmot-lib", type=Path, default=repo / "stage5_operator/build/botsort/botsort_capi.so" if repo else Path("~/drone_stage5_operator/build/botsort/botsort_capi.so"))
    p.add_argument("--torchreid-root", type=Path, default=Path(os.environ["FCV_TORCHREID_ROOT"]) if "FCV_TORCHREID_ROOT" in os.environ else repo / "third_party/deep-person-reid" if repo else Path("~/drone_vision_refs/operator_lock/deep-person-reid"))
    p.add_argument("--osnet-checkpoint", type=Path, default=repo / "models/reid/osnet_x0_25_msmt17.pth" if repo else None)
    p.add_argument("--reid-backend", choices=("torch", "rknn"), default=os.environ.get("FCV_REID_BACKEND", "torch"))
    p.add_argument("--rknn-osnet-model", type=Path, default=repo / "models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn" if repo else None)
    p.add_argument("--rknn-osnet-sha256", default=None, help="expected RKNN model SHA256; default is the validated RK3576 artifact")
    p.add_argument("--camera", default=os.environ.get("FCV_CAMERA_DEVICE", "/dev/video0"))
    p.add_argument("--num-poses", type=int, default=4)
    p.add_argument("--output", type=Path, default=Path("runs_v2"))
    p.add_argument("--record", action="store_true", help="start an annotated diagnostic clip at launch")
    p.add_argument("--record-raw-stereo", action="store_true", help="also save every recorded decoded SBS frame as lossless PNG; uses substantial disk space")
    p.add_argument("--record-playback-fps", type=float, default=10.0, help="constant FPS in annotated AVI; real frame times remain in JSONL")
    p.add_argument("--no-display", action="store_true")
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--auto-reauthorize", action="store_true",
                   help="EXPERIMENTAL: enable conservative long-term reauthorization after full loss")
    return p.parse_args()


def draw(frame, people, authorized, log, debug=False, recording=False):
    out = frame.copy()
    current = authorized.current_track_id
    state = log["ui_state"]
    suspected = log["suspected_track_id"]
    for person in people:
        if state == "GREEN" and person.track_id == current:
            color, label = (0, 210, 0), "TRUSTED OPERATOR"
        elif state == "RED" and person.track_id == suspected:
            color, label = (0, 0, 255), "SUSPECTED OPERATOR"
        else:
            color, label = (0, 220, 255), "UNKNOWN PERSON"
        x1,y1,x2,y2 = [int(v) for v in person.bbox_xyxy]
        cv2.rectangle(out, (x1,y1),(x2,y2), color, 2)
        cv2.putText(out, f"{label} ID {person.track_id}", (max(4,x1),max(24,y1-6)),
                    cv2.FONT_HERSHEY_SIMPLEX,.58,color,2,cv2.LINE_AA)
    if state == "GRAY":
        cv2.putText(out,"OPERATOR LOST",(20,210),cv2.FONT_HERSHEY_SIMPLEX,1.0,(160,160,160),3)
    elif log["ownership_state"] == "AUTO_REAUTHORIZE_CONFIRMING":
        cv2.putText(out,"AUTO REAUTH CONFIRMING - NO AUTHORIZATION",(20,210),
                    cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,255),2)
    elif log["ownership_state"] == "REACQUIRE_CONFIRMING":
        cv2.putText(out,"REACQUIRE CONFIRMING - NO AUTHORIZATION",(20,210),
                    cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,255),2)
    best = log["best_candidate"] or {}
    lines = [f"Ownership: {log['ownership_state']}  UI: {state}",
             f"Identity: {best.get('normalized_fused_score',0):.2f}  ReID: {best.get('S_reid')}  Depth: {best.get('S_depth')}",
             f"Margin: {log['ambiguity_margin']:.2f}  Gesture: {authorized.gesture}  Authorized: {'YES' if authorized.valid else 'NO'}",
             f"Reject: {log['reject_reason'] or '-'}  REC: {'ON' if recording else 'OFF'}  [R] Record [Q] Quit [X] Release [V] Debug"]
    if debug:
        lines += [f"Depth {best.get('S_depth')} / R {best.get('R_depth')}  Motion {best.get('S_motion')} / R {best.get('R_motion')}",
                  f"Gallery {log['gallery_size']}  Latency {log['latency_ms']['stage5_total']:.1f}ms"]
        if log.get("reacquire_confirmation_active"):
            remaining = max(0, log["reacquire_confirmation_deadline_ms"]-log["timestamp_ms"])
            lines.append(f"Confirm ID {log['reacquire_confirmation_candidate_id']}  "
                         f"{log['reacquire_confirmation_frames']} frames / "
                         f"{log['reacquire_confirmation_elapsed_ms']}ms  "
                         f"deadline {remaining}ms  identity {best.get('normalized_fused_score')}  "
                         f"margin {log['ambiguity_margin']:.2f}")
        if log.get("auto_reauthorize_enabled"):
            lines.append(f"Auto ReID {log.get('auto_reauthorize_gallery_max')} "
                         f"TopK {log.get('auto_reauthorize_gallery_topk_mean')} "
                         f"Matches {log.get('auto_reauthorize_gallery_match_count')} "
                         f"Score {log.get('auto_reauthorize_identity_score')} "
                         f"Margin {log.get('auto_reauthorize_margin')}")
            lines.append(f"Auto {log['auto_reauthorize_state']} "
                         f"{log['auto_reauthorize_frames']} frames / "
                         f"{log['auto_reauthorize_elapsed_ms']}ms  "
                         f"{log['auto_reauthorize_reject_reason']}")
        checks = log.get("acquisition_checks") or []
        if checks:
            check = checks[0]
            lines.append(f"{check['mode']} T-Pose {check['tpose_matched']} Pose {check['pose_valid']} "
                         f"Crop {check['crop_quality']:.2f} Old ReID {check.get('old_gallery_similarity')}")
    if log.get("auto_reauthorize_success_notice"):
        lines.append("AUTO REAUTHORIZED - NEW SESSION; WAIT FOR NEW GESTURE")
    cv2.rectangle(out,(0,0),(out.shape[1],len(lines)*30+10),(0,0,0),-1)
    for i,line in enumerate(lines):
        cv2.putText(out,line,(10,25+i*30),cv2.FONT_HERSHEY_SIMPLEX,.56,(255,255,255),1,cv2.LINE_AA)
    return out


def main():
    args = args_parser()
    stage3 = args.stage3_root.expanduser().resolve()
    stage4 = args.stage4_root.expanduser().resolve()
    for root, marker in ((stage3,"main.py"),(stage4,"gesture/geometry.py")):
        if not (root/marker).is_file():
            raise FileNotFoundError(f"Frozen dependency missing: {root/marker}")
    sys.path[:0] = [str(stage3), str(stage4)]
    from camera.stereo_left_source import open_stereo_source
    from config import AppConfig, BackendConfig, CameraConfig
    from pose.factory import create_pose_backend
    from pose.normalize import SkeletonNormalizer
    from pose.quality import PoseQualityEvaluator
    from stage5_v2.depth import StereoPersonDepthAdapter
    from stage5_v2.pipeline import Stage5PipelineV2
    from stage5_v2.ownership import Settings
    from stage5_v2.reid import OSNetEmbedder
    from stage5_v2.reid_rknn import RKNNOSNetEmbedder, VALIDATED_RK3576_SHA256
    from stage5_v2.recording import DiagnosticRecorder
    from stage5_v2.tracker import BotSortTrackerAdapter

    # Initialize all authority-bearing dependencies before opening the camera.
    if args.reid_backend == "rknn":
        if args.rknn_osnet_model is None:
            raise ValueError("--rknn-osnet-model is required for RKNN backend")
        embedder = RKNNOSNetEmbedder(args.rknn_osnet_model,
                                    expected_sha256=args.rknn_osnet_sha256 or VALIDATED_RK3576_SHA256)
    else:
        if args.osnet_checkpoint is None:
            raise ValueError("--osnet-checkpoint is required for Torch backend")
        embedder = OSNetEmbedder(args.osnet_checkpoint, args.torchreid_root)
    try:
        depth = StereoPersonDepthAdapter(args.calibration, args.stage2_root)
        tracker = BotSortTrackerAdapter(args.boxmot_lib)
        try:
            pipeline = Stage5PipelineV2(tracker, embedder, depth,
                                        ownership_settings=Settings(auto_reauthorize_enabled=args.auto_reauthorize))
        except BaseException:
            tracker.close()
            raise
    except BaseException:
        embedder.close()
        raise
    config = AppConfig(camera=replace(CameraConfig(),device=args.camera),
                       backend=replace(BackendConfig(),num_poses=args.num_poses))
    evaluator = PoseQualityEvaluator(config.quality)
    normalizer = SkeletonNormalizer(config.normalization)
    output = args.output.expanduser().resolve()/datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    output.mkdir(parents=True,exist_ok=False)
    log_path = output/"ownership_frames.jsonl"
    recorder = DiagnosticRecorder(output, raw_stereo=args.record_raw_stereo,
                                  playback_fps=args.record_playback_fps)
    pending_record_start = args.record
    count = 0; failure = None; debug = False; started = time.perf_counter()
    try:
        with log_path.open("w",encoding="utf-8") as handle:
            with create_pose_backend(config.backend) as backend, open_stereo_source(config.camera) as camera:
                while not args.max_frames or count < args.max_frames:
                    frame = camera.read()
                    capture_wall_time_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    right = frame.raw_sbs[:,config.camera.eye_width:].copy()
                    # Stage 3 pose expects Run B rectified LEFT.
                    rectified_left = cv2.remap(frame.left_raw,*depth.maps[0],cv2.INTER_LINEAR)
                    pose_frame = backend.infer(rectified_left,frame.timestamp_ns//1_000_000,frame.frame_id)
                    rectified,people,authorized,record = pipeline.process(
                        frame.left_raw,right,pose_frame,evaluator,normalizer,
                        rectified_left=rectified_left)
                    record["camera_device"] = args.camera
                    record["calibration_path"] = str(depth.calibration_path)
                    record["reid_backend"] = args.reid_backend
                    record["reid_model_sha256"] = embedder.sha256
                    if args.reid_backend == "torch":
                        record["osnet_checkpoint_sha256"] = embedder.sha256
                    record["pose_inference_latency_ms"] = pose_frame.inference_latency_ms
                    record["capture_monotonic_ns"] = frame.timestamp_ns
                    record["capture_wall_time_utc"] = capture_wall_time_utc
                    record["camera_read_latency_ms"] = frame.read_latency_ms
                    handle.write(json.dumps(record,allow_nan=False)+"\n")
                    handle.flush()
                    annotated = None
                    if not args.no_display or pending_record_start or recorder.active:
                        annotated = draw(rectified,people,authorized,record,debug,
                                         recording=pending_record_start or recorder.active)
                    if pending_record_start:
                        recorder.start(annotated, {
                            "camera_device": args.camera,
                            "source_runner": "live_operator_v2.py",
                            "calibration_path": str(depth.calibration_path),
                            "reid_backend": args.reid_backend,
                            "reid_model_sha256": embedder.sha256,
                            **({"osnet_checkpoint_sha256": embedder.sha256}
                               if args.reid_backend == "torch" else {}),
                            "num_poses": args.num_poses,
                            "raw_sbs_size": [frame.raw_sbs.shape[1], frame.raw_sbs.shape[0]],
                        })
                        pending_record_start = False
                    if recorder.active:
                        recorder.write(annotated,frame.raw_sbs,record,frame.timestamp_ns)
                    count += 1
                    if not args.no_display:
                        cv2.imshow("Stage 5 V2 Visual Ownership",annotated)
                        key = cv2.waitKey(1)&0xff
                        if key in (ord("q"),27): break
                        if key == ord("x"):
                            pipeline.ownership.reset(); pipeline.temporal.reset()
                        if key == ord("v"): debug = not debug
                        if key == ord("r"):
                            if recorder.active:
                                print("RECORDING STOPPED:",recorder.stop(reason="USER_STOP"))
                            else:
                                pending_record_start = True
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        print("STAGE5 V2 FAIL-CLOSED:",failure,file=sys.stderr)
    finally:
        recorder.stop(reason="ERROR" if failure else "RUN_EXIT")
        tracker.close()
        embedder.close()
        if not args.no_display:
            cv2.destroyAllWindows()
    elapsed = max(1e-6,time.perf_counter()-started)
    summary = {"status":"ERROR_FAIL_CLOSED" if failure else "VISUAL_ONLY_RUN_COMPLETE",
               "frames":count,"fps":count/elapsed,"elapsed_s":elapsed,"error":failure,
               "log_path":str(log_path),"recordings":recorder.clips,
               "no_ros2_px4_output":True,
               "reid_backend":args.reid_backend,"reid_model_sha256":embedder.sha256}
    (output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
