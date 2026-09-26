# RK3576 heterogeneous runtime V2, Phase 1

## Scope and outcome

This phase changes the scheduling of the RK3576 AI branch. The single-camera MPP H.264 video branch, Stage3–6 decision thresholds, calibration, models, stereo SGBM settings, and PX4 output are unchanged. All runs below were Stage6 dry-run only.

**Result: partial.** The three requested architecture tasks are complete: GStreamer AI callback decoupling, asynchronous tracked ROI depth, and a Pose/ReID NPU contention benchmark. In the six-minute concurrent human run the AI rate was 10.33 FPS (10.38 excluding startup), above the prior 9.89 FPS AI+video baseline but below the 12 FPS minimum target. The `LOCKED_HIGH` 5-second windows averaged 10.32 FPS, above the previous 8.18 FPS human baseline but also below 12 FPS. Video stayed at 29.11 FPS in steady windows. The Phase 1 work stops here rather than changing recognition or safety semantics to chase a rate target.

## Baseline and critical-path audit

V1 baseline: video 29.37 FPS, AI only 11.11 FPS, AI+video 9.89 FPS, human AI 8.18 FPS; `q_ai:src` used 98.53% of one CPU core; NPU reported `100@950000000Hz`; temperature mean 83.96 C, maximum 85.92 C. The first audit corrected a misleading assumption: `q_ai:src` mostly performed the GStreamer NV12-to-BGR `videoconvert` (mean 92.06 ms, P95 96.93 ms). The Python appsink path then pulled and copied the sample, split the stereo image, rectified and rotated LEFT, ran Pose, Stage5/Stage4, optional synchronous ROI depth, and submitted to Stage6. The Stage6 lease clock was already a separate approximately 20 Hz thread.

Phase A, 95-second audit (`/tmp/fcv_v2_phase_a_20260926.jsonl`): video 29.36 FPS, AI 10.8 FPS, full SBS copy mean/P95 6.28/13.52 ms; RIGHT copy 1.86/2.39 ms; LEFT rectify 10.93/13.21 ms; rotate 3.88/6.24 ms; native Pose preprocess/infer/decode 3.16/21.32/0.12 ms (total 25.08 ms); Stage5 ReID section 14.62 ms, tracking 5.85 ms, synchronous depth section 17.18 ms (P95 58.54 ms), Stage4 authorization 0.08 ms. Input age mean 129.77 ms, output age 213.61 ms. OpenCV reported eight threads. These are measurements of different sections; do not add them as if they were an independent end-to-end timeline.

## V2 thread and ownership model

```text
camera -> one MJPEG decoder -> NV12 tee
                              +-> frozen LEFT rotate/H.264/RTP video branch
                              +-> q_ai appsink callback -> LatestFrameSlot (one Gst.Sample)
                                                    -> Perception Worker
                                                       NV12-to-BGR, split,
                                                       LEFT rectify/rotate,
                                                       Pose, Stage5/Stage4
                                                       -> Stage6 dry-run submit
                                                       -> latest tracked ROI depth task
                                                             -> one Depth Worker
Stage6 independent 20 Hz lease clock --------------------------> timeout HOVER
```

The callback retains a ref-counted `Gst.Sample`, replaces any unconsumed sample, and returns immediately. The one-slot queue cannot grow. The worker owns the conversion/copy and the existing perception flow. Processed `frame_id` advances once per processed observation; original `source_frame_id` and timestamps are logged separately, so dropped camera frames cannot count as extra Stage4 Temporal FSM observations. The video branch continues to use the same decoder, caps, rotation, MPP encoder, RTP path, 1280x960, 30 FPS target, and 4 Mbps target.

The depth worker also has only one pending task. It runs the existing rectification, ROI and SGBM implementation with a 5 Hz per-track scheduling target and 500 ms freshness limit. Results carry track ID, source frame and time, completion time, depth validity/quality, and operator session/epoch identity. Stage5 accepts a result only for the current matching track and session while fresh; a vanished track, changed candidate/session/epoch, stale result, or failed worker yields invalid depth instead of reusing an old person's measurement. No Perception call waits for SGBM completion. The independent Stage6 lease thread is unchanged.

