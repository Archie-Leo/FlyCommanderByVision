# RK3576 test report — 2026-09-25

All tests below were run on the Taishan Pi RK3576. No aircraft arm, Offboard entry, velocity command, or trajectory setpoint was sent to PX4.

| Area | Result | Evidence |
| --- | --- | --- |
| Environment | PASS with dependency drift | `bash scripts/check_rk3576_environment.sh`: aarch64, Jazzy, px4_msgs, Gateway, Agent, camera node, RKNN runtime present; vision imports NumPy 2.5.3 / OpenCV 5.0.0. |
| Python syntax | PASS | `/usr/bin/python3 -m compileall -q stage3_pose stage4_gesture stage5_operator stage6_closed_loop`. |
| Stage3 | PASS logic / BLOCKED live | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/venvs/fcv/bin/python3 -m pytest -q stage3_pose/tests`: **16 passed**, including camera filter geometry, subprocess cleanup, and no-LSE guard. No compatible live Pose backend. |
| Stage4 | PASS logic / BLOCKED live | Same pytest invocation for `stage4_gesture/tests`: **14 passed, 5 subtests passed**. No threshold or Unknown Reject change. |
| Stage5 V1 | PASS logic / BLOCKED live input | `stage5_operator/tests/test_stage5.py`: **19 passed**. |
| Stage5 V2 | PASS logic / BLOCKED native/model | Three `test_stage5_v2*.py` files: **78 passed**. Official OSNet checkpoint and native tracker missing. |
| Stage6 core | PASS | `/usr/bin/python3 -m pytest -q` on `test_flight_authority.py`, `test_ros_intent_safety.py`, `test_intent_adapter.py`: **44 passed**. |
| Stage6 Gate6C evidence | PASS mock/dry-run | `bash scripts/test_rk3576_gate6_evidence.sh`: **21 passed**. Test ROS publisher uses `/interaction/intent_dry_run`; no Gateway live run. |
| Gateway build | PASS | `colcon build --packages-select drone_control_gateway --symlink-install`, system Python: one package finished in 1m02s. |
| Gateway unit tests | PASS | `colcon test --packages-select drone_control_gateway`: **6 tests, 0 failures**. Package prefix and executable resolve. |
| Camera left crop | PARTIAL performance | 600/600 frames, 13.960 s, **42.98 FPS**, zero failure/drop. Re-run with larger pipe buffer: 600/600, 14.189 s, **42.29 FPS**, eight dropped. Prior reported 55.53 FPS not met. |
| Camera full stereo | PARTIAL performance | 120/120 frames, 5.323 s, **22.54 FPS**, zero failure/drop, 2560×960 BGR. |
| Camera latest-frame | PASS behavior | 30/30 frames with 100 ms simulated consumer delay; IDs 0–166, **137 dropped old frames**, zero failures. |
| Direct FFmpeg pipe comparison | OBSERVED | Same 640×480 left crop/scale, 600 requested frames to `dd` in 11.427 s (about 52.5 FPS); this isolates extra cost in the Python adapter path. |
| NPU inference | PASS single check | `~/venvs/fcv/bin/python3 scripts/check_rk3576_rknn.py`: one MobileNet inference with shape `(1,224,224,3)` input returned one finite output of shape `(1,1001)`. No throughput measurement. |
| Aircraft/PX4 live control | NOT USED | No Gateway/live pipeline launch, control topic publish, or parameter change. |

One concurrent final sweep measured 354 ms in `test_timeout_received_within_350_ms` and failed that 350 ms timing assertion by 4 ms. The same 44-test suite had passed earlier and passed again when rerun serially (44/44, 3.91 s). No timeout or safety threshold was modified. Treat this as a load-sensitive timing observation; repeat under controlled load before claiming a hard real-time bound.

The default ROS pytest auto-loaded `launch_testing` into the vision venv and failed on missing `yaml`. The commands above set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; the Gate6C runner appends system `dist-packages` after vision packages so ROS can find its system PyYAML without changing the venv or NumPy selection. Running several stage test directories together also caused a `tests` package-name collision; each stage was run separately.

Reproduce camera checks after `source scripts/env_rk3576.sh`:

```bash
~/venvs/fcv/bin/python3 scripts/benchmark_rk3576_camera.py --frames 600
~/venvs/fcv/bin/python3 scripts/benchmark_rk3576_camera.py --eye stereo --output-width 2560 --output-height 960 --frames 120
~/venvs/fcv/bin/python3 scripts/benchmark_rk3576_camera.py --frames 30 --consumer-sleep 0.1
```

The Python benchmark includes FFmpeg startup and shutdown in elapsed time. Its CPU-seconds field measures only the Python process, not FFmpeg. Camera timestamps are host receipt times.
