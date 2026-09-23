# Stage 4 Validation Report

> Historical implementation/Pilot/interim audit log. The formal 2026-09-23 Gate decision and
> primary **raw** held-out metrics are in `STAGE4_FINAL_VALIDATION_REPORT.md`. Earlier 46-sequence
> operator-curated results do not supersede the 48-sequence raw result.

Date: 2026-09-16  
Status: **IMPLEMENTED / REAL GESTURE DATASET PENDING**

## Frozen protocol and interface

- Legal labels: LEFT, RIGHT, ASCEND, DESCEND, HOVER.
- Reject labels: UNKNOWN, INVALID.
- T-Pose is reserved for possible Stage 5 authorization and is UNKNOWN in Stage 4.
- Natural arms-down is UNKNOWN; HOVER is a deliberate double-goalpost pose.
- Input is only `NormalizedSkeletonV1`; output is frozen `GestureCandidateV1`.
- Geometry thresholds are configurable engineering heuristics, not calibrated probabilities or
  official MediaPipe safety thresholds.
- No Tracking, ReID, Operator Ownership, ROS 2/PX4 intent or flight-control output is present.

## Software verification

- Python compileall: PASS.
- Deterministic unittest: 14/14 PASS, including auditable relabel/backup and the borderline
  natural-down DESCEND regression.
- Five synthetic legal gestures: PASS.
- Anatomical LEFT/RIGHT separation: PASS.
- Natural standing UNKNOWN: PASS.
- Reserved T-Pose UNKNOWN: PASS.
- Invalid/missing/low-confidence/non-finite input rejection: PASS.
- Temporal 300 ms / 4-frame confirmation: PASS.
- 120 ms / 2-frame release grace: PASS.
- INVALID immediately clears temporal state: PASS.
- Non-monotonic timestamp rejection: PASS.
- Metric calculation including false-trigger count and legal rejection rate: PASS.
- Synthetic temporal sequence evaluator: PASS; measured confirmation latency 300 ms and zero
  negative-sequence false triggers.

## Real NUC/camera smoke

- Host/environment: sentinel-S600 / `~/venvs/drone_stage3`.
- Project: `~/drone_stage4_gesture`.
- Real chain: `/dev/video0` -> Run B rectified LEFT -> MediaPipe -> PoseQuality ->
  NormalizedSkeletonV1 -> Geometry Rules -> Temporal Stabilizer.
- Post-implementation headless runs: 60 frames and 30 frames, both completed cleanly with camera
  release and runtime summary; no sample was auto-saved.

## Retained Stage 3 real-sample replay

Nineteen retained real snapshots were labeled under the new Gesture V1 protocol:

| Ground truth | Support | Correct result |
|---|---:|---:|
| ASCEND | 1 | 1 |
| UNKNOWN | 12 | 12 |
| INVALID | 6 | 6 |
| LEFT | 0 | n/a |
| RIGHT | 0 | n/a |
| DESCEND | 0 | n/a |
| HOVER | 0 | n/a |

- False legal control triggers: 0.
- Existing both-arms-up sample: ASCEND score 0.965692.
- Natural positions/distances, incomplete single-arm raises and reserved T-Pose: UNKNOWN.
- Stage 3 arm occlusion, no-person and heavy truncation: INVALID.
- The chair-occlusion hard negative, although Stage 3 quality accepted it, is rejected by Stage 4's
  stricter required-arm confidence gate.

This replay is useful hard-negative evidence but is not a balanced five-class evaluation. Precision
and recall are currently meaningful only for the single ASCEND observation. No Stage 4 Gate PASS is
claimed.

## Remaining Gate work

- Collect the balanced real sequence matrix in `STAGE4_DATASET_PLAN.md`.
- Evaluate all five gestures across position, distance, repetition and mild viewpoint changes.
- Record negative/hard-negative sequences and transition frames.
- Review confusion matrix, per-class precision/recall, legal-action rejection rate, false-trigger
  sources and confirmation latency.
- Tune only with documented changes, then evaluate on held-out complete sequences.

## Pilot threshold diagnosis (2026-09-16)

The first 22-sequence development Pilot contained 15 legal sequences (3 per label) and 7 UNKNOWN
sequences. All 15 legal sequences confirmed the intended label with no wrong-label output. One
UNKNOWN sequence produced one DESCEND confirmation plus four release-grace frames because both
wrists briefly exceeded the original 0.35 outward threshold (observed minimum side 0.351–0.362).
The configured threshold was revised to 0.40. Replaying thresholds 0.40–0.60 removed this Pilot
false trigger while retaining all 15 legal confirmations; 0.40 is the smallest tested correction
and is used for V1. The Pilot is development data and cannot be reported as held-out proof of the
change.

Recomputed Pilot sequence metrics at 0.40: every legal label confirmed in 3/3 sequences; LEFT,
RIGHT, ASCEND, DESCEND and HOVER each have sequence-level precision/recall 1.0 on this development
set; 0/7 negative sequences and 0/3197 negative frames produced a legal stable label. Confirmation
from the first correct raw match was approximately 300–339 ms, consistent with the configured
300 ms/4-frame rule. These are development-set results, not the final Gate metrics.

## New independent collection audit (2026-09-23)

