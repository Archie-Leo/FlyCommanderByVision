# RK3576 porting plan — not started

Frozen development baseline: NUC x86_64, Ubuntu 22.04, Python 3.10,
ROS 2 Humble, OpenCV 4.10.0, NumPy 1.26.4, PyTorch 2.5.1+cpu, PX4
v1.17.0 SITL. Gate 6A/B PASS; Gate 6C requires full revalidation after
Safety Pilot Flight Authority integration. **RK3576 is not READY and no
RKNN conversion or algorithm migration is performed in this baseline.**

Portable at the source/logic level (after tests on ARM64): Stage 3
normalization and pose-quality logic; Stage 4 geometry/temporal rules;
Stage 5 ownership FSM/Gallery policy; Stage 6 Flight Authority, freshness
and Intent mapping; Gateway Intent protocol and NED semantics. Portable
does not mean performance- or safety-validated on RK3576.

Adaptation required: ARM64-compatible MediaPipe/Pose runtime or RKNN pose
model, OSNet inference backend and weight conversion/precision validation,
BoxMOT native C++ build and AGPL compliance, OpenCV/V4L2 stereo capture,
hardware video encoding, stereo rectification throughput, ROS 2/px4_msgs
distribution compatibility, and end-to-end monotonic timing. RKNN Runtime
and NPU model accuracy must be independently validated against the frozen
NUC outputs; never lower ownership/gesture rejection gates to compensate.

First migration step: on the RK3576, inventory Ubuntu/kernel, NPU/RKNN
Runtime, camera/V4L2, ROS 2 and compiler support; reproduce the **same
recorded Stage 3/4/5/6 unit fixtures** on ARM64 CPU before attempting any
NPU conversion or live control. No true aircraft is in this plan.
