# RK3576 production autostart

This stack starts the existing single-camera H.264 and Stage3–6 fanout, PX4
MicroXRCE Agent, read-only telemetry, and guarded LIVE Gateway. It never arms,
changes PX4 mode, or creates a VehicleCommand publisher. The disabled ARM,
TAKEOFF, LAND and RETURN buttons in Ground Station remain display-only.

## Install and operate

On the RK3576, with props removed and the aircraft secured:

```bash
cd /home/lckfb/FlyCommanderByVision
bash scripts/install_fcv_autostart.sh
fcvctl start
fcvctl status
fcvctl check
```

The installer enables `fcv-stack.target` for the next boot. It does not start
the stack by itself. Start/stop/restart it with `fcvctl start`, `fcvctl stop`
and `fcvctl restart`. Use `fcvctl logs -n 100` to inspect all services;
`fcvctl vision`, `fcvctl gateway`, and `fcvctl xrce` select relevant logs.
`bash scripts/check_fcv_live_stack.sh` runs the same read-only health check.
The current JSON health report is `/tmp/fcv_system_status.json`.

Open Windows Ground Station before powering the aircraft with
`powershell -NoProfile -ExecutionPolicy Bypass -File "D:\FlyCommanderByVision\scripts\start_ground_station.ps1"`.
The board sends H.264 RTP to the configured `GROUND_HOST` on UDP 5600 and
metadata on UDP 5603. At deployment, Windows was `192.168.1.7`; check the
current Windows IPv4 address if reception stops after a network change.

## Configuration and dependency chain

All adjustable destinations and device paths are in `/etc/fcv/fcv.env`.
Edit `GROUND_HOST` there to change the Ground Station IP, then run
`fcvctl restart`. The checked-in template is `deploy/fcv/fcv.env.example`.
The camera uses `/dev/v4l/by-id/usb-Generic_USB_Camera_20000001-video-index0`
and is verified as Realtek 0bda:5883, MJPG 2560x960 at 60 FPS before the
resolved `/dev/videoN` reaches GStreamer. CH340 uses
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`, verified as 1a86:7523.
If a device is absent or has changed identity, the relevant service retries.

`fcv-stack.target` starts Agent → fresh PX4 DDS → telemetry and runtime.
Runtime verifies the Ground Station route, camera, models, and calibration,
then starts LIVE Gateway and checks its ROS endpoints and `shadow_mode=false`
before starting the one-camera fanout. Gateway and vision share a supervisor:
if either exits, both stop and restart so a previous Stage6 session cannot
resume. Services retry every five seconds on failure. `fcv-health.service`
reports every ten seconds without publishing control messages.

The Gateway publishes `/fmu/in/offboard_control_mode` and
`/fmu/in/trajectory_setpoint` at zero/HOVER while DISARMED. Stage6 publishes
guarded intents to `/interaction/intent`. Real movement remains gated by PX4
ARMED+OFFBOARD and the existing Stage6 FlightAuthority and DemoSafetyLimiter.
The limits remain 0.30 m/s horizontal, 0.20 m/s vertical, and 0.50 m per
episode. `fcvctl check` reads current Gateway parameters and a LIVE snapshot;
it reports PARTIAL if evidence is missing. `READY` does not grant flight
authority.

## Troubleshooting and manual recovery

If video or metadata is missing, check `fcvctl check` and
`fcvctl logs -n 200` first. `GROUND_HOST` must have a route on the board.
The health check confirms sending, not Windows receipt. Camera failure is
reported if the stable symlink no longer provides the specified MJPG mode or
if another process owns it. Agent failure is reported if the serial symlink
is absent or PX4 topics are stale. A PARTIAL health report blocks any claim
of system readiness; it does not change PX4 mode.

To stop automatic startup and return to manual operation:

```bash
fcvctl stop
sudo systemctl disable fcv-stack.target
```

For full removal, run `bash scripts/uninstall_fcv_autostart.sh`. It preserves
`/etc/fcv/fcv.env` and does not delete logs or user data. After stopping the
stack, the existing manual commands remain available; do not run manual Agent,
Gateway or camera fanout alongside this stack.
