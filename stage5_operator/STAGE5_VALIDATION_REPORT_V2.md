# Stage 5 V2 validation report — 2026-09-24

**Current result: V2.2 SINGLE-OPERATOR DEMO BASELINE PASS / full multi-person safety validation deferred to Stage 7.** This is not a full safety Gate PASS. Older counts below are historical checkpoints.

2026-09-24 single-person follow-up: completed clip
`runs_v2/20260924_105951_UTC/recordings/clip_001` contains first T-Pose
authorization followed by **four** complete `OPERATOR_LOST` episodes that
each returned via `AUTO_REAUTHORIZE_CONFIRMING` into a **new** Session UUID.
All 36 confirmation frames had Authorized=NO and no Gallery write; success
frames did not carry an old Gesture. The clip contains one detected person
at most, zero valid authorized flight gestures, and no stereo-depth evidence
on the four auto-success frames. Therefore it supports the single-operator
Demo baseline, **not** bystander non-impersonation, crossing, same-clothing,
wrong-operator authorization rate or full Stage 5 safety acceptance.

Verified on NUC `sentinel-S600`:

| Check | Result |
|---|---|
| Python V2 compileall | PASS |
| V2 safety + recording unit cases | 25/25 PASS (V1+V2: 44/44) |
| Native BoxMOT BoT-SORT out-of-tree compile | PASS |
| Native ABI one detection / empty frame smoke | PASS |
| Run B calibration + live SBS depth adapter | PASS; Stage 2/3 `config.py` import collision fixed without changing either project |
| Six-frame SGBM timing | 74.7–91.2 ms, median 77.1 ms (depth component only) |
| OSNet architecture forward, CPU PyTorch 2.5.1 | PASS; output `(1,512)` |
| Supplied OSNet checkpoint | Loads with `weights_only=True`; SHA256 `cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18` |
| Trained OSNet extraction on synthetic crop | PASS; 512-D, unit norm, ~23.9 ms; not person accuracy |
| V2 12-frame empty-scene runtime | PASS; median capture interval ~9.79 FPS; depth 73.7 ms, Stage 5 74.9 ms; no people detected |
| Real-person first authorization / conservative loss | PARTIAL; four recorded clips acquired sessions, but post-fix reauthorization untested |
| Two-person, same-color, occlusion and authorized gesture | GATE PENDING |

2026-09-24 diagnostic-recording update: three recorder tests passed; full
V1+V2 suite is now 44/44. An actual `/dev/video0` two-frame V1 headless
recording with optional raw SBS produced readable `annotated.avi` (2 frames),
two matching `frames.jsonl` entries and lossless 2560×960 PNGs. Saved at
`/home/sentinel/drone_stage5_operator/runs/20260924_041709_UTC/recordings/clip_001`.
This verifies recording plumbing, **not** V2 trained ReID or Stage 5 Gate.

After the checkpoint was supplied, V2 headless runs with `--record` passed.
The two-frame raw-stereo clip at
`/home/sentinel/drone_stage5_operator/runs_v2/20260924_042732_UTC/recordings/clip_001`
contains 2 readable video frames, 2 JSONL rows and 2 decoded SBS PNGs; all
frame IDs match. A later 12-frame annotated clip at
`/home/sentinel/drone_stage5_operator/runs_v2/20260924_042851_UTC/recordings/clip_001`
has 12 video frames and 12 JSONL rows. The summary's 1.65 FPS includes model
and camera startup; median capture-timestamp interval is ~9.79 FPS in the
empty scene. OSNet per-person latency was zero in this run because no person
was present. Do not extrapolate to a person-present frame rate.

The synthetic suite checks continuous/invalid/unavailable depth, impossible lateral XYZ jump, ReID conflict,
temporary quality drop, RED recovery and timeout, candidate ambiguity,
track-ID change/reuse, same-HSV mismatch, Gallery write gating, authorization
colors and nonfinite evidence rejection. It does **not** prove detector,
OSNet, depth and tracker work together on real people.

The old V1 real video showed safe denial but frequent false loss. The V2
runner now starts with the supplied OSNet checkpoint and still refuses a
missing one. Next: conduct and record the prescribed single-person,
two-person and same-clothing manual tests with JSONL review. Do not mark
Stage 5 PASS or start Stage 6 on this report.

## 2026-09-24 loss/re-authorization fix (Gate still NOT PASSED)

Four later real-person clips (`045609`, `045722`, `045829`, `045944` UTC) all
reached `LOCKED_HIGH`. In `045944`, near/out-of-frame observations reduced crop
quality to 0.25–0.50 and caused `LOCKED_LOW`/detection misses. Once the old
session timed out, a returning track could not recover after the 3-second
motion gate expired. This was a state-machine defect, not evidence that the
returning person was an impostor.

