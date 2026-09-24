# Stage 6 integration design — 2026-09-24

## Frozen contracts and scope

Stage 1 `drone_control_gateway/msg/Intent.msg` contains `stamp`
(`builtin_interfaces/Time`), `operator_id` (`int64`), `intent` (`string`),
`confidence` (`float32`), `valid` (`bool`), `seq` (`uint64`), `reason`
(`string`), `requested_speed_m_s` and `requested_yaw_rate_rad_s` (`float32`).
The C++ Gateway subscribes reliably, queue depth 10, on
`/interaction/intent`. Its own 10-Hz heartbeat, world-NED mapping, clamps,
0.5-s command timeout and PX4 failsafe are unchanged. Stage 6 always sends
requested speed/rate `0`, meaning the Gateway chooses its frozen nominal
values. RIGHT is NED +Y, LEFT -Y, ASCEND -Z, DESCEND +Z; Stage 6 maps only
labels and does not calculate PX4 velocity.

Stage 5 `AuthorizedGestureV1` has `timestamp_ms`, `frame_id`,
`operator_session_id` (UUID string or None), `current_track_id`,
`authorization_state`, `gesture`, `gesture_confidence`, `valid`,
`reject_reasons`, `ownership_score`, `schema_version`. The control source is
**only** a fresh instance with `valid=true`, `authorization_state=LOCKED_HIGH`,
a Session ID and one of LEFT/RIGHT/ASCEND/DESCEND/HOVER. Raw/stable gesture,
pose and track ID are never independent control sources. The ROS `operator_id`
is a stable 63-bit diagnostic hash of the Session string, not an identity or
authorization key; the full UUID remains in Stage 6 JSONL.

## Lease and fail-closed behavior

`AuthorizedGestureIntentAdapter` holds only the latest observation, not a
last-valid movement command. A new invalid/UNKNOWN/RED/LOST observation
immediately clears the lease. Out-of-order, future, stale, malformed or
nonfinite timestamp/confidence input is rejected. Each 20-Hz ROS timer tick
rechecks age against configurable `vision_command_timeout_ms=300` (must be
<=500). At age >=300 ms, it publishes `HOVER, valid=false`; the frozen
Gateway then resets its lease. If the Stage 6 process stops altogether, the
Gateway's 500-ms timeout independently causes HOVER. New Session events
revoke the previous Session, so old queued gestures cannot resume motion.
The Stage 5 success frame has no gesture, and Stage 6 does not replay one.

Vision runs in the camera loop; the ROS 2 single-thread executor runs in a
separate timer thread. A lock protects the adapter's latest input and timer
decision. The timer never waits for a pose/ReID/SGBM inference. A ROS timer
exception fails the run instead of reporting false success. SIGINT is handled
by Python while the ROS context remains active: the runner revokes the lease,
cancels the timer, publishes a final `HOVER valid=false / VISION_STOPPED`,
allows transport delivery, then joins the executor and shuts ROS down once.
All logs use actual
monotonic capture time for age checks; there is no fixed 60-FPS assumption.

## Mapping

| AuthorizedGestureV1.gesture | Existing Gateway Intent |
|---|---|
| LEFT | MOVE_LEFT |
| RIGHT | MOVE_RIGHT |
| ASCEND | ASCEND |
| DESCEND | DESCEND |
| HOVER | HOVER |
| UNKNOWN/INVALID/valid=false | HOVER with `valid=false` |

There is no ARM, TAKEOFF, LAND, PX4 message or motor path in Stage 6. The
default ROS topic is deliberately isolated for Gate 6B; an explicit flag is
required before publishing to the Gateway topic in Gate 6C. Stage 7 retains
all deferred multi-person safety validation.

## Gate 6C evidence architecture (preparation only)

Gate 6A/B remain frozen. `live_closed_loop.py` now allocates one UTC run
directory and `RunContext.t0_monotonic_ns` before opening ROS/camera. Each
vision frame retains `frame_id`, `capture_monotonic_ns` and `run_elapsed_ms`;
each 20-Hz Intent publication retains its host monotonic receive/publish
time, ROS stamp, Session, valid bit, reason and input age. The Intent topic
defaults to `/interaction/intent_dry_run`; recording does not change that.

