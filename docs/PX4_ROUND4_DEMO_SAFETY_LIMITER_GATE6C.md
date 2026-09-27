# Round 4 — Demo Safety Limiter / Gate 6C

## Scope and architecture

Software and shadow validation only. No ARM, OFFBOARD, motors, takeoff, real flight, or live human movement output to PX4 was performed.

The production path remains Stage3 pose → Stage4 gesture → Stage5 operator authorization → Stage6 intent and 300 ms vision lease → FlightAuthority → Gateway IntentMapper → **DemoSafetyLimiter** → PX4 velocity setpoint. Stage3–6 algorithms, authorization thresholds, 300 ms lease, FlightAuthority, calibration, and body/camera left–right mapping are unchanged. The Gateway also checks fresh PX4 VehicleStatus directly before allowing any nonzero motion. Its shadow mode creates no `/fmu/in/*` publishers.

`IntentMapper` still maps an accepted intent into local NED velocity. The limiter then caps that vector and measures one continuous directional gesture episode using read-only PX4 VehicleLocalPosition. It rejects every other movement intent in the demo path. The independent shadow candidate projection uses the same limiter with an explicit hypothetical authority context; this does not fake PX4 status in production or transmit a setpoint.

## Episode state and reset semantics

States: `IDLE`, `ACTIVE`, `LIMIT_REACHED`, and `BLOCKED_INVALID_POSITION`. The limiter tracks episode ID, intent, start position, start time, last valid position receipt, current distance, limit, velocity cap, and reason. Lease renewals and repeated identical gesture messages retain the same episode. A missing processed frame does not reset it while Stage6's 300 ms lease is still live.

An episode starts only for a valid `MOVE_LEFT`, `MOVE_RIGHT`, `ASCEND`, or `DESCEND` from a nonzero operator session with authority and a fresh valid position. A trusted `HOVER`, release/unknown/invalid Stage6 output, gesture switch, session change, vision timeout, authority loss, stale PX4 status, OFFBOARD exit, or disarm closes the active episode. After a **distance or invalid-position block**, holding the same gesture cannot restart motion. A new trusted neutral release or a different confirmed directional gesture establishes a new baseline. After an authority loss, an old held gesture is inhibited until Stage6 emits its existing `FRESH_GESTURE_READY` event following at least two trusted neutral frames over at least 150 ms. The limiter does not implement or weaken that Stage6 rule.

## Distance, validity, and caps

The initial configurable values in `control/drone_control_gateway/config/gateway.yaml` are:

| Limit | Value |
| --- | ---: |
| Horizontal velocity magnitude | 0.30 m/s |
| Vertical velocity magnitude | 0.20 m/s |
| Continuous directional episode displacement | 0.50 m |
| Local-position receipt freshness | 500 ms |

Left/right uses `hypot(x_current-x_start, y_current-y_start)`. Ascend/descend uses `abs(z_current-z_start)` in PX4 local NED; negative Z is up. The limiter does not integrate commanded velocity. XY or Z validity must match the direction; nonfinite, missing, source-stale, or receipt-stale position immediately produces zero velocity and `BLOCKED_INVALID_POSITION`. At measured displacement `>=0.50 m`, the output is HOVER with `LIMIT_REACHED` and `EPISODE_DISTANCE_LIMIT`. Repeated held commands remain HOVER until a reset transition. Cardinal and 45-degree headings preserve the original body-to-NED direction and cap the horizontal vector norm.

This software gate stops commanding motion when a fresh measured sample reaches the threshold. It cannot guarantee a physical stopping distance of exactly 0.50 m because PX4 sampling, transport, actuator response, and vehicle inertia may add travel. A controlled low-altitude flight must validate physical overshoot separately after the remaining transport gate is closed.

## Shadow evidence

Gateway shadow JSON contains `safety_limiter_state`, `episode_id`, `episode_intent`, `episode_distance_m`, `episode_limit_m`, `velocity_cap_mps`, `limit_reached`, and `limiter_reason` for the hypothetical authorized candidate. It also retains effective HOVER and `transmitted=false`. The ground station transports five compact limiter values to stay inside its 1400-byte UDP packet limit; the full named fields remain in the board trace. If optional limiter values would overflow a packet, the sender removes only those values and preserves the existing metadata stream. Competition Mode shows a concise `SAFE HOVER / DISTANCE LIMIT` when the current packet includes the limit state; Engineering exposes episode values. Stale metadata cannot retain a limit claim.

## Gate 6C deterministic tests and regressions

On RK3576, the package was built in separate `/tmp/fcv_r4_*` build/install/log directories; the installed production Gateway was not launched or replaced. `colcon test-result` reported **20 tests, 0 errors, 0 failures**. The limiter tests cover both lateral directions, measured 0.10–0.40 m progression, 0.50 m stop, repeated held gesture, neutral release and new baseline, direction switch, both vertical NED directions, invalid/stale/NaN position, horizontal/vertical caps at 0°, 45°, and 90°, authority exit/re-entry, Stage6 trusted neutral event, session change, timeout, operator loss, and initial empty shadow state. Existing Stage6 tests cover the unchanged 300 ms lease and FlightAuthority fresh-neutral rule.

An isolated `ROS_DOMAIN_ID=197` shadow runtime smoke test confirmed a projected NED vector `[0,-0.3,0]` m/s, measured distance 0.4 m while active, `LIMIT_REACHED` and zero velocity at 0.5 m, no restart while held in episode 1, and a new episode 2 after HOVER release. The isolated graph had zero `/fmu/in/offboard_control_mode` and `/fmu/in/trajectory_setpoint` publishers. The temporary shadow process was stopped afterward.

| Regression | Result |
| --- | --- |
| Stage3 | 31 passed |
| Stage4 | 14 passed, 5 subtests passed |
| Stage5 | 123 passed |
| Stage6 | 81 passed |
| Gateway/limiter | 20 passed, 0 failures |
| Ground Station including Qt and packet-size fallback | 18 passed |

Tests were run without starting a real PX4 input publisher. The Python suite needed `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` because the vision venv lacks the optional ROS launch-testing dependency `lark`; the tests then completed. Ground Station Qt tests used the existing Windows LibreOffice Python and existing PySide6 target directory.

## Round 3 remaining limitation and flight prerequisites

The Round 4 request reports **OffboardControlMode ROS 2 → PX4 uORB transport PASS**. The exact `TrajectorySetpoint` sentinel inside PX4 uORB remains **NOT YET PROVEN**. Round 4 does not test or close that gap and does not establish full end-to-end or flight readiness.

Before any first controlled low-altitude flight: prove the exact trajectory sentinel in PX4 uORB under the agreed disarmed props-off procedure; then independently verify position estimator quality, end-to-end authority and safety gates, command sign and magnitude, measured stopping distance/overshoot, manual takeover, and on-site flight safety approval. No next phase starts automatically.
