# RK3576 single-camera AI and H264 fan-out V1

## Hardware audit

Board: RK3576, Ubuntu 24.04, GStreamer 1.24.2. `/dev/video73` is a USB UVC SBS camera, `0bda:5883`, MJPEG 2560×960. V4L2 advertises 60 FPS at this resolution. A real `v4l2src` negotiation request for 30 FPS failed with `not-negotiated`; the V1 camera source therefore uses 60 FPS and the video branch produces 30 FPS.

`mppjpegdec` and `mpph264enc` are installed. The JPEG decoder can output NV12 and advertises `video/x-raw(memory:DMABuf)`. The encoder accepts NV12 and supports 180° rotation, 4 Mbps CBR, GOP 30, Baseline profile, one pending frame and repeated IDR headers. `librga.so` and `/dev/rga` are present, but `rgaconvert`/`rga` GStreamer elements are absent. The existing encoder logs `rga_api` during rotation. `/dev/dri` is present; `/sys/kernel/debug` is not readable as the runtime user.

The implemented V1 does **not** force DMABuf caps or AFBC. The negotiated tee format is linear NV12. The video branch uses GStreamer `videocrop` on NV12 and MPP H264 hardware encoding with encoder rotation. `videocrop` is native GStreamer and may incur a CPU copy; V1 does not claim a fully zero-copy video path. The AI branch has one deliberate NV12→BGR conversion at `videoconvert`/`appsink`; Python owns one BGR copy because the Gst.Buffer is unmapped after each sample.

## Architecture and ownership

```text
/dev/video73, one v4l2src, MJPEG 2560×960 @ 60
  → bounded compressed queue → jpegparse → one mppjpegdec → NV12 SBS → tee
      ├→ bounded video queue → videorate 30 → crop LEFT 1280×960
      │   → mpph264enc rotate180, 4 Mbps, GOP30, no B frames
      │   → h264parse → rtph264pay → UDP 192.168.1.16:5600
      └→ bounded AI queue → videoconvert BGR full SBS → appsink latest only
          → Run B rectification + unrotated stereo ROI depth
          → rotate rectified LEFT 180° for RKNN Pose
          → existing Stage4, Stage5, Stage6 AuthorizedGestureIntentAdapter
          → local DRY-RUN log only; NO PX4 OUTPUT
```

All three `queue` elements have `max-size-buffers=1`, disabled byte/time limits and `leaky=downstream`. `appsink` has `max-buffers=1`, `drop=true` and `sync=false`. No Python BGR frame enters the video branch. An AI consumer can run slowly while GStreamer capture, decode and encode continue on their own threads. The runtime fails closed on pipeline ERROR/EOS, clears the Stage6 lease and sets the GStreamer pipeline to NULL. UDP receiver loss is not expected to be a pipeline error.

The camera source probe counts captured compressed frames. The JPEG parser exit probe assigns a source delivery timestamp and ordered ID to compressed frames reaching the hardware decoder; the decoder exit probe binds these by exact PTS. This avoids assuming the V4L2 and decoder PTS are bit-identical. AI frame IDs increment **per processed frame** so Stage4 temporal frame counts remain contiguous; `source_frame_id` in metrics reveals skipped decoder input frames. `CameraFrame.timestamp_ns` uses the monotonic JPEG parser delivery time, which includes any earlier camera/source-queue delay. It is not an exposure timestamp. AI frame age is measured against this timestamp. Sample and buffer references are released every iteration; the timestamp map is bounded to 512 entries.

Stage5 uses the existing `TrackedRotatedDepthAdapter` from the Stage5 preview, preserving the full SBS frame, Run B calibration, ROI depth 5 Hz/500 ms and unrotated stereo geometry. Stage6 uses the existing `Stage6DryRun` wrapper around `AuthorizedGestureIntentAdapter`; an independent 20 Hz clock enforces its 300 ms lease. No ROS, Flight Gateway or `/fmu/in/*` publisher is created.

