# Repository Sync Report

Date: 2026-09-24. This report covers the independent NUC integration copy; it does not replace the original Stage directories. Final GitHub verification is recorded below after push.

## Local source inventory and consolidated path

See `PROJECT_INVENTORY.md` for source-by-source ownership, paths, sizes and Stage status. Integration repository: `/home/sentinel/FlyCommanderByVision`; NUC original workspaces remain unchanged. Windows workspace `D:\FlyCommanderByVision` was accessible, and its latest project status, checklist and research docs were explicitly copied into the integration repository. Stage 3/4 README status notes were corrected in the integration copy; algorithms were untouched.

## Files copied and intentionally excluded

Copied: Stage 1 Control Gateway source/message/launch/config/tests; Stage 2 stereo calibration and depth validation source/tests/research; Stage 3 Pose/Quality/Normalization source/tests/docs; Stage 4 Gesture source/tests/protocol/evaluation/final reports; Stage 5 visual ownership source/tests/design/validation; project roadmap/status/checklist and third-party references. `configs/calibration/run_b.yaml` is byte-for-byte identical to the original Run B YAML (SHA-256 `9730eb49d136d426b84846319c7c3fecaf73dbd74be3f38ac4c376b40843e174`).

Excluded: all raw camera captures, PNG/video, Stage 4 dataset JSONL sequences, Stage 3 `.task` model, ROS bags, ULogs, runtime output, build/install/log/cache, virtualenvs, and entire third-party repositories. The largest staged file was approximately 119 KB; no prohibited data or model file was staged. All original data remain on the NUC; see `DATASETS.md`. Upstream versions, URLs and licenses are in `third_party/README.md`, not vendored.

## Tests on the copied repository

| Component | Result |
|---|---|
| Stage 1 Control Gateway | independent `colcon build` PASS; IntentMapper 5/5 PASS |
| Stage 2 calibration | 11/11 PASS |
| Stage 2 depth validation | 3/3 PASS |
| Stage 3 Pose | 12/12 PASS |
| Stage 4 Gesture | 14/14 PASS |
| Stage 5 visual ownership | 19/19 synthetic tests PASS; real two-person Gate **not run** |

All tests used the existing NUC environments. No algorithm, dependency or operating-system upgrade was made. A first Stage 1 `colcon` invocation had `--log-base` in the wrong CLI position; it was rerun correctly and passed.

## Git, GitHub and privacy

Windows `gh` authentication was invalid; NUC `gh auth status` confirmed active account `Archie-Leo` with `repo` scope. Before push, the staged filenames and diff were reviewed, large/prohibited files checked, and case-insensitive TOKEN/SECRET/PASSWORD/PRIVATE KEY and higher-risk credential patterns scanned. The only generic TOKEN occurrence described an authorization token in prose; an exact numeric `123456` match came from an evaluation fraction, not a password. Existing source markdown has trailing-whitespace warnings in `git diff --check`; no source behavior was changed to normalize historical files.

Initial consolidation commit: `e717dd4ffce807625bea3e994f099e16936191d4` (119 files). GitHub repository: `Archie-Leo/FlyCommanderByVision`, `https://github.com/Archie-Leo/FlyCommanderByVision`. Owner and visibility were queried both before and after the first push: **PRIVATE**. The `main` branch push succeeded; `git ls-remote` matched the local initial HEAD. GitHub API confirmed `README.md` and `PROJECT_STATUS.md` exist, the remote Stage 5 status says real multi-person Gate pending, and the remote tree contains no raw JSONL dataset, model `.task`, image/video, `.env` or `.ssh` path.

This report is committed as a follow-up documentation update. After that push, verify final HEAD equality, PRIVATE visibility and a clean working tree once more; do not infer those final checks from the initial push alone.

## Current Stage status and limitations

0 PASS; 1 PASS/frozen; 2 PASS, Run B development default; 3 PASS/V1 FROZEN; 4 PASS/Gesture V1 FROZEN; 5 IMPLEMENTED/MANUAL VALIDATION PENDING; 6–9 NOT STARTED. Stage 5's tracker is Kalman/two-stage IoU, not BoT-SORT/official ByteTrack; Gallery descriptor is weak HSV clothing colour, not OSNet. Similar clothing, occlusion and crossing require a real two-person Gate before any flight integration.
