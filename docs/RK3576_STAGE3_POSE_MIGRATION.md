# RK3576 Stage3 Pose native migration — 2026-09-25

## Selected artifact and runtime

- Model: Rockchip RKNN Model Zoo **v2.3.2**, commit `bad6c7334531becaf90a561988519b7bec34d0ab`, YOLOv8n-Pose INT8 for RK3576. No model reselection or retraining.
- Source ONNX SHA256: `308495ebe4416b40adf376485252a7b8ba7933a169368b31e74e0f977ded8663`.
- Generated RKNN SHA256: `95c857fc580814ff08ab5e417b904ea4a926bfdded68104520c540a17c186b9c`, 7,441,335 bytes. Board path: `~/fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/yolov8n-pose-rk3576-int8.rknn`. It is not committed.
- Native Pose links the frozen Model Zoo v2.3.2 `librknnrt.so` at `~/fcv_third_party/rknn-toolkit2-v2.3.2/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so`; SHA256 `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8`. The system runtime is untouched. Stage5 OSNet retains its existing RKNNLite runtime.
- Input is 1×640×640×3 INT8 NHWC, supplied as RGB UINT8 for runtime quantization. Outputs are INT8 `[1,65,80,80]`, `[1,65,40,40]`, `[1,65,20,20]` and FP16 `[1,17,3,8400]`.

## Implementation and contract

`scripts/build_stage3_pose_native.sh` checks the exact upstream commit and runtime hash, then compiles `stage3_pose/native/rknn_pose/pose_capi.cpp` with upstream `examples/yolov8_pose/cpp/postprocess.cc` **directly**. Decode, BOX threshold 0.5 and NMS threshold 0.4 come from that upstream source. The C ABI v1 (`pose_capi.h`) owns one RKNN context, prepares BGR→RGB letterbox input, returns up to four COCO17 people with timing, and releases outputs/context. The `.so` is a generated artifact under ignored `stage3_pose/build/`.

`RKNNPoseBackend` uses ctypes and validates the RKNN hash before opening the native runtime. It preserves `PoseBackend.infer(bgr_frame, timestamp_ms, frame_id) -> PoseFrameV1`, `close()`, and context-manager behavior. RK3576 selects it through `scripts/env_rk3576.sh`; without that environment the factory defaults to MediaPipe for NUC. Stage3, Stage4, Stage5 V2 and Stage6 live entrypoints use the same factory. Their geometry, temporal FSM, Unknown Reject, ownership, command safety, calibration and thresholds were not changed.

COCO17 anatomical left/right indexes map to the existing canonical 13 joints. Each joint carries raw YOLO keypoint confidence; `visibility`/`presence` are unknown (`None`). Coordinates are restored by upstream postprocess from the 640-square letterbox to the original rectified-left frame. The downstream bbox is min/max of valid canonical 13 joints, as in the MediaPipe adapter; it is not the detector box. `pose_score` is median valid joint confidence, not detector person score. Original frame ID, dimensions and host monotonic milliseconds are retained. Multiple detections are returned in upstream score/NMS order, capped by the configured number (four in Stage5/6). Empty detections return an empty frame; invalid shape, nonfinite output, missing/mismatched model, ABI mismatch and native errors fail closed. Confidence is **not** claimed numerically equivalent to MediaPipe visibility/presence.

## Measured performance and evidence

Board: Taishan Pi 3M RK3576. Frozen `bus.jpg` was resized to a 1280×960 timing fixture; 5 warmups then 220 iterations. This fixture is not a gesture validation set. The previously measured native raw RKNN benchmark was 19.65 ms / 50.88 FPS; it measures a different scope.

| Measurement | Mean | P50 | P95 |
| --- | ---: | ---: | ---: |
| Stage3 preprocess | 7.66 ms | 5.39 ms | 21.91 ms |
| RKNN inputs/run/outputs | 27.09 ms | 26.90 ms | 31.83 ms |
| Official decode/NMS | 0.46 ms | 0.38 ms | 0.75 ms |
| ctypes bridge | 0.94 ms | 0.85 ms | 1.43 ms |
| PoseFrame mapping | 0.82 ms | 0.66 ms | 1.30 ms |
| Total Stage3 infer | 36.97 ms | 34.72 ms | 58.13 ms |

Effective Stage3 fixture inference: **27.05 FPS**. All 220 frames returned three people. First person's existing Stage3 quality gate passed 220/220; the existing raw Stage4 recognizer returned `UNKNOWN` 220/220, as expected for a non-gesture image. Eight required joints' mean confidence was 0.9718. This does not establish gesture threshold compatibility across people or poses.

Pose plus Stage5 RKNN OSNet in one process: 220 Pose iterations, 22 successful ReID calls, Pose total mean 37.58 ms / P95 40.91 ms, ReID mean 24.00 ms / P95 27.43 ms. No runtime conflict or model corruption was observed.

Actual `/dev/video73` 2560×960 MJPEG stereo, calibrated rectified-left, headless Stage3: 120 frames, 0 visible people, mean Pose infer 29.88 ms / P50 29.21 ms / P95 33.86 ms, effective sampling 17.03 FPS, 279 old frames dropped by latest-frame capture. Runtime report: `outputs/stage3_rknn_pose_smoke/runtime_20260925_040130_UTC.json` on the board. The dropped frames prevent queue buildup. Two earlier camera attempts ended with FFmpeg EOF; a diagnostic run and two subsequent 120-frame runs completed. The underlying transient cause was not established.

Board tests: Stage3 **31 passed**, Stage4 **14 passed and 5 subtests**, Stage5 **108 passed**, Stage6 core dry-run **44 passed**, Gate6C dry-run evidence **21 passed**. Native tests include real model multi-person output at both native and rectified-left sizes, original-image coordinate bounds, repeated-result stability, and synthetic contract/fail-closed cases.

## Remaining validation

The functional RKNN backend and camera pipeline pass. A human or approved recorded sequence showing supported gestures at several distances is still needed to compare raw YOLO confidence against the frozen Stage3 0.50 and Stage4 0.65 gates and to assess left/right gesture behavior. No thresholds should be changed from the bus fixture. Stage5/6 live person ownership, real gesture recognition and any PX4 output remain untested. The old MediaPipe LSE failure remains historical; RK3576 uses the RKNN backend.

Rebuild: `bash scripts/build_stage3_pose_native.sh`; then `source scripts/env_rk3576.sh`. Benchmark: `~/venvs/fcv_stage5/bin/python3 scripts/benchmark_stage3_pose.py --iterations 220 [--coexist-osnet]`. The upstream Model Zoo checkout and matching toolkit runtime are required to rebuild; keep them until a reproducible artifact distribution is agreed. No third-party binaries or generated models are in Git.
