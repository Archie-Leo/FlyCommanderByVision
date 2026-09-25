# RK3576 Pose Backend Requirements

Status: **interface audit and requirements freeze**, 2026-09-25. Based on board branch `rk3576-migration` at `f097a81`. This document records the current behavior; it neither selects nor implements a Pose model.

## 1. Scope

The intended boundary is rectified left-eye BGR frame → `PoseBackend.infer` → `PoseFrameV1` → existing Stage3 quality/normalization → existing Stage4 geometry/temporal logic. Stage5 V2 also consumes every `PersonPose` for depth, ReID, BoT-SORT and ownership. The MediaPipe ARM64 LSE guard prevents this board from loading the current wheel, so a future backend must meet the same downstream contract. No Stage3/4/5/6 algorithm, threshold, schema, or safety logic was changed by this audit.

Evidence: `stage3_pose/pose/backend.py`, `mediapipe_backend.py`, `types.py`, `quality.py`, `normalize.py`; `stage4_gesture/gesture/{geometry,temporal,config,types}.py`; their test suites; and the read-only Stage5 V2 boundary in `stage5_operator/stage5_v2/pipeline.py`.

## 2. Current Stage3 / Stage4 data flow

1. Camera supplies a 1280×960 rectified **left** BGR image. The RK3576 FFmpeg camera stamps host receipt with `time.monotonic_ns()`; callers pass `timestamp_ns // 1_000_000` and `frame_id` to `infer` (`stage3_pose/camera/ffmpeg_source.py:101`, `stage3_pose/main.py:87-90`). The image is not mirrored for inference; Stage3's optional mirror affects display only (`stage3_pose/main.py`).
2. `PoseBackend.infer(bgr_frame: np.ndarray, timestamp_ms: int, frame_id: int) -> PoseFrame` is synchronous and owns `close`/context-manager cleanup (`stage3_pose/pose/backend.py:8-23`). Current MediaPipe code checks H×W×3, converts BGR→RGB, calls video inference with an increasing millisecond timestamp, and returns zero or more poses (`mediapipe_backend.py:74-101`).
3. Stage3 evaluates `poses[0]` or `None`, then normalizes only a present pose (`stage3_pose/main.py:90-95`). The standalone Stage4 runner does the same (`stage4_gesture/live_gesture.py:124-129`). Stage4 geometry consumes `NormalizedSkeletonV1`, not raw MediaPipe data or `PersonPose` (`gesture/geometry.py:62-116`).
4. Stage5 V2 consumes the whole `pose_frame.poses` list and uses each pose's bbox, score and skeleton (`stage5_operator/stage5_v2/pipeline.py:24-55`).

## 3. PoseFrame contract

These are Python dataclasses, **not** a single keypoint array. All fields without a listed default are constructor-required. `Optional` means `None` is allowed by the type; constructors do not enforce units or value ranges (`stage3_pose/pose/types.py:8-52`).

| `PoseFrameV1` field | Python type; shape / units | Default / `None` | Producer and consumer |
| --- | --- | --- | --- |
| `timestamp_ms` | `int`; one host-monotonic-derived millisecond value per frame | required; not `Optional` | Caller→backend; Stage3/4 temporal and Stage5 ownership |
| `frame_id` | `int`; camera frame sequence | required | Caller→backend; logging and temporal association |
| `image_width`, `image_height` | `int`, `int`; pixels of inferred rectified left image | required | Backend; quality geometry and normalization |
| `poses` | `List[PersonPose]`; length 0..configured maximum | required; empty list means no detection | Backend; Stage3/4 take first, Stage5 iterates all |
| `backend_name` | `str`; backend identifier | required | Backend; provenance/logging |
| `inference_latency_ms` | `float`; model call latency in ms | required | Backend; runner diagnostics |
| `schema_version` | `str` | `"PoseFrameV1"` | Serialization/version boundary |

