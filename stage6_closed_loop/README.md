# Stage 6 — single-operator Authorized Gesture closed loop

Status (2026-09-24): **IN PROGRESS**. Gate 6A and真人 dry-run Gate 6B PASS;
65/65 Stage 6 tests PASS. Gate 6C evidence preparation is READY, but the
formal aircraft-response run remains NOT STARTED. The Safety Pilot authority
gate has changed and must be revalidated in Gate 6C. Gate 6D is
not started.

This is a separate NUC integration layer. It imports frozen Stage 3/4/5
code but does not modify it. Its only authority-bearing input is
`stage5.types.AuthorizedGestureV1`; the ROS output uses the existing
`drone_control_gateway/msg/Intent` on a reliable QoS topic. The actual
Gateway topic is `/interaction/intent`. The default run instead uses the
isolated `/interaction/intent_dry_run`, which cannot reach the Gateway.

## NUC setup and dry-run

```bash
cd ~/drone_stage6_closed_loop
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
source ~/venvs/drone_stage5_v2/bin/activate
export PYTHONNOUSERSITE=1
export PYTHONPATH=.:$HOME/drone_stage5_operator:$PYTHONPATH
python -m unittest discover -s tests -q
python ros_dry_run_probe.py
python live_closed_loop.py \
  --osnet-checkpoint ~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth \
  --auto-reauthorize --record --rosbag
```

In another ROS-sourced terminal, inspect the safe topic:

```bash
ros2 topic echo /interaction/intent_dry_run drone_control_gateway/msg/Intent
```

The preview reuses Stage 5 ownership annotations and adds skeleton arms,
Intent/ACTIVE/SAFE, short Session ID, run time, and fresh PX4 NED position,
velocity, arming/nav/failsafe when available. `R` toggles annotated recording;
`--record` starts at the first frame. `Q`/Esc exits; `X` releases the operator.
Every run has a unique `runs/<UTC>/` directory:

```text
run_manifest.json  summary.json  gate6c_analysis.json
vision_frames.jsonl  intent_events.jsonl
gateway_events.jsonl  px4_state.jsonl
recordings/clip_001/annotated.avi
recordings/clip_001/frames.jsonl  recordings/clip_001/manifest.json
rosbag/  rosbag_topics.txt  rosbag_process.log  # when --rosbag is used
```

The AVI has constant *playback* FPS; actual capture timing is the frame's
`capture_monotonic_ns`, shared with `vision_frames.jsonl`. Stage 5's tested
`DiagnosticRecorder` writes the AVI and clip frame log; Stage 6 adds only
the evidence overlay and alignment fields. `gateway_events.jsonl` is a
read-only observer of Intent, TrajectorySetpoint and OffboardControlMode;
`px4_state.jsonl` records real PX4 local position/status only when those
topics exist. Empty files mean unavailable, never zero-valued fake state.

All autonomous logs use one run-start `t0_monotonic_ns` and `run_elapsed_ms`.
ROS source stamps and PX4 source microseconds are retained separately;
their clock domain is **not assumed** to match host monotonic. Nonfinite
PX4/setpoint values become JSON null with `nonfinite_fields` diagnostics.

If there is no fresh authorized gesture, the publisher sends `HOVER` with `valid=false`;
the Gateway treats this as an immediate safe reset. On process failure, the
Gateway's independent 0.5-s timeout remains the backstop.

On the explicit **live** topic only, Stage 6 additionally requires fresh PX4
`VehicleStatus`: armed, Offboard and not in failsafe. A status older than the
configurable `--px4-status-timeout-ms` (default 1500) disables flight
authority. Exiting Offboard/failsafe/disarm revokes the movement lease at the
status callback, without altering Stage 5 Session or Gallery. Returning to
Offboard never replays a held pose: the same trusted operator must first
show stable neutral `UNKNOWN` after re-entry for at least 150 ms/two frames,
then make a new legal gesture. The existing 300-ms vision lease, 20-Hz safety
publisher and Gateway 500-ms timeout are unchanged. The overlay/logs separate
Identity Authority, Flight Authority and Motion Lease. Dry-run retains its
Gate 6A/B semantics and cannot control the Gateway.

