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
