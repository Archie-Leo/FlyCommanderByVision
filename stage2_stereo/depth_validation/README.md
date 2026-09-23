# Stage 2 Stereo Depth Validation

Uses the provisional Run B calibration read-only for real-time rectification, StereoSGBM disparity,
Q reprojection, click-to-measure, snapshots, and tape-measure CSV logging.

```bash
cd ~/drone_stage2/stereo_depth_validation
source ~/venvs/drone_stage2/bin/activate
python stereo_depth_validation.py
```

- Click `Disparity / Depth` to sample a 7×7 median depth at that rectified-left pixel.
- `R`: enter ground truth in millimetres and log the current valid click.
- `L`: log current measurement without ground truth.
- `S`: save rectified LEFT/RIGHT, disparity visualization, and measurement JSON.
- `Q`/`ESC`: release camera and quit.

CSV: `outputs/depth_validation.csv`; snapshots: `outputs/snapshots/`.

The disparity color image is visualization only. Measurement uses float disparity divided by 16 and
the original Run B Q matrix. This tool does not modify calibration or captures.
