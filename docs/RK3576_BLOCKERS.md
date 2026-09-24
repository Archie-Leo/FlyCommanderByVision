# RK3576 blockers — 2026-09-25

## BLOCKER_RK3576_POSE_BACKEND

- 模块：Stage3 Pose inference；连带影响 Stage4/5/6 实时视觉证据。
- 现象：已试的 MediaPipe 1.0.1 ARM64 wheel 在 PoseLandmarker 启动时因 LSE 指令缺失 SIGILL；本板 `/proc/cpuinfo` 的 `Features` 无 `atomics`。本仓库 `stage3_pose/models/pose_landmarker_full.task` 也不存在。
- 根因：当前 MediaPipe wheel 与 RK3576 CPU ISA 不兼容；尚无完成 `PoseFrameV1`、质量和置信度映射验证的 RKNN Pose 模型。
- 已尝试：保留原有 `PoseBackend` / MediaPipe backend、Normalize、`PoseFrame`；新增启动前 LSE 检查；Stage3 16 项逻辑测试通过。核对 Stage4 的六个肩/肘/腕关节及 Stage3 质量门所需髋部和置信度。
- 为什么停止：直接替换成常见 COCO 17 点模型会改变置信度、可见度与质量门语义；未证明它与现有手势安全拒绝逻辑等价。
- 是否影响其他模块：不影响 Stage4–6 单元测试和 Gateway 构建；阻止真实视觉控制链运行。
- 用户需要做什么：提供或选定可用于 RK3576 的 Pose 模型及来源；若要继续使用 NUC MediaPipe 模型，请提供原文件并按 `models/manifest.json` 校验 SHA256 `4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad`（仅供 NUC/兼容 CPU 使用）。
- 下一步命令/文件：`stage3_pose/pose/backend.py`、`stage3_pose/pose/types.py`、`stage3_pose/pose/normalize.py`、`stage4_gesture/gesture/geometry.py`；先写 RKNN→`PoseFrame` 映射测试，再分别运行 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/venvs/fcv/bin/python3 -m pytest -q stage3_pose/tests` 和相同命令的 `stage4_gesture/tests`。分别运行可避免 `tests` 包名冲突。

## BLOCKER_MISSING_OSNET_CHECKPOINT

- 模块：Stage5 V2 OSNet ReID。
- 现象：`/home/lckfb/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth` 不存在。
- 根因：checkpoint 没有随约 2.2 MB Git 仓库上传。
- 已尝试：核对 `models/manifest.json` 和实际文件；V2 的 78 项无权重逻辑测试通过，没有使用替代权重。
- 为什么停止：错误的权重或未知镜像会改变身份特征和 Ownership/Session 安全判断。
- 是否影响其他模块：不影响 V1 fallback、V2 逻辑单测、Stage6/Gateway 构建；阻止 V2 真正 ReID。
- 用户需要做什么：从原 NUC 的同一路径复制官方 OSNet x0.25 MSMT17 文件，或按 `models/manifest.json` 的官方 source 页面获取**同一文件**。预期大小 9,336,983 字节，SHA256 `cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18`。
- 下一步命令/文件：`mkdir -p ~/FlyCommanderByVision/models/reid`；复制后运行 `sha256sum ~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth`；应与上述 hash 完全相同，再检查 `stage5_operator/stage5_v2/reid.py` 加载。

## BLOCKER_STAGE5_V2_NATIVE_DEPENDENCIES

- 模块：Stage5 V2 BoT-SORT / OSNet runtime。
- 现象：`stage5_operator/build/botsort/botsort_capi.so` 与 `third_party/deep-person-reid/torchreid/models/osnet.py` 均不存在；当前 `fcv` venv 未见 PyTorch/BoxMOT。
- 根因：NUC 的原生 `.so` 和源码不是仓库内容，x86_64 编译产物不能直接用于 aarch64；V2 使用固定 C ABI v2 适配器及指定 OSNet 架构。
- 已尝试：阅读 `stage5_operator/stage5_v2/tracker.py`、`reid.py`，运行 V2 逻辑测试 78 项通过；未编译 PyTorch 或替换算法。
- 为什么停止：缺 checkpoint 时仍无法验证真实 ReID；未经 ABI 和 checkpoint 验证的二进制/替代包不应接入 Ownership 链。
- 是否影响其他模块：不影响 Stage5 V1、V2 纯 Python 逻辑测试和 Stage6 单测；V2 真人视觉验证阻塞。
- 用户需要做什么：优先补齐上面的官方 checkpoint；确认是否可迁移原 NUC 所用 deep-person-reid、BoxMOT 精确版本或源码来源。ARM64 PyTorch 需要独立 `~/venvs/fcv_stage5`，不要覆盖 `fcv`。
- 下一步命令/文件：先核对 `scripts/build_boxmot_native.sh` 和 `stage5_operator/stage5_v2/tracker.py` 的 ABI v2，再在 ARM64 上构建并验证 `.so`；PyTorch 只用可信 wheel，禁止从源码长时间编译。

## BLOCKER_CAMERA_THROUGHPUT

- 模块：RK3576 FFmpeg/V4L2 Python camera adapter。
- 现象：左目 640×480 600 帧约 42–43 FPS，低于之前记录的 55.53 FPS；完整双目 2560×960 120 帧为 22.54 FPS。
- 根因：FFmpeg 相同左目 crop/scale 直出到 `dd` 约 52.5 FPS，说明 Python pipe / 数据搬运 / 调度仍有额外成本；完整双目 BGR pipe 每帧约 7.37 MB。尚未细分这些开销。
- 已尝试：无缓冲 600 帧 42.98 FPS；扩大 pipe 读缓冲 600 帧 42.29 FPS（无改善）；最新帧丢弃测试通过；未降低双目标定尺寸或质量门。
- 为什么停止：现有数据不足以证明进一步调整线程/像素格式/硬件解码可保持原始 BGR 和标定语义。
- 是否影响其他模块：不影响软件单测；限制未来完整视觉链频率。控制安全门不能直接以 Pose FPS 驱动。
- 用户需要做什么：无需立即操作；如后续要求 60 FPS 全分辨率深度与记录，需要明确可接受的吞吐/延迟目标。
- 下一步命令/文件：`scripts/benchmark_rk3576_camera.py`、`stage3_pose/camera/ffmpeg_source.py`；测量 FFmpeg 子进程 CPU、pipe read 系统调用及 BGR 转换，任何优化都要复测左/右语义和完整双目 1280×960 每眼几何。

## BLOCKER_VISION_ENVIRONMENT_DRIFT

- 模块：`~/venvs/fcv` 复现性及 NPU 当前状态。
- 现象：实际 NumPy 2.5.3、OpenCV 5.0.0，和移交说明的 1.26.4/4.10.0 不同；`pip list` 同时显示两个 OpenCV 发行包。
- 根因：当前 venv 包状态与此前快照不一致；包何时改变尚未查明。
- 已尝试：读取 pip 元数据和实际导入路径/版本；Stage3–5 和 Gate6C 软件测试通过；当前 venv 的 MobileNet 单次 RKNN 推理输出 `(1,1001)` 并通过有限值检查；未重装已配置环境。
- 为什么停止：直接卸载/重装可能破坏现有 OpenCV/RKNN；需要在隔离 venv 做可重复安装和单次 NPU self-check 后再切换。
- 是否影响其他模块：当前软件单测、Camera 后端及一次 RKNN 推理能运行；长期可复现性仍未确证。
- 用户需要做什么：无需立即操作。不要把 `requirements-nuc.txt` 整体安装到 RK3576。
- 下一步命令/文件：`requirements-rk3576.txt`、`scripts/check_rk3576_rknn.py`；在新的独立 venv 安装经验证版本，检查实际 NumPy/OpenCV 导入及一次 RKNN 推理，再决定是否替换原 `fcv`。