## Start and receive

On RK3576:

```bash
cd ~/FlyCommanderByVision
source scripts/env_rk3576.sh
~/venvs/fcv_stage5/bin/python3 scripts/run_rk3576_fanout.py \
  --camera /dev/video73 --camera-fps 60 --video-fps 30 \
  --host 192.168.1.16 --port 5600 --bitrate-kbps 4000 \
  --auto-reauthorize --duration 300 \
  --metrics-jsonl /tmp/fcv_fanout_metrics.jsonl \
  --stage6-jsonl /tmp/fcv_fanout_stage6.jsonl
```

On the current Windows workstation, use `ffplay.exe` with the matching `video_link.sdp`, `-fflags nobuffer -flags low_delay -framedrop -max_delay 0 -sync ext`. The video carries no AI overlay. Stop the sender with Ctrl+C or a duration; it releases the camera.

Diagnostic switches: `--no-ai` measures video alone, `--no-video` measures AI alone with the same producer, and `--ai-sleep-ms 500` tests video independence under deliberately slow AI. No test switch enables live flight output.

## Validation evidence, 2026-09-26

Negotiated tee output was NV12 2560×960; the video encoder output was constrained-baseline H264 1280×960 at 30/1 caps; the AI appsink received full SBS BGR. During the 360-second concurrent run, `fuser` showed one PID holding `/dev/video73`, with one `v4l2src` and one `mppjpegdec` in the pipeline. The parser and decoder matched by exact PTS; the decoded-frame count was 21,080 versus 21,083 camera-source frames at shutdown. Camera and HTTP preview were not left running after the test.

| Test | Camera | Video steady FPS | AI steady FPS | Observation |
| --- | --- | ---: | ---: | --- |
| Video only, 35 s | MJPEG 60 | ~29.4 | off | One camera and one JPEG decoder; process CPU about 1.5 cores. |
| AI only, 35 s | MJPEG 60 | off | ~11.4–11.6 | Same GStreamer producer, video branch replaced by `fakesink`. |
| AI + video, 360 s | MJPEG 60 | mean 29.39, min 29.13 | mean 11.67, min 11.15 | 70 five-second steady windows after warmup; no accumulating queue. |
| AI + 500 ms deliberate sleep, 32 s | MJPEG 60 | ~29.3–29.5 | ~1.8 | Video did not follow AI slowdown; AI returned to the newest frame. |
| Camera MJPEG 30 | attempted | — | — | V4L2/GStreamer returned `not-negotiated`; camera advertises only 60 at 2560×960. |

Concurrent frame age measured from JPEG parser delivery to AI input: **mean 102.61 ms, P95 113.08 ms, max 164.26 ms**. Pose inference: mean **25.20 ms**, P95 **27.14 ms**. Stage5 total: mean **6.49 ms**, P95 **8.79 ms**. ReID mean 0.30 ms and ROI depth mean 0.09 ms in this no-person run, so those numbers do not represent a tracked operator. A final 10-second smoke with the Stage6 timing field enabled measured Stage6 wrapper latency at mean **0.0443 ms**, P95 **0.0611 ms**.

The concurrent run's sampled RSS ranged **223–259 MB**, with 238 MB near the beginning and 234 MB near the end; no sustained growth. Process CPU averaged **291%** of one core. The highest observed thermal zone was **85°C**, without FPS degradation. All sampled queues stayed at 0–1 frame. After the run, `/dev/video73` had no owner. A Windows ffplay process was closed and reopened during streaming; the sender and AI remained alive, though visual recovery awaits operator confirmation.

The 360-second run had no person in frame (`WAIT_OPERATOR` throughout). Therefore it establishes concurrent media and AI execution, **not** the requested human T-Pose/gesture acceptance. Ten manual glass-to-glass samples were not supplied. Those two cases remain pending and must not be represented by the prior separate Stage6 human run or by GStreamer PTS.

