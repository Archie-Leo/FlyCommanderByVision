# Stage 5 V2 — visual-only operator ownership design

Date: 2026-09-24. Current status: **V2.2 SINGLE-OPERATOR DEMO BASELINE PASS / full multi-person safety validation deferred to Stage 7**. The latest single-person recording showed four full-loss automatic new-Session returns; this is not a multi-person safety PASS. Earlier V2/V2.1 evidence below is historical.

## Boundary and sources

V1's Operator Session concept, 600 ms / 6-frame T-Pose recognizer, Stage 4
`GeometryGestureRecognizer` / `TemporalStabilizer`, `AuthorizedGestureV1`, JSONL
and fail-closed rule are retained. Stage 3/4 frozen algorithms and V1
tracking/ownership algorithms are not changed; the V1 runner only gained
diagnostic recording. No ROS 2, PX4, Gazebo or real-drone output exists in V2.

V2 consumes the synchronized 2560×960 SBS frame. Pose runs on Run B rectified
LEFT; depth uses original LEFT/RIGHT with the same Run B calibration and Stage 2
StereoSGBM settings. The per-person depth adapter samples the central torso
area of each rectified bbox (x=32–68%, y=22–62%), filters invalid disparity,
nonfinite XYZ and implausible Z, then uses median Z and MAD. Depth is measured
in metres after `Q` yields millimetres. Invalid depth is *unavailable*, not
zero-confidence evidence. Quality depends on valid-pixel ratio, spread and
distance; 0.5–2.5 m is the only physically checked Stage 2 range.

Global MOT is BoxMOT native BoT-SORT via its typed C ABI v2. The frozen
reference is commit `857628343860db1ea48ea50db7a73c25b7a3be13`, license
AGPL-3.0. It is compiled out of tree in `build/botsort`, not modified. The
adapter requires external person embeddings and accepts no silent IoU/HSV
fallback. BoxMOT's AGPL obligations need review before distribution/service
use. OSNet is sourced from the frozen `deep-person-reid` reference commit
`f8cd150`, architecture `osnet_x0_25`, expected 512-dimensional L2-normalized
embedding. An isolated NUC V2 venv has PyTorch CPU 2.5.1 and matching
NumPy/OpenCV. The supplied MSMT17 checkpoint at
`~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth` loaded with
`weights_only=True`; SHA256 is
`cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18`.
The runner raises before camera use if the checkpoint is missing. A synthetic
crop produced a normalized 512-D embedding; this is not a real-person ReID
accuracy validation.

## Ownership model

`operator_session_id` is a UUID that survives MOT track ID changes.
`OperatorTargetMemory` stores bbox/depth/XYZ history, velocities, gallery,
quality and last confirmed time. T-Pose observations accumulate in a temporary
seed buffer; only one unique, temporally confirmed, embedding-consistent person
creates a session and commits the seed. No per-frame tracker output writes the
Gallery. Later gallery updates require GREEN, identity score ≥0.85, crop
quality ≥0.75, occlusion ≤0.25, candidate margin ≥0.10, no conflict and
motion/depth gates passing. RED/ambiguous/reacquiring never update it.

Every candidate has `Evidence(score, reliability, available)` for OSNet,
depth, 2D motion, MOT continuity, weak shoulder/torso geometry and weak HSV. Initial engineering
weights are 0.40/0.20/0.15/0.10/0.10/0.05, configured in `Settings`.
Available terms are weighted by reliability and normalized by effective
weight. Track ID is only weak continuity evidence, never an identity key.
Hard gates run first: valid person/timestamp, minimum crop quality, plausible
2D motion, plausible depth/XYZ innovation when depth exists, no face conflict,
and no strong ReID mismatch. An impossible depth jump cannot be compensated
by a high ReID score. These constants are provisional heuristics, not
published accuracy guarantees or calibrated probabilities.

