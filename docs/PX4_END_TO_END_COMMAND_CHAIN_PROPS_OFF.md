# PX4 end-to-end command chain, props-off checkpoint

Date: 2026-09-27. Branch: `rk3576-migration`. This is a **partial, shadow-only** validation. No PX4 input publisher was started, no VehicleCommand was sent, no ARM or mode transition was attempted, and no uORB setpoint receipt was claimed.

## Hardware safety setup

The operator confirmed all propellers removed, the airframe secured, and nearby people clear of rotating parts. PX4 reported `arming_state=1` (DISARMED) during read-only sampling. This confirmation does not authorize a PX4 input test. The requested Phase C requires a separate explicit checkpoint, and the coordinate/authority blockers below must be resolved first.

## ROS 2 and PX4 topics observed

The active serial link was CH340 `/dev/ttyUSB0` with `MicroXRCEAgent` at **921600 baud**. The prior 57600 instance was stopped after a working 921600 session appeared. Actual DDS topics and types:

| Topic | Type | Observed data |
| --- | --- | --- |
| `/fmu/out/vehicle_status_v4` | `px4_msgs/msg/VehicleStatus` | DISARMED, `nav_state=4` = AUTO_LOITER, `failsafe=false`, `accepts_offboard_setpoints=false` |
| `/fmu/out/vehicle_local_position_v1` | `px4_msgs/msg/VehicleLocalPosition` | samples received; `xy_valid=false`, `z_valid=true`, `v_xy_valid=false`, `v_z_valid=true` in sampled interval |
| `/fmu/out/failsafe_flags` | `px4_msgs/msg/FailsafeFlags` | topic and receive timestamp observed |
| `/fmu/out/vehicle_command_ack_v1` | `px4_msgs/msg/VehicleCommandAck` | topic exists; no ACK received in this run |
| `/fmu/in/offboard_control_mode` | `px4_msgs/msg/OffboardControlMode` | DDS topic exists; no FCV publisher in this run |
| `/fmu/in/trajectory_setpoint` | `px4_msgs/msg/TrajectorySetpoint` | DDS topic exists; no FCV publisher in this run |

The message definitions were checked from the installed `px4_msgs` checkout. `VehicleStatus` has `MESSAGE_VERSION=4`; `VehicleLocalPosition` has `MESSAGE_VERSION=1`. A ROS 2 topic appearing in discovery alone is not proof of a fresh sample. The reader uses **host monotonic receive time** to decide freshness.

## Telemetry mapping and Ground Station

`scripts/px4_telemetry_readonly.py` creates only four `/fmu/out/*` subscriptions and writes an atomic local snapshot. `ground_station/px4_telemetry.py` treats VehicleStatus older than **1500 ms**, the Flight Authority default timeout, as disconnected; mode, armed, failsafe, position, and velocity are then null. Position/velocity are shown only when their respective PX4 validity flags are true and the position sample is fresh. The board's existing UDP metadata adds optional `px4` data. The Windows Ground Station uses metadata age plus status age, and clears the PX4 card on timeout; it does not use a ROS 2 connection itself.

The operator confirmed the live Ground Station displayed `CONNECTED`, `AUTO_LOITER`, `Armed False`, `Failsafe False`, and `Local Pos INVALID`. The video/AI fan-out kept one camera and one decoder. Board sampling initially showed about 29 video FPS and 10 AI FPS; after prolonged person/depth activity, both Phase A and Phase B fell to roughly 24–27 video FPS and 3.5–4.6 AI FPS. The drop therefore cannot be attributed to shadow alone. Metadata remained near 20 Hz with no oversized packets (observed max 1352 of 1400 bytes). This hot-state performance remains open.

For an actual stale check, the read-only telemetry process was paused while PX4 and shadow remained running. The reader returned `DISCONNECTED` with `status_age_ms=1512.9`; all cached vehicle values were null. After resuming it returned `CONNECTED` with `status_age_ms=116.7`. This verifies the configured 1500 ms status threshold, with polling granularity about 100 ms. It does not test an XRCE Agent failure or an armed Flight Authority transition.

## Stage6, Flight Authority and Gateway shadow

`--px4-shadow` runs the existing Stage6 `AuthorizedGestureIntentAdapter` through `AuthorizedIntentPublisher` on `/interaction/intent_shadow`, using the existing `FlightAuthorityGate` and the observed `/fmu/out/vehicle_status_v4`. A separate `/interaction/candidate_shadow` publishes the existing Stage6 dry-run decision solely for projected mapping. The Gateway's **same production `IntentMapper`** maps both; only the gated result drives its effective command lease. In `shadow_mode=true`, Gateway never creates either PX4 input publisher. `ros2 node info /control_gateway` listed only `/interaction/candidate_shadow` and `/interaction/intent_shadow` subscriptions, `/parameter_events` and `/rosout` publishers.

The Gateway writes an effective shadow command and a projected candidate to `/tmp/fcv_gateway_shadow_v2.jsonl`. `projected_*` means **if Flight Authority granted**; it is never the transmitted command. The Ground Station labels the mode `SHADOW · NOT TRANSMITTED` and shows effective and projected vectors separately. During this run, PX4 was DISARMED, so Flight Authority blocked movement and effective velocity stayed `[0,0,0]` local NED. The trace can be joined to `/tmp/fcv_px4_e2e_shadow_v2.jsonl` by Stage6 sequence; source frame IDs and operator sessions are in the vision metrics. Copies under the ignored `runs/` directory preserve this checkpoint evidence.

