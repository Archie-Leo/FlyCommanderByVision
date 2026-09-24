# Tracking / ReID upgrade record

2026-09-24: V1's Kalman+two-pass IoU and 65%-weighted HSV clothing histogram
are insufficient. In the first 75 s of the real run, `LOCKED_HIGH` appeared
520/1553 frames, `LOST` 751/1553 and `REACQUIRING` 113/1553. This supports
the upgrade need; it does **not** measure V2 improvement.

- MOT: BoxMOT v25.0.0 commit `8576283` native BoT-SORT C++ typed ABI v2,
  AGPL-3.0. Built out of tree on NUC with system OpenCV 4.5.4; ABI smoke
  update with one detection and an empty frame passed. The reference repo
  remains unchanged. CMC is `sof`; thresholds initially mirror its frozen
  `boxmot/configs/trackers/botsort.yaml`, not tuned to this camera.
- ReID: frozen `deep-person-reid` OSNet x0.25, 512-D normalized embeddings.
  Isolated `~/venvs/drone_stage5_v2` has PyTorch 2.5.1+cpu; architecture
  forward output `(1,512)` was verified. The user-provided MSMT17 checkpoint
  is now on NUC at `~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth`
  and loads with `weights_only=True`; SHA256 `cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18`.
  Stage 3 venv remains unchanged. No random checkpoint or HSV fallback is used.
- Gallery: bounded 16 embeddings, committed only after unique T-Pose seed;
  subsequent updates require high-confidence GREEN and anti-contamination
  conditions. Model is not yet calibrated on this operator/camera.
- Stereo: Stage 2 Run B reused at 1280×960/eye. Median torso Z/MAD and
  quality are logged; unavailable depth is not a hard failure. A physically
  impossible innovation is a hard gate when two valid measurements exist.
- Stage 3/4 source, V1 tracking/ownership algorithms and operator-lock reference repos: unchanged.
  V1 runner gained recording diagnostics only.

V2 now starts with the supplied checkpoint. The next performance profile
must include real people so OSNet/MOT latency and identity continuity can be
measured before any threshold tuning or Gate claim.