The best candidate must also beat second-best by ≥0.10. GREEN requires
normalized score ≥0.82, effective evidence weight ≥0.35 and ReID similarity
≥0.76. A changed MOT ID or return from RED requires ≥400 ms and ≥5 frames
of unique evidence. RED grace is 1100 ms; a detection miss enters
`SHADOW_TRACKING`, and hard conflict enters `REACQUIRING` directly. After
grace timeout state becomes `OPERATOR_LOST`. The state remains fail-closed
throughout: only `LOCKED_HIGH` plus valid Stage 4 stable gesture produces
`AuthorizedGestureV1.valid=true`.

UI: GREEN=`LOCKED_HIGH` trusted operator; RED=`LOCKED_LOW`,
`SHADOW_TRACKING`, `REACQUIRING`, `AMBIGUOUS` suspected operator; YELLOW=new
person/bystander or pre-authorization; GRAY=`OPERATOR_LOST`. A hard-rejected
bystander is not displayed as a red suspected operator. Colors are presentation,
not identity evidence.

## Known limitations and next validation

### V2.2 experimental safe auto reauthorization after full loss (2026-09-24)

`WAIT_OPERATOR` still needs the original 6-frame/600-ms unique T-Pose. The
existing same-Session short `REACQUIRE_CONFIRMING` path is unchanged. After
`OPERATOR_LOST`, the prior Session remains dead and its Gallery is read-only.
The original T-Pose + old-Gallery >=0.90 fallback also remains unchanged.

An *opt-in*, separate path (`--auto-reauthorize`, default OFF) can enter
`AUTO_REAUTHORIZE_CONFIRMING`. It uses a fresh candidate, not the stale
old-session motion/depth gate or MOT ID as proof. First it requires a valid
person/timestamp, crop >=0.75, valid pose, no face/strong ReID conflict,
old-Gallery max similarity >=0.94, top-3 mean >=0.92, at least two Gallery
entries >=0.90, fused score >=0.88, effective evidence weight >=0.35,
unique candidate and best/second margin >=0.15. A second candidate with
Gallery max >=0.94 is ambiguous even if one scores slightly higher. The
fused score uses the existing evidence weights but only available fresh
ReID, weak pose geometry and weak HSV; it does **not** treat stale motion,
old depth, or track ID as long-term identity. During confirmation, consecutive
candidate motion/depth changes must remain physically plausible. A MOT ID
change aborts this attempt; a fresh attempt with the new ID may start next
frame, but never inherits the prior frame count.

`AUTO_REAUTHORIZE_CONFIRMING` is RED, Authorized=NO and Gallery-write=NO.
It requires >=8 valid frames **and** >=700 ms within a 1500-ms attempt.
Any disappearance, weak identity, low score/weight, low crop, hard gate,
competition, ID change, invalid timestamp, nonfinite evidence or timeout
aborts to `OPERATOR_LOST`; no previous count survives. Success creates a
**new** Session UUID and logs `authorization_source=AUTO_REAUTHORIZE`, old/new
Session IDs and `AUTO_REAUTH_SUCCESS`. The old Gallery is not updated during
confirmation; even after GREEN, writes are held for another 1000 ms. The
Stage 4 temporal state is reset on Session change, and the success frame
does not process a Gesture. No previous Gesture/Intent/lease is replayed.

All thresholds are independent `Settings` values and **provisional
engineering values**, not calibrated ROC cutoffs or accuracy guarantees.
The current OSNet is appearance-based. A similar-clothing bystander remains
a serious unmeasured risk. This implementation is visual-only and must not
be connected to ROS2/PX4 or marked Stage 5 PASS before A/B, crossing,
same-clothing and authorized-Gesture真人 Gate tests.

Every frame logs auto enabled/state/candidate, Gallery max/top-k/count,
long-term fused score and margin, confirmation frames/elapsed/deadline,
reject reason, authorization source and old/new Session IDs. `V` exposes
these in the preview. The latest V2.1 JSONL lacks raw embeddings and these
new statistics: its offline ReID windows are an upper-bound feasibility
check, **not** a full V2.2 replay or real-person validation.

### V2.1 bounded reacquisition confirmation (2026-09-24)

Problem: the old 1100 ms RED grace timer continued during valid multi-frame
reacquisition. In recorded `clip_002`, the old operator reappeared as track 3
with fused scores 0.949/0.940/0.941 at frames 403–405, but the earlier RED
timer changed `REACQUIRING` to `OPERATOR_LOST` at frame 405. This was a timer
FSM defect, not a reason to lower identity thresholds.

