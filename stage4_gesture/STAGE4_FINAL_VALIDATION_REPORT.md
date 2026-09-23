# Stage 4 Final Validation Report

Date: 2026-09-23  
Decision (updated after explicit operator confirmation): **PASS / Gesture V1 FROZEN**.

## 1. Gate Scope

This is a final audit of the existing frozen Geometry Gesture V1 and real recordings, not
training, retuning, new data collection, Stage 5 work, or flight-control integration. The NUC
`/home/sentinel/drone_stage4_gesture` was the execution source. All sequence results below were
recomputed from recorded `NormalizedSkeletonV1` using the existing recognizer and stabilizer.
The original recorded predictions were also checked against replay. No runtime Gesture code,
threshold, source sequence, or ground-truth label was changed during this audit.

The final valid held-out set contains 46 sequences after the operator explicitly confirmed that
`0039` and `0040` were recording errors in which the target gestures were not correctly
performed. The 48-sequence pre-exclusion result remains visible as a mandatory raw diagnostic;
the exclusion is based on invalid ground truth, not on model predictions.
Metrics describe this dataset; no universal official PASS threshold is claimed.

## 2. Frozen Gesture V1

| Label | Deliberate pose |
|---|---|
| LEFT | Anatomical left arm horizontal, opposite arm down |
| RIGHT | Anatomical right arm horizontal, opposite arm down |
| ASCEND | Both arms straight upward / Y |
| DESCEND | Both arms diagonally downward and outward / inverted Y |
| HOVER | Both elbows bent about 90 degrees, forearms upward / goalpost |
| UNKNOWN | Natural arms down, T-Pose, or non-protocol pose |
| INVALID | Insufficient, invalid, or low-confidence required geometry |

T-Pose remains reserved for possible Stage 5 authorization, not a Stage 4 legal flight gesture.
Input/output remain `NormalizedSkeletonV1` / `GestureCandidateV1`.

## 3. Frozen Parameters

`descend_out_x_min=0.40`; temporal confirmation requires **at least 300 ms and 4 frames**;
release requires **at least 120 ms and 2 frames**; raw INVALID immediately clears temporal
state. The DESCEND threshold had previously changed from 0.35 to 0.40 using Pilot data. No
parameter was tuned on validation data. NUC unit tests: **14/14 PASS**.

The NUC Stage 4 directory has no Git commit. Frozen-file SHA-256 prefixes for traceability:
`gesture/config.py` `5b3984d9`, `gesture/geometry.py` `662bc3e2`,
`gesture/temporal.py` `f9de146b`, `gesture/types.py` `25d7e667`.

## 4. Dataset Inventory

| Cohort | Source | Sequence count | Legal classes | UNKNOWN | Role |
|---|---|---:|---|---:|---|
| Pilot / development | `datasets/20260916_101001_UTC` | 22 | 3 per class = 15 | 7 | Rule discovery/tuning; not independent Gate evidence |
| Validation / raw diagnostic | `datasets/20260923_051541_UTC` root **plus** `excluded/` | 48 | LEFT 8, RIGHT 7, ASCEND 8, DESCEND 8, HOVER 7 = 38 | 10 | Mandatory pre-exclusion history |
| Final valid held-out | Same session root only | 46 | LEFT 8; others 7 each = 36 | 10 | Final Gate evidence after operator-confirmed invalid-ground-truth exclusions |

The validation session was collected after the Pilot's 0.40 threshold revision. Stored raw/stable
predictions match frozen-code replay on every validation frame. Session provenance is therefore
known; **no whole session is `PROVENANCE UNCERTAIN`**. The operator has explicitly confirmed that
the intended physical actions in `0039` and `0040` were not correctly performed. Source video
was not saved, so this cannot be independently visually reconfirmed. Negative-sequence subtypes
are not recorded. The operator confirmation is recorded as provenance of the exclusion.

Earlier in the validation session, the operator confirmed `0017`/`0018` were negative samples
accidentally recorded under HOVER. They were auditably relabeled UNKNOWN before this final audit;
their original files, hashes and relabel log remain in `relabel_backup/` and
`relabel_audit.jsonl`. “Raw validation” below means **before the later `0039`/`0040` exclusions,
after that operator-confirmed acquisition-label correction**. The original-as-recorded labels
are not misrepresented as independently correct ground truth.

Collection included varied subject image positions (active legal sequence median body-center x
spans approximately 276–1110 px in the 1280-px eye) and operator-reported distance variation.
**Exact per-sequence physical distance was not recorded**; body scale/pixel position was not
converted into invented distance ground truth. A complete distance-by-position matrix cannot
be proven from these files and is not required for this Gate decision.

## 5. Dataset Integrity

