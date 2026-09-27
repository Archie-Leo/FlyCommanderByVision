# Ground Station V1 — read-only H.264 and AI metadata

## Purpose and safety boundary

The Windows **灵眸控飞 / Vision Flight Console** displays the existing RK3576 1280×960 H.264 RTP stream with a locally drawn operator box, canonical skeleton, gesture, Stage6 intent, depth and link status. It is a viewer. ARM, TAKEOFF 1.2m, LAND and RETURN buttons are present and disabled. Neither the viewer nor the board metadata sender imports ROS/PX4 command publishers or sends a flight command. Stage6 keeps its independent 300 ms lease clock and does not use the viewer as a heartbeat.

The video media path remains frozen: one `/dev/video73`, one `mppjpegdec`, NV12 tee, LEFT crop and 180° rotation, MPP H.264, RTP/UDP port 5600, 30 FPS target and about 4 Mbps. AI/Stage3–6 thresholds, models, ownership, depth, calibration and safety semantics were not changed. The metadata sender is an optional side output of `scripts/run_rk3576_fanout.py`; leaving out `--metadata-host` preserves the old behavior.

## Architecture and ports

```text
RK3576: /dev/video73 -> one MPP JPEG decoder -> NV12 tee
        ├─ frozen MPP H.264 RTP ----------------------> Windows FFmpeg -> latest video frame
        └─ existing Stage3/4/5/6 dry-run -> snapshot -> 20 Hz UDP -> latest metadata packet
                                                            Windows Qt draws local overlay
```

Video uses 5600/UDP. Metadata uses **5603/UDP**. The initially suggested 5601 collided with this Windows FFmpeg SDP receiver's adjacent RTP/RTCP bind; its `local_rtcpport` override and an SDP `a=rtcp` trial did not resolve that bind. Moving only the independent metadata receiver to 5603 removed the collision. No video encoder, RTP sender, SDP video port, or bitrate was changed. Ground Station closure has no ACK or backpressure path to the RK3576. The board sender keeps one immutable latest visual snapshot, reads a fresh Stage6 decision on each 20 Hz send, emits a complete compact JSON datagram, and drops send failures. No FIFO or image crosses the metadata link.

The schema version is 1. Each packet includes sequence, camera source and processed frame IDs, current AI age, people count, operator/candidate state and track/session IDs, one bbox and the 13 canonical joints when present, raw/stable gesture, Stage6 intent/valid/reason/lease, selected-track depth/value/age/quality, and measured AI/video FPS. Missing fields are `null`. The 13 joints are ordered by `ground_station.protocol.JOINTS`; edges match the Stage3 canonical display topology. A packet is capped at 1400 bytes. Camera and Windows monotonic clocks are not compared: the receiver stores local monotonic arrival time. UI actions become stale after 500 ms without a new metadata packet or a fresh AI frame; an old person overlay clears after 1000 ms. A live metadata packet cannot keep an old AI detection visible, because the board's `ai_age_ms` continues to advance while Perception is stalled.

## Display coordinates

AI detections are in **rotated rectified LEFT** coordinates; the H.264 picture is **rotated raw LEFT**. The adapter uses the already loaded Run B LEFT `initUndistortRectifyMap` maps. It undoes analysis rotation for each point, samples the rectification map bilinearly to obtain the raw source point, and rotates again into the video display. Eight boundary points, including all four corners and four edge midpoints, determine each display-only bbox. No full image remap, fixed offset, or change to AI coordinates is made. Synthetic center/edge/rotation tests passed. The operator visually confirmed bbox and shoulder/elbow/wrist/hip/knee/ankle placement at center, left and right, with the correct image direction.

## Windows decoder and UI

`ground_station.video_receiver` launches the existing Windows FFmpeg binary with `nobuffer`, `low_delay`, `max_delay 0`, a validated 0.5 s codec probe and raw BGRA output. A dedicated reader drains the pipe continuously into a single latest-frame slot. The GUI reads the latest frame on a precise 16 ms Qt timer; a late frame replaces the previous one rather than accumulating a raw-frame queue. FFmpeg input and the UDP metadata receiver each restart or recover independently. The Qt canvas retains the current frame backing bytes, draws only the operator or candidate, and uses the same Stage3 skeleton edges. Candidate and `LOCKED_HIGH` operator use different labels and colors. `OPERATOR_LOST`, stale AI, stale metadata and video loss clear or inhibit stale visual state. System status and the four disabled flight buttons are displayed in a restrained dark layout.

The current Windows environment had an FFmpeg binary but no ordinary `python` command, PySide6 or PyAV. LibreOffice's installed Python 3.13 and pip were used, with PySide6 6.11.2 installed into the ignored local `.ground_station_deps` directory. The launcher checks for an available Python and explains the one-time dependency installation if missing. This portable Qt combination did not enumerate system fonts; the app loads installed Windows Segoe UI and Microsoft YaHei font files at startup. No font files are copied into the repository.

