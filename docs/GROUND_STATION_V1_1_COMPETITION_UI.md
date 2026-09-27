# Ground Station V1.1 — Competition UI

## Scope

Ground Station V1.1 changes only the Windows presentation layer. The RK3576 camera, H.264 RTP media path (UDP 5600), Stage3–6 algorithms, coordinate adapter, metadata packet semantics (UDP 5603), and Stage6 lease remain unchanged. The station is read-only: ARM, TAKEOFF, LAND and RETURN buttons are disabled; no ROS or PX4 command publisher is loaded. The PX4 node and card always say `DISCONNECTED`; `DRY-RUN` is shown separately as the current control mode. No PX4 telemetry is invented.

## Competition Mode

The default view follows the actual evidence path across five nodes: **PERCEPTION → AUTHORIZATION → GESTURE → SAFETY → PX4**. The video stays the primary visual and shows only the selected person's bbox, canonical skeleton and operator/candidate label. The right side has Operator, Control and PX4 cards. The lower Command to PX4 / PX4 Feedback region states `NOT FORWARDED` / `NO DATA`, with the four disabled flight buttons next to the command placeholder.

`ground_station.evidence.competition_state` maps the existing V1 packet to display text. Perception reads `people_count` and the selected person; Authorization preserves the Stage5 ownership state, with `LOCKED_HIGH` displayed as `AUTHORIZED` plus the raw state; Gesture uses only the stable gesture; Safety reads the actual Stage6 intent, valid flag and reason. `SAFE HOVER` is shown only when Stage6 reports invalid `HOVER`, not from a new UI safety decision. Stage5 `OPERATOR_LOST` removes the prior gesture. Metadata or AI older than the configured freshness limit removes the old person overlay and marks the chain as stale. Video loss has its own status and does not hide fresh metadata.

The palette uses restrained slate, teal, green and amber. The 4:3 video is letterboxed without stretching or cutting coordinates. The Qt layout uses stretch factors and was visually inspected with synthetic data at 1920×1080, 1463×914 (the development machine's secondary display), and 1366×768. The locally generated, Git-ignored `runs/gs_v1_1_*_synthetic.png` files check layout only and are not live-camera evidence.

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

Pure display-mapping tests cover no person, candidate, locked RIGHT/MOVE_RIGHT, invalid non-hover Stage6 output, operator loss, stale metadata, stale AI and video loss. Offscreen Qt tests cover the default chain, one-click Engineering toggle, disabled flight buttons, stale UI state, and three window sizes. The Windows local run passed 12 tests; the board's V1 protocol/coordinate/receiver plus pure V1.1 mapping run passed 11. The unchanged Stage3, Stage4, Stage5 and Stage6 suites passed 31, 14 plus 5 subtests, 123 and 81 tests respectively.

The user opened the V1.1 window on Windows and confirmed current video; center bbox and shoulder/elbow/wrist/lower-body skeleton alignment; correct image direction; T-Pose authorization; all five gestures and their corresponding Stage6 intents; OPERATOR_LOST and SAFE HOVER; immediate latest data after minimization; one-click Engineering metrics; and video, evidence chain and Engineering recovery after closing/reopening the station. The board's live log independently recorded valid MOVE_LEFT, MOVE_RIGHT, ASCEND, DESCEND and HOVER frames, plus one `OPERATOR_LOST → AUTO_REAUTHORIZE_CONFIRMING → LOCKED_HIGH` transition.

The first live Windows interval from 20.02 to 355.02 seconds (68 five-second windows) averaged **29.238 FPS decode, 28.409 FPS display, 19.997 Hz metadata and 62.462 Hz UI timer**, against the V1 reference of about 29.13/28.29 FPS decode/display. This interval had zero new packet sequence gaps and zero FFmpeg restarts. After the user reopened the station, a short window accumulated 14 metadata sequence gaps and one later isolated gap brought the total to 15; receiving returned to about 20 Hz without an FFmpeg restart. These gaps are retained as an observation; the V1.1 UI makes no network/protocol change.

With Engineering open for a user-confirmed 30-second observation, video stayed smooth and numerical fields updated. Approximate surrounding log windows averaged **29.13 FPS decode, 27.91 FPS display and 20 Hz metadata**; nearby closed-panel windows averaged **29.07 FPS decode, 28.38 FPS display and 20 Hz metadata**. The open interval was identified by the user action rather than a logged toggle timestamp, so these figures are indicative, not a controlled benchmark.

Independent board-side loss trials confirmed video-only delivery at about 29 FPS while metadata was redirected away from Windows: the chain said `METADATA LOST` and no old person overlay remained. With the existing `--no-video` fakesink option and 20 Hz metadata still sent to Windows, the canvas said `VIDEO LOST` while the chain continued to update. A 25-second video-only trial was too short for the cold FFmpeg receiver to settle; a later 75-second trial confirmed the steady video-only condition. Normal video and metadata were restored afterward.
