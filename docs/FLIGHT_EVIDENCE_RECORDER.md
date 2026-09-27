# Flight Evidence Recorder

The RK3576 recorder starts with `fcv-stack.target` and writes continuously to
`/var/lib/fcv/logs/sessions/<YYYYMMDD_HHMMSS_Fnnn>/`. The Windows Ground Station
joins the current board session automatically using authenticated local network
messages and writes to `runs/<same-session-id>/`. Neither flight control nor
FlightAuthority depends on either recorder.

## Evidence and limits

The board directory contains `manifest.json`, `events.jsonl`,
`system_events.jsonl`, `telemetry.csv`, and, after close,
`session_summary.json`. The manifest records the board boot ID, git revision,
device/port/calibration settings, wall and monotonic start times, and the
initial PX4 snapshot. Missing values are null. PX4 firmware version and ULog
filename are marked unavailable unless measured separately.

Each JSONL event has `timestamp_wall`, `timestamp_monotonic`, `session_id`,
`boot_id`, `source`, `event`, `old_state`, `new_state`, `reason`, and `details`.
Only transitions/decisions are written. The numeric telemetry stream runs at
about 10 Hz when armed, offboard, in a takeoff/land transaction, or when an
operator/gesture is active. Inactive periods still retain state-change events.
The most recent 30 completed board sessions are retained; the active session
is never removed.

The Windows directory contains `ground_station_manifest.json`,
`ground_station_events.jsonl`, and `session_summary.json`. Pending events are
kept locally while disconnected and joined to the next board session. H.264
recording is optional and is not enabled in V1; the existing video receive
path is unchanged. Engineering Mode has manual START/END as an aid, while
automatic recording requires no button press.

The recorder distinguishes calculated Gateway output from the actual ROS
publish call. ROS TX is observed from Gateway publish counters/snapshots.
`VehicleControlMode` is read from `/fmu/out/vehicle_control_mode` as PX4 state,
but it is **not** direct proof that PX4 uORB received a particular
`trajectory_setpoint`. The summary therefore records
`px4_rx_direct_evidence=unavailable_online` and recommends correlating the
PX4 ULog or a simultaneous PX4 NSH `listener trajectory_setpoint` capture.
PX4 ULog correlation uses the session wall time and PX4 timestamps; ULog
filename requires manual capture. A `CLOCK_ADJUSTED` event marks wall-clock
jumps. Old incomplete sessions are closed as `INCOMPLETE` after recorder restart
and never resumed under a new boot ID.

## Read-only diagnosis

`fcvctl status` shows service health. `journalctl -u fcv-recorder.service -b`
shows recorder degradation. `system_events.jsonl` distinguishes service,
camera-device, PX4 telemetry, and XRCE agent transitions. `XRCE_READY` means
the agent process is present; fresh PX4 telemetry is tracked separately.
Disk errors mark `RECORDER_DEGRADED` in the recorder journal and do not alter
arming, modes, setpoints, or safety gates.

The Ground Station shows `LAST BLOCKER` from existing button/authority/safety
decisions. `TAKEOFF_BUTTON_EVAL` and `LAND_BUTTON_EVAL` include all input
conditions, enabled/blocked result, and a primary reason. A grey TAKEOFF
button is diagnosable from that event. If a motion gesture reaches ROS TX but
no corresponding PX4 movement is observed, the summary leaves the primary
failure layer `UNKNOWN` and names the missing direct PX4 RX evidence.

## Next real-flight test checklist

1. Obtain fresh authorization and site safety confirmation; verify manual RC
   takeover, flight hardware, camera, PX4 telemetry, and local position.
2. Confirm recorder service is active and Windows Ground Station joined the
   same session ID. Note the PX4 ULog filename or start time if available.
3. Before TAKEOFF, inspect `TAKEOFF_BUTTON_EVAL` and `LAST BLOCKER`; record
   the actual enabled/blocked reason.
4. For one gesture, correlate OPERATOR, GESTURE, INTENT, AUTHORITY, SAFETY,
   GATEWAY, ROS TX, PX4 mode, and actual velocity/position by timestamp.
5. Capture a simultaneous PX4 ULog or NSH `trajectory_setpoint` listener if
   direct receive proof is needed. Do not infer PX4 RX from ROS TX.
6. End safely, wait for landed and disarmed, then preserve both summaries,
   JSONL streams, telemetry CSV, and PX4 ULog for review.