Audit scope: 22 Pilot + 46 active validation + 2 operator-isolated validation files = **70
unique JSONL files**. All 70 parsed; corrupted 0, empty 0, mixed/unsupported labels 0,
non-increasing timestamp or frame-ID sequences 0, shorter than 1 s 0, exact SHA-256 duplicate
groups 0. The 2 isolated files are retained intact with unchanged hashes. Exact byte-duplicate
checking does not establish whether two separately recorded poses looked similar.

Validation raw contains **6,551 frames**; 10 UNKNOWN sequences contain **3,385 frames**.
Pilot stored-versus-replayed labels differed in 29 raw-label and 8 stable-state frames, all
within Pilot recordings made before the 0.40 revision. Validation mismatch count is **0**.
The audit did not silently discard malformed or short files. Source sequence JSONLs do not
contain RGB/video frames or per-frame action-onset/offset annotations.

## 6. Raw Evaluation — mandatory pre-exclusion diagnostic

Sequence-level intended-label accuracy on held-out legal actions: **36/38 = 94.74%**.
LEFT 8/8, RIGHT 7/7, ASCEND 7/8, DESCEND 7/8, HOVER 7/7. The supplementary side-position
batch `0027`–`0048` by itself is **20/22**, not 20/20 raw.

- `sequence_0039_ASCEND.jsonl`: 132 frames / 4.545 s; 0 ASCEND raw or stable frames; 12 raw
  DESCEND frames and one 5-frame stable DESCEND event. Recorded skeleton wrists slope below
  shoulders, strongly suggesting a label/action mismatch. Original video is unavailable, so
  this was later confirmed by the operator as a recording error; visual reconfirmation is impossible.
- `sequence_0040_DESCEND.jsonl`: 82 frames / 2.982 s; 3 raw DESCEND frames and **no stable
  DESCEND**. For 79 frames the failed rule was `right_wrist_outward`; median right-wrist outward
  displacement 0.398 body-scale against the frozen 0.40 threshold. This is a genuine observed
  false rejection under the as-recorded label. The operator later confirmed that the intended
  inverted-Y pose was not correctly performed. A recognizer failure alone would **not** have
  been an admissible exclusion reason; invalid target execution is the explicit reason here.

No other held-out legal sequence had a wrong stable legal label.

## 7. Final Valid Held-out Evaluation

At the operator's request, `0039` and `0040` were moved, not deleted, into
`datasets/20260923_051541_UTC/excluded/`; exact paths, reasons, and SHA-256 are in
`reports/EXCLUSIONS_20260923.md`. The operator subsequently explicitly confirmed that both
target gestures had not been correctly executed during recording. Therefore the 46-sequence
active set is now the **final valid held-out set**: **36/36 legal correct**, 10/10 UNKNOWN
rejected, 0 legal-to-legal confusion. The raw 48-sequence result remains reported above.

The earlier sensitivity analysis excluding only `0039` was **36/37 = 97.30%**. It is historical
diagnostic context, not the final valid cohort. Pre-exclusion reports and both recordings are
retained; no failure or negative counterexample was erased.

## 8. Sequence-level Confusion Matrix

Each sequence contributes one prediction: its most frequent stable legal label; if none was
confirmed, UNKNOWN (or INVALID for an INVALID-truth sequence). Multiple stable legal labels,
wrong-label frames and events are also counted separately; thus this matrix never replaces
the false-trigger analysis below. Raw held-out matrix:

| Truth / predicted | LEFT | RIGHT | ASCEND | DESCEND | HOVER | UNKNOWN | INVALID |
|---|---:|---:|---:|---:|---:|---:|---:|
| LEFT | 8 | 0 | 0 | 0 | 0 | 0 | 0 |
| RIGHT | 0 | 7 | 0 | 0 | 0 | 0 | 0 |
| ASCEND | 0 | 0 | 7 | 1 | 0 | 0 | 0 |
| DESCEND | 0 | 0 | 0 | 7 | 0 | 1 | 0 |
| HOVER | 0 | 0 | 0 | 0 | 7 | 0 | 0 |
| UNKNOWN | 0 | 0 | 0 | 0 | 0 | 10 | 0 |
| INVALID | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

The final valid held-out 46-sequence matrix is diagonal: LEFT 8, RIGHT 7, ASCEND 7,
DESCEND 7, HOVER 7, UNKNOWN 10; INVALID support remains 0.

## 9. Precision / Recall — sequence level

