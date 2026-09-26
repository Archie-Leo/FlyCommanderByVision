# RK3576 Stage3–6 human dry-run, 2026-09-26

## Scope and isolation

This run used `/dev/video73`, Run B rectification, a 180° rotation of the analysis image, RKNN pose, Stage4 gesture, Stage5 operator ownership and the existing `AuthorizedGestureIntentAdapter`. The browser mode is `DRY-RUN NO PX4 OUTPUT`. It does not initialize ROS, create an Intent publisher, start the Flight Gateway, or publish any `/fmu/in/*` message. H264 was stopped and the camera had one owner.

Start from `~/FlyCommanderByVision`:

```bash
source scripts/env_rk3576.sh
~/venvs/fcv_stage5/bin/python3 scripts/preview_stage5_web.py \
  --stage6-dry-run --auto-reauthorize --camera /dev/video73 \
  --host 0.0.0.0 --port 8082 \
  --stage6-log-jsonl /tmp/fcv_stage6_human.jsonl
```

Open `http://192.168.1.100:8082/` on the local network. The page and `/status` expose the latest frame, Stage3/4/5 fields, Stage6 decision, lease state, depth and age. The preview retains a single latest analysis frame and single latest JPEG. Stage6 lease ticking is independent of camera and browser activity. Ctrl+C clears the lease, closes the camera and HTTP server.

The existing ROS runner remains `scripts/start_vision_dry_run.sh` with isolated `/interaction/intent_dry_run`. It was audited but was not used in this human run; the browser uses the same Stage6 adapter directly. There was no PX4 output.

## Existing Stage5 → Stage6 contract

Stage5 emits `AuthorizedGestureV1` containing timestamp, frame ID, operator session ID, track ID, authorization state, stable gesture, confidence and validity. Only a fresh valid `LOCKED_HIGH` gesture with a session, integer track ID and one of the legal gestures renews the Stage6 lease. Invalid/unknown input clears it immediately. The Stage4 gesture is generated from the selected Stage5 operator only. The 180° rotation occurs before pose and Stage4; Stage6 does not swap left and right.

| Stable gesture | Existing Stage6 intent |
| --- | --- |
| HOVER | HOVER |
| ASCEND | ASCEND |
| DESCEND | DESCEND |
| LEFT | MOVE_LEFT |
| RIGHT | MOVE_RIGHT |

The real enum names are `MOVE_LEFT` and `MOVE_RIGHT`; this run did not validate future physical operator-relative movement. Intent has no requested speed in the browser. The ROS dry-run publisher, when used separately, sets requested speed and yaw rate to zero.

Stage6's existing vision-command timeout is **300 ms**. Its 20 Hz tick returns invalid HOVER after timeout and does not resurrect the old gesture. The browser labels `interaction_ready` as a **derived diagnostic** equal to current `IntentDecision.valid`; the repository has no separate `interaction_ready` gate or Stage6 safety FSM for this dry-run path. `safety_state` is an adapter acceptance display, not a separate flight safety state. Stage6 does not consume depth or depth age in this path. Stage5 continues to compute ROI stereo depth with its existing 5 Hz / 500 ms settings; stale depth is displayed but does not independently suppress Stage6 intent. This is a design limitation to resolve before live control, not a new gate added during this task.

## Human evidence

Source: `/tmp/fcv_stage6_human_20260926.jsonl` on RK3576. The run covered **563.712 s** of camera frames. The web page and snapshot returned HTTP 200, and the operator confirmed live video. The browser initially showed a transient `ERR_CONNECTION_CLOSED`; a new request returned HTTP 200 and the operator subsequently confirmed the picture. This run's process was stopped and `/dev/video73` and port 8082 were released.

| Case | Result | Evidence / limit |
| --- | --- | --- |
| Initial T-Pose / lock | PASS | `WAIT_OPERATOR → ACQUIRING → LOCKED_HIGH`; no valid movement before lock. |
| HOVER | PASS | Stable HOVER and valid Stage6 HOVER observed. |
| LEFT / RIGHT | PASS mapping | Stable LEFT/RIGHT yielded `MOVE_LEFT`/`MOVE_RIGHT`, 3 periodic samples each. Physical movement semantics untested. |
| ASCEND / DESCEND | PASS mapping | 3 ASCEND and 5 DESCEND periodic valid samples. |
| Release / UNKNOWN / INVALID | PASS safe output | Following each active movement, later records returned invalid HOVER. Logs include raw UNKNOWN and INVALID. The one-second periodic sampling cannot measure exact release latency. |
| Operator lost | PASS safe output | `OPERATOR_LOST` observed with invalid HOVER. |
| Lease timeout | PASS | One `VISION_COMMAND_TIMEOUT` event yielded invalid HOVER; the adapter timeout is 300 ms. |
| Auto reauthorize | INCOMPLETE | The operator ended this round before recovery. Last state `OPERATOR_LOST`, reject reason `AUTO_REAUTH_LOW_CROP_QUALITY`; no claim of successful return. |
| Second-person isolation | NOT RUN | Only the original operator was available. Existing Stage5 two-person testing is prior evidence, not a Stage6 result for this run. |
| Pose invalid / depth stale | PARTIAL | Invalid gesture fails closed in this run and adapter tests; a controlled depth-stale human case was not performed. Depth is not a Stage6 gate in this design. |

The recorded Stage6 wrapper processing latency was mean **0.0539 ms**, P95 **0.0924 ms**, maximum **1.7154 ms** over 611 frame records. A live `/status` snapshot showed whole-loop **10.05 FPS** at that instant. The Stage6 JSONL intentionally logs state changes and periodic samples, so it does not contain a complete per-frame latency series for Pose, ReID, ROI depth, FPS min/mean or precise gesture confirmation and release delays. Those metrics remain unmeasured for this run.

## Regression and cleanup

With `source scripts/env_rk3576.sh` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, separate pytest invocations passed:

- Stage3: 31 passed.
- Stage4: 14 passed, 5 subtests passed.
- Stage5: 123 passed.
- Stage6: 68 passed, including 1 new browser adapter regression.

The new test checks valid movement, invalid-input HOVER and a 300 ms lease timeout. No Stage3, Stage4, Stage5 core, Stage6 core, ReID threshold, stereo algorithm or H264 code was changed. No PX4 live output was used.

## Next validation

Keep the dry-run tool available. When two people are available, repeat the Stage6 second-person isolation case. If auto reauthorization is required for acceptance, repeat return without T-Pose under adequate full-body crop quality and confirm the old movement is not restored. Before live PX4 control, define whether depth and a distinct `interaction_ready` safety gate are required; this dry-run did not add either. The planned next camera architecture remains one producer with AI and H264 branches.
