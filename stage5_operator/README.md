# Stage 5 visual-only operator baseline

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
