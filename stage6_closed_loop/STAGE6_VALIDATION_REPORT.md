# Stage 6 validation report — 2026-09-24

**Current state: IN PROGRESS. Stage 6 PASS is not claimed.**

| Gate/check | Evidence and result |
|---|---|
| Gate 6A pure AuthorizedGesture→Intent mapping/lease | **27/27 deterministic tests PASS** on NUC; `compileall` PASS. |
| ROS 2 message transport smoke | **PASS** on isolated reliable `/interaction/intent_dry_run`: observed `MOVE_LEFT valid=true`, then after simulated vision stop `HOVER valid=false`. Gateway topic not used. |
| Real camera + ROS thread empty-scene smoke | **PASS** after fixing log-file shutdown order: 20 Stage 5 frames, 66 ROS Intent events, all `HOVER valid=false`; no PX4/Gazebo movement. Run `runs/20260924_113440_UTC/`. |
| Gate 6B真人 five-gesture and safety dry-run | **PASS** on the isolated `/interaction/intent_dry_run` topic; evidence below. This does not establish aircraft response. |
| Gate 6C Gazebo X500 aircraft-response test | **NOT STARTED**. Observability/recording preparation is in progress; dry-run and observer-only SITL smoke below do not count as Gate 6C flight. |
| Gate 6D safety with aircraft | **NOT STARTED**. |

The first 20-frame camera run exposed an asynchronous cleanup bug: the
ROS timer wrote after its event log was closed while the main process printed
success. The runner now stops and joins the ROS executor **before** closing
logs, propagates timer exceptions into `ERROR_FAIL_CLOSED`, and was retested
as above. The accepted run contains 20 vision JSONL rows and 66 Intent rows;
its final rows explicitly show `VISION_STOPPED` and no valid movement.

Gate 6B真人 evidence: `runs/20260924_115642_UTC/` produced valid LEFT/RIGHT/
ASCEND/DESCEND mappings, invalid HOVER on gesture release, and an
`OPERATOR_LOST` to new-Session auto-reauthorization without stale movement.
The dedicated HOVER supplement `runs/20260924_120423_UTC/` produced 24
authorized HOVER frames and 35 `HOVER valid=true` ROS events; release and
loss reverted to `HOVER valid=false`.

Gate 6B safety closure after the 2026-09-24 fix:

| Check | Result and evidence |
|---|---|
| Input lease timeout | **PASS**. Configured 300 ms, 20-Hz ROS timer, stale at `>=300 ms`. Isolated authorized RIGHT injection: last valid MOVE received 279 ms after input; first `HOVER valid=false / VISION_COMMAND_TIMEOUT` received at 329 ms; no subsequent valid movement. `runs/gate6b_timeout_fixed_mydCBw/` contains the Intent JSONL and ROS-observer record. |
| SIGINT after valid movement | **PASS**. With a test-only Stage 5 authorization injection in the real `live_closed_loop.py` process, both the JSONL and ROS subscriber observed valid `MOVE_RIGHT` followed by final `HOVER valid=false / VISION_STOPPED`. Process exit code 0, summary written, no duplicate ROS shutdown. `runs/gate6b_sigint_fixed/20260924_122835_UTC/`. Injection was runtime-only; Stage 5 code was not modified. |
| Invalid camera path | **PASS**. `runs/20260924_122904_UTC/`: `ERROR_FAIL_CLOSED`, summary states `Cannot open Stage 2 camera`, zero vision frames and zero valid movement, final `HOVER valid=false / VISION_PIPELINE_EXCEPTION`. |
| Stage 6 verification suite | **PASS**. `compileall` clean; 29/29 tests pass, including ROS-topic timeout timing and SIGINT final-message tests. |

These are dry-run Intent and fail-closed tests only. Gate 6C remains NOT
STARTED; Gate 6B PASS does not authorize a real drone or prove Gazebo motion.

Frozen Stage 1/2/3/4/5 algorithms and Gateway behavior were not modified.
## Gate 6C evidence-system preparation — 2026-09-24

`compileall` passed and **51/51 Stage 6 tests passed** on the NUC (22 new
evidence/ROS tests, 29 existing tests). New tests cover monotonic run time,
vision/Intent fields, PX4 serializers, NaN/Inf-as-null diagnostics, the
RIGHT/LEFT/ASCEND/DESCEND NED signs, HOVER zero-setpoint rule, insufficient
evidence, AVI/frame alignment and closure, observer log closure and rosbag
topic checks. The analysis windows and thresholds are configurable
engineering heuristics, not official PX4 pass criteria.

