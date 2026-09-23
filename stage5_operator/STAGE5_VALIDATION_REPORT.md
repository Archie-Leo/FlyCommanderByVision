# Stage 5 V1 Validation Report

Date: 2026-09-23. Decision: **IMPLEMENTED BASELINE / MANUAL MULTI-PERSON VALIDATION PENDING**. This is not Stage 5 PASS.

## Verified on NUC

- Frozen Stage 3/4 source was imported, not edited; no ROS 2 or PX4 output path exists.
- `PYTHONNOUSERSITE=1 PYTHONPATH=.:~/drone_stage4_gesture python -m unittest discover -s tests -v`: 19/19 synthetic tests pass. Scenarios include one/multiple/no T-Pose, temporal acquisition, bystander rejection, invalid/unknown gesture, lost/ambiguous state, ID change/reuse, timeout, gallery contamination, non-finite serialization, and Stage 4 T-Pose remaining UNKNOWN.
- `PYTHONNOUSERSITE=1 python live_operator.py --no-display --max-frames 12`: opened `/dev/video0`, ran 12 frames, wrote `runs/20260923_151129_UTC/ownership_frames.jsonl` and summary, and exited cleanly. This room had no person; it does **not** validate multi-pose or ownership performance.
- `PYTHONNOUSERSITE=1 python live_operator.py --camera /dev/video999 --no-display --max-frames 1`: returned `CAMERA_OR_PIPELINE_ERROR_FAIL_CLOSED` with zero processed frames and an invalid authorization record. No fake authorization was emitted.

NUC environment: Ubuntu 22.04.5, Python 3.10, existing `~/venvs/drone_stage3`, OpenCV 4.10.0, NumPy 1.26.4, MediaPipe 1.0.1. No dependency install or environment upgrade was performed. This new Stage 5 directory has no Git commit yet; source revision is the 2026-09-23 workspace snapshot.

## Manual Gate still required

Record or observe with two people and retain JSONL plus video if consented:

1. A performs T-Pose, B does not: exactly one session becomes `LOCKED_HIGH` after temporal confirmation, never earlier.
2. B performs each legal gesture while A remains operator: zero valid B authorizations. A's legal gestures may authorize only while A has valid pose and stable Stage 4 output.
3. A leaves/occludes: immediate invalid output; B cannot inherit the old track ID or session. Measure latency and ID switches.
4. A returns after short occlusion/crossing: only sufficiently evidenced reacquisition; ambiguous or similar-looking people must remain invalid. Audit the UUID, candidate scores and gallery updates.
5. Two people T-Pose or cross in similar clothing: no silent transfer. If uncertainty exists, `AMBIGUOUS`/`LOST` is preferable.
6. Pose invalid, waving/scratching, turning, missed frames, and camera failure: no stable legal authorization. Test explicit `X` release and a new T-Pose session.

Metrics to calculate from real recordings: false authorization count and rate, bystander false triggers, acquisition/reacquisition latency, ID-switch count, lost/ambiguous durations, gesture precision/recall conditional on correct operator, and rejection rate. Synthetic pass does not set a product safety threshold. The HSV descriptor and provisional tracker are known limitations; no product-level long-term identity claim is made.