| `PersonPoseV1` field | Python type; shape / units | Default / `None` | Producer and consumer |
| --- | --- | --- | --- |
| `timestamp_ms`, `frame_id` | `int`, `int`; same frame values as parent | required | Backend; normalizer, Stage4, Stage5 |
| `local_detection_id` | `int`; index within current inference result, **not** persistent track ID | required | Backend; normalizer, gesture metadata |
| `image_width`, `image_height` | `int`, `int`; pixel dimensions | required | Backend; quality/normalization |
| `bbox_xyxy` | `Tuple[float,float,float,float]`; pixel `(x1,y1,x2,y2)` on rectified left image | required, no `Optional` | Backend; Stage3 quality, Stage5 depth/crop/tracker |
| `pose_score` | `Optional[float]`; present backend uses median canonical confidence of valid mapped joints | required but may be `None` | Backend; normalizer metadata; **Stage5 tracker detection score** |
| `backend_name` | `str` | required | Backend; provenance |
| `joints` | `Dict[str,JointObservation]`; canonical names | required | Stage3 quality/normalizer; indirectly Stage4 |
| `schema_version` | `str` | `"PersonPoseV1"` | Serialization/version boundary |

Each `JointObservation` has required `name: str`, `x_px/y_px: Optional[float]`, `x_norm_image/y_norm_image: Optional[float]`, and `confidence: Optional[float]`; defaults are `visibility=None`, `presence=None`, `valid=False`, `derived=False`. Its shape is one named record, not `[x,y,score]` or `[x,y,z]`. The present backend emits 13 canonical names (table in §6) even when a mapped observation is invalid (`mediapipe_backend.py:103-128`). `z` is not in the schema. `NormalizedSkeletonV1` contains normalized joints, bones, angles, quality, body center/scale and copied timestamp/IDs; Stage4 accepts either dataclass or serialized dict (`types.py:69-118`, `gesture/geometry.py:20-24`).

## 4. Coordinate system

`x_px = x_norm_image × image_width`, `y_px = y_norm_image × image_height`; the current backend's input coordinates are MediaPipe image-relative values (`mediapipe_backend.py:108-119`). Pixel origin is the image's top-left, x increases image-right, y increases down. Values are **not clamped** to `[0,1]` or the image boundary; the quality evaluator checks off-frame key joints. `bbox_xyxy` and Stage5 crops use the same pixel frame, not source SBS or bbox-relative coordinates.

For a valid-quality pose, normalization sets `neck = midpoint(shoulders)`, `pelvis = midpoint(hips)`, `scale = (shoulder_width + neck-to-pelvis torso_length)/2`, then `joint.x/y = (pixel - pelvis)/scale` (`normalize.py:65-101`). The origin becomes pelvis; x remains image-right and y remains down. There is no orientation rotation or mirror correction. New model resize/letterbox coordinates must be undone before populating `PersonPose`.

## 5. Left / Right Semantic Contract

`left_*` and `right_*` mean **the person's anatomical left/right**, not image-left/right. The existing MediaPipe mapping uses left shoulder/elbow/wrist/hip indices 11/13/15/23 and right 12/14/16/24 (`mediapipe_backend.py:18-31`); Stage3's swap test checks names remain distinct (`stage3_pose/tests/test_pose_core.py:72-84`). The frozen Stage4 protocol states this explicitly, and its LEFT rule requires the anatomical left wrist to extend in positive image x for the front-facing validation posture (`stage4_gesture/GESTURE_V1_PROTOCOL.md:9-14`, `gesture/geometry.py:247-270`). A candidate COCO mapper must verify its own left/right conventions, camera mirroring and orientation on actual frames. It must not swap labels based only on which joint lies at image-left.

## 6. Runtime landmark dependencies

“Required” here means an invalid/missing point prevents the current quality gate or gesture from accepting an otherwise valid pose. Stage3's current adapter outputs 13 names, although Stage4 uses six. A point can still affect bbox/`pose_score` without being a hard quality joint.

