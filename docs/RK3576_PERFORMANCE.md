# RK3576 Stage 9 performance evidence

## Phase 1: measurement before optimization (2026-09-25)

Board: Taishan Pi 3M RK3576, Ubuntu 24.04, MJPG SBS 2560×960 requested at 60 FPS, Run B left rectification, existing YOLOv8n-Pose RKNN. These runs used the deployed RKNN backends; the earlier roadmap's proposed CPU Pose baseline cannot be reconstructed by installing the ARM-incompatible MediaPipe wheel.

The existing FFmpeg camera already has a capture thread, a single overwriteable latest-frame slot, host monotonic receipt timestamp, and drop counting. V4L2 reports a `ts-monotonic, ts-src-soe` sensor timestamp, but the current FFmpeg rawvideo pipe does **not** carry that timestamp into `CameraFrame`. Values called host receipt age below therefore omit sensor-to-FFmpeg time. A separate V4L2 probe measured that segment.

| Measurement | Mean | P95 | Max | Scope |
| --- | ---: | ---: | ---: | --- |
| Sensor exposure to V4L2 dequeue | 16.75 ms | 17.02 ms | 20.99 ms | 180 steady frames after 10 warmups; V4L2 monotonic SOE timestamp |
| Sensor input rate | 58.73 FPS | — | — | Same V4L2 run |
| Host receipt to Stage4 end, no preview | 64.39 ms | 74.28 ms | 78.86 ms | 180 frames, one operator pipeline through Stage4 |
| Host receipt to JPEG ready | 90.02 ms | 100.69 ms | 109.42 ms | Separate 180-frame run with Stage3 overlay and JPEG |
| Host receipt to board local MJPEG receive | 101.93 ms | 116.05 ms | 238.52 ms | 80 frames, same-board HTTP client |
| Host receipt to ground PC receive, direct LAN | 253.15 ms | 379.18 ms | 396.30 ms | 80 frames; monotonic clock offset estimated using minimum 58 ms HTTP RTT |
| Host receipt to ground PC receive, SSH tunnel | 253.82 ms | 427.73 ms | 471.89 ms | 80 frames; minimum clock-sample RTT 56 ms |

Stage3/Stage4 no-preview: **18.00 effective FPS**, source frame-ID rate **57.52 FPS**, 422 camera frames dropped rather than queued. Mean stage times: camera read 4.61 ms, rectify **21.09 ms**, Pose **29.71 ms**, Quality/Normalize/Gesture **0.09 ms**, total **55.50 ms**. Full data on the board: `outputs/stage9_profile/vision_without_preview.json`.

Stage3/Stage4 with overlay/JPEG in the same vision worker: **12.18 effective FPS**, source frame-ID rate **58.59 FPS**, 720 camera frames dropped rather than queued. Mean stage times: read 4.58 ms, rectify **20.79 ms**, Pose **30.36 ms**, gesture **0.09 ms**, overlay **17.60 ms**, JPEG encode **8.58 ms**, total **82.00 ms**. Full data: `outputs/stage9_profile/vision_with_preview.json`.

Stage5 V2 visual-only no-display baseline: **120 frames in 8.49 s, 14.13 FPS**. Mean Pose 30.66 ms (P95 35.11), BoT-SORT 8.55 ms (P95 14.61), Stage5 total 9.10 ms (P95 15.06). There were **zero detected people** during this run, so OSNet ReID and stereo depth evidence cost were zero and this does not measure an authorized operator. The runner is not yet RK3576-ready for Stage6 live control. Log: `outputs/stage9_stage5_baseline/20260925_061735_UTC/ownership_frames.jsonl`.

**Diagnostic conclusion:** No measured board camera, Stage3/4, or board-local HTTP queue approached the reported 3-second delay. The largest measured cost that lowers vision throughput is synchronous overlay/JPEG: roughly 26 ms per processed frame, reducing Stage3/4 from 18.00 to 12.18 FPS. Direct-LAN ground receipt is slower (P95 379 ms) but still did not reproduce 3 seconds. Browser display, remote desktop, and intermittent USB reconnection remain plausible additional sources; no exact 3-second source is asserted without a reproducible display observation. Frame-age values are not sensor-to-photon measurements. Stage4's 300 ms gesture confirmation is a separate protocol delay.

**Reliability blocker:** `journalctl -k` recorded `usb 2-1: USB disconnect` and UVC URB failures, followed by re-enumeration of `0bda:5883`; FFmpeg then exits and vision fails closed. Physical USB connection/power needs checking. No auto-restart should be interpreted as a substitute for a stable camera link.

## Phase 2: bounded preview handoff

