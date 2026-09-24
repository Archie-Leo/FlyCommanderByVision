# RK3576 migration report — 2026-09-25

## 1. Hardware and 2. OS / architecture

- Board: Taishan Pi 3M, Rockchip RK3576, aarch64, 8 CPU cores.
- Observed OS: Ubuntu 24.04.5 LTS; Linux 6.1.99 Rockchip kernel.
- Repository: `/home/lckfb/FlyCommanderByVision`, branch `rk3576-migration` from NUC baseline `62ebd2b`. `main` was not modified.

## 3. ROS 2 / PX4 middleware

- ROS 2 Jazzy: `/opt/ros/jazzy`.
- `px4_msgs`: `~/fcv_ros_ws/src/px4_msgs`, branch `release/1.17`; `VehicleStatus` interface resolves.
- Micro XRCE DDS Agent: `/usr/local/bin/MicroXRCEAgent` present. It was not started for live control.
- Gateway symlink: `~/fcv_ros_ws/src/drone_control_gateway` points to this repository.

## 4. Python environment separation

- ROS builds use `/usr/bin/python3` 3.12.3 with no active venv. The Gateway was rebuilt this way.
- Vision uses `~/venvs/fcv/bin/python3` 3.12.3. On this session the actual imports were NumPy **2.5.3**, OpenCV **5.0.0**, and RKNN Lite2 2.3.2. This differs from the previously reported NumPy 1.26.4 / OpenCV 4.10.0. `pip list` also shows `opencv-python` 4.10.0.84 alongside `opencv-contrib-python` 5.0.0.93. No package was reinstalled during this session.
- `source scripts/env_rk3576.sh` sets the ROS overlay, Stage3–6 `PYTHONPATH`, RK3576 camera backend, and repository Run B calibration path. Stage5/6 runner defaults use repository paths under this environment while the NUC defaults remain available without it. Deactivate the vision venv before `colcon build`; verify `command -v python3` is `/usr/bin/python3`.
- `requirements-rk3576.txt` records imported vision versions. A clean installation from it has not yet been verified.

## 5. Camera implementation and 6. benchmark

- USB stereo camera `/dev/video73`, MJPEG 2560×960 at requested 60 FPS. Layout is **LEFT|RIGHT**, each eye 1280×960.
- `stage3_pose/camera/ffmpeg_source.py` reads FFmpeg/V4L2 rawvideo in a background thread and keeps one latest frame. It timestamps host receipt with `time.monotonic_ns()`, counts skipped frame IDs, and terminates FFmpeg on close/error. Device, input FPS/dimensions, eye crop, output dimensions, and BGR/RGB output are configurable.
- The shared camera factory selects FFmpeg only when `FCV_CAMERA_BACKEND=ffmpeg`; NUC default remains OpenCV. The RK3576 environment script selects FFmpeg and `/dev/video73`. Full stereo BGR 2560×960 is the live adapter default so Stage5/6 depth and the 1280×960 calibration geometry remain valid. The 640×480 crop is **benchmark only**, not fed into the existing rectifier.
- The first benchmark counted approximately 1 second of FFmpeg startup and a 2 second shutdown wait as capture time. Closing the pipe before waiting for FFmpeg reduced shutdown to 0.17–0.18 seconds and left no capture thread running. Final 600-frame results: left-eye 640×480 BGR **56.15 FPS steady**, **50.80 FPS end-to-end**, one old frame dropped, zero failures; full stereo 2560×960 BGR **54.33 FPS steady**, **49.16 FPS end-to-end**, zero drops/failures. The earlier supplied left-eye result was 55.53 FPS. A slow-consumer check received 30 frames with IDs 0–126 and discarded 97 old frames. Direct FFmpeg-to-`dd` with left crop/scale took 11.427 seconds for 600 requested frames (about 52.5 FPS end-to-end). Steady capture is close to the supplied reference; downstream Pose/Tracking throughput remains unmeasured.
- A further 600-frame full-stereo run with the actual Run B left-eye rectifier received all frames without drops/failures: **57.85 FPS steady**, **52.20 FPS end-to-end**, mean rectification time **6.567 ms**. Pose/Tracking throughput remains unmeasured.
- `CameraFrame.timestamp_ns` is a host receipt timestamp, not a sensor exposure timestamp. Existing calibration `configs/calibration/run_b.yaml` was not changed (SHA256 `9730eb49d136d426b84846319c7c3fecaf73dbd74be3f38ac4c376b40843e174`).

## 7. NPU status