| Landmark | Stage3 normalize | Quality gate | Stage4 gesture | Other current effect / status |
| --- | --- | --- | --- | --- |
| Left/right shoulder | **anchor**, shoulder width, bones/angles | **required** | **required**, every rule | T-Pose authorization; bbox/score |
| Left/right hip | **anchor**, pelvis/torso scale, bones | **required** | indirect through normalization | bbox/score |
| Left/right elbow | arm bones/angles | **required** with default `require_both_arms=True` | **required**, every rule | T-Pose; bbox/score |
| Left/right wrist | arm bones/angles | **required** with default `require_both_arms=True` | **required**, every rule | T-Pose; bbox/score |
| Nose | normalized if present | not a key joint | NOT USED | contributes to current bbox/median score if valid; optional for acceptance |
| Left/right knee | normalized if present | not a key joint | NOT USED | contributes to bbox/median score if valid; UI lower-leg drawing |
| Left/right ankle | normalized if present | not a key joint | NOT USED | contributes to bbox/median score if valid; UI lower-leg drawing |
| Eyes, ears | NOT USED | NOT USED | NOT USED | not emitted by current 13-name adapter |
| Mouth, pinky, index, thumb, heel, foot index | NOT USED | NOT USED | NOT USED | not emitted by current adapter |

Quality's `KEY_JOINTS` is precisely eight points: both shoulders, both hips, both elbows and both wrists (`quality.py:11-13`). The Stage4 `REQUIRED_JOINTS` is precisely six shoulder/elbow/wrist points (`geometry.py:11-19`). All 13 current names may influence bbox and pose score as described in §§11–12; knee/ankle/nose are therefore **optional for gesture validity**, not semantically invisible to the entire pipeline.

## 7. Stage3 Normalize dependencies

Four anchors—both shoulders and hips—must be valid finite pixel points. A valid `PoseQuality` is checked first; a missing anchor gives `MISSING_NORMALIZATION_ANCHORS`, and `scale < 8 px` or nonfinite scale gives `INVALID_BODY_SCALE` (`normalize.py:58-84`, `config.py:45-47`). The normalizer does not use bbox size, camera orientation, or image dimensions to compute its scale; dimensions are copied to `image_size` and used for derived joint image-normalized fields. It derives neck and pelvis internally. Arms produce bones and elbow/upper-arm angles. Missing non-anchor joints remain normalized `valid=False` with `x=y=None`, and dependent features become invalid (`normalize.py:86-186`). No MediaPipe-only extra point is needed to calculate center, scale or angles. **COCO17 GAP: none at the named-landmark level for this normalizer.**

## 8. Stage3 quality / confidence contract

The current adapter converts finite MediaPipe `visibility` and `presence` to `confidence = min(available finite values)`; if neither is finite, confidence is `None`. A joint is marked `valid` when finite x, finite y and some finite confidence exist; it can still have low confidence or lie outside the frame (`mediapipe_backend.py:34-42,108-128`). This number is a 0..1 confidence-like value in the existing data, but the dataclass itself does not validate that range. A new model's raw score, heatmap peak or detector score is **not automatically equivalent** to MediaPipe visibility/presence.

Quality requires each key joint's `valid`, finite x/y, and finite confidence **≥0.50**. Both shoulders and hips are mandatory; default `require_both_arms=True` also requires all four arm joints. Coverage must be ≥0.75, though with default mandatory checks all eight effectively need to pass. A finite positive bbox must cover ≥20% of image height and ≥2.5% of image area after image clipping; more than two key joints outside the frame is heavy truncation. `PoseQuality.score = clamp(0.6×coverage + 0.4×median finite key-joint confidence, 0,1)` and is **not** the same field as `PersonPose.pose_score` (`quality.py:17-105`, `config.py:34-40`). Stage4 again requires each of six normalized arm points at confidence **≥0.65** (`geometry.py:149-175`). Stage5 T-Pose also checks its six arm points at 0.65 (`stage5_operator/stage5/tpose.py:29-36`).