Known limits: camera 30 FPS negotiation is unsupported at this resolution; DMABuf zero-copy and RGA crop have not been established. A GStreamer branch error can terminate the shared pipeline; V1 fails closed and cleans up rather than attempting transparent branch recovery. Stage6 remains a dry-run intent and lease adapter, with no independent depth-based `interaction_ready` safety gate.

## Concurrent integration and system profile, 2026-09-26

The single-camera architecture above was retained. `run_rk3576_fanout.py` now records encoded H.264 bytes per reporting window (before RTP packetization), AI frame age at Stage6 output, and the lease clock's native thread ID. `profile_rk3576_fanout.py` is a read-only `/proc` and sysfs sampler; it neither changes CPU affinity nor touches the camera. All runs below used Run B calibration, the existing RKNN Pose and ReID models, and **DRY-RUN NO PX4 OUTPUT**.

| Run | Duration | Video FPS (steady five-second windows) | H.264 bitrate | AI FPS | Process CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| Video only, AI off | 75 s | mean 29.40, min 29.25 | mean 3,946 kbps | off | mean 153% |
| AI only, video off | 75 s | off | off | mean 11.11, min 10.88 | mean 258% |
| AI + video, system profile, no person | 360 s | mean 29.37, min 28.27 | encoder set to 4 Mbps; bitrate field added after this run | mean 9.89, min 7.13 | mean 350% |
| AI + video, human gestures | 210 s | mean 29.36, min 28.90 | mean 3,931 kbps | mean 8.18, min 4.95 | mean 370% |

The concurrent system run had 70 steady video windows and 3,546 AI frames. Source averaged 58.77 FPS. Its parser-delivery-to-AI-input age was mean 140.66 ms, P50 132.50, P95 200.92, max 299.80. Pose mean/P95 was 26.72/32.09 ms; Stage5 total 41.79/107.01 ms; ReID 17.37/27.61 ms; stereo depth 15.16/76.59 ms. All queue levels sampled at 0–1; 5-minute age did not grow monotonically. The AI-only run processed 814 frames, with input age mean/P95 108.30/118.07 ms and output age mean/P95 162.25/188.04 ms. Its Pose mean/P95 was 25.04/27.13 ms.

The human run reached `LOCKED_HIGH` and logged valid Stage6 dry-run decisions for HOVER (19 frames), ASCEND (11), DESCEND (15), MOVE_LEFT (9), and MOVE_RIGHT (10). It processed 1,707 frames. Input frame age mean/P95 was 162.83/217.89 ms; age at AI output mean/P95 was 277.82/395.00 ms. Pose mean/P95 was 26.91/33.14 ms, Stage5 total 68.45/173.82 ms, ReID 23.81/29.88 ms, and ROI depth 30.44/133.63 ms. These figures distinguish actual operator processing from the earlier no-person baseline. Video stayed above 28.5 FPS even while human AI fell to 8.18 FPS on average. The 75-second video-only run's H.264 parser payload averaged 3.95 Mbps; RTP/UDP wire rate is somewhat higher due to headers.

The 360-second run's RSS was 230 MB near 10 s, 248 MB near 60 s, 259 MB near 180 s, 259 MB near 300 s, and 259 MB near 350 s: warm-up growth followed by a plateau. `MemAvailable` was about 2.30 GB near 300 s. Maximum thermal-zone readings were 68.4°C near 10 s, 77.6°C near 60 s, 84.1°C near 180 s, and 85.0°C near 300 s. Frequency scaling was observed, but these measurements alone do not prove thermal throttling.

