# RK3576 Stage 9 runtime status

Status on 2026-09-25: **partial dry-run baseline; live PX4 control not verified**. A board reboot later in the task left `~/fcv_ros_ws/install/setup.bash` absent, so the ROS workspace must be restored before another Stage6 run.

## Observed platform

- Taishan Pi 3M RK3576, aarch64, Linux 6.1.99, Ubuntu 24.04.5 LTS, Python 3.12.3, ROS 2 Jazzy.
- UVC camera `0bda:5883`, `/dev/video73`, MJPG side-by-side 2560×960 at 60 FPS; Run B calibration `configs/calibration/run_b.yaml`.
- RKNN YOLOv8n Pose model from `~/fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/yolov8n-pose-rk3576-int8.rknn`; RKNN OSNet `models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn`, validated SHA in the existing ReID backend.
- `MicroXRCEAgent` at `/usr/local/bin/MicroXRCEAgent`. PX4 USB appeared as `/dev/ttyACM0`, but the actual XRCE link is the CH340 adapter `/dev/ttyUSB0`, stable alias `usb-1a86_USB_Serial-if00-port0`, at the user-provided 57600 baud. Agent logs confirmed `create_client`, `session established`, and `participant created` on CH340. No `/fmu/*` ROS topics were visible afterward; PX4-side client status and topic publication still need confirmation. The user confirmed disconnected motors and an onsite safety pilot with RC mode control.
- CH340 serial belongs to group `dialout`; user `lckfb` is not in that group. The Agent probe required `sudo -n`. No persistent permission changes were made.

## Start the isolated RK3576 dry-run

```bash
cd ~/FlyCommanderByVision
scripts/start_vision_dry_run.sh --max-frames 120 --output outputs/stage9_stage6_dry_run
```

The script sources ROS Jazzy and the board environment, validates the camera and calibration, and fixes the output topic to `/interaction/intent_dry_run`. It does not start Gateway, arm, take off, land, or publish live movement. Optional `--record` and `--rosbag` can be passed when evidence capture is required. The old `start_stage6_*` scripts still target NUC/ROS Humble and are not RK3576 commands.

The confirmed CH340 Agent command is `scripts/start_xrce_agent.sh`. It opens the stable device alias at 57600 baud and uses `sudo -n` when serial permissions require it. A created XRCE session alone is insufficient evidence that PX4 topics are present.

A 120-frame board dry-run completed at 16.07 FPS, with 175 observed isolated Intent messages; all were HOVER because there was no detected person. Mean frame processing was 69.82 ms, Stage6 submit 0.08 ms, tracker 9.82 ms, ReID 0 ms, and stereo depth 0.015 ms in this empty-scene sample. A later upright person-visible run reached only 1.88 FPS because full-frame stereo depth takes about 1.1 seconds when a tracked person exists; see `RK3576_PERFORMANCE.md`. T-Pose authorization, gesture movement, flight authority transitions, Gateway, and PX4 setpoints remain unverified. An earlier attempt ended fail-closed at frame zero when the USB camera stopped.

## Current blockers for real-hardware test

1. Inspect PX4-side `uxrce_dds_client status` and make `/fmu/out/vehicle_status_v1` and `/fmu/out/vehicle_local_position_v1` observable on ROS. Confirm the Gateway's sole `/fmu/in/trajectory_setpoint` publisher before any live output.
2. Stabilize camera USB connection. Kernel logs repeatedly show `usb 2-1: USB disconnect` and re-enumeration; FFmpeg then stops. The user is checking cable/port/power.
3. Obtain logged single-operator T-Pose and gesture evidence in the dry-run. The current 120-frame run saw no person and cannot validate the authorization chain.
4. Restore the ROS Jazzy workspace install artifacts after the board reboot and resolve the full-frame stereo depth cost before live control. A fast torso-ROI variant was rejected after producing an 8 cm depth difference on one diagnostic frame.

Phase 4 hardware H.264 RTP/UDP video and integrated control/video have not started because the Phase 3 real PX4 chain is not yet stable.