| Gesture | Raw support | Raw precision | Raw recall | Final valid support | Final valid precision / recall |
|---|---:|---:|---:|---:|---:|
| LEFT | 8 | 1.000 | 1.000 | 8 | 1.000 / 1.000 |
| RIGHT | 7 | 1.000 | 1.000 | 7 | 1.000 / 1.000 |
| ASCEND | 8 | 1.000 | 0.875 | 7 | 1.000 / 1.000 |
| DESCEND | 8 | 0.875 | 0.875 | 7 | 1.000 / 1.000 |
| HOVER | 7 | 1.000 | 1.000 | 7 | 1.000 / 1.000 |

These are small-sample, sequence-level dataset measurements. Whole-recording frame-level
recall is not held-pose recall because preparation and transition frames are included.

## 10. UNKNOWN Rejection

Held-out UNKNOWN: **10/10 sequences** without a stable legal label; **0/3,385 frames** with
a stable legal label. Pilot (development only): 7/7 sequences and 0/3,197 frames after replay
at the frozen threshold. The Pilot result is regression evidence, not independent confirmation.
UNKNOWN frame-level replay split includes UNKNOWN and INVALID outputs; neither is a legal
control gesture.

## 11. False Trigger Analysis

| Ground truth → stable legal output | Held-out frames | Held-out sequences |
|---|---:|---:|
| UNKNOWN → LEFT | 0 | 0 |
| UNKNOWN → RIGHT | 0 | 0 |
| UNKNOWN → ASCEND | 0 | 0 |
| UNKNOWN → DESCEND | 0 | 0 |
| UNKNOWN → HOVER | 0 | 0 |
| INVALID → any legal | not measured: 0 INVALID-truth sequences | not measured |
| ASCEND → DESCEND | **5** | **1** (`0039`) |
| Other legal A → legal B | 0 | 0 |

The single wrong-label episode is an actual 5-frame stable confirmation, not merely a raw
candidate. Stage 3 snapshot regression separately includes 6 INVALID-truth frames, all
rejected as INVALID by the Stage 4 recognizer; it is not a temporal INVALID-sequence test.

## 12. T-Pose Reserved Gesture Check

The sequence JSONL has no negative-subtype tag: the number of T-Pose sequences within the 10
held-out UNKNOWN recordings is **not identifiable**, and their T-Pose-specific trigger count
cannot be isolated. In the labeled Stage 3 snapshot regression, there is **1 confirmed T-Pose
snapshot**, predicted UNKNOWN, **0 legal triggers**. The frozen unit test also checks T-Pose
rejection. This supports reservation of the pose but does not prove held-out temporal T-Pose
coverage. No T-Pose was promoted to a legal Stage 4 command.

## 13. Natural Standing / DESCEND Regression

The earlier Pilot at threshold 0.35 had one natural-standing false DESCEND confirmation plus
four release-grace frames. After the documented change to 0.40, Pilot replay has 0 stable
DESCEND frames among its 3,197 UNKNOWN frames. The labeled Stage 3 regression contains **9
natural-standing snapshots**, all UNKNOWN and **0 DESCEND false triggers**. The held-out
UNKNOWN set has **0/3,385 stable DESCEND frames**, but exact natural-standing frame and event
counts are **not measurable** because sequence negative subtypes were not recorded. Therefore
the held-out evidence supports general UNKNOWN rejection but cannot, by itself, prove the
specific natural-standing subtype regression.

## 14. Temporal Confirmation

Measured from first correct raw candidate to first correct stable confirmation in the **36/38
raw held-out legal successes** (timestamps, not an assumed FPS): min **301 ms**, mean
**337.6 ms**, median **318 ms**, p95 **341 ms**, max **978 ms**. The 978-ms outlier is
`sequence_0013_DESCEND`, whose early raw matches were interrupted. Most successful sequences
are consistent with the configured 300 ms / 4-frame minimum. `0039` had no correct ASCEND raw
candidate; `0040` had raw candidates but no confirmation, so neither was assigned a fabricated
confirmation latency. Pilot successes (15) had mean 320.2 ms, median 321 ms, p95 332 ms.

## 15. Release Behavior

Using the first raw departure from a confirmed legal label as a proxy for action exit, the
pre-exclusion raw set has **4 measurable releases**: 0, 122, 133, 152 ms; mean **101.8 ms**,
median **127.5 ms**, p95 **149.2 ms**, max **152 ms**. The 122-ms case came from excluded
`0039`. The **final valid set has 3** measurable releases (0, 133, 152 ms; mean 95 ms,
median 133 ms, p95 150.1 ms). The 0-ms case was INVALID, consistent with immediate clearing.
No measured sample showed a
long sticky confirmation. **Release coverage is sparse**: most recordings end while the pose
is still held, and no manual action-end timestamps exist. This is not proof of release behavior
for every gesture/transition in production.

## 16. Gesture Transition and Sustained Confirmation