The verified topology is four Cortex-A53 cores (CPU0–3, up to 2.016 GHz) and four Cortex-A72 cores (CPU4–7, up to 2.208 GHz). From 146 two-second samples spanning 296 s, mean busy percentages were CPU0–7: **40.1, 36.3, 35.7, 34.9, 61.1, 71.3, 56.8, 55.4**. The largest measured threads, expressed as one-core percentages, were GStreamer `q_ai:src` 98.5%, Python main 63.6%, `q_video:src` 30.4%, several unnamed Python/RKNN worker threads about 8–21% each, encoder output 14.2%, source queue 11.1%, and JPEG decoder output 4.6%. Thread names identify GStreamer branch workers; the unnamed workers cannot be reliably assigned to Pose, ReID, stereo, or Stage5 tracking from `/proc` alone. The separate Stage6 lease thread is exposed by native TID in the runtime metrics; it runs at 20 Hz. Per-stage elapsed times above are more reliable for attributing algorithm latency.

The RK NPU devfreq interface reported **950 MHz** and raw `load=100@950000000Hz` in all 146 samples. This is a driver-reported value, not a separated Pose/ReID utilization trace; NPU serialization cannot be concluded from it. `mppjpegdec` and `mpph264enc` ran and encoded/decoded continuously, but no readable per-engine MPP or RGA utilization counter was found as the runtime user (**NOT AVAILABLE**). `librga` was invoked for encoder rotation; there is no separate RGA GStreamer element in this path.

Full-frame-copy estimate from code audit: GStreamer `videoconvert` produces one full SBS BGR conversion, and `read_frame()` makes one explicit full SBS NumPy copy before unmapping the sample. The RIGHT half makes one eye-size copy, LEFT rectification makes one eye-size remap output, and analysis rotation makes one eye-size output. ROI depth normally remaps only a cropped RIGHT region; full-frame stereo remap exists in a fallback path but is not the selected ROI path. `videocrop` may copy one LEFT NV12 image; no claim of zero-copy or exact DDR traffic is made.

The real 30 FPS camera request at 2560×960 failed caps negotiation, so the demo default remains **camera MJPEG 60 FPS, video H.264 1280×960 at 30 FPS, 4 Mbps**. The Windows operator reported that the stopwatch and video appeared nearly simultaneous, with observed differences within about 10 ms and several matching displayed times. Ten individual readings were not supplied, so formal glass-to-glass sample statistics are **NOT AVAILABLE**. GStreamer PTS and AI frame age are not substitutes for glass-to-glass measurement.

The new 45-second `--ai-sleep-ms 500` run held video at mean/min **29.40/29.31 FPS** while AI fell to **1.49 FPS**; frame age remained bounded and all queues stayed at 0–1. A deterministic test starts the fan-out's same 20 Hz lease clock, submits a valid RIGHT command, withholds AI frames for 500 ms, and verifies invalid HOVER with `VISION_COMMAND_TIMEOUT` around the 300 ms deadline; this tests lease independence without PX4 output. A human RIGHT gesture followed by artificial sleep was not separately run. In a separate 100-second AI+video receiver test, the Windows operator closed and reopened ffplay and confirmed the image returned smoothly. The sender continued at mean/min **29.37/29.15 FPS**, AI averaged **8.8 FPS**, and queues stayed at 0–1. UDP has no receiver acknowledgment, so that visual confirmation is operator-reported.

Current bottlenecks are the full SBS BGR conversion/copy thread and operator-time Stage5 stereo depth, with the NPU devfreq load also high. The next stage should use these measurements to design RK3576 heterogeneous runtime V2; CPU affinity, NPU core masks, AFBC, DMA-BUF-to-NPU, and flight output remain outside V1.

Regression after the metrics and test changes: Stage3 **31 passed**, Stage4 **14 passed and 5 subtests**, Stage5 **123 passed**, Stage6 **76 passed**. The `video_link_rtp.sh` and `preview_stage5_web.py` implementations were not changed. After all runs, `fuser /dev/video73` reported no owner. The calibration, Stage3–6 algorithms, ReID threshold, stereo parameters, and Safety Pilot were not changed.