Fix: a unique candidate passing the existing hard gates, ReID check, GREEN
score/effective-weight gates and candidate margin, with crop quality >=0.50,
enters `REACQUIRE_CONFIRMING`. Invalid pose is weak identity evidence, not a
hard identity gate; it still blocks gesture authorization. The state is RED,
uses the same operator Session UUID, stores the candidate separately from the
confirmed track ID, and never authorizes gestures or updates the Gallery.
Confirmation requires the same candidate for 5 frames and >=400 ms. It has an
independent 900 ms per-attempt deadline; the *absolute* overall reacquire
horizon is 2000 ms from first RED evidence. Both are `Settings` parameters.
The old 1100 ms grace remains for ordinary RED without a valid confirmation
attempt. Starting one valid attempt switches to the bounded overall horizon;
subsequent weak/ambiguous/disappearing candidates cannot reset that horizon.
Conflict, failed hard gate, changed candidate or deadline aborts confirmation;
success returns to `LOCKED_HIGH` without replaying any previous Gesture.

Every frame logs confirmation active/candidate/frames/elapsed/deadline, overall
elapsed/deadline and `state_transition_reason`; the UI marks confirmation RED
and unauthorized. The existing 0.90 *lost-session T-Pose reauthorization*
threshold, OSNet model, BoT-SORT, evidence weights and hard gates are unchanged.
Depth beyond the 0.5–2.5 m physically checked range already has tapered
reliability; unavailable depth is not treated as a strong negative. At the
recorded 4.5–4.9 m crossing, pose was often invalid, so the Stage 5 Gate
should first be measured at about 0.8, 1.5, 2.0 and 2.5 m.

Future Upgrade only (not implemented): `LongTermOperatorMemory` could hold a
quality-controlled multi-view OSNet gallery, upper/lower clothing profiles,
body proportions, estimated height and optional face evidence. It would aid
long-term out-of-frame identity matching, but could not prove continuity while
the operator is unobserved or automatically grant flight-control authority.

2026-09-24 post-video fix: The four recorded V2 clips under
`runs_v2/20260924_045609_UTC` through `20260924_045944_UTC` each established
an Operator Session. Near-field crop/pose degradation and full departure
caused loss. Previously, `OPERATOR_LOST` retained the old memory but did not
enter T-Pose acquisition, while the old 3-second motion gate eventually made
recovery impossible. V2 now retains the old gallery read-only while lost and
requires a fresh, unique, 600 ms / 6-frame T-Pose with valid pose, full crop,
and provisional old-gallery similarity >=0.90. Success creates a **new**
Session UUID; ordinary motion/track-ID continuity alone cannot resurrect the
expired session. A similar-looking impostor remains a residual risk; the
threshold is an engineering heuristic pending two-person testing, not a
verified identity guarantee. Explicit `X` remains available to abandon the
old identity and start a new first authorization.

`acquisition_checks` now records per-person T-Pose match/reasons, pose validity,
crop quality, embedding/bbox availability and (for reauthorization) old-gallery
similarity. `V` debug overlay shows these. Empty scenes no longer run stereo
SGBM and the live runner reuses its already-rectified LEFT frame. Person-present
depth settings and fail-closed authorization gates are unchanged. A 20-frame
empty-scene NUC camera smoke test showed median depth-stage latency ~0 ms;
this does **not** establish person-present FPS improvement.

The V2 runner completed a 12-frame *empty-scene* camera/recording smoke test.
Its capture timestamp intervals had median ~9.79 FPS; Stage 5 median depth
was 73.7 ms and total Stage 5 processing 74.9 ms with no detected people.
This is not a person-present OSNet/MOT performance result; do not disable
safety evidence just to improve FPS. The bbox-center torso ROI is a first-pass approximation and
must be checked against people/occluders. OSNet similarity thresholds and all
state/weight constants require real single-/two-person, same-color,
near/mid/far, crossing and occlusion tests. Wrong-operator authorization is
the primary metric; no synthetic test can establish a zero real-world rate.
