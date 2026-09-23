# Dataset and calibration provenance

Large raw data remain on the NUC; the integration Git repository intentionally excludes captures, image/video recordings, Stage 4 JSONL sequences, model binaries, ROS bags, ULogs and runtime outputs. No source data were deleted or moved.

| Original location | Session/use | In Git? |
|---|---|---|
| `~/drone_stage2/stereo_calibration/captures/` | Raw same-frame stereo chessboard pairs | No |
| `~/drone_stage2/stereo_calibration/stereo_calibration_output/20260914_092939_UTC__run_B_exclude_0004_0027/` | Run B, pair 0004/0027 explicitly excluded | Only `calibration.yaml` copied to `configs/calibration/run_b.yaml` |
| `~/drone_stage3_pose/outputs/`, snapshots and models | Pose runtime/validation evidence | No |
| `~/drone_stage4_gesture/datasets/20260916_101001_UTC/` | Pilot/development, 22 sequences | No |
| `~/drone_stage4_gesture/datasets/20260923_051541_UTC/` | Final held-out 46 valid sequences; raw diagnostic 48 including `excluded/` 0039/0040 | No; small derived reports and exclusion manifest only |
| `~/drone_stage5_operator/runs/` | Visual ownership frame logs and summaries | No |

Run B is a development default, not a forced 65 mm baseline. The calibration YAML's source session, dimensions and parameters are retained. SHA-256 for the model is recorded in `models/README.md`; any other hashes are only asserted where already present in source reports. Restoring the repository alone does not restore the raw datasets.