Dry-run recorder smoke `runs/20260924_130549_UTC/`: 20 real camera frames,
20 readable MJPG AVI frames, 20 clip-frame JSONL rows and 20 vision JSONL
rows matched by `frame_id` and capture monotonic timestamp. 133 Intent
messages were observed on `/interaction/intent_dry_run`; PX4 and Gateway
state were genuinely unavailable and the analyzer returned
`INSUFFICIENT_EVIDENCE`.

Dry-run rosbag smoke `runs/20260924_130958_UTC/`: 20 aligned video/vision
frames; rosbag metadata finalized normally and `ros2 bag info` showed
108 messages on `/interaction/intent_dry_run`. No control topic was used.
Synthetic RIGHT smoke
`runs/gate6c_synthetic_right/20260924_131701_UTC/`: 20 aligned frames,
16 valid `MOVE_RIGHT` Intent messages and 16 corresponding observer
receipts, with rosbag present. Its video is visibly marked synthetic and
the vision JSONL carries `test_injection=true`; analysis correctly returned
`INSUFFICIENT_EVIDENCE`, never a真人/aircraft PASS.

PX4/Gazebo **observer-only** smoke `runs/20260924_132136_UTC/`:
`HEADLESS=1 make px4_sitl gz_x500` plus the existing XRCE Agent, with Stage 6
still on `/interaction/intent_dry_run`. Actual discovered topics were
`/fmu/out/vehicle_local_position_v1` and
`/fmu/out/vehicle_status_v1`; the observer logged **403** local-position
and **16** vehicle-status events, with host receive monotonic time and PX4
source timestamp recorded separately. PX4 remained disarmed (`armed=false`,
`nav_state=4` in the inspected sample), no Gateway trajectory publisher
was present, and no visual control reached PX4. The bag contained 293
local-position, 12 status and 113 dry-run Intent messages. Setpoint and
OffboardControlMode topics existed as PX4 inputs but had no publisher or
recorded messages, so the analysis remained `INSUFFICIENT_EVIDENCE`.
The PX4/Gazebo and Agent processes started for this smoke were stopped.

Current formal state: **Gate 6A PASS, Gate 6B PASS, Gate 6C
OBSERVABILITY / RECORDING PREPARATION IN PROGRESS; Gate 6C aircraft response
NOT STARTED.** Before Gateway-topic publication, explicitly schedule a
supervised Gazebo-only Gate 6C; never use a real drone in this stage.

## Gate 6C formal-test preflight attempt — 2026-09-24

The recording/observer preparation is READY, but the formal flight-response
run was stopped at preflight. The NUC camera produced a 2560×960 frame,
the official OSNet checkpoint loaded, ROS Humble/XRCE Agent and Gazebo X500
SITL started, PX4 local-position/status topics had messages, and the existing
Gateway was the sole ROS trajectory-setpoint publisher. Disk free space was
approximately 326 GB. Stage 6 tests remained 51/51 PASS.

The temporary **dry-run only** preflight Run is
`runs/20260924_135943_UTC/`: 20 readable/paired AVI frames, 197 Intent
events, 399 Gateway observer events, 530 PX4 events, and a finalized rosbag.
All 101 observed Gateway trajectory setpoints were zero; the analyzer returned
`INSUFFICIENT_EVIDENCE` because no aircraft-control claim is possible from
`/interaction/intent_dry_run`.

PX4 repeatedly reported `Preflight Fail: No connection to the GCS`.
Human-in-front-of-camera supervision was not yet confirmed. Per the mandatory
preflight stop rule, there was **no ARM, TAKEOFF, Offboard, or live Stage 6
publication to `/interaction/intent`**. The temporary Gateway, PX4/Gazebo,
and Agent were stopped. This is **PREFLIGHT ABORTED**, not a Gate 6C gesture
PASS/FAIL; rerun all checks after GCS and human supervision are available.

## Safety Pilot gate follow-up — 2026-09-24

Stage 6 now gates live Intent by actual fresh PX4 armed/Offboard/no-failsafe
state and a post-entry trusted neutral release. Offboard exit, failsafe,
disarm and status timeout clear its movement lease without touching Stage 5
Session/Gallery. An operator holding RIGHT across re-entry cannot immediately
resume MOVE_RIGHT. A neutral release followed by a new RIGHT can.

The NUC `compileall` passed and **65/65 Stage 6 tests PASS**: 14 new
Safety Pilot tests (13 pure state-machine and one isolated ROS callback/
transport test), plus the previous 51. The ROS test published only to
`/interaction/intent_dry_run`, never to the Gateway. A new camera smoke was
attempted but could not open `/dev/video0` because no `/dev/video*` node
existed on the NUC at that time. Other user-started PX4/Gazebo/Agent/Gateway
processes were detected and left untouched. Gate 6C flight response remains
**NOT STARTED / requires complete revalidation**.