The fix adds explicit lost-session T-Pose + old-gallery re-authorization, a
fresh Session UUID on success, and detailed acquisition diagnostics. Synthetic
tests cover delayed return, no-T-Pose denial, mismatching bystander denial and
two matching candidates remaining ambiguous. NUC suite: **49/49 PASS** with
the existing Stage 4 module on `PYTHONPATH`. A 20-frame headless `/dev/video0`
smoke test completed without error; empty-frame SGBM latency median ~0 ms.
No real-person post-fix reauthorization test has yet been performed. The
re-authorization similarity cutoff 0.90 is provisional and must be judged
against real operator/bystander samples before claiming safety or robustness.

## 2026-09-24 bounded REACQUIRE_CONFIRMING fix

The second recorded clip's log slice at frames 398–405 is now a deterministic
regression fixture. At frame 398 only bystander track 4 remained, fused score
0.650; operator track 3 returned at frame 403 with fused score 0.949 and
margin 0.239. Frames 404/405 retained scores 0.940/0.941 and passing hard
gates. At frame 405 the old log had `GRACE_TIMEOUT`, although only 3 of the
required 5 confirmation frames had occurred. The new FSM remains
`REACQUIRE_CONFIRMING`, Authorized=NO, with the original Session UUID.
Frames 406/407 in the old log lack candidate scores because the old FSM had
already entered LOST; a **separately labelled hypothetical continuation**
shows that two further valid frames would return `LOCKED_HIGH`. This is not
claimed as a full raw-video replay: these recordings did not save raw SBS PNGs.

NUC synthetic + log-driven suite: **68/68 PASS** with Stage 4 on `PYTHONPATH`.
Checks include confirmation entry, old-grace isolation, bounded success and
failure, no authorization/Gallery update during confirmation, bystander/ID
conflict, unavailable/impossible depth, changed track ID and JSON diagnostics.
A post-change `/dev/video0` 20-frame empty-scene smoke completed without error
and all new JSONL fields were present; this does not validate the real-person
post-fix crossing or FPS. The first clip's matched T-Poses were all below the
unchanged 0.90 lost-session ReID threshold and remain rejected. Stage 5 stays
**IMPLEMENTED V2.1 / REAL-PERSON VALIDATION PARTIAL / GATE NOT PASSED**;
Stage 6 remains NOT STARTED.

## 2026-09-24 V2.2 experimental full-loss auto reauthorization

NUC code checks: `compileall` PASS; V1+V2 full suite **97/97 PASS** (68
existing + 29 new). Tests cover default-off compatibility, first-frame
denial, RED/no-authority confirmation, Gallery read-only behavior, frames and
duration, new Session UUID, no old Gesture on the success frame, weak ReID,
fused-score/weight, hard/crop gates, two matching candidates, margin,
disappearance/timeout, changed/reused MOT ID, single accidental Gallery
match, nonfinite evidence, manual T-Pose fallback, nonmonotonic/stale data,
JSON serialization and confirmation UI rendering. These are synthetic/logical tests, not person-ID
accuracy measurements.

`/dev/video0` opt-in empty-scene smoke: 20/20 frames, no exception, no
authorization or Gallery write, all required new JSONL fields present.
Final-code output: `runs_v2/20260924_104710_UTC/`; startup-inclusive 3.13 FPS is
**not** a person-present throughput benchmark.

Latest real-person V2.1 log used for **log-driven analysis only**:
`runs_v2/20260924_084846_UTC/ownership_frames.jsonl`. The LOST period has
134 candidate checks; 134/134 T-Pose checks failed. Consecutive runs below
require the same track ID and <=500 ms frame gap; no raw SBS was saved for
new OSNet inference or top-k Gallery replay.

| Old Gallery max ReID >= | Frames | Longest continuous run | Elapsed |
|---:|---:|---:|---:|
| 0.90 | 97 | 34 | 5060 ms |
| 0.92 | 69 | 27 | 4072 ms |
| 0.94 | 54 | 17 | 2472 ms |
| 0.95 | 41 | 13 | 1864 ms |
| 0.96 | 22 | 12 | 1715 ms |

At >=0.94 **plus crop >=0.75 and valid pose**, the longest old-log run is
15 frames / 2177 ms. Thus an 8-frame/700-ms *ReID/crop/pose-only* window
existed. The log does not contain the new top-3 Gallery statistic, fresh
long-term fused score, or all V2.2 competition/continuity checks. Whether
the full auto path would have succeeded is **not determinable** from this
recording. It is not a raw-frame replay and cannot establish bystander safety.
Reproduce the limited audit with `python analyze_auto_reauth_log.py <JSONL>`.

Next minimum真人 Gate: A first T-Pose/GREEN, A fully leaves to LOST, B alone
enters and performs T-Pose/wave/walk without ever inheriting A, B leaves,
A returns and may confirm into a *new* GREEN Session without T-Pose, then A+B
together must fail closed when ambiguous. Repeat with similar dark clothes;
check AuthorizedGesture remains NO during confirmation and no old Gesture
resumes. Until this is observed, Stage 5 remains **GATE NOT PASSED** and
Stage 6 **NOT STARTED**. No OSNet, BoT-SORT, Stage 3/4, Evidence weights or
original 0.90 manual-reauthorization cutoff were changed.
