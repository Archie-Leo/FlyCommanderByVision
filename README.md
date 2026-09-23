# FlyCommanderByVision

灵眸控飞 — 基于视觉人体交互的无人机控制系统。项目目标是以非接触动作表达方向/高度意图，同时坚持唯一操作手授权、未知动作拒绝和失联安全降级。当前视觉链与飞控链**尚未闭环**，不得把 Stage 5 输出用于真机控制。

## 架构与现状

双目相机 → 校正/深度 → 多人 Pose → PoseQuality / `NormalizedSkeletonV1` → 跟踪及 Operator Ownership → 冻结的 Gesture V1 → 安全授权 →（Stage 6 以后）Intent → Control Gateway → PX4。Stage 1 的 Control Gateway 曾在独立 SITL 中通过；Stage 5 的视觉授权尚未连接它。

| Stage | 当前状态 |
|---|---|
| 0 | PASS：PX4/ROS2/Gazebo 基础环境与日志 |
| 1 | PASS：Control Gateway 冻结 |
| 2 | PASS：双目几何基线；Run B 是开发默认标定 |
| 3 | PASS / Pose V1 FROZEN |
| 4 | PASS / Gesture V1 FROZEN |
| 5 | IMPLEMENTED / MANUAL VALIDATION PENDING；真人双人 Gate 未通过 |
| 6–9 | NOT STARTED |

已冻结的接口包括 Stage 1 `Intent`、Stage 3 `PersonPoseV1` / `PoseQualityV1` / `NormalizedSkeletonV1`、Stage 4 `GestureCandidateV1`。Stage 5 新增 `TrackedPersonV1` 和 `AuthorizedGestureV1`，但尚未完成真人 Gate。五个普通手势为 LEFT、RIGHT、ASCEND、DESCEND、HOVER；T-Pose 只用于首次操作手授权。

## 目录

- `control/drone_control_gateway/`：Stage 1 自主 ROS 2 package。
- `stage2_stereo/`：采集/标定、深度验证源码；`configs/calibration/run_b.yaml` 为小型开发配置。
- `stage3_pose/`、`stage4_gesture/`：已冻结视觉算法、测试和阶段报告。
- `stage5_operator/`：独立视觉授权基线、测试、逐帧日志程序。
- `docs/research/`：Operator Ownership 开源审计；`third_party/README.md`：引用和许可证。
- `PROJECT_STATUS.md`、`ROADMAP.md`、中文执行清单：进度与详细 Gate。

正式运行环境是 NUC Ubuntu 22.04.5、Python 3.10、ROS 2 Humble、PX4 v1.17.0、Gazebo Harmonic 8.15.0。Stage 2 使用 OpenCV 4.10.0 / NumPy 1.26.4；Stage 3–5 使用现有 `~/venvs/drone_stage3`。本集成仓库是**复制品**，原运行目录不移动。各阶段命令见其 README；自动测试见 `REPOSITORY_SYNC_REPORT.md`。

当前可演示能力：独立 PX4 SITL Control Gateway、双目校正与静态测距、单人 Pose/Quality/Normalization、冻结的五手势识别；Stage 5 可显示候选跟踪和授权状态，但仅做视觉观察。其 tracker 是 OpenCV Kalman + 两阶段 IoU，**不是**正式 BoT-SORT/ByteTrack；外观 Gallery 使用临时 HSV 衣色描述子，**不是** OSNet。相似衣着、交叉、遮挡和身份切换未经过双人真实验收，不能声称可靠锁人。

下一步是按 `ROADMAP.md` 完成真人 A/B 双人 Operator Ownership Gate。未知、无效、丢失、歧义、超时均应拒绝授权；任何失效都不能复用上一条动作。仅在后续 Stage 6 经单独验证后，才考虑把视觉授权接入仿真控制。

此私人仓库未发布项目自有代码许可证。上游软件、模型和参考项目各保留原许可证，尤其 BoxMOT AGPL-3.0 不得未经合规评估纳入 runtime；详见 `LICENSE_POLICY.md` 和 `third_party/README.md`。模型二进制与数据集不在 Git 内，见 `models/README.md`、`DATASETS.md`。