NUC session `datasets/20260923_051541_UTC` contains 26 JSONL sequences / 5,061 frames. The
operator confirmed `sequence_0017_HOVER` and `sequence_0018_HOVER` were negative samples recorded
under the wrong UI label. They were explicitly relabeled to UNKNOWN, including each frame's
`ground_truth_label`. Original files remain in the session's `relabel_backup/`, and
`relabel_audit.jsonl` records original/new paths and SHA-256 hashes. No raw sequence was deleted.

After relabeling: LEFT 4, RIGHT 3, ASCEND 3, DESCEND 3, HOVER 3, UNKNOWN 10. No empty or
mixed-label sequence was found. The frozen recognizer was recomputed without threshold changes;
machine-readable results are in `reports/heldout_20260923_051541_recomputed.json` (also copied
to the Windows project). All 16 legal sequences confirmed their intended stable gesture at least
once, with no wrong stable legal label. The 10 UNKNOWN sequences contained 3,385 frames and
produced zero stable legal false triggers. On this small set, sequence-level precision and recall
are 1.0 for each class (support only 3–4 per class). This is encouraging, not a Gate PASS.

The whole-recording frame-level legal rejection rate is about 48.2%, but recordings include
neutral setup and transitions and lack frame-level action-onset annotations. It must not be
presented as held-pose recall. Confirmation after the first correct raw match was generally
300–344 ms; `sequence_0013_DESCEND` took 978 ms because early matches were interrupted. Eight
of 16 legal sequences had a longest uninterrupted correct stable run below 1.5 s; the next
collection should deliberately hold each pose for about 2–3 s.

Combined active Pilot + new session: LEFT 7, each other legal class 6, UNKNOWN 17. Relative to
the project's engineering minimum of 9 sequences per legal class and 15 negatives, 14 legal
sequences are still missing (LEFT 2; RIGHT/ASCEND/DESCEND/HOVER 3 each); negatives meet count.
The new-session body centers cluster near the image center (median x about 576–695 on a 1280-px
eye), so clear image-left/right coverage is not demonstrated. Physical distances were not stored
in sequence metadata. These JSONLs contain skeleton/features and predictions, not source video;
this audit cannot independently judge blur, exact pose execution, or actual distance. Finish
position/distance coverage and manually review capture quality before final Stage 4 acceptance.

## Supplementary side-position collection audit (2026-09-23)

The same NUC session later gained sequences `0027`–`0048`: 22 new legal recordings / 1,490
frames (LEFT 4, RIGHT 4, ASCEND 5, DESCEND 5, HOVER 4). Recompute with the frozen code is saved
in `reports/new_20260923_0027_0048_recomputed.json`. Twenty of 22 sequences confirmed their
label. LEFT 4/4, RIGHT 4/4, HOVER 4/4, ASCEND 4/5, DESCEND 4/5. This is a label/recognizer
audit, not proof that every recording had correct physical form.

- `sequence_0039_ASCEND.jsonl` (132 frames, 4.545 s) never produced ASCEND, had 120 raw UNKNOWN
  and 12 raw DESCEND frames, and 5 stable DESCEND frames. The recorded skeleton's arms slope
  downward rather than upward (midpoint wrists below shoulders), suggesting a mislabeled or
  wrong-action recording. No source video is stored, so only the operator can confirm intent.
- `sequence_0040_DESCEND.jsonl` (82 frames, 2.982 s) had just 3 raw DESCEND frames and no
  stable confirmation. In 79 frames, the rule failure was `right_wrist_outward`; median
  shoulder-to-wrist outward displacement was 0.398 body-scale versus the frozen 0.40 threshold.
  This is a borderline/too-narrow pose, not a corrupt file. `0041`–`0044` all confirmed
  DESCEND continuously after temporal startup.

All 22 files have frames; no raw data or label was modified. Do not silently relabel `0039` or
loosen the DESCEND rule based on this sample: preserve it for review, and re-record a clear
ASCEND and a wider inverted-Y DESCEND if the operator confirms those were intended labels.
Across Pilot + current session, counts now exceed the 60-sequence engineering minimum (53 legal,
17 negatives), but actual distance cells are not encoded and these two anomalous recordings
remain unresolved. Stage 4 Gate stays pending manual data review and coverage verification.

## Operator-authorized exclusion and re-evaluation (2026-09-23)

At the operator's request, `sequence_0039_ASCEND.jsonl` and `sequence_0040_DESCEND.jsonl` were
moved from the active session root into `datasets/20260923_051541_UTC/excluded/`. They were not
deleted or relabeled. Their hashes, reasons, and pre-/post-exclusion report names are recorded in
`reports/EXCLUSIONS_20260923.md`. The pre-exclusion diagnostic report remains available.

After exclusion, the supplementary batch has 20 active legal sequences (4 per gesture), all
20 confirmed their intended stable label and none had a wrong stable label. The current session
has 46 active sequences: 36 legal and 10 UNKNOWN. Combined with the 22-sequence Pilot, there
are 68 active sequences: 51 legal and 17 UNKNOWN. All 51 legal sequences confirmed their
intended label at least once; the 17 UNKNOWN sequences / 6,582 negative frames produced no
stable legal false trigger. These figures describe the active recorded set, not generalization
or final Gate acceptance. Distance metadata and visual form/blur remain unverified; Pilot data
were used for earlier threshold tuning.
