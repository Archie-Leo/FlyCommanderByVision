# Model binaries are not committed

The NUC baseline uses two exact model files listed in `manifest.json`:
MediaPipe Pose Landmarker Full and OSNet x0.25 MSMT17. Neither binary is
uploaded to Git, because the model-weight redistribution terms have not been
independently confirmed. The deep-person-reid **source** is MIT-licensed;
that alone is not a claim that its hosted training data or weights may be
repackaged. Keep the original model URLs and verify SHA-256 before use.

From an activated deployment venv, run `scripts/download_models.sh`. It
downloads to `stage3_pose/models/pose_landmarker_full.task` and
`models/reid/osnet_x0_25_msmt17.pth` and refuses a mismatched existing file.
The OSNet download requires `gdown` (install it into the venv separately if
needed). No binary is silently substituted or checked into Git.