The camera capture backend already uses a single latest-frame slot. The Stage3/4 browser preview now also hands analysis frames to a separate overlay/JPEG worker through one overwriteable slot. A slow viewer only receives the newest encoded frame. Render errors are reported without stopping vision. The 640-pixel display setting changes only the final displayed image; Run B rectification, 180-degree analysis rotation, anatomical left/right, Pose, and Gesture are unchanged.

With the preview at 640 pixels and one direct-LAN receiver, a 10-second status window processed **158 vision frames (15.8 FPS)** and displayed 157. The `/status` rolling FPS at the end was 15.44; one observed host-receipt-to-vision-ready age was 75.97 ms. A separate 80-frame direct-LAN MJPEG sample, after correcting the display timestamp handoff, measured host-receipt-to-ground-receive age **mean 119.01 ms, P95 142.91 ms, max 156.55 ms**. Board `/status` observed host-receipt-to-JPEG-ready age 98.94 ms. These are not sensor-to-screen measurements; add the measured sensor-to-V4L2 dequeue segment and unknown browser decode/display latency. The 960-pixel run varied widely (one sample mean 405.53 ms), so use `--display-width 640` for this diagnostic preview until more receiver data is available.

Three bounded-preview unit tests passed on the RK3576: latest analysis overwrites old frames while preserving the timestamp, display timestamps stay paired with JPEGs, and a render exception does not stop vision. The camera USB link disconnected again during two attempted preview runs; the successful run does not clear that reliability blocker.

## Phase 3 dry-run checkpoint

The RK3576 Stage6 runner was wired to the existing RKNN OSNet backend and the ROS Jazzy environment. A 120-frame isolated `/interaction/intent_dry_run` run completed at **16.07 FPS** and emitted 175 observed dry-run Intent messages, all HOVER. Mean `frame_processing_ms` was **69.82 ms**, Stage6 submit **0.08 ms**, tracker **9.82 ms**, OSNet **0 ms**, stereo depth **0.015 ms**. This scene had no detected person, so OSNet, depth with a person, T-Pose, and movement latency remain unmeasured. The run did not produce a valid full-chain result: PX4 topics and Gateway were absent, and no live output was requested. A prior dry-run attempt ended fail-closed at frame zero during another camera USB interruption.

Unit tests run separately on the board: Stage3 **31 passed**, Stage4 **14 passed plus five subtests**, Stage5 **108 passed**, Stage6 **65 passed**, and preview **3 passed**. Combined collection had cross-directory import conflicts; separate module runs were all green. These automated checks do not substitute for real PX4 and single-operator evidence.

### Subsequent person-visible checkpoint

A later Stage6 run without input rotation produced five Pose frames in 30 and exposed full-frame stereo depth as the long-latency source: on those frames SGBM took **1.11–1.15 seconds**, with Intent age **1.21–1.24 seconds**. Scheduling depth only for current tracker observations avoids that cost when no observation survives. A 120-frame run with this scheduling had frame-age mean **102.54 ms**, P95 **98.92 ms**, but one tracked frame still reached **1283.81 ms** and effective FPS was **10.90**. This scheduling does not solve the active-operator performance requirement.

The camera was visually verified to be upside down. Stage6 now rotates the rectified left analysis image by 180 degrees via an explicit option while mapping Stage5 depth boxes back to original Run B coordinates. With this correct integration and unchanged full-frame depth, a 60-frame isolated dry-run yielded **60 Pose frames, 24 person-observation frames, 1.88 effective FPS**, Intent-age mean **550.48 ms**, P95 **1258.58 ms**, max **1292.94 ms**, mean depth cost **448.39 ms across all frames**, max **1186.31 ms**. No authorization occurred in this short run. The full-chain performance gate remains **FAIL**.

A diagnostic torso-ROI SGBM probe cut one-frame compute from about 0.88 s to 0.024 s, but another frame changed median depth from **0.519 m to 0.439 m**. This could alter identity evidence, so the ROI change was **reverted**. Changing OpenCV SGBM threads from 1 to 2, 4, or 8 did not approach the required latency. No depth thresholds, calibration, or safety gates were changed. Further acceleration requires a validated depth method with an equivalence/safety study, not a silent ROI substitution.

After a board reboot during this task, `~/fcv_ros_ws/install/setup.bash` was absent. Stage5 tests passed **109/109** including the new scheduling test, rotated-depth tests passed **2/2**, and preview tests passed **3/3** using the remaining vision environment. The full Stage6 suite cannot be recollected until `px4_msgs` and `drone_control_gateway` are restored; the earlier **65/65** run was before the reboot.