| File/config | Frozen default | Meaning |
| --- | ---: | --- |
| `stage3_pose/config.py`, `BackendConfig.num_poses` | 1 | default standalone MediaPipe maximum pose count; Stage5/6 runner overrides to 4 |
| `BackendConfig.min_pose_detection_confidence` | 0.5 | MediaPipe detector option, not a downstream joint gate |
| `BackendConfig.min_pose_presence_confidence` | 0.5 | MediaPipe task presence option |
| `BackendConfig.min_tracking_confidence` | 0.5 | MediaPipe task tracking option |
| `QualityConfig.joint_confidence_min` | 0.50 | per-key-joint reliability gate |
| `QualityConfig.min_key_joint_coverage` | 0.75 | fraction of eight key joints |
| `QualityConfig.min_bbox_height_ratio` | 0.20 | clipped bbox/image height |
| `QualityConfig.min_bbox_area_ratio` | 0.025 | clipped bbox/image area |
| `QualityConfig.max_out_of_frame_key_joints` | 2 | heavy-truncation rejection when exceeded |
| `NormalizationConfig.min_scale_px` | 8.0 | minimum pelvis/torso body scale |
| `GeometryConfig.min_joint_confidence` | 0.65 | each of six Stage4 required joints |
| `stage5_operator/stage5/tpose.py` | 0.65 | each of six T-Pose arm joints |

The geometry and temporal thresholds are transcribed in §9/§12 and have not been changed. Model score calibration is an open risk (§18).

## 9. Stage4 gesture landmark matrix

**Every** legal rule first needs all six valid finite normalized shoulder/elbow/wrist joints at confidence ≥0.65 and all four upper/lower arm segment lengths ≥0.20 body-scale units (`geometry.py:118-125,149-175,234-245`). Stage4 recomputes vectors and elbow angles from normalized joints; it does not read the normalizer's `angles_deg` or `bones` for these rules (`geometry.py:186-210`). Each rule must have every listed check pass; zero or multiple rule matches return `UNKNOWN` (`geometry.py:123-145`).

| Gesture | Six-joint geometry beyond common segment gate | Frozen `GeometryConfig` values |
| --- | --- | --- |
| `LEFT` | Anatomical left wrist extends image-right from left shoulder; near shoulder height; left elbow straight. Opposite right arm straight, down, and laterally near right shoulder. | reach x ≥0.65; abs vertical ≤0.35; elbow ≥145°; opposite down y ≥0.65 and abs x ≤0.45 (`geometry.py:247-258`) |
| `RIGHT` | Mirror of LEFT using anatomical right wrist image-left; opposite left arm down. | same thresholds (`geometry.py:259-270`) |
| `ASCEND` | Both wrists above their shoulders; both elbows straight. | rise y ≥0.65; elbows ≥145° (`geometry.py:271-280`) |
| `DESCEND` | Both wrists below and outward from shoulders; both elbows straight. | drop y ≥0.65; outward x ≥0.40; elbows ≥145° (`geometry.py:281-292`) |
| `HOVER` | Both upper arms outward and near horizontal; both forearms up and near vertical; both elbows bent. | upper x ≥0.40; abs upper y ≤0.35; forearm rise y ≥0.35; abs forearm x ≤0.40; elbow 65–125° (`geometry.py:293-310`) |

`UNKNOWN` and `INVALID` are reject states, not legal gestures. T-Pose is deliberately `UNKNOWN` in Stage4, while Stage5's separate T-Pose recognizer uses the same six arm points and normalized elbow angles (`GESTURE_V1_PROTOCOL.md:11-17`, `stage5_operator/stage5/tpose.py`).

## 10. Missing / Invalid Landmark Contract

Current MediaPipe conversion emits every canonical key; unavailable/nonfinite coordinate becomes `None`, unavailable confidence becomes `None`, and `valid=False` when x/y/confidence is unavailable. It does **not** use `(0,0)`, NaN or an old observation as a missing sentinel (`mediapipe_backend.py:103-128`). A low score can coexist with `valid=True`; quality/geometry then reject by threshold. Quality uses `.get`, so an absent key behaves as unreliable, but maintaining the 13-name mapping gives the closest present behavior. Normalization propagates missing non-anchor points as `x=y=None, valid=False`; missing anchors make the whole skeleton invalid. Stage4 returns `INVALID` for invalid source quality/skeleton, missing required joint, nonfinite normalized x/y, or absent/low confidence. A nonmatching but otherwise valid geometry returns `UNKNOWN` (`geometry.py:87-145,149-175`).