The Stage 5 `DiagnosticRecorder` is reused for MJPG `annotated.avi` and its
frame-index log, nested under the same Run. Stage 6's thin overlay adds
arm skeleton lines, current Intent, Session abbreviation, run time and fresh
PX4 fields. AVI playback FPS is not treated as capture time; every video
frame maps to one `vision_frames.jsonl` entry by `frame_id` and
`capture_monotonic_ns`. `--record` is the formal auto-start path; `R` can
toggle clips interactively.

`Gate6CObserver` is a read-only ROS node in the existing executor. It
records received Intent and type-checked Gateway-input trajectory/offboard
topics to `gateway_events.jsonl`. It discovers actual PX4 topics before
subscribing and records `VehicleLocalPosition` and `VehicleStatus` to
`px4_state.jsonl` using sensor-data-compatible QoS. On the tested PX4
v1.17 bridge, these are `/fmu/out/vehicle_local_position_v1` and
`/fmu/out/vehicle_status_v1`. The observer records publisher node names;
only a unique `/control_gateway` setpoint publisher can support a live
Gateway claim. PX4's original `timestamp` is preserved as
`px4_source_timestamp_us`, but its domain is **not** equated to the host
clock. End-to-end alignment uses host receive/capture monotonic time and
`run_elapsed_ms`, never a fixed FPS or assumed PX4 offset.

`--rosbag` starts the type-checked recording script in a separate process
group and stops it before the logs close. In live mode it requires all
control and PX4 state topic types. The analyzer requires nonempty bag
metadata for every required topic, full-run video coverage, actual
authorized Gesture→Intent→Gateway setpoint→valid PX4 state, and a
post-gesture invalid HOVER plus zero setpoints. Movement uses configurable
delay/window and displacement/median-velocity sign checks in NED; HOVER
requires zero setpoint and low final speed. These numeric cutoffs are
engineering heuristics, **not** PX4/OpenCV official standards. Missing,
synthetic or dry-run evidence yields `INSUFFICIENT_EVIDENCE`, never PASS.

Recorder failure aborts the run and revokes the Stage 6 movement lease;
the existing SIGINT safe HOVER precedes executor shutdown. No Stage 1/3/4/5
source, Gateway parameters, PX4 failsafe or aircraft-control API changed.
Gate 6C aircraft-response validation is not yet started.

## Safety Pilot / Flight Authority gate (Stage 6 only)

Identity Authority is Stage 5's Session/LOCKED_HIGH/Gallery; Flight Authority
is the Safety Pilot's PX4 armed+Offboard+no-failsafe state; Motion Lease is
the short-lived Stage 6 AuthorizedGesture→Intent permission. These are
independent. On the explicit live topic, Stage 6 subscribes to actual
`/fmu/out/vehicle_status_v1`, using the `px4_msgs` message's
`ARMING_STATE_ARMED` and `NAVIGATION_STATE_OFFBOARD` constants (verified on
the NUC as 2 and 14). Status absence/staleness is fail-closed; default
freshness timeout is 1500 ms to cover the measured ~2-Hz status topic.

An Offboard exit, failsafe, disarm or status timeout clears only Stage 6's
lease and immediately emits invalid HOVER on an observed authority edge.
Stage 5 identity/Session/Gallery are untouched. Offboard re-entry sets
`require_fresh_gesture`; the previous legal pose cannot be replayed. Only
two or more trusted frames spanning at least 150 ms with **both** Stage 4
raw and stable labels UNKNOWN, still LOCKED_HIGH in the same Session,
constitute a neutral release. A later new authorized legal gesture can then
create a new lease. Occlusion, INVALID pose, unconfirmed legal poses and a
Session switch do not silently unlock the gate. These 150-ms/two-frame
values are conservative engineering settings, not PX4 standards.

Logs include PX4 armed/nav/failsafe, identity and flight authority,
`motion_lease_active`, `require_fresh_gesture` and transition reason. The
overlay names the three authorities separately. Existing Stage 1 Gateway,
Stage 3/4/5 algorithms, 300-ms vision lease, 20-Hz ROS timer and 500-ms
Gateway timeout remain unchanged. This code change **does not** pass Gate 6C;
the full supervised Gazebo gesture response test must be rerun.
