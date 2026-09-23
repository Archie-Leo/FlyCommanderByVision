# Stage 4 Gesture Baseline

Current status (2026-09-24 documentation sync): **PASS / Gesture V1 FROZEN**. The
final valid held-out cohort is 46 sequences; see `STAGE4_FINAL_VALIDATION_REPORT.md`.
The old hard-negative-only result below is retained as development history, not the Gate.

This project consumes frozen `NormalizedSkeletonV1` and produces `GestureCandidateV1`. It contains
no tracking, ReID, operator ownership, ROS 2/PX4 intent publishing, or flight control.

## Protocol

The frozen definitions and engineering tolerances are in `GESTURE_V1_PROTOCOL.md`:

- LEFT: anatomical left arm straight and horizontal; opposite arm down.
- RIGHT: anatomical right arm straight and horizontal; opposite arm down.
- ASCEND: both arms straight overhead/Y-shaped.
- DESCEND: both arms straight diagonally down/outward.
- HOVER: deliberate double goalpost pose.
- Natural standing and reserved T-Pose: UNKNOWN.

## Structure

```text
gesture/types.py          GestureCandidateV1 and labels
gesture/config.py         Configurable V1 engineering tolerances
gesture/geometry.py       Geometry rules, completeness and exclusivity reject
gesture/temporal.py       Short-horizon confirmation/release stabilizer
gesture/evaluation.py     Confusion matrix, precision/recall and false triggers
live_gesture.py           Real Camera/Stage 3 adapter and labeled collector
classify_snapshot.py      Offline Stage 3 snapshot replay
evaluate_dataset.py       Snapshot manifest evaluation
evaluate_sequences.py     Temporal JSONL sequence evaluation
```

## Environment and tests

Use the existing isolated Stage 3 environment; no new dependency is required:

```bash
cd ~/drone_stage4_gesture
source ~/venvs/drone_stage3/bin/activate
unset PYTHONPATH
export PYTHONNOUSERSITE=1
python -m compileall .
python -m unittest discover -s tests -v
```

## Live use and collection

```bash
cd ~/drone_stage4_gesture
source ~/venvs/drone_stage3/bin/activate
unset PYTHONPATH
export PYTHONNOUSERSITE=1
python live_gesture.py
```

Select the ground-truth label with `0`–`5`, press `R` to start/stop a temporal sequence, or use
Space for a three-second diagnostic snapshot. Data is stored under a new UTC session in
`~/drone_stage4_gesture/datasets/`. See `STAGE4_DATASET_PLAN.md` before formal collection.

## Existing hard-negative replay

The 19 retained Stage 3 samples provide one real ASCEND and 18 negative/invalid observations.
They currently produce one correct ASCEND, 12 correct UNKNOWN, 6 correct INVALID, and zero false
control triggers. This is a smoke/hard-negative result only: LEFT, RIGHT, DESCEND and HOVER have
zero positive support *at that earlier stage*. This result alone did not establish PASS;
the later independent held-out evaluation did.

```bash
python evaluate_dataset.py reports/stage3_hard_negative_manifest.jsonl \
  --output reports/stage3_hard_negative_evaluation.json
```

## Current boundary

The Stage 4 software baseline, protocol, interface, reject paths, temporal stabilizer, collector and
metric tooling are frozen. The balanced real dataset has been reviewed: 36/36 valid legal
sequences confirmed, 10/10 UNKNOWN sequences rejected with zero stable legal false trigger
over 3385 UNKNOWN frames. The operator-confirmed recording errors 0039/0040 remain in
`excluded/`; the pre-exclusion 48-sequence diagnostic remains visible in the final report.
No new Stage 4 collection or threshold tuning is planned.

## Correcting a mislabeled sequence

Do not delete or hand-edit JSONL files. Preview first, then apply an auditable relabel:

```bash
python relabel_sequences.py --label UNKNOWN datasets/SESSION/sequence_0016_HOVER.jsonl
python relabel_sequences.py --apply --label UNKNOWN datasets/SESSION/sequence_0016_HOVER.jsonl
```

The tool updates every frame label, renames the sequence consistently, preserves the original under
`relabel_backup/`, and appends hashes and paths to `relabel_audit.jsonl`.
