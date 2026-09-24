# FlyCommanderByVision

灵眸控飞 — 基于视觉人体交互的无人机控制系统。项目目标是以非接触动作表达方向/高度意图，同时坚持唯一操作手授权、未知动作拒绝和失联安全降级。Stage 6A/B 的视觉→Intent 隔离 dry-run 已通过；Gate 6C 的真人手势→Gazebo X500 飞行响应**尚未验证**。禁止真机/真桨。

## 架构与现状

双目相机 → 校正/深度 → 多人 Pose → PoseQuality / `NormalizedSkeletonV1` → BoxMOT native BoT-SORT + OSNet Operator Ownership → 冻结的 Gesture V1 → `AuthorizedGestureV1` → Stage 6 Flight Authority/Motion Lease → Intent → Control Gateway → PX4。Stage 6 默认只发布到 `/interaction/intent_dry_run`；真实 Gateway topic 需要显式 flag 且仅允许受监督的 Gazebo SITL。

| Stage | 当前状态 |
|---|---|
| 0 | PASS：PX4/ROS2/Gazebo 基础环境与日志 |
| 1 | PASS：Control Gateway 冻结 |
| 2 | PASS：双目几何基线；Run B 是开发默认标定 |
| 3 | PASS / Pose V1 FROZEN |
| 4 | PASS / Gesture V1 FROZEN |
| 5 | V2.2 SINGLE-OPERATOR DEMO BASELINE PASS；多人安全完整 Gate 延至 Stage 7 |
| 6 | Gate 6A/B PASS；Safety Pilot 权限门控已加入，65/65 测试 PASS；Gate 6C 正式飞行响应 NOT STARTED |
| 7–9 | NOT STARTED |

已冻结的接口包括 Stage 1 `Intent`、Stage 3 `PersonPoseV1` / `PoseQualityV1` / `NormalizedSkeletonV1`、Stage 4 `GestureCandidateV1`。Stage 5 V2.2 提供 Operator Session/ReID Gallery/`AuthorizedGestureV1`；五个普通手势为 LEFT、RIGHT、ASCEND、DESCEND、HOVER，T-Pose 用于身份授权。Safety Pilot 单独掌握 ARM/TAKEOFF/OFFBOARD/HOLD/LAND；退出 Offboard 不删除 Session，但撤销 Stage 6 Motion Lease，重新进入必须释放旧手势再发新动作。

## 目录

- `control/drone_control_gateway/`：Stage 1 自主 ROS 2 package。
- `stage2_stereo/`：采集/标定、深度验证源码；`configs/calibration/run_b.yaml` 为小型开发配置。
- `stage3_pose/`、`stage4_gesture/`：已冻结视觉算法、测试和阶段报告。
- `stage5_operator/`：V1 历史基线及当前 V2.2 BoT-SORT/OSNet 授权逻辑、测试。
- `stage6_closed_loop/`：安全权限门、Intent 适配、录像/rosbag/PX4 旁路证据和分析器。
- `scripts/`、`requirements-nuc.txt`、`models/manifest.json`：可复现安装/启动与模型哈希。
- `docs/PX4_SETUP.md`、`docs/CAMERA_SETUP.md`、`docs/RK3576_PORTING.md`：外部运行环境与未来移植边界。
- `docs/research/`：Operator Ownership 开源审计；`third_party/README.md`：引用和许可证。
- `PROJECT_STATUS.md`、`ROADMAP.md`、中文执行清单：进度与详细 Gate。

正式开发环境是 NUC Ubuntu 22.04.5、Python 3.10、ROS 2 Humble、PX4 v1.17.0、Gazebo Harmonic 8.15.0；Stage 6 当前使用 `~/venvs/drone_stage5_v2`，OpenCV 4.10.0 / NumPy 1.26.4 / MediaPipe 1.0.1 / PyTorch 2.5.1+cpu。原 NUC 工作目录不移动。新机器先按 `docs/PX4_SETUP.md`、`docs/CAMERA_SETUP.md`、`THIRD_PARTY_NOTICES.md` 配环境，再运行 `scripts/build_boxmot_native.sh`、`scripts/download_models.sh`、`scripts/check_environment.sh --dry-run` 和 `scripts/start_stage6_dry_run.sh`。**`scripts/start_stage6_live.sh --confirm-sitl` 只可在已检查完毕且安全员监护的 Gazebo SITL 中使用。**

当前可演示能力：独立 PX4 SITL Control Gateway、双目校正与静态测距、Pose/Quality/Normalization、冻结五手势、Stage 5 V2.2 单人授权与出画重获、Stage 6 dry-run Intent/安全超时及完整证据记录。Stage 5 V2.2 使用 BoxMOT native BoT-SORT + OSNet；V1 的 Kalman/IoU+HSV 仍是历史基线，不能据 V2.2 单人结果声称已通过多人交叉安全 Gate。

下一步是恢复相机/GCS预检后，在安全员监督下完整重做 Gate 6C：HOVER、RIGHT、LEFT、ASCEND、DESCEND 和释放行为逐层核验。任何证据不足不得标 PASS。Gate 6D/Stage 7 与 RK3576 算法移植均未启动。

此私人仓库未发布项目自有代码许可证。BoxMOT 当前确为外部 AGPL-3.0 runtime 依赖，合规审查尚未完成；OSNet 权重再分发权未确认，因此权重不入 Git。详见 `LICENSE_POLICY.md`、`THIRD_PARTY_NOTICES.md`、`models/README.md`、`DATASETS.md`。
