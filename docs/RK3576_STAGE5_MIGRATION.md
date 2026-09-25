# Stage5 V2 RK3576 integration — 2026-09-25

## Result and scope

The existing Stage5 V2 now selects `torch` or `rknn` via `--reid-backend`.
`scripts/env_rk3576.sh` selects RKNN and the board camera; without it, the
runner defaults to Torch and the original NUC camera. Gallery, ownership,
session, thresholds, Stage4 gesture, and Stage6 authority logic were not
changed. This work did not start the live Stage6/PX4 path.

The RKNN backend initializes one RKNNLite runtime, serializes calls, and
releases it at shutdown. Startup checks the validated model SHA256. A missing
model, import/load/init failure, malformed 512-D output, nonfinite values, or
invalid norm fails closed. No implicit CPU, HSV, or stale-feature fallback is
used. The Torch backend remains available explicitly. Both backends use the
same `prepare_crop` function: BGR crop, 128x256 resize, RGB, float32 / 255,
ImageNet mean/std, then Torch NCHW or RKNN contiguous NHWC.

## Assets and toolchain

| Asset | Verified value |
| --- | --- |
| Official OSNet x0.25 MSMT17 checkpoint | SHA256 `cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18` |
| RK3576 FP16 RKNN | SHA256 `b84b904776a33ad3f2dcbd5d113b0e820ae55c236d18a4ad327703fa472ed0f5` |
| ONNX export | SHA256 `78cfe1d3a979af742b5c206b58aa8a46b318dc0855d67ed07473db59e89810db` |
| deep-person-reid source | commit `f8cd150fdf77e8d9e1ed143b7f308c2c609ded50` |
| BoxMOT source | tag `v25.0.0`, commit `857628343860db1ea48ea50db7a73c25b7a3be13` |
| Compiler and OpenCV native | GCC 13.3.0; Ubuntu OpenCV dev 4.6.0 |
| Python environment | `~/venvs/fcv_stage5`, Python 3.12.3, NumPy 1.26.4, OpenCV 4.10.0, Torch 2.5.1, RKNN Lite2 2.3.2 |

Model binaries are untracked and ignored. `requirements-rk3576-stage5.txt`
records Python dependencies; the vendor RKNN wheel was copied into the
isolated Stage5 environment with its two runtime dependencies. For a fresh
deployment, install that exact vendor ARM64 wheel, then verify imports and
the model hash. It is not fetched from an unverified mirror.

The original BoxMOT CMake build initially stopped at missing `OpenCVConfig.cmake`.
Installing Ubuntu `libopencv-dev` resolved it. `bash scripts/build_boxmot_native.sh`
then built only `botsort_capi.so` from the frozen source. Stage5's ctypes ABI v2
loaded the library and tracked a synthetic person for two consecutive frames,
retaining track ID 1 and returning the expected `bbox_xyxy`, `confidence`,
`track_id`, and `detection_index` fields.

## Real person alignment and latency

`scripts/validate_stage5_reid.py` used 8 existing Market-1501 mini crops and
32 person boxes from existing MOT17-mini ground truth, all from the local
frozen BoxMOT assets. They are validation inputs, not added to this repository.
40 valid crops passed quality checks. Torch versus RKNN per-crop cosine:
mean **0.9999898765**, minimum **0.9999767092**, P5 **0.9999824739**,
P50 **0.9999910343**. Pairwise similarity Pearson correlation was
**0.9999976081** and maximum absolute difference **0.0013653920**. These
numbers include several images of the same tracked person and different
people. This checks feature alignment; it does not establish field identity
accuracy or justify changing ReID thresholds.

Integrated `extract` timing on one existing person crop, 220 iterations:

| Segment | Mean | P95 |
| --- | ---: | ---: |
| Crop, quality, resize, RGB, normalize | 2.680 ms | 3.363 ms |
| RKNN inference | 9.996 ms | 11.203 ms |
| Feature validation and L2 | 0.213 ms | 0.260 ms |
| Full ReID call | **12.908 ms** | **14.399 ms** |

Full ReID call throughput is **77.47/s** for this serial benchmark. This is
slower than the previously validated synthetic tensor-only NHWC inference
mean **7.749 ms** (P50 **7.163 ms**, P95 **10.546 ms**, **129.04/s**), as the
new measurement includes image work and ran under a different load/input.
The earlier Torch CPU single-thread reference was **137.88 ms** and **7.25/s**;
previous synthetic PyTorch→ONNX cosine was **1.0**, and PyTorch→RKNN cosine
was **0.9999639391899109**. These historical figures are retained as supplied
reference measurements, not relabeled as this run's measurements.

## Tests and current limit

- Stage5 full suite: **108 passed**, including RKNN fail-closed contract and
  native BoT-SORT adapter integration. Original isolated V2 tests: **78 passed**.
- Stage5 V1: **19 passed**. Stage3 logic: **17 passed**. Stage4: **14 passed,
  5 subtests passed**. Stage6 unit and dry evidence: **44 + 21 passed**.
- RKNN startup and inference with the actual FP16 model: **passed**.
- Stage5 live camera + Pose + ownership run: **blocked** by the existing
  RK3576 Stage3 Pose backend issue described in `RK3576_BLOCKERS.md`; no Pose
  substitute was introduced. Stage6 live output: **not used**.

For repeatable validation on the board:

```bash
cd ~/FlyCommanderByVision
source scripts/env_rk3576.sh
bash scripts/build_boxmot_native.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/venvs/fcv_stage5/bin/python3 -m pytest -q stage5_operator/tests
OMP_NUM_THREADS=1 ~/venvs/fcv_stage5/bin/python3 scripts/validate_stage5_reid.py \
  --images ~/fcv_third_party/boxmot/assets/reid-mini/Market-1501-v15.09.15 \
  --mot-root ~/fcv_third_party/boxmot/assets/MOT17-mini/train/MOT17-04-FRCNN \
  --iterations 220
```
