# PX4 Round 1: operator lateral direction, shadow only

## Scope and result

Baseline: `rk3576-migration` at `38cee10246a0665853da7f6ca967a5536251f5cc` (clean). Round 1 changes the production Gateway's LEFT/RIGHT coordinate mapping. All runtime checks used `shadow_mode:=true`. No PX4 input topic was published, and no ARM or OFFBOARD command was sent.

The old mapper assigned `MOVE_RIGHT` to fixed local NED East `+0.8 m/s` and `MOVE_LEFT` to fixed West `-0.8 m/s`, regardless of vehicle heading. That was inconsistent with the interaction protocol.

## Frozen semantics and frame conversion

The operator faces the front camera and vehicle. Thus Stage6 `MOVE_RIGHT` means operator right, which is vehicle BODY LEFT; `MOVE_LEFT` means vehicle BODY RIGHT. This is a semantic definition, independent of image pixel axes or shoulder coordinates.

Installed `px4_msgs/msg/VehicleLocalPosition.msg` describes `heading` as Euler yaw relative to local NED, in `[-pi, pi]`, and exposes `heading_good_for_control`. The same message defines local X North, Y East, Z Down. PX4 body is FRD (X forward, Y right, Z down). With `psi=0` facing North and `psi=+pi/2` facing East:

| Intent | Body direction | Local NED horizontal velocity at speed `v` |
| --- | --- | --- |
| `MOVE_RIGHT` | LEFT | `[v sin(psi), -v cos(psi)]` |
| `MOVE_LEFT` | RIGHT | `[-v sin(psi), v cos(psi)]` |

Nominal lateral speed remains `0.8 m/s`. No orbit or yaw-facing controller was added; LEFT/RIGHT yaw speed remains zero. `ASCEND=[0,0,-0.5]`, `DESCEND=[0,0,+0.5]`, and `HOVER=[0,0,0]` remain unchanged.

The stereo camera is physically upside down. The analysis pipeline rectifies the left image, then applies `cv2.ROTATE_180` before Pose/Gesture processing (`scripts/run_rk3576_fanout.py`). The video encoder also uses `rotation=180` for display. Consequently, raw image-left is not used as body-left; the corrected view supports gesture recognition, while navigation direction comes from the fixed operator-facing-body semantic above. No calibration was changed.

## Heading safety

The Gateway subscribes read-only to `/fmu/out/vehicle_local_position_v1`. Lateral mapping is accepted only when the PX4 `heading_good_for_control` flag is true, heading is finite, the source `timestamp_sample` is within 1.5 s of board system time (allowing at most 0.25 s future skew), and a sample was received within 1.5 s. Otherwise it returns invalid `HOVER` with zero velocity and `HEADING_INVALID` or `HEADING_STALE`. An already accepted lateral command is recalculated against the current heading on every publish cycle; loss of heading revokes its lease. Vertical commands do not acquire a new heading requirement. FlightAuthority is unchanged.

Live PX4 telemetry on 2026-09-27 reported `heading_good_for_control=false`. Both injected shadow-only lateral candidates produced `HEADING_INVALID`, `projected_valid=false`, zero projected and effective velocity, `transmitted=false`, and `ros_published=false`. The running shadow Gateway's ROS graph showed a `/fmu/out/vehicle_local_position_v1` subscription and no `/fmu/in/*` publisher.

## Deterministic mapping matrix

All values are local NED `[North, East, Down]` m/s with synthetic **valid** headings:

| Heading | `MOVE_RIGHT` / BODY LEFT | `MOVE_LEFT` / BODY RIGHT |
| --- | --- | --- |
| 0° | `[0,-0.8,0]` | `[0,+0.8,0]` |
| +90° | `[+0.8,0,0]` | `[-0.8,0,0]` |
| 180° | `[0,+0.8,0]` | `[0,-0.8,0]` |
| -90° | `[-0.8,0,0]` | `[+0.8,0,0]` |
| 45° | `[+0.566,-0.566,0]` | `[-0.566,+0.566,0]` |

The shared production `IntentMapper` is used for shadow and live modes. Its tests verify each row with floating point tolerance, norm `0.8 m/s`, LEFT equal to negative RIGHT, invalid and stale heading fail closed, and unchanged vertical signs. No heading zero fallback exists.

## Changed code and regressions

- `control/drone_control_gateway/include/drone_control_gateway/intent_mapper.hpp`: explicit control-frame context.
- `control/drone_control_gateway/src/intent_mapper.cpp`: body lateral to local NED transform and heading rejection.
- `control/drone_control_gateway/src/control_gateway_node.cpp`: read-only PX4 heading subscription, sample age checks, lease revalidation, shadow trace fields.
- `control/drone_control_gateway/test/test_intent_mapper.cpp`: direction matrix and heading failure cases.

Regression results: Stage3 30 passed, 1 skipped; Stage4 14 passed and 5 subtests; Stage5 123 passed; Stage6 81 passed on retry after a 353 ms timing result under concurrent compilation exceeded its 350 ms threshold by 3 ms; Gateway 8 passed; Ground Station 16 passed. The first Stage6 run was not counted as a pass. `git diff --check` passed.

## Limits

This is **not** operator orbit control, live PX4 command transport validation, or flight validation. Round 1 does not prove continuous yaw-facing behavior. Current PX4 heading remains invalid for control, so real lateral commands correctly stay blocked; investigation of XY, VXY, and heading validity belongs to Round 2.
