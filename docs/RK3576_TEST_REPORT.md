# RK3576 test report — 2026-09-25

All tests below were run on the Taishan Pi RK3576. No aircraft arm, Offboard entry, velocity command, or trajectory setpoint was sent to PX4.

| Area | Result | Evidence |
| --- | --- | --- |
| Environment | PASS with dependency drift | `bash scripts/check_rk3576_environment.sh`: aarch64, Jazzy, px4_msgs, Gateway, Agent, camera node, RKNN runtime present; vision imports NumPy 2.5.3 / OpenCV 5.0.0. |
| Python syntax | PASS | `/usr/bin/python3 -m compileall -q stage3_pose stage4_gesture stage5_operator stage6_closed_loop`. |
| Stage3 | PASS logic / BLOCKED live | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/venvs/fcv/bin/python3 -m pytest -q stage3_pose/tests`: **17 passed**, including camera filter geometry, subprocess cleanup, RK runner paths, and no-LSE guard. No compatible live Pose backend. |
| Stage4 | PASS logic / BLOCKED live | Same pytest invocation for `stage4_gesture/tests`: **14 passed, 5 subtests passed**. No threshold or Unknown Reject change. |
| Stage5 V1 | PASS logic / BLOCKED live input | `stage5_operator/tests/test_stage5.py`: **19 passed**. |
| Stage5 V2 | PASS logic / BLOCKED native/model | Three `test_stage5_v2*.py` files: **78 passed**. Official OSNet checkpoint and native tracker missing. |
| Stage6 core | PASS | `/usr/bin/python3 -m pytest -q` on `test_flight_authority.py`, `test_ros_intent_safety.py`, `test_intent_adapter.py`: **44 passed**. |
| Stage6 Gate6C evidence | PASS mock/dry-run | `bash scripts/test_rk3576_gate6_evidence.sh`: **21 passed**. Test ROS publisher uses `/interaction/intent_dry_run`; no Gateway live run. |
| Gateway build | PASS | `colcon build --packages-select drone_control_gateway --symlink-install`, system Python: one package finished in 1m02s. |
| Gateway unit tests | PASS | `colcon test --packages-select drone_control_gateway`: **6 tests, 0 failures**. Package prefix and executable resolve. |
| Camera left crop | PASS capture | Final 600/600 frames: **56.15 FPS steady** over 10.668 s; **50.80 FPS end-to-end** over 11.812 s including 0.973 s startup and 0.171 s shutdown. One old frame dropped, zero failures; capture thread exited. Prior supplied reference: 55.53 FPS. |
| Camera full stereo | PASS capture | Final 600/600 frames at 2560×960 BGR: **54.33 FPS steady** over 11.025 s; **49.16 FPS end-to-end** over 12.205 s including 0.999 s startup and 0.181 s shutdown. Zero drops/failures; capture thread exited. |
| Camera latest-frame | PASS behavior | Final 30/30 frames with 100 ms simulated consumer delay; IDs 0–126, **97 dropped old frames**, zero failures; capture thread exited. |
| Direct FFmpeg pipe comparison | OBSERVED | Same 640×480 left crop/scale, 600 requested frames to `dd` in 11.427 s (about 52.5 FPS end-to-end). |
| NPU inference | PASS single check | `~/venvs/fcv/bin/python3 scripts/check_rk3576_rknn.py`: one MobileNet inference with shape `(1,224,224,3)` input returned one finite output of shape `(1,1001)`. No throughput measurement. |
| Aircraft/PX4 live control | NOT USED | No Gateway/live pipeline launch, control topic publish, or parameter change. |

One concurrent final sweep measured 354 ms in `test_timeout_received_within_350_ms` and failed that 350 ms timing assertion by 4 ms. The same 44-test suite had passed earlier and passed again when rerun serially (44/44, 3.91 s). No timeout or safety threshold was modified. Treat this as a load-sensitive timing observation; repeat under controlled load before claiming a hard real-time bound.

The default ROS pytest auto-loaded `launch_testing` into the vision venv and failed on missing `yaml`. The commands above set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; the Gate6C runner appends system `dist-packages` after vision packages so ROS can find its system PyYAML without changing the venv or NumPy selection. Running several stage test directories together also caused a `tests` package-name collision; each stage was run separately.

Reproduce camera checks after `source scripts/env_rk3576.sh`:

```bash
~/venvs/fcv/bin/python3 scripts/benchmark_rk3576_camera.py --frames 600
~/venvs/fcv/bin/python3 scripts/benchmark_rk3576_camera.py --eye stereo --output-width 2560 --output-height 960 --frames 600
~/venvs/fcv/bin/python3 scripts/benchmark_rk3576_camera.py --frames 30 --consumer-sleep 0.1
```

Initial measurements before the shutdown fix included an approximately 2-second FFmpeg wait and misleading 22–43 FPS end-to-end values. Increasing Python pipe buffering did not help and was reverted. The benchmark reports both steady and end-to-end FPS; its CPU-seconds field measures only the Python process. Camera timestamps are host receipt times.