The 2026-09-24 follow-up NUC test had 65/65 tests PASS, including an isolated
ROS authority-edge test. Camera smoke could not run because `/dev/video*`
was absent at that time; do not interpret this as a camera or Gate 6C PASS.

To perform a headless empty-scene integration smoke only, add
`--no-display --max-frames 20`. This does **not** validate gestures or
aircraft motion. Run `python analyze_gate6c_run.py runs/<UTC>/` to recheck
evidence; dry-run and missing PX4/video/rosbag result in
`INSUFFICIENT_EVIDENCE`, never aircraft PASS. No TAKEOFF/LAND/ARM command
exists in this adapter.

`scripts/record_gate6c_rosbag.sh EXISTING_RUN_DIR dry-run|live` checks actual
ROS topic types before recording. `--rosbag` invokes it automatically and
closes it before writing the run summary. A formal live run must record
`/interaction/intent`, `/fmu/in/trajectory_setpoint`,
`/fmu/in/offboard_control_mode`, and the discovered PX4 local-position and
vehicle-status topics. The analyzer also requires the trajectory publisher
to be uniquely `/control_gateway` and all required bag topics nonempty.

Publishing to the real Gateway topic requires the explicit pair
`--topic /interaction/intent --allow-live-output`. **Do not use that pair
until Gate 6C is explicitly started under supervised Gazebo SITL.** This
package does not enter Offboard or arm/take off. Gate 6C preparation so far
has used only `/interaction/intent_dry_run`; do not connect a real drone.

## Next supervised Gazebo Gate 6C — commands, not yet executed

Only after reviewing the observer-only Run and with a human supervising
Gazebo/QGroundControl, open separate NUC terminals:

```bash
# Terminal 1: existing DDS bridge
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
MicroXRCEAgent udp4 -p 8888
```

```bash
# Terminal 2: Gazebo SITL, never a real aircraft
cd ~/PX4-Autopilot
HEADLESS=1 make px4_sitl gz_x500
```

```bash
# Terminal 3: unchanged Stage 1 Gateway
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
ros2 launch drone_control_gateway gateway.launch.py
```

```bash
# Terminal 4: evidence-enabled Stage 6; explicit live-output pair
cd ~/drone_stage6_closed_loop
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
source ~/venvs/drone_stage5_v2/bin/activate
export PYTHONNOUSERSITE=1
export PYTHONPATH=.:$HOME/drone_stage5_operator:$PYTHONPATH
python live_closed_loop.py \
  --osnet-checkpoint ~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth \
  --auto-reauthorize --record --rosbag \
  --topic /interaction/intent --allow-live-output
```

Before arming, confirm QGroundControl connected, all required ROS topics
present, the Stage 6 Run is recording, and Gateway is publishing only zero
setpoints. In the PX4 SITL console (`pxh>`), use the *separate* manual
commands `commander arm`, `commander takeoff`, then after observing stable
2–3 m altitude and ongoing Gateway heartbeat, `commander mode offboard`.
Confirm `armed=true`, `nav_state=14`, `failsafe=false` in the evidence/UI.
Begin with HOVER, then RIGHT, release/settle, LEFT, release/settle, ASCEND,
DESCEND, and HOVER; do not chain gestures rapidly. Any wrong sign, persistent
motion, loss of authorization, log/rosbag failure, or PX4 failsafe means
stop Stage 6 immediately and use the separate PX4/QGroundControl landing
procedure. Analyze the completed Run:

```bash
python analyze_gate6c_run.py runs/<UTC>/
```

These are **future supervised steps**, not permission to run a real drone
or a claim that Gate 6C aircraft-response testing has happened.