## Control coordinate semantics and blocked direction validation

The current **implementation** in `control/drone_control_gateway/src/intent_mapper.cpp` maps directly into **world local NED**, without any vehicle-heading, camera-to-world, or operator-relative transform:

| Stage6 intent | Gateway projected NED velocity, m/s | Meaning in current code |
| --- | --- | --- |
| HOVER | `[0,0,0]` | zero velocity and yaw rate; not a position-hold setpoint |
| ASCEND | `[0,0,-0.5]` | NED Z points down |
| DESCEND | `[0,0,+0.5]` | NED Z points down |
| MOVE_LEFT | `[0,-0.8,0]` | world West, independent of operator pose |
| MOVE_RIGHT | `[0,+0.8,0]` | world East, independent of operator pose |

The shadow trace observed the four nonzero projections above from real gestures. The current Gateway does **not** implement the requested `MOVE_OPERATOR_LEFT/RIGHT` orbit around the operator or yaw-to-operator behavior. There is no transform to explain relative to the current vehicle attitude. This is a safety-significant mismatch. **Overall direction validation is BLOCKED; do not fly or transmit movement setpoints under an operator-relative interpretation.** The frozen mapper was not silently changed in this validation task.

In the second human run, projected valid samples were ASCEND 13 cycles, DESCEND 35, MOVE_LEFT 5, and MOVE_RIGHT 18. The effective Gateway output was `[0,0,0]` on every recorded cycle because authority remained blocked. Valid HOVER as a recognized gesture was not captured; invalid/safety HOVER and zero output were captured. Operator Lost appeared in the vision metrics and continued to produce invalid HOVER. These counts are trace cycles at 10 Hz, not independent human trials.

The Gateway's setpoint code uses `position=[NaN,NaN,NaN]`, `velocity` as above, `yaw=NaN`, and `yawspeed` from the mapper. `OffboardControlMode` has `velocity=true` and other control flags false. HOVER is zero velocity, not a claim that the vehicle holds a fixed position while disarmed.

## Phase status and evidence

| Phase | Status | Evidence / limit |
| --- | --- | --- |
| A read-only telemetry | PASS for VehicleStatus and UI; position INVALID as PX4 reports | `/tmp/fcv_px4_telemetry.json`, `/tmp/fcv_px4_e2e_phase_a_run2.jsonl`; operator confirmed Windows display |
| B gesture and Gateway shadow | PARTIAL | `/tmp/fcv_px4_e2e_shadow_v2.jsonl`, `/tmp/fcv_gateway_shadow_v2.jsonl`; projected ASCEND, DESCEND, LEFT, RIGHT; effective output blocked; operator-relative mismatch |
| C disarmed props-off transport | NOT STARTED | Separate approval checkpoint not reached; authority requires armed + OFFBOARD, and direction semantics conflict |
| D PX4 uORB cross-check | NOT STARTED | No `/fmu/in/*` data was published; no `listener` evidence exists |
| E live safety gates | PARTIAL | Existing lease/authority tests and shadow HOVER; no live PX4 input timeout or OFFBOARD transition test |

Do not interpret the absence of VehicleCommand ACK as setpoint failure: TrajectorySetpoint has no per-setpoint VehicleCommand ACK. ROS-side publication would establish only board output; a future approved transport test requires a matching PX4 NSH `listener trajectory_setpoint` and `listener offboard_control_mode` capture to establish PX4 internal receipt.

## Reproducible read-only and shadow startup

Source `/opt/ros/jazzy/setup.bash` and `~/fcv_ros_ws/install/setup.bash` on the board. Start one correctly configured MicroXRCEAgent, then:

```bash
python3 scripts/px4_telemetry_readonly.py --output /tmp/fcv_px4_telemetry.json
ros2 run drone_control_gateway control_gateway_node --ros-args \
  -p shadow_mode:=true \
  -p shadow_snapshot_path:=/tmp/fcv_gateway_shadow.json \
  -p shadow_trace_path:=/tmp/fcv_gateway_shadow.jsonl
```

The existing `scripts/run_rk3576_fanout.py` command adds `--px4-telemetry-file /tmp/fcv_px4_telemetry.json --px4-shadow --gateway-shadow-file /tmp/fcv_gateway_shadow.json` to its normal one-camera invocation. It must run with the ROS environment sourced. Its `--pose-model` is the installed RK3576 INT8 model. No live Gateway mode or `/interaction/intent` route is used in this checkpoint.

## Regression

On RK3576: Stage3 30 passed and 1 skipped; Stage4 14 passed plus 5 subtests; Stage5 123 passed; Stage6 81 passed; Gateway 7 passed. On Windows: Ground Station 16 passed, including offscreen PX4 card and shadow command display. `git diff --check` passed. The Stage3 skipped case was not counted as a pass.

## Next flight-safety phase

Before any motion-capable PX4 input: resolve the operator-relative control frame and yaw behavior, verify a valid local position source or define a safe fallback, keep the existing armed/OFFBOARD Flight Authority rule, and define a separately approved disarmed transport experiment. Actual ARM, motor actuation, takeoff, flight limiting, and flight tests remain outside this phase.