## Start commands

On RK3576, update both destination IP arguments if the Windows DHCP address changes:

```bash
cd ~/FlyCommanderByVision
source scripts/env_rk3576.sh
~/venvs/fcv_stage5/bin/python3 scripts/run_rk3576_fanout.py \
  --camera /dev/video73 --camera-fps 60 --video-fps 30 \
  --host 192.168.1.7 --port 5600 --bitrate-kbps 4000 \
  --auto-reauthorize --async-perception --async-depth \
  --metadata-host 192.168.1.7 --metadata-port 5603 --metadata-hz 20 \
  --duration 3600
```

On the Windows machine used for acceptance:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\FlyCommanderByVision\scripts\start_ground_station.ps1"
```

Settings, including the FFmpeg path, SDP, ports and stale intervals, live in `ground_station/config_v1.json`. The Windows launcher starts both receivers and the UI. A `runs/ground_station_v1.jsonl` local file records five-second viewer metrics. No separate ffplay or metadata terminal is needed.

## Validation record

Validation was performed on 27 September 2026 using the board branch `rk3576-migration`, RK3576 `run_b.yaml`, and the Windows FFmpeg installation at `D:/K230IDE/share/qtcreator/ffmpeg/windows/bin/ffmpeg.exe`. User visual acceptance confirmed center/left/right alignment, T-Pose lock, repeated movement gestures, two operator exits followed by automatic relock, video recovery after a board-stream restart, close/reopen recovery for the Windows station, and immediate updates after restoring the window from minimization. HOVER was separately held until it produced 47 valid frames. The board log also recorded valid ASCEND, DESCEND, MOVE_LEFT and MOVE_RIGHT. Window minimization correctly pauses Qt paint events while the decoder and metadata receiver keep consuming live input.

The 476-second human acceptance run recorded 95 five-second board windows. Its steady-state results (from 30 seconds onward) were: video **29.079 FPS mean**, **28.38 FPS minimum 5-second window**; AI **10.091 FPS mean** versus the frozen 10.33 FPS reference (about 2.3% lower); process CPU **302.08% mean** across cores; RSS **268,416 KiB maximum**; temperature **84.999°C maximum**. The sender sent 9,503 packets, with **780.5 / 790 / 804 bytes** mean / P95 / maximum and zero send, size or snapshot errors. A simultaneous 300-second system profile recorded metadata-thread CPU **2.20% mean, 3.0% maximum of one core**, below the 5% budget. The 28.38 FPS short window missed the 28.5 FPS floor by 0.12 FPS; the sustained mean stayed above the floor.

A separate 355-second board run confirmed continuous operation: video **29.146 FPS mean**, **28.49 FPS minimum 5-second window**, AI **10.568 FPS mean**, sender 7,094 packets and zero send/size/snapshot errors. The Windows station had a simultaneous **340-second uninterrupted interval** (viewer elapsed 340.17–680.17 s): decoded video **29.128 FPS mean**, displayed video **28.293 FPS mean**, UI timer **62.484 Hz mean**, and metadata **19.988 Hz mean**. During that interval packet-gap count remained 1 (no new gap), FFmpeg restart count remained 3 (no new restart), and sampled video and metadata ages were each at most 62 ms. The cumulative counts include earlier deliberate stream-stop/recovery trials.

Independent failure trials verified both asymmetric cases. With board video still sent to Windows but metadata redirected to loopback, Windows kept decoding/displaying about 29/28 FPS, marked METADATA LOST and suppressed the old skeleton. With board AI and metadata still sent to Windows but the video branch replaced by its existing fakesink option, Windows received 20 Hz metadata while VIDEO LOST appeared. Board operation continued when the station was absent or restarted; reopening the station recovered both streams. The human operator confirmed that restoring an occluded/minimized window immediately resumed current video and overlays.

Unit tests cover schema/JSON, packet cap, no operator, locked operator, invalid Stage6 state, coordinate center/edge/180° mapping, stale and lost-person display policy, latest UDP packet replacement, receiver sequence recovery, and sender behavior without a receiver. The Stage3–6 regression suites are run separately because their test packages share names. No live PX4 output is involved.

## Known limits

Video and metadata are intentionally paired by latest state without buffering video for exact temporal alignment. Fast motion can leave a visible AI overlay behind the 30 FPS video by the existing AI inference age. The UI shows only the selected operator/candidate skeleton, not all detected persons. The Windows font and FFmpeg paths may need adjustment on another machine. The board's prior auto-reauthorization thresholds were left untouched; this acceptance run showed two successful returns but does not guarantee every return will pass the existing quality and identity gates.
