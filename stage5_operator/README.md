# Stage 5 visual-only operator baseline

Current (2026-09-24): **V2.2 single-operator Demo baseline PASS** from four
real-person full-loss/new-Session returns. Full multi-person safety validation
is deferred to Stage 7; Stage 5 remains visual-only. Stage 6 integration is
separate under `~/drone_stage6_closed_loop` on the NUC.

V1 below is retained for reproducibility, **not** the accepted Stage 5 Gate.
V2 is in `live_operator_v2.py` and `stage5_v2/`; see `STAGE5_DESIGN_V2.md`,
`STAGE5_TRACKING_REID_UPGRADE.md`, `STAGE5_VALIDATION_REPORT_V2.md`. V2 now
loads the user-provided OSNet checkpoint and completes empty-scene camera runs;
real-person ownership and gesture authorization still require manual testing.
The separate NUC runtime is `~/venvs/drone_stage5_v2` with pinned Stage 3
NumPy/OpenCV and PyTorch CPU 2.5.1; the validated Stage 3 venv was untouched.
V2 never falls back silently to V1/HSV.

## Diagnostic recording (V1 and V2)

On the NUC, run the existing visual program normally and press **R** to start
or stop each clip. The top bar displays `REC ON/OFF`. `Q` closes an active clip
safely. For headless or immediate recording, add `--record`; use
`--record-raw-stereo` only when you need pixels for depth/replay analysis:

```bash
cd ~/drone_stage5_operator
source ~/venvs/drone_stage3/bin/activate
python live_operator.py --record --record-raw-stereo
```

The same flags and `R` key are supported by `live_operator_v2.py`. Each run retains its full
`ownership_frames.jsonl` and `summary.json`. Each recorded segment is saved in
`runs/<UTC>/recordings/clip_001/` (V2: `runs_v2/<UTC>/recordings/clip_001/`):

- `annotated.avi`: MJPG preview with boxes, ownership color/state, gesture and
  authorization. It is *lossy* and uses a fixed nominal playback FPS (default
  10); do not use its playback time as the true frame clock.
- `frames.jsonl`: one complete diagnostic record per video frame, including
  frame ID, capture monotonic timestamp, approximate capture UTC, candidate
  evidence/quality, rejection reason and stage latency. Use this for real time.
- `manifest.json`: source runner, calibration/model identity, dimensions,
  codec, frame count and stop reason.
- `raw_sbs_png/frame_*.png`: optional, decoded 2560×960 LEFT|RIGHT from the
  same camera frame, lossless relative to the decoded UVC image. This does not
  recover the USB MJPEG bitstream. It can consume multiple MB **per frame**;
  the NUC smoke test produced about 5 MB for only two raw frames.

Recording is off by default. Existing runs/captures are never overwritten or
deleted. Human video is sensitive; keep it on the NUC and share only clips
needed for analysis. `--record-playback-fps` changes AVI playback rate, not
the timestamps in JSONL.

For V2 with the verified local checkpoint:

```bash
cd ~/drone_stage5_operator
source ~/venvs/drone_stage5_v2/bin/activate
python live_operator_v2.py --osnet-checkpoint ~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth
```

Experimental V2.2 full-loss auto reauthorization is **OFF by default**.
To test it visually (no ROS2/PX4 output), add `--auto-reauthorize`:

```bash
cd ~/drone_stage5_operator
source ~/venvs/drone_stage5_v2/bin/activate
PYTHONNOUSERSITE=1 PYTHONPATH=.:~/drone_stage4_gesture python live_operator_v2.py \
  --osnet-checkpoint ~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth \
  --auto-reauthorize
```

After full loss, an old-Gallery match first enters RED
`AUTO_REAUTHORIZE_CONFIRMING` with Authorized=NO. Only sustained unique,
multi-evidence confirmation creates a **new** Session. Original T-Pose
reauthorization remains available. `V` shows Gallery Top-K, margin, frames,
elapsed time and reject reason. This path has **not passed真人 A/B Gate**;
do not connect it to flight control. Without the flag, V2.1 behavior remains.

Only visual logging/UI is produced. `Q` quits, `X` releases the session and
`V` toggles debug display. Logs go to `runs_v2/<UTC>/`. No Stage 6 link exists.

With auto reauthorization disabled, after a long absence `OPERATOR_LOST`
does not recover merely because a track reappears. Stand fully inside the
frame and make a fresh T-Pose; the system also checks against the previous
OSNet gallery and creates a new Session only if identity is unique and strong.
If you intend to
authorize a different person, use `X` to explicitly release the old identity
first. The JSONL field `acquisition_checks` and `V` overlay expose T-Pose,
pose, crop and old-gallery checks; `UNKNOWN` remains unauthorized. Near-field
partial-body views may still lose the lock. Real-person post-fix Gate testing
is pending.

Run on the NUC, using the already installed Stage 3 venv:

```bash
cd ~/drone_stage5_operator
source ~/venvs/drone_stage3/bin/activate
PYTHONNOUSERSITE=1 python live_operator.py
```

`Q`/Esc quits; `X` explicitly releases the operator session. The display shows ownership, current gesture, authorization and reject reasons. Logs are written under `runs/<UTC timestamp>/ownership_frames.jsonl` with a `summary.json`; they contain no image data. Use `--no-display --max-frames 12` for a headless smoke test. Use `--camera /dev/video999 --no-display` to test a missing-camera fail-closed path. Do not connect its output to flight control.

Run tests with:

```bash
PYTHONNOUSERSITE=1 PYTHONPATH=.:~/drone_stage4_gesture python -m unittest discover -s tests -v
```

See `STAGE5_DESIGN_V1.md` and `STAGE5_VALIDATION_REPORT.md` for the actual implementation boundary and unverified Gate items. The `runs/` directory is runtime output, not code or a dataset backup. Existing NUC Stage 3/4 projects and operator-lock reference repositories remain untouched.