- `/usr/lib/librknnrt.so` and RKNN Lite2 2.3.2 are present. A single zero-input inference of `/usr/share/model/RK3576/mobilenet_v1.rknn` passed in the current venv and returned shape `(1, 1001)`; the model is unrelated to the required Pose backend. The user supplied a prior real MobileNet benchmark (average 3.145 ms). No performance benchmark was repeated in this session.

## 8. Stage3

- `PoseBackend` abstraction and MediaPipe-to-`PoseFrame` mapping already existed. Normalization and `PoseFrame` schemas were retained.
- Added an ARM LSE CPU feature guard before MediaPipe wheel load. On CPUs without `atomics`, it raises a Python error rather than entering the known SIGILL path. MediaPipe remains available to the NUC.
- `stage3_pose/models/pose_landmarker_full.task` is absent on this board. There is no validated RKNN Pose model/adapter yet. Stage3 logic tests pass; live pose inference is **blocked**. The Stage4 geometry rules require six shoulder/elbow/wrist joints, while Stage3 quality/normalization also use hips and confidence. A 17-joint model cannot be substituted without mapping and behavior validation.

## 9. Stage4

- Geometry, temporal FSM, thresholds, and Unknown Reject code were unchanged. Tests: **14 passed, 5 subtests passed** on RK3576. Live gesture output depends on the Stage3 pose blocker.

## 10. Stage5

- V1 logic: **19 passed**; dependency-light fallback remains present.
- V2 logic: **78 passed** without real checkpoint/native library. Ownership, Session, Gallery, ReID thresholds, and safety gates were unchanged.
- The official OSNet checkpoint, native BoT-SORT `.so`, and deep-person-reid source are absent. V2 live ReID/tracking is **blocked**. No lookalike weights, HSV substitute, source-built PyTorch, or unverified BoxMOT wheel was introduced.

## 11. Stage6

- ROS/system Python safety and intent tests: **44 passed**. Gate6C evidence tests with the vision OpenCV plus ROS Python path: **21 passed**. All 65 Stage6 tests passed with mock/dry-run data. The existing ROS executor has a 50 ms publish timer on a separate thread, so lease expiry does not wait for the camera/Pose loop. Intent, Flight Authority, Safety, and Command Lease semantics were unchanged. No live control pipeline was started.

## 12. Gateway

- Cleaned only `build/drone_control_gateway` and `install/drone_control_gateway`, then rebuilt with Jazzy and `/usr/bin/python3`: **PASS**.
- `ros2 pkg prefix` resolves; `ros2 pkg executables` lists `control_gateway_node`. `colcon test`: **6 tests, 0 failures**. Gateway executable was not run against PX4.

## 13. NUC → RK3576 differences

| Area | NUC baseline | RK3576 session |
| --- | --- | --- |
| CPU / OS | x86_64, Ubuntu 22.04 | aarch64, Ubuntu 24.04.5 |
| ROS | Humble | Jazzy |
| Camera | OpenCV V4L2 `/dev/video0` | FFmpeg V4L2 `/dev/video73` selected by environment |
| Pose | MediaPipe 33 landmarks | Existing wheel requires unavailable LSE; backend blocked |
| Stage5 V2 | Native BoxMOT + OSNet files | Logic tests pass; deployment assets missing |
| Vision Python | NUC frozen NumPy 1.26.4 / OpenCV 4.10 | Current board imports NumPy 2.5.3 / OpenCV 5.0.0 |

## 14. Changes made

- Added RK3576 environment and health scripts, repository-relative runner defaults, and an observed dependency manifest.
- Added a configurable FFmpeg latest-frame camera source and shared factory, preserving the NUC default and stereo API.
- Added a camera benchmark command and ARM MediaPipe fail-closed guard.
- Added RK3576-specific compatibility checks and a Stage6 evidence test runner.
- Added this report, the test report, and concrete blocker records.

## 15. Outstanding and 16. next steps

1. Select and validate a Pose backend that maps geometry, left/right labels, and confidence into existing `PoseFrame`/quality behavior. Do not enable live Stage6 before this and on-device safety validation.
2. Restore the exact official OSNet checkpoint and Stage5 V2 native dependencies; verify hashes/ABI before visual testing.
3. Once a Pose backend exists, measure rectification, inference, tracking, and end-to-end frame age separately; retain original 1280×960 per-eye calibration geometry.
4. Reconcile the vision venv's conflicting OpenCV distributions in an isolated clean environment, then repeat the short RKNN self-check and software tests in that new environment before switching it into use.
5. Keep all aircraft/PX4 control tests for an attended safety session. This session published **no live PX4 control**.