No pose should be `poses=[]`; Stage3/4 runners then pass `None` to geometry, which returns `INVALID`. An inference exception currently exits the runner rather than fabricating a pose. `TemporalStabilizer` clears immediately on raw `INVALID`; raw `UNKNOWN` may retain an earlier stable label for the frozen 120 ms/two-frame release grace (`temporal.py:40-108`). Thus uncertain or malformed input must be represented as **invalid**, not as a plausible low-quality legal pose or merely `UNKNOWN`, when immediate invalidation is required by the existing logic. This describes current behavior, not a new policy.

## 11. BBox and pose-score contract; Stage5 boundary

The current backend computes `(min x_px, min y_px, max x_px, max y_px)` across its **valid mapped 13 joints**, without clamping or padding; if no valid point exists it emits `(0,0,0,0)` (`mediapipe_backend.py:103-140`). Quality rejects nonfinite/degenerate boxes and uses clipped bbox coverage. The backend/adapter, rather than Stage3 quality or Stage5, currently supplies `bbox_xyxy`. A future adapter can compute it, but the final `PersonPose` must retain this pixel `xyxy` contract. Nose/knees/ankles can affect the resulting box even though they are not quality-key joints.

`pose_score` is the **median canonical confidence of valid mapped joints**, or `None` if none are valid (`mediapipe_backend.py:129-140`). It is copied into `NormalizedSkeleton.source_pose_score`; Stage3 quality does **not** gate on `pose_score`. Stage5 V2 passes `float(pose.pose_score or 0.)` to the native tracker as its detection score (`stage5_operator/stage5_v2/pipeline.py:38-39`). It also sends bbox to stereo depth, ReID crop, tracker, and torso appearance; quality/skeleton feed gesture and ownership (`pipeline.py:24-55`). Thus bbox and score distribution changes may alter Stage5 behavior even when Stage4's eight required joints look valid. Existing Stage5 thresholds must not be changed to absorb a new model's score semantics.

## 12. Timestamp / temporal contract

Input `timestamp_ms` is host monotonic camera receipt nanoseconds floored to milliseconds; it is **not wall clock** and **not proven sensor exposure time** (`ffmpeg_source.py:101`, `main.py:87-90`). The MediaPipe adapter enforces `max(input_ms, last_ms+1)` to satisfy video inference ordering and copies that adjusted value to frame and poses (`mediapipe_backend.py:78-99`). The new backend must preserve one consistent, increasing millisecond time base and pass the same `frame_id` to parent and child poses; changing to wall time or per-person arbitrary timestamps would alter debounce/hold behavior.

Stage4 confirms a legal raw gesture after both **300 ms and 4 frames**; it releases after both **120 ms and 2 frames**; a regressing timestamp yields `INVALID` and resets (`gesture/config.py:26-30`, `gesture/temporal.py:31-108`). Stage5 T-Pose has its own **600 ms and 6 frames** check (`stage5_operator/stage5/tpose.py:71-91`). These timing semantics depend on real frame timestamps, not inference completion time.

## 13. Multi-person requirement

`PoseFrame.poses` is a list, and MediaPipe enumerates detections into per-frame `local_detection_id` values (`mediapipe_backend.py:90-94`). Standalone Stage3 and Stage4 currently use only `poses[0]`, with `BackendConfig.num_poses=1` by default. However Stage5 V2 and Stage6 runners request **4** poses and Stage5 V2 iterates all poses for tracking and operator selection (`stage5_operator/live_operator_v2.py:31,149`, `stage6_closed_loop/live_closed_loop.py:35,93`, `stage5_operator/stage5_v2/pipeline.py:24-55`). Therefore multi-person output is **REQUIRED for a full Stage5/6 production replacement**, although not required by the standalone Stage3/4 demo contract. `local_detection_id` must remain a per-frame index; persistent identity belongs to Stage5's tracker.

## 14. COCO17 coverage matrix

This is an **interface landmark coverage** audit only. It says nothing about any model's accuracy, score calibration, NPU compatibility or safety readiness.

