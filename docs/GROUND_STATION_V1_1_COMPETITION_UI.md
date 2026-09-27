# Ground Station V1.1 — Competition UI

## Scope

Ground Station V1.1 changes only the Windows presentation layer. The RK3576 camera, H.264 RTP media path (UDP 5600), Stage3–6 algorithms, coordinate adapter, metadata packet semantics (UDP 5603), and Stage6 lease remain unchanged. The station is read-only: ARM, TAKEOFF, LAND and RETURN buttons are disabled; no ROS or PX4 command publisher is loaded. The PX4 node and card always say `DISCONNECTED`; `DRY-RUN` is shown separately as the current control mode. No PX4 telemetry is invented.

## Competition Mode

The default view follows the actual evidence path across five nodes: **PERCEPTION → AUTHORIZATION → GESTURE → SAFETY → PX4**. The video stays the primary visual and shows only the selected person's bbox, canonical skeleton and operator/candidate label. The right side has Operator, Control and PX4 cards. The lower Command to PX4 / PX4 Feedback region states `NOT FORWARDED` / `NO DATA`, with the four disabled flight buttons next to the command placeholder.

`ground_station.evidence.competition_state` maps the existing V1 packet to display text. Perception reads `people_count` and the selected person; Authorization preserves the Stage5 ownership state, with `LOCKED_HIGH` displayed as `AUTHORIZED` plus the raw state; Gesture uses only the stable gesture; Safety reads the actual Stage6 intent, valid flag and reason. `SAFE HOVER` is shown only when Stage6 reports invalid `HOVER`, not from a new UI safety decision. Stage5 `OPERATOR_LOST` removes the prior gesture. Metadata or AI older than the configured freshness limit removes the old person overlay and marks the chain as stale. Video loss has its own status and does not hide fresh metadata.

The palette uses restrained slate, teal, green and amber. The 4:3 video is letterboxed without stretching or cutting coordinates. The Qt layout uses stretch factors and was visually inspected with synthetic data at 1920×1080, 1463×914 (the development machine's secondary display), and 1366×768. The synthetic visual QA files are under `runs/gs_v1_1_*_synthetic.png`; they check layout only and are not live-camera evidence.

## Engineering panel

The **Engineering** button opens an independent, non-modal panel; it does not change the receivers or the RK3576 workload. Values refresh about 5 times per second only while the panel is visible. It exposes packet and local receiver data: board video and AI FPS, 5-second local decode/display FPS and metadata rate, metadata/AI/depth ages, sequence gaps, invalid packets, FFmpeg restarts, people count, track/session IDs, pose score, raw/stable gesture, and Stage6 intent/valid/reason/lease. Packet bytes and board diagnostics show `--` / `NOT AVAILABLE IN V1` because V1 metadata does not carry them; the UI does not fabricate these values. Competition Mode keeps all numerical diagnostics out of the main view.

## Start and recovery

The existing Windows command is unchanged:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\FlyCommanderByVision\scripts\start_ground_station.ps1"
```

The RK3576 sender command remains the one in `docs/GROUND_STATION_V1.md`. Video and metadata receive independently. If metadata stops, the evidence chain says `METADATA LOST` and the old bbox/skeleton/gesture disappear. If video stops while metadata continues, the canvas says `VIDEO LOST` and the chain still reflects the current metadata. Closing/reopening the station and minimizing/restoring the window do not control Stage6 or affect the board stream.

Five-second Windows receiver metrics are written to `runs/ground_station_v1_1.jsonl`, preserving the earlier V1 run log.

## Validation

Pure display-mapping tests cover no person, candidate, locked RIGHT/MOVE_RIGHT, operator loss, stale metadata, stale AI and video loss. Offscreen Qt tests cover the default chain, one-click Engineering toggle and all disabled flight buttons. V1 protocol, coordinate-adapter and UDP receiver tests are rerun unchanged. Final live-camera acceptance and Windows decode/display measurements are recorded in the task result after the user opens the V1.1 window.
