# Stereo camera and calibration baseline

Verified camera: Shenzhen Dechuangxin stereo RGB USB UVC, VID:PID
`0bda:5883`, `uvcvideo`, USB 3.0 SuperSpeed. Capture is `/dev/video0`,
MJPG 2560×960 Side-by-Side, declared 60 FPS and observed about 58.7 FPS.
`/dev/video1` is **not** the right camera. Each eye is original 1280×960:
LEFT `frame[:,0:1280]`, RIGHT `frame[:,1280:2560]`. Physical left-lens
occlusion darkened the left half. Never assume fixed `dt=1/60`.

Global shutter, fixed focus, approximate 102° FOV and nominal 65-mm
baseline. The calibration board has 11×8 **inner corners** at 45.0 mm
square spacing. The current development calibration is Run B, explicitly
excluding pairs 0004/0027 but preserving original captures:
`configs/calibration/run_b.yaml`. This checked-in YAML is byte-identical
to the NUC source
`~/drone_stage2/stereo_calibration/stereo_calibration_output/20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml`
(SHA-256 `9730eb49d136d426b84846319c7c3fecaf73dbd74be3f38ac4c376b40843e174`).
It is the **DEVELOPMENT/PROVISIONAL** pinhole model, not a claim of
production depth accuracy. See `stage2_stereo/calibration/` and
`STAGE2_STEREO_CAMERA_FINAL.md` for collection and validation details.

Source images, rosbag, ULog, rectified previews and datasets stay outside
Git. A new physical camera or changed focus/resolution requires a new
calibration; do not silently reuse Run B.