| Current requirement | COCO17 available / canonical mapping | Interface risk |
| --- | --- | --- |
| Nose | yes; 0 → `nose` | optional for validity, affects current bbox/median |
| Left/right shoulders | yes; 5/6 → `left_shoulder`/`right_shoulder` | anatomical side and coordinate transform must be verified |
| Left/right elbows | yes; 7/8 → `left_elbow`/`right_elbow` | same; required by quality and every gesture |
| Left/right wrists | yes; 9/10 → `left_wrist`/`right_wrist` | same; action direction particularly sensitive |
| Left/right hips | yes; 11/12 → `left_hip`/`right_hip` | anchor geometry/body scale and quality gate |
| Left/right knees | yes; 13/14 → `left_knee`/`right_knee` | optional gate-wise, current bbox/score effect |
| Left/right ankles | yes; 15/16 → `left_ankle`/`right_ankle` | optional gate-wise, current bbox/score effect |
| Eyes/ears | yes; 1–4 | not consumed by current canonical adapter |
| MediaPipe mouth, fingers, heel, foot index | absent | no current Stage3/4 runtime consumer |
| Visibility/presence **semantics** | no direct equivalent established by index coverage | major confidence calibration gap |
| Multi-person, bbox and detection-score behavior | model-dependent | required by Stage5 boundary, not proven by 17 point names |

**Verdict: CONDITIONAL PASS for landmark-name coverage.** COCO17 contains all eight hard-gated points and all 13 currently emitted canonical names; an adapter can retain `PoseFrameV1`/`PersonPoseV1`/`NormalizedSkeletonV1` structure without schema changes. The verdict remains conditional because left/right semantics, 0..1 confidence meaning, pixel transforms, visibility/missing-point behavior, bbox/pose-score distribution and multi-person output are not established by the landmark list.

## 15. MediaPipe33 features not required

The current adapter selects only nose, six arm joints, two hips, two knees and two ankles from MediaPipe's 33 landmarks. It does not emit or use eye, ear, mouth, pinky, index, thumb, heel or foot-index landmarks. Current Stage3/4 runtime does **not** require all 33 MediaPipe points. Stage3 UI can draw knee/ankle bones, but UI drawing is not a quality/gesture gate (`stage3_pose/ui.py:7-12`).

## 16. Existing test coverage

The tests below were audited by reading them; this task adds no test code.

| Contract | Existing test coverage | RECOMMENDED TEST before replacement |
| --- | --- | --- |
| Translation/scale and normalization anchors | `stage3_pose/tests/test_pose_core.py:60-69,86-94` | Model-output→pixel→normalized round trip after resize/letterbox |
| Left/right distinction | Stage3 swapped-joint test `:72-84`; Stage4 LEFT/RIGHT test `stage4_gesture/tests/test_gesture_core.py:105-110` | Actual front/back/turning person frames with anatomical labels; mirror check |
| Low/missing joint and no pose | Stage3 low wrist/no pose `:97-104`; Stage4 missing skeleton, low confidence, NaN `:119-131` | All eight required joints separately; missing hips; actual model confidence calibration |
| Zero body scale/nonfinite | Stage3 `:86-94`; Stage4 nonfinite x `:126-131` | Nonfinite score/visibility/presence, bbox coordinates, out-of-frame/truncation |
| Five gestures, natural-down reject, T-Pose reserved | Stage4 `:71-110` | Full adapter replay confusion and false-trigger matrix on held-out real sequences |
| Temporal confirm/release and regression | Stage4 `:133-156` | Backend timestamp floor/strict increase and dropped/skipped frames |
| RK3576 MediaPipe ISA guard | `stage3_pose/tests/test_rk3576_compat.py:25-32` | New backend missing model/load/inference/close fail-closed behavior |
| Multi-person list, local IDs, bbox and `pose_score` to Stage5 | No direct Stage3→Stage5 adapter contract test | 0/1/4 persons, per-frame IDs, bbox/score distributions and tracker input |

## 17. Required adapter contract

The future RK3576 backend must:

