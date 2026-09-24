# PX4 / ROS 2 SITL deployment — frozen NUC baseline

Verified host: sentinel-S600, Ubuntu 22.04.5 x86_64, ROS 2 Humble,
PX4-Autopilot `v1.17.0`, Gazebo Harmonic 8.15.0, `px4_msgs v1.17.0`,
Micro-XRCE-DDS-Agent `v2.4.2`. PX4, px4_msgs and Agent are **external**;
do not copy their build/install/log trees into this repository.

Fresh ROS workspace: install matching ROS Humble/colcon and clone
`PX4/px4_msgs` at `v1.17.0` into `~/ros2_px4_ws/src/px4_msgs`. Run
`scripts/setup_ros_workspace.sh ~/ros2_px4_ws` to link and build this
repository's `control/drone_control_gateway` and `px4_msgs`. Existing NUC
uses its already-verified Gateway source in that workspace; the setup script
will refuse to overwrite a different target. `Intent.msg` is owned by the
Gateway package; no second custom-message package is used.
Run colcon with the **system Python**, not the Stage 5 virtualenv; otherwise
ROS `rosidl_adapter` may fail to import the system `em` module. The NUC's
native BoT-SORT C++ build found system OpenCV 4.5.4, separate from its
Python `opencv-contrib-python 4.10.0.84` wheel.

In separate terminals, source `/opt/ros/humble/setup.bash` and
`~/ros2_px4_ws/install/setup.bash`, then start:

1. `MicroXRCEAgent udp4 -p 8888`
2. `cd ~/PX4-Autopilot && HEADLESS=1 make px4_sitl gz_x500`
3. `scripts/start_gateway.sh`

Verify actual `/fmu/out/vehicle_local_position_v1` and
`/fmu/out/vehicle_status_v1` messages, one Gateway subscriber on
`/interaction/intent`, and one `/control_gateway` trajectory publisher.
The Gateway sends velocity setpoints with Offboard heartbeat; it does not
auto-arm/take off/land. World NED is frozen: RIGHT +Y, LEFT -Y, ASCEND -Z,
DESCEND +Z. Gateway command timeout is 500 ms, Stage 6 vision lease 300 ms
at 20-Hz publish, and PX4's own Offboard failsafe remains enabled.

For **supervised Gazebo only**, complete preflight and connect
QGroundControl/Safety Pilot before `scripts/start_stage6_live.sh
--confirm-sitl`. Stage 6 defaults to dry-run otherwise. ARM, TAKEOFF,
HOLD/POSITION, OFFBOARD and LAND remain separate manual Safety Pilot/PX4
actions. In the PX4 SITL console the existing flow is `commander arm`,
`commander takeoff`, wait for stable 2–3 m, then `commander mode offboard`;
confirm armed/Offboard/no-failsafe before any gesture. If GCS connection,
status, recorder, topic uniqueness or camera is unavailable, stop preflight.
Never use these commands with real propellers or a physical aircraft.
