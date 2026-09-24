# Reproduce the frozen NUC baseline from this repository

Target baseline: Ubuntu 22.04.5 x86_64, Python 3.10.12, ROS 2 Humble,
Gazebo Harmonic 8.15.0, PX4-Autopilot v1.17.0, px4_msgs v1.17.0,
Micro-XRCE-DDS-Agent v2.4.2. Keep system ROS Python separate from the
visual-runtime venv. See `docs/PX4_SETUP.md` for safety and SITL details.

1. Install the listed OS/ROS/Gazebo dependencies from their official
   distributions. Clone this private repository. Clone external
   PX4-Autopilot at tag `v1.17.0`, px4_msgs at tag `v1.17.0`, and Agent at
   tag `v2.4.2` into their own directories. Do not vendor those repositories.
   With no Python venv active, `scripts/setup_ros_workspace.sh` checks the
   px4_msgs tag and builds the Gateway. `control/drone_control_gateway`
   contains the sole custom `Intent.msg`.
2. Place external BoxMOT and deep-person-reid under
   `${DRONE_REF_ROOT:-$HOME/drone_vision_refs/operator_lock}`. Checkout
   BoxMOT `857628343860db1ea48ea50db7a73c25b7a3be13` and
   deep-person-reid `f8cd150fdf77e8d9e1ed143b7f308c2c609ded50`.
   `scripts/build_boxmot_native.sh` checks BoxMOT is clean and builds its
   C++ BoT-SORT library into the ignored `stage5_operator/build/` directory.
   Review AGPL-3.0 obligations before redistribution.
3. Create `~/venvs/drone_stage5_v2` with Python 3.10. Install the official
   CPU PyTorch `2.5.1+cpu` wheel from
   `https://download.pytorch.org/whl/cpu`, then `pip install -r
   requirements-nuc.txt`. The NUC-observed Python versions are in that
   file; NumPy must remain 1.26.4 for this baseline. ROS's rclpy and
   generated px4_msgs/Gateway Python bindings come from sourced ROS workspaces.
4. Activate that venv, install `gdown==6.4.0` if model download is needed,
   and run `scripts/download_models.sh`. It verifies exact upstream SHA-256
   before placing the ignored MediaPipe and OSNet binaries. No model weight
   is in Git. The verified Run B calibration is
   `configs/calibration/run_b.yaml`.
5. Source `/opt/ros/humble/setup.bash` and the ROS workspace install, then
   run `python -m compileall -q stage2_stereo stage3_pose stage4_gesture
   stage5_operator stage6_closed_loop`. Set `PYTHONPATH` to those Stage
   directories and run `python -m unittest discover -s <stage>/tests -q`
   for Stages 3–6. On the frozen NUC: 12/14/97/65 tests passed respectively;
   the separate Gateway colcon package build and six C++ tests passed.
6. With `/dev/video0` attached, run `scripts/check_environment.sh --dry-run`
   and `scripts/start_stage6_dry_run.sh`. It always publishes to isolated
   `/interaction/intent_dry_run`. For supervised Gazebo only, use the
   explicit `scripts/start_stage6_live.sh --confirm-sitl` after all
   Preflight checks and a Safety Pilot/QGroundControl are available.

The current source paths and hashes are frozen, but this is **not** an RK3576
binary package, real-aircraft approval, model redistribution clearance, or
Gate 6C aircraft-response PASS. Keep runs, videos, bags, ULogs, datasets,
build/install/log and venv directories out of Git.