Pre-exclusion raw held-out contains **1 wrong-legal stable-confirm event**, the 5-frame
ASCEND→DESCEND episode in operator-confirmed recording error `0039`; final valid set contains
**0**. These are wrong-label-event counts,
not a complete test of deliberate A→B transitions: neither transition start/end nor a framewise
true action label was recorded. Third-gesture confirmations during unannotated transitions
cannot be ruled out beyond the observed wrong stable labels.

The active set shows sustained correct stable runs, but whole-sequence ratios include setup and
release and are **not hold-only recognition rates**. Median longest continuous correct runs by
class were LEFT 1,929 ms, RIGHT 1,499 ms, ASCEND 1,027 ms, DESCEND 2,027 ms and HOVER
2,323 ms. A few otherwise successful recordings had runs shorter than 1 s; no active sequence
showed a stable wrong legal class. There is no justified frame-level “during intended hold”
ground truth, so no such metric is invented.

## 17. Hard-negative Results / Cross-stage Defense

The frozen recognizer was rerun over the labeled Stage 3 snapshot manifest: **19/19 correct**,
comprising 1 ASCEND, 12 UNKNOWN and 6 INVALID. Confirmed subtypes: 9 natural-standing,
1 T-Pose, 2 partial single-arm, 1 partial-arm occlusion, 1 chair occlusion, 3 heavy truncation,
1 no-person. UNKNOWN/INVALID snapshots produced **0 legal triggers**. Other suggested hard
negative types (scratching, clothing adjustment, walking/turning, etc.) cannot be counted
individually in the unlabeled held-out UNKNOWN sequences; they are not claimed as verified.

The chair-occluded snapshot is important defense-in-depth evidence: Stage 3 PoseQuality marked
it valid (score approximately 0.991), yet Stage 4 returned INVALID due low right-elbow and
right-wrist confidence. Stage 3 acceptance did **not** become a legal gesture trigger.

## 18. Known Limitations and Decision Risks

- Sequence JSONLs do not preserve source RGB/video; **original visual recording quality is not
  independently reviewable from sequence JSONL alone**. Blur and exact execution cannot be
  certified here. This limitation does not automatically fail the Gate.
- No exact distance or negative-subtype metadata; image-position variation is demonstrable,
  physical-distance variation is operator-reported rather than per-sequence ground truth.
- The Pilot was used for threshold tuning; its perfect replay is not independent evidence.
- The operator explicitly confirmed that `0039` and `0040` were incorrectly executed target
  gestures. Source video is unavailable for independent reconfirmation. The pre-exclusion
  36/38 and post-exclusion 36/36 are both reported so this judgment remains auditable.
- Held-out INVALID temporal sequences, T-Pose-tagged sequences, framewise transitions and
  comprehensive release coverage are absent or unidentifiable. The 19 labeled snapshots and
  deterministic tests support these paths, but do not replace temporal coverage.
- This is a single available human/camera setting, not a population-level reliability claim.

## 19. Gate Decision

**Stage 4 — PASS / Gesture V1 FROZEN**, on the explicit operator confirmation that `0039` and
`0040` did not correctly execute their target gestures and thus have invalid ground truth.
Final valid held-out results are 36/36 legal confirmations, 10/10 UNKNOWN rejections across
3,385 frames, 0 stable legal false triggers and 0 legal-to-legal confusion. All five gestures
have real sequence support; the frozen interface and rejection logic are implemented and tested;
confirmation latency largely matches the 300-ms design. The raw 36/38 diagnosis and the lack
of independent visual evidence for those exclusions remain visible, as do limited subtype and
release coverage. These limitations do not block this V1 Gate, but prohibit claims of universal
identity/gesture reliability.

Gesture V1 geometry, `GestureCandidateV1`, temporal parameters and UNKNOWN semantics are now
frozen. T-Pose stays reserved for Stage 5 authorization. Stage 5 visual ownership work may
begin, but this decision does **not** authorize ROS2/PX4/Gazebo flight-loop integration or
real flight.

Reproducibility artifacts (NUC and Windows report backup):

- `reports/final_validation_audit.py` — read-only evaluator, no source/rule changes.
- `reports/stage4_final_audit_metrics.json` — integrity, per-sequence, sequence-matrix,
  confusion, false-trigger, confirmation/release and sensitivity metrics.
- `reports/stage4_final_validation_raw_recomputed.json` — existing evaluator on all 48
  held-out sequences, explicitly including `excluded/`.
- `reports/stage3_hard_negative_final_replay.json` — frozen recognizer on labeled Stage 3
  snapshots.
- `reports/EXCLUSIONS_20260923.md` — operator exclusion log and Gate-use caveat.
