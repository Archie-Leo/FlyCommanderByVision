# Drone Stage 3 Pose

> Current status (2026-09-24 documentation sync): **PASS / V1 FROZEN**. The historical
> manual-validation language at the end of this README describes the earlier development
> state; the completed evidence and limits are in `STAGE3_VALIDATION_REPORT.md`.

Formal Stage 3 V1 tool:

```text
/dev/video0 SBS → Stage 2 Run B LEFT rectification → MediaPipe Pose Landmarker Full
→ PersonPose V1 → PoseQuality V1 → NormalizedSkeleton V1
```

It does not classify gestures, track identity, select an operator, generate intents, control PX4,
or reconstruct stereo 3D keypoints.

## Install on the NUC

```bash
python3 -m venv ~/venvs/drone_stage3
source ~/venvs/drone_stage3/bin/activate
python -m pip install -r ~/drone_stage3_pose/requirements-lock.txt
mkdir -p ~/drone_stage3_pose/models
curl -fL https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task \
  -o ~/drone_stage3_pose/models/pose_landmarker_full.task
```

Stage 2's `~/venvs/drone_stage2` is not modified.
`requirements.txt` freezes direct dependencies; `requirements-lock.txt` records the complete set
actually validated on sentinel-S600.

## Run

```bash
cd ~/drone_stage3_pose
source ~/venvs/drone_stage3/bin/activate
env -u PYTHONPATH PYTHONNOUSERSITE=1 python main.py
```

Keys: `SPACE` starts a non-blocking 3→2→1 countdown and then saves rectified input, overlay and
canonical JSON; `S` saves immediately; `L` toggles joint names; `Q`/ESC exits.
`--display-mirror` mirrors only the display copy and never inference/canonical geometry.

Headless bounded smoke/performance run:

```bash
env -u PYTHONPATH PYTHONNOUSERSITE=1 python main.py --no-display --max-frames 100
```

Compare snapshots of the same pose at different positions/distances:

```bash
env -u PYTHONPATH PYTHONNOUSERSITE=1 python normalization_debug.py \
  outputs/snapshots/reference.json outputs/snapshots/left.json outputs/snapshots/far.json
```

The comparison reports joint RMSE, bone-vector difference and angle difference without inventing
an official PASS threshold.

## Tests

```bash
env -u PYTHONPATH PYTHONNOUSERSITE=1 python -m compileall .
env -u PYTHONPATH PYTHONNOUSERSITE=1 python -m unittest discover -s tests -v
```

## Manual validation

Test front standing, arms down, left/right/both arm raised, image-left/image-right movement,
near/far positions, partial arm occlusion, partial body truncation and no-person. Confirm anatomical
left/right on the non-mirrored inference overlay. This was the earlier test plan; the
current Gate result is `PASS / V1 FROZEN` as documented above.