Phase B (callback/worker only, 90 seconds): AI 9.00 FPS, video 29.07 FPS, mean output age 126.48 ms; callback CPU 2.82% of one core, Perception Worker 68.99%. This step alone reduced callback occupancy and frame age, but decreased throughput. Phase C (async depth, 90 seconds): AI 11.09 FPS, video 29.01 FPS, mean output age 106.59 ms; Stage5 depth section mean/P95 0.14/0.20 ms. The depth worker completed 210 tasks. Bounded-slot, stale-result, identity-switch, shutdown/failure tests passed.

## NPU contention and selection

The board uses RKNN runtime 2.3.0 for ReID, native Pose runtime 2.3.2, driver 0.9.8. The installed RKNN headers and RKNNLite support `RKNN_NPU_CORE_0` and `RKNN_NPU_CORE_1`. Optional core-mask arguments were added to the native Pose and ReID constructors for benchmarking; both default to AUTO, preserving production behavior. The fixed-input benchmark used the existing RK3576 Pose and OSNet models, real-person crop fixture, and 250 inferences per phase (`/tmp/fcv_v2_npu_contention.json`). The NPU monitor reported `100@950000000Hz` throughout, a device-level aggregate that does not identify per-core occupancy.

| Run | Pose infer mean/P95 ms | Pose total mean/P95 ms | ReID infer mean/P95 ms | ReID total mean/P95 ms | Combined throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pose only, AUTO | 23.65 / 29.77 | 30.37 / 37.73 | — | — | — |
| ReID only, AUTO | — | — | 9.45 / 11.05 | 15.00 / 16.81 | — |
| Concurrent, AUTO | 26.13 / 30.20 | 32.79 / 37.92 | 8.74 / 10.56 | 14.48 / 16.51 | 59.19 ops/s |
| Concurrent, Pose core 0 and ReID core 1 | 25.61 / 28.54 | 31.83 / 36.40 | 7.96 / 9.10 | 13.61 / 15.25 | 60.67 ops/s |

Concurrent AUTO Pose inference slowed by about 10.5% versus Pose-only. Split core improved combined throughput by 2.5%, below the requested greater-than-10% adoption threshold. **Selected policy: AUTO.** No production mask is set. The benchmark is a controlled model-level comparison, not a proof of a whole-camera FPS gain.

## Copy and rectify audit

The V2 worker receives NV12 and converts it with OpenCV, bypassing the previous GStreamer full-frame `videoconvert`. For the same NV12 sample, OpenCV and GStreamer BGR output differed by mean absolute channel value about 2.3 and P95 about 5; they are close but not pixel identical. The human recognition regression below is therefore essential evidence, and exact pixel equivalence is not claimed. An experiment removing the explicit owned full-SBS NumPy copy gave about 11.0 versus 11.09 FPS and was reverted, retaining safe buffer ownership. DMA-BUF, RGA zero-copy, and model changes were not attempted.

Final per-frame profile: NV12-to-BGR mean/P95 4.69/9.92 ms, owned full-SBS copy 6.42/7.86 ms, RIGHT copy 1.79/2.85 ms, LEFT rectify 19.61/49.13 ms, rotate 3.70/5.61 ms. Depth-worker RIGHT rectify mean/P95 9.65/15.28 ms, ROI preparation 15.70/25.19 ms, SGBM matcher 71.96/129.20 ms, aggregation 8.95/15.11 ms, total 96.67/158.06 ms. `cv2.getNumThreads()` was 8; no thread count or CPU affinity policy was changed. Cluster-level affinity A/B was optional and not run; the Linux default scheduler remains selected.

## Six-minute concurrent run, 26 September 2026

Command used on the board (with `source scripts/env_rk3576.sh`):

```bash
~/venvs/fcv_stage5/bin/python3 scripts/run_rk3576_fanout.py \
  --camera /dev/video73 --camera-fps 60 --video-fps 30 \
  --host 192.168.1.16 --port 5600 --bitrate-kbps 4000 \
  --auto-reauthorize --async-perception --async-depth --duration 360 \
  --metrics-jsonl /tmp/fcv_v2_final_20260926.jsonl \
  --stage6-jsonl /tmp/fcv_v2_final_stage6_20260926.jsonl
```

Run completed without pipeline error: one camera open, one MJPEG decoder, 20,881 decoded frames, 10,454 encoded video frames, 3,719 AI frames, elapsed 360.14 s. Video overall 29.03 FPS; steady 5-second windows after startup mean 29.11 FPS, minimum 28.57 FPS. AI overall 10.33 FPS; 5-second window mean 10.33 FPS (steady mean 10.38, minimum 7.40 during operator loss). `LOCKED_HIGH` window mean 10.32 FPS. Compared with the prior 9.89 FPS AI+video baseline, overall speedup is about 1.04x; compared with the prior 8.18 FPS human baseline, locked-window improvement is about 1.26x. These baselines came from earlier runs and are not strict same-scene A/B controls.

