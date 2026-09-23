# Stage 5 Operator Ownership V1 — Visual-Only Design

Date: 2026-09-23. Status: implemented baseline; real multi-person Gate pending.

## Boundary and inputs

The NUC implementation lives at `/home/sentinel/drone_stage5_operator`. It imports the frozen Stage 3 camera, rectification, pose, quality, and normalization interfaces and the frozen Stage 4 geometry/temporal recognizer. It does not edit either project or emit ROS 2/PX4 commands. Stage 4 T-Pose remains `UNKNOWN`; Stage 5 alone uses it to request ownership.

Pipeline: calibrated left camera frame → MediaPipe multi-pose (`num_poses=4`) → per-person PoseQuality and `NormalizedSkeletonV1` → global tracking → `TrackedPersonV1` → ownership manager → **only the selected operator's** frozen Stage 4 recognizer → `AuthorizedGestureV1`. Every frame is logged as JSONL, including rejected frames. A valid output requires `LOCKED_HIGH`, a current matching person, valid pose, and a stable legal gesture. A track ID is ephemeral and is never an operator identity or authorization token.

## Tracking and appearance choice

`stage5/tracking.py` is an explicitly named `kalman_iou_two_pass_v1` fallback using OpenCV Kalman prediction and high/low-score IoU matching. It is **not** the official ByteTrack, BoT-SORT, or BoxMOT implementation; it has no camera-motion compensation or learned ReID. We chose a runnable, dependency-light baseline because the NUC Stage 3 venv has no Torch/BoxMOT/ONNX runtime and the local OSNet files are not usable weights. The upstream tracking-family audit remains in `../docs/research/OPERATOR_LOCK_OPEN_SOURCE_AUDIT.md`; this implementation does not claim comparable ID-switch performance. In particular, camera motion, crossing, occlusion, and similar clothing require real testing and probably a mature MOT/ReID backend before a product-level claim.

`OperatorReIDGallery` holds at most 12 high-quality samples from an already uniquely locked operator. Its current descriptor is `hsv_torso_hist_v1`, a **weak clothing-colour proxy, not OSNet and not a biometric identity embedding**. Gallery similarity alone never grants initial ownership. Cross-track reacquisition requires appearance, geometric/time continuity, candidate separation, and a 500 ms / 5-frame wait. Similar-looking people can still defeat this proxy; the current module is for visual-only validation, not safety-critical control.

## Ownership and safety

States: `WAIT_OPERATOR`, `ACQUIRING`, `LOCKED_HIGH`, `LOST`, `REACQUIRING`, `AMBIGUOUS`. One eligible person's deliberate, geometrically valid T-Pose must persist at least 600 ms and six frames. Multiple simultaneous T-Poses are ambiguous. Acquisition creates a UUID `operator_session_id` separate from `track_id`. Explicit `X` releases it. Missing, low-quality, stale, ambiguous, or non-monotonic observations fail closed. Same-track continuity is checked against gallery, geometry, and a 350 ms frame timeout; a changed track ID must reacquire with stronger evidence. During LOST/REACQUIRING/AMBIGUOUS, all gestures are invalid, and the Stage 4 temporal state is reset. A bystander gesture is never evaluated as the operator's gesture.

`AuthorizedGestureV1` records timestamp/frame, session UUID, current ephemeral track ID, ownership state and score, gesture and confidence, validity, and reject reasons. The JSONL log also includes all tracked persons, candidate scores, gallery version/size, raw/stable gesture, camera and calibration ID. A camera/pipeline error writes a fail-closed invalid record and returns a nonzero exit status. No actuator, intent, or flight-control interface exists here.

## Module map

- `live_operator.py`: camera loop, display, logs, release/quit.
- `stage5/types.py`: `DetectionV1`, `TrackedPersonV1`, `AuthorizedGestureV1`.
- `stage5/tracking.py`: provisional global tracker.
- `stage5/appearance.py`: bounded gallery and provisional appearance descriptor.
- `stage5/tpose.py`: Stage 5-only T-Pose geometry and temporal confirmation.
- `stage5/ownership.py`: unique session, candidate arbitration, reject and reacquire.
- `stage5/pipeline.py`: integration with the frozen Stage 4 recognizer.
- `tests/test_stage5.py`: synthetic safety and state-transition tests.

## Not yet proven

The real-person A/B tests, crossing, short occlusion, operator leaving/re-entering, track-ID switch frequency, and same-clothes impostor behavior have not been measured. A 12-frame empty-room camera smoke test proves startup/logging only. Stage 5 Gate remains open. Before any Stage 6 connection, replace/validate the weak appearance and MOT backend as needed, collect adversarial real-person data, and set measured reject/false-authorization criteria. No automatic transfer to a new person should be inferred from these synthetic tests.
