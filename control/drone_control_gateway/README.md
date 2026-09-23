# drone_control_gateway V1.1

Thin ROS 2 Intent-to-PX4 Offboard velocity adapter. It never arms, takes off,
lands, disarms, or changes flight mode. Those operator actions stay separate.

## Coordinate contract

All output is in the PX4 local **world NED** frame. V1.1 is intentionally not
body-relative:

- `MOVE_FORWARD`: +X / North (world-forward, not body-forward)
- `MOVE_BACKWARD`: -X / South
- `MOVE_RIGHT`: +Y / East
- `MOVE_LEFT`: -Y / West
- `ASCEND`: -Z because NED +Z points down
- `DESCEND`: +Z
- `YAW_RIGHT`: positive NED yaw rate, clockwise viewed from above
- `YAW_LEFT`: negative NED yaw rate, counter-clockwise viewed from above
- `HOVER`: [0, 0, 0] m/s and 0 rad/s yaw rate

Changing vehicle yaw does not rotate forward/backward/left/right. A future
body-relative version must use an explicit yaw rotation and separate tests; it
must not change V1.1 silently.

## PX4 setpoint contract

`OffboardControlMode.velocity=true`; other control flags are false.
`TrajectorySetpoint.velocity` and `yawspeed` are finite. Position, acceleration,
jerk and absolute yaw are NaN. Yaw intents use rate control because they express
a continuous turn direction, not an absolute heading. PX4 v1.17 NED positive yaw
is clockwise viewed from above, so right is positive and left is negative.

The gateway starts in HOVER, rejects invalid/unsupported intents to HOVER,
returns to zero linear velocity and zero yaw rate after `command_timeout_s`, and
clamps both speed and yaw rate. It continuously publishes heartbeat/setpoint
while alive. If it stops, publishing stops too, so PX4's native Offboard-loss
policy remains authoritative.

Default parameters are 0.8 m/s forward/lateral, 0.5 m/s vertical, 0.35 rad/s yaw
rate, with limits of 1.0 m/s horizontal, 0.7 m/s vertical and 0.6 rad/s yaw rate.

## Run and send a test intent

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
ros2 launch drone_control_gateway gateway.launch.py

ros2 topic pub --once /interaction/intent drone_control_gateway/msg/Intent \
  "{intent: MOVE_FORWARD, valid: true, seq: 1, requested_speed_m_s: 0.8}"

ros2 topic pub --once /interaction/intent drone_control_gateway/msg/Intent \
  "{intent: YAW_RIGHT, valid: true, seq: 2, requested_yaw_rate_rad_s: 0.35}"
```

An intent is a short lease, not a latched motion command. Renew it faster than
`command_timeout_s` for continuous motion. A one-shot command automatically
returns to HOVER.