- Implement the synchronous `PoseBackend` API and `close` lifecycle. Accept a rectified left-eye `np.ndarray` BGR8 H×W×3 and caller-supplied host-monotonic `timestamp_ms`/`frame_id`; return one `PoseFrameV1` for that frame or raise a clear inference error.
- Return a list of `PersonPoseV1`, empty when no person. For Stage5/6 deployment, support multiple visible people (current runner requests four). Preserve per-frame local detection IDs; do not invent stable operator IDs.
- Populate anatomically named observations for the present 13-name canonical map, at minimum valid finite bilateral shoulders, hips, elbows and wrists for any pose permitted to pass quality. Convert model-space coordinates to the rectified image's pixel and image-relative coordinates; retain top-left/image-right/image-down axes and no hidden mirror.
- Preserve `JointObservation` missing-value semantics: unavailable value as `None` and `valid=False`, not zero, NaN, recycled coordinates or a fabricated positive score. Provide finite confidence values with documented meaning and validate their relation to the existing 0.50/0.65 gates before treating detections as eligible.
- Supply finite pixel `bbox_xyxy` and calibrated `pose_score` compatible with the current quality/crop/tracker paths. Replicating the current 13-joint bbox/median rule is the reference to compare against; alternate model outputs cannot be assumed equivalent without evidence.
- Keep frame and pose timestamps and IDs consistent and monotonic in the existing millisecond host-receipt time base. Report inference latency; release NPU resources on close.
- Fail closed on invalid frame, model/inference error, nonfinite/missing required points, uncertain side assignment or unsupported output: no plausible legal `NormalizedSkeleton` or gesture may result. The current quality/normalizer/geometry path must mark such evidence invalid; no stale pose reuse.

## 18. Open risks and next-stage measures

The largest semantic risk is **confidence mismatch**: COCO-style keypoint scores need measured calibration against the existing visibility/presence minimum, quality coverage, and Stage4 0.65 checks. The largest geometry risks are anatomical side swap, model resize/letterbox reversal, truncation and center/scale drift. The largest Stage5 risks are changed bbox extent, `pose_score` distribution, missed secondary people and identity handoff. Model throughput also has to be measured as rectified-camera→PoseFrame latency, including NPU sharing with Stage5 RKNN ReID; raw model FPS alone does not answer this.

Before selecting/accepting a model, measure per-key-joint location error and availability (especially eight hard-gated points), left/right swap rate, finite/missing output behavior, quality-valid/reject rates, normalized-joint and bbox/score differences from the existing reference on comparable data, Stage4 per-gesture recall/precision and false legal triggers including natural-down/T-Pose/partial-body negatives, temporal behavior over dropped frames, multi-person recall/ordering, Stage5 tracker/ownership effects, and end-to-end latency. Use held-out real data and existing frozen gesture/safety gates; do not change thresholds to fit a model during this audit.

## 19. Conclusion: requested decisions

| Question | Audit answer |
| --- | --- |
| Q1 runtime hard-required landmarks | Left/right shoulders, hips, elbows, wrists (8); Stage4 directly uses the six arm points. |
| Q2 COCO17 coverage | **CONDITIONAL PASS** for named landmarks: all eight and all 13 currently mapped points are present. |
| Q3 preserve current schema via adapter | **Yes at the structural level**; semantic equivalence remains unproven. |
| Q4 semantic mismatch | Pixel transform/letterbox, bbox extent, pose-score distribution, absent/invalid point handling, multi-person ordering. |
| Q5 confidence mismatch | MediaPipe min(visibility,presence) versus unspecified model score; thresholds 0.50 and 0.65 may not mean the same thing. |
| Q6 left/right mismatch | Anatomical side must be preserved independently of image side and display mirror. |
| Q7 MediaPipe 33 strictly required | **No** for current Stage3/4 runtime; the adapter presently exports 13 named points. |
| Q8 minimum output | Synchronous BGR frame→`PoseFrameV1` list of `PersonPoseV1` with canonical joints, valid pixel/image coordinates, comparable confidence, bbox/pose score, monotonic ms timestamp and close/fail-closed behavior. |
| Q9 multi-person | **YES** for complete Stage5/6 production replacement; standalone Stage3/4 currently process one. |
| Q10 next-stage tests | Real keypoint/side/confidence calibration, missing/partial/negative gestures, bbox/score and multi-person tracker behavior, timing/NPU coexistence. |

**Recommended next step: MODEL SELECTION against this frozen contract**, followed by adapter-specific validation. Interface coverage alone is not a model acceptance result. **PX4 LIVE OUTPUT: NOT USED.**
