# Stage 3 Validation Report

Date: 2026-09-15  
Status: **PASS**

## AUTO TEST

- Module compile: PASS
- Deterministic unittest: 12/12 PASS
- Translation invariance: PASS
- Uniform scale invariance: PASS
- Translation + scale invariance: PASS
- Anatomical left/right preservation: PASS
- Missing/low-confidence key joint reject: PASS
- Degenerate body scale reject without NaN/crash: PASS
- Bone vectors and geometric joint angles: PASS
- JSON serialization with no NaN: PASS
- Run B calibration FileStorage/map construction: PASS
- MediaPipe 1.0.1 Full backend initialization: PASS
- Synthetic blank frame no-person result (`poses=[]`): PASS
- Non-blocking 3→2→1 SPACE countdown, duplicate-start rejection and one-shot capture: PASS

## REAL CAMERA / NUC SMOKE

- Host: sentinel-S600, Ubuntu 22.04.5 x86_64, i9-13900HK
- Environment: `~/venvs/drone_stage3`
- Versions: MediaPipe 1.0.1, OpenCV 4.10.0, NumPy 1.26.4
- Full model SHA-256: `4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad`
- Camera: `/dev/video0`, 2560×960 MJPEG SBS → physical LEFT 1280×960
- Calibration: Run B `20260914_092939_UTC__run_B_exclude_0004_0027`
- Bounded run: 120/120 frames processed; camera released cleanly
- Camera read latency mean/median/p95: 11.13 / 7.01 / 10.68 ms
- Pose inference latency mean/median/p95: 11.86 / 11.00 / 16.81 ms
- Total processing latency mean/median/p95: 24.53 / 19.59 / 29.12 ms
- Effective synchronous pipeline FPS from frame intervals: 44.97
- Post-change 60-frame regression: 60/60, 0 poses in unattended view, 47.55 FPS,
  pose inference mean/median/p95 11.13/10.98/12.73 ms
- Invalid camera path: explicit error, no crash/hang

### Sustained operator run

- Runtime: 2026-09-15 08:59:45–09:30:52 UTC (about 31 minutes)
- Frames processed: 62,962
- Camera read latency mean/median/p95: 8.58 / 7.57 / 13.99 ms
- Pose inference latency mean/median/p95: 13.82 / 11.60 / 25.06 ms
- Total processing latency mean/median/p95: 24.09 / 21.09 / 39.45 ms
- Effective synchronous pipeline FPS: 33.75
- Camera release and runtime summary write: PASS

The earlier headless smoke contained no confirmed operator. Real-human validation was completed
separately on 2026-09-15 using the lossless snapshot PNG and JSON records listed below.

## MANUAL TEST

- [x] Front standing
- [x] Arms naturally down
- [x] Anatomical left arm raised
- [x] Anatomical right arm raised
- [x] Both arms raised
- [x] Move to image left / center / right
- [x] Same pose at approximately 0.8 / 1.5 / 2.0 m
- [x] Partial arm occlusion
- [x] Partial body truncation
- [x] No person

### Real-human evidence

- Valid person scenes had quality scores 0.9946–0.9995. Raised-arm samples had 100% key-joint
  coverage and minimum canonical confidence 0.9570–0.9872.
- Anatomical left/right remained consistent. The left-only, right-only, both-up and horizontal-arm
  samples produced geometry consistent with the visible operator pose.
- Center to image-left/image-right raw joint displacement was about 280/178 px. Normalized joint
  RMSE was 0.179/0.090 body-scale units; mean angle difference was 3.77/3.58 degrees.
- Approximately 0.8 m to 1.5/2.0 m raw joint displacement was about 112/204 px. Normalized joint
  RMSE was 0.057/0.115 body-scale units; mean angle difference was 3.35/2.84 degrees.
- No official PASS threshold exists for these differences. They are engineering comparisons and
  show a material reduction of global translation/scale effects while preserving pose geometry.
- No-person was rejected with `NO_POSE` and no normalized skeleton.
- Partial right-arm occlusion was rejected with `MISSING_ARMS`, `MISSING_RIGHT_ELBOW` and
  `MISSING_RIGHT_WRIST`; no valid normalized skeleton was emitted.
- Three repeated right/top boundary truncation captures were rejected with `HEAVY_TRUNCATION`,
  `LOW_CONFIDENCE` and missing-arm reasons. Their inferred bboxes exceeded the 1280x960 image
  bounds and no valid normalized skeleton was emitted.

## KNOWN LIMITATION

- MediaPipe world landmarks are not stereo-measured camera XYZ and are not used as such.
- V1 is single-person; local_detection_id is per-frame only.
- Thresholds are engineering heuristics, not official safety thresholds.
- Stage 3 does not classify gestures or establish identity.
- Host timestamps are monotonic receive/processing timestamps, not sensor exposure timestamps.
- One extra chair-occluded sample (`frame_00000430_20260915_093256_538559_UTC`) was accepted by
  the V1 quality heuristic because inferred key-joint confidence remained above configured gates.
  This is retained as a hard-negative/known limitation for later robustness work; it does not
  override the explicit arm-occlusion and boundary-truncation rejection evidence above.
- Real different-height generalization was not tested; V1 has deterministic uniform-scale
  invariance, but broader subject diversity remains future validation rather than an official
  model guarantee.

## Gate decision

Camera -> Pose -> PoseQuality -> NormalizedSkeleton ran continuously on the real NUC/camera,
12/12 deterministic tests pass, all required manual Stage 3 scenes are recorded, and the V1
interfaces are frozen. **Stage 3 Gate: PASS.** This does not authorize identity tracking or
gesture-command semantics, which remain later stages.