AI age at Perception start mean/P50/P95/max 32.03/31.10/49.63/96.61 ms; at output 116.04/108.47/168.57/261.35 ms. No sustained age growth was observed. Callback mean/P95 service time in the final window was 0.143/0.219 ms, callback CPU mean/P95 2.85/3.5% of one core. Perception Worker CPU 74.09/77.4%, depth worker 22.15/31.6%, independent lease thread 0.53/1.0%. The depth worker completed 1,099 tasks and discarded seven; it had zero pending tasks at the end. Effective completed depth rate was about 3.05 Hz over the run, below the 5 Hz scheduling target. The observed Stage5 depth section mean/P95 remained 0.144/0.195 ms. The current CPU hotspots are LEFT rectify and the existing ROI SGBM matcher, plus ReID/tracking during human operation.

CPU0–7 mean utilization in a 239-second overlapping system sample was 44.84, 43.97, 43.14, 42.75, 36.21, 40.36, 31.09, and 47.20%. A72 sampled mean frequency was 2.055 GHz, versus 2.208 GHz maximum; the A53 mean was about 1.863 GHz. NPU samples: 118/118 `100@950000000Hz`. Overlapping system temperature mean/max 83.14/85.00 C. A 360-second process window recorded temperature rising from 64.69 C startup to about 84 C, with no clear sustained A72 frequency collapse; no thermal-limit conclusion is proven. The process RSS was 228,996 kB at the first steady 10-second window, 252,996 kB at 60 seconds, 261,204 kB at 180 seconds, 269,384 kB at 300 seconds, and 269,572 kB at 356 seconds. It largely flattened in the final minute; no unbounded queue or monotonic late-run leak was observed. The system profiler started after the run began, so its CPU/frequency/temperature series spans 239 rather than the full 360 seconds; the runtime window and per-frame series span the full run.

## Human, safety, and regression

In this concurrent run the operator reached `LOCKED_HIGH`. The Stage6 dry-run JSONL recorded valid `HOVER`, `ASCEND`, `DESCEND`, `MOVE_LEFT`, and `MOVE_RIGHT` decisions. Leaving the frame caused `OPERATOR_LOST` and invalid HOVER. On return, auto-reauthorization briefly entered confirmation but did not regain `LOCKED_HIGH` in this run; rejection reasons included low crop quality, low identity, and hard-gate failure. Earlier V1 human acceptance had recovered, but that does not make this V2 retest a pass. The user chose to stop further repetition. Auto-reauthorization in this V2 run is **FAIL / not reproduced**, without changing its thresholds.

The independent Stage6 clock 500 ms Perception-stall test passed in the Stage6 regression suite: an ACTIVE valid RIGHT lease expired around 300 ms and returned invalid HOVER with `VISION_COMMAND_TIMEOUT`. This was a controlled in-process test of the actual `run_lease_clock` thread and `Stage6DryRun` adapter; a second live-camera RIGHT-then-stall trial was not performed. PX4 live output was not used. Safety Pilot and flight authority logic were not changed. Receiver close/reopen was already verified for the frozen video branch in V1 and was not repeated in this phase.

Final board regression with `source scripts/env_rk3576.sh` and separate pytest invocations: Stage3 **31 passed**; Stage4 **14 passed, 5 subtests passed**; Stage5 **123 passed**; Stage6 **81 passed**. The default AUTO model paths, calibration, Stage3/4/5 decision code, 300 ms Stage6 lease, and frozen video media path remain unchanged. Unit coverage includes latest-frame replacement, depth stale/identity/failure handling, and lease expiry; hardware observation does not prove all possible async races absent.

## Next phase

The 12 FPS target remains unmet. A later Phase 2 can evaluate LEFT/RIGHT remap cost, OpenCV thread scheduling, safe RGA/copy reduction and, only with parity testing, RKNN zero-copy or a narrow C++ hot path. The observed auto-reauthorization rejection and 3 Hz effective depth update rate deserve focused diagnosis with the existing safety gates intact. No PX4 live or flight test is implied by this result.
