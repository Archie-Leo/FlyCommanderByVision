# PROJECT STATUS — 无人机视觉交互

> 最后更新：2026-09-24（Asia/Shanghai）

## 当前阶段

- Stage 0：PASS
- S1-01 官方 `px4_ros_com/offboard_control` Baseline：PASS
- S1-02 Control Gateway V1：PASS
- Control Gateway V1.1（world NED +X/-X 与 yaw-rate）：PASS
- Stage 1 整体：PASS，控制层冻结
- Stage 2：PASS（2026-09-14）；双目采集、标定、校正、视差、Q→3D 与 0.5–2.5 m 静态测距闭环已实测
- Stage 3：PASS / V1 FROZEN（2026-09-15）
- Stage 4：PASS / Gesture V1 FROZEN（2026-09-23；操作员确认两段目标动作录制错误，原始诊断保留）
- Stage 5 V2.2：**SINGLE-OPERATOR DEMO BASELINE PASS / FULL MULTI-PERSON SAFETY VALIDATION DEFERRED TO STAGE 7**；单人真人出画后自动重新授权 4/4 成功，且每次创建 NEW Session；不将双人安全 Gate 写成 PASS
- Stage 6：**IN PROGRESS**；Gate 6A PASS、真人五手势及安全项 Gate 6B PASS；Safety Pilot Flight Authority Gate 已加入、65/65 测试 PASS；Gate 6C **证据系统 READY / 正式五动作仍 NOT STARTED**，须按新权限逻辑完整重测；Gate 6D 未启动
- Stage 7–9：NOT STARTED
- PRE-STAGE-5 Operator Ownership 开源源码审计：COMPLETE
- 当前开发默认标定：Run B（显式排除 pair 4/27；原始 captures 完整保留），状态为 `DEVELOPMENT/PROVISIONAL`
- 当前边界：Stage 4 不补采、不调参；Stage 5 仅实现视觉安全链，不进入 ROS2/PX4/Gazebo 或真机闭环

2026-09-24 单操作手 Demo 主线正式收口并启动 Stage 6：Stage 5 V2.2 最近
真人片段 `runs_v2/20260924_105951_UTC/recordings/clip_001` 中，首次
T-Pose 授权后四次完整 `OPERATOR_LOST → AUTO_REAUTHORIZE_CONFIRMING →
LOCKED_HIGH` 均建立不同新 Session；36 帧确认期间 Authorized=NO、Gallery
无写入，成功当帧无旧 Gesture。该片段仅一人，合法手势授权次数为 0，
四次成功时 stereo depth 不可用；因此仅认定**单操作手 Demo 基线 PASS**，
不认定旁人冒领/交叉/同衣服等完整安全 Gate。上述事项迁至 Stage 7。

Stage 6 独立工程位于 NUC `~/drone_stage6_closed_loop`，Windows 算法/文档
镜像 `D:\FlyCommanderByVision\drone_stage6_closed_loop`。已核实冻结
`Intent.msg` 与可靠 QoS `/interaction/intent`；新增仅消费
`AuthorizedGestureV1` 的映射/300 ms 租约、20 Hz ROS 定时发布节点和独立视觉入口。
默认只发 `/interaction/intent_dry_run`，未接 Gateway/PX4/Gazebo。
Gate 6A 27/27 deterministic tests PASS；隔离 ROS 传输收到有效 MOVE_LEFT
及视觉停止后的无效 HOVER；真实相机空场 20 帧、66 条 Intent 事件均为安全
HOVER。第一次 dry-run 暴露日志文件关闭顺序问题，已修复并重测；当前待
真人 Gate 6B 五手势及拒绝路径已通过隔离 dry-run 验证。超时输入在
329 ms 收到 `HOVER valid=false / VISION_COMMAND_TIMEOUT`；SIGINT 后 ROS
最后一条是 `HOVER valid=false / VISION_STOPPED`，无重复 shutdown；坏相机
路径仍 fail-closed。29/29 Stage 6 测试 PASS。Gate 6C 尚未启动，未接
Gateway/PX4/Gazebo。详见 Stage 6 的设计与验证报告。

2026-09-24 Gate 6C 证据系统准备：NUC `~/drone_stage6_closed_loop` 新增
`--record`/`R` 标注 AVI、同 Run 下的视觉/Intent/Gateway/PX4 JSONL、
`run_manifest.json`、动态 topic 旁路 observer、`--rosbag` 与保守离线分析。
统一 `t0_monotonic_ns`/`run_elapsed_ms`；保留 PX4 source timestamp 原值，
不假定其与主机 monotonic 同域。20 帧真实相机 dry-run 的 AVI/JSONL
逐帧对齐；合成 RIGHT Run 的 16 条有效 Intent 与 16 条旁路接收一致，
并明确标记 `test_injection`，不得用于真人/飞行 PASS。PX4/Gazebo
observer-only smoke 仍用 `/interaction/intent_dry_run`：实际订阅
`/fmu/out/vehicle_local_position_v1`（403 条）和
`/fmu/out/vehicle_status_v1`（16 条）；rosbag 相应录得 293/12 条，
PX4 未武装且无 Gateway setpoint 发布。Stage 6 `compileall` 与
**51/51 tests PASS**；自动分析结果均为 `INSUFFICIENT_EVIDENCE`，
Gate 6C 飞行响应测试尚未开始。没有修改 Stage 1/3/4/5。

2026-09-24 Gate 6C 正式测试 Preflight：NUC 相机 `/dev/video0` 可读取
2560×960 原始帧，OSNet checkpoint 成功加载；ROS Humble、XRCE Agent、
Gazebo X500 SITL、Gateway 和真实 PX4 位置/状态 topic 均正常。Gateway 是
`/interaction/intent` 唯一 subscriber、`/fmu/in/trajectory_setpoint` 唯一
ROS publisher。磁盘剩余约 326 GB。预检 Run
`~/drone_stage6_closed_loop/runs/20260924_135943_UTC/` 仍在
`/interaction/intent_dry_run`：20 帧 AVI、四份日志及 rosbag 均完整，
101 条 Gateway trajectory setpoint 全部为零，分析为
`INSUFFICIENT_EVIDENCE`。但 PX4 控制台持续报告
`Preflight Fail: No connection to the GCS`，且真人在相机前监护/动作尚未确认；
依本轮“一项失败即停止”规则，未 ARM、未 TAKEOFF、未切 Offboard、未使用
`--allow-live-output`。临时 Gateway、PX4/Gazebo、Agent 已关闭。
正式五动作飞行响应没有执行，Gate 6C 不得标 PASS/FAIL；待 GCS 与真人监护
就绪后重新从完整 Preflight 开始。

2026-09-24 Safety Pilot/Offboard 权限修正：只改 Stage 6。真实 PX4
`VehicleStatus` 的 armed+Offboard+非 failsafe 且状态新鲜，才有 Flight
Authority；Offboard 退出/失效只清 Stage 6 Motion Lease，不更改 Stage 5
Session/Gallery。重新进入 Offboard 必须先观察同一可信操作手的稳定中性释放，
再接收新的合法手势；持续保持旧 RIGHT 不会自动恢复 MOVE_RIGHT。
新增 14 项测试，Stage 6 **65/65 PASS**。NUC 随后 `/dev/video*` 不存在，
故本轮新版真实相机 smoke 未完成；未启动 Gate 6C 飞行验证。

2026-09-24 Stage 5 V2.2 实验功能：在保留首次 T-Pose、V2.1 短期同 Session
重获和旧的 T-Pose 重新授权前提下，新增默认关闭的完整 `OPERATOR_LOST` 后
安全自动重新授权。`--auto-reauthorize` 才显式启用；旧 OSNet Gallery 多样本
max/top-3/count、长期候选融合分数、hard gates、候选竞争和 8 帧/700 ms 的
RED 确认全部通过才建立 **NEW Session**，旧 Session/旧 Gesture 不复活。
旧 Gallery 在失锁和确认阶段只读。NUC V1+V2 自动测试 **97/97 PASS**，
`/dev/video0` 空场 20 帧 PASS（零误授权、新 JSON 字段完整）。最新旧录像
`20260924_084846_UTC` 日志驱动分析：134 帧 LOST 候选检查中，ReID
≥0.94 为 54 帧，最长连续 17 帧/2472 ms；加入 crop≥0.75 与 pose valid
后最长 15 帧/2177 ms。旧日志缺少新 Top-K/长期融合数据，**不能据此声称
真人自动恢复已成功**。旁人和同衣服冒名风险未实测；Gate NOT PASSED，
Stage 6 NOT STARTED。详细参数、reject reason 与下一轮最小真人 Gate 见
`drone_stage5_operator/STAGE5_DESIGN_V2.md` 和
`drone_stage5_operator/STAGE5_VALIDATION_REPORT_V2.md`。

2026-09-24 Stage 5 V2.1 定向修复：真人录像 `clip_002` 的多人交叉显示，
原操作手 track 3 在旧 RED grace 到期前已重新出现，身份综合分数
0.949/0.940/0.941、hard gates 通过，却在 5 帧确认尚未完成时被旧
1100 ms 计时判为 LOST。现新增 RED、不可授权的 `REACQUIRE_CONFIRMING`：
5 帧且 ≥400 ms，单次确认窗口 900 ms，首次 RED 起总体上限 2000 ms；
普通 RED 仍维持 1100 ms，候选失败不能无限续命。已记录逐帧计时/候选/
状态转移字段。NUC V1+V2 合成及旧录像日志切片回归 **68/68 PASS**；
20 帧空场相机冒烟 PASS。旧录像只证明帧 405 不应被提前判 LOST，
后续原始候选分数缺失，真人后修复效果仍待验证。0.90 身份阈值、
OSNet、BoT-SORT、Evidence 权重、Stage 3/4 均未改；Stage 5 Gate
**NOT PASSED**，Stage 6 **NOT STARTED**。

2026-09-24 真人录像与对应 JSONL 首约 75 秒复核：1553 帧中 `LOCKED_HIGH`
520 帧、`LOST` 751 帧、`REACQUIRING` 113 帧；两人同帧 68 帧，
`AuthorizedGestureV1.valid=true` 为 0。T-Pose 首次授权、同一 Session 跨临时
track ID 2/4/5 重获及 fail-closed 有证据；频繁失锁表明身份底座不足。
操作手合法 Gesture YES 与旁人合法 Gesture NO 尚未完成真人对照，禁止把 Stage 5 写 PASS。

2026-09-24 V2 进展：独立 `stage5_v2` / `live_operator_v2.py` 已实现 BoxMOT
native BoT-SORT adapter、OSNet 接口、Run B 人体躯干深度、多证据可靠度融合、
hard gates、候选竞争、RED/GREEN/YELLOW/GRAY 状态与 Gallery 防污染；
NUC 上 V2 安全合成测试 22/22（新增录制测试 3/3；全套 44/44）、BoT-SORT ABI 冒烟通过。原生 BoT-SORT 参考 commit
`8576283`，AGPL-3.0；OSNet 来自 deep-person-reid `f8cd150`，目标为 x0.25
MSMT17、512-D。NUC 独立 `~/venvs/drone_stage5_v2` 已装 PyTorch 2.5.1+cpu，
OSNet 架构前向输出 `(1,512)` 已验证。随后操作员提供训练 checkpoint，
`~/FlyCommanderByVision/models/reid/osnet_x0_25_msmt17.pth`（SHA256
`cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18`），
已安全加载并对合成 crop 输出单位范数 512-D embedding。V2 可以运行，
真人首次授权已有录制证据，但失锁后重新授权、近距离稳定性与多人授权
仍未通过后修复版本的真人 Gate，不能 PASS。
Stage 2 SGBM 单项六帧耗时中位约 77.1 ms；需先 profile 全链路，再做真人复测。

2026-09-24 诊断录制：V1/V2 可视化入口均支持 `R` 开始/停止片段，
`--record` 无界面自动录制，按需 `--record-raw-stereo` 保存同帧 SBS 无损 PNG；
带标注 MJPG、逐帧完整 JSONL、manifest 与真实 monotonic 时间戳落在每次
run 的 `recordings/clip_XXX/`。NUC V1 实机 2 帧录制并读回通过；
新增 3 项录制测试后 V1+V2 回归 44/44。V2 权重到位后，NUC 空场
2 帧原始 SBS+标注录像对齐读回、12 帧标注录像对齐读回通过；
后者 capture 时间戳中位间隔约 9.79 FPS，深度/Stage 5 阶段中位
73.7/74.9 ms（**空场，没有人像 OSNet 推理**）。Gate 不变。

## 正式运行环境（远端 NUC）

| 项目 | 冻结值 |
|---|---|
| Host | `sentinel-S600` / `192.168.1.18` |
| OS | Ubuntu 22.04.5 LTS x86_64 |
| ROS 2 | Humble |
| Gazebo | Harmonic 8.15.0 |
| PX4 | v1.17.0，`~/PX4-Autopilot` |
| ROS workspace | `~/ros2_px4_ws` |
| px4_msgs | v1.17.0 |
| px4_ros_com | `86e9aeb20e55a4673fa8a9f1c29ea06a6c5ad1af` |
| Micro XRCE-DDS Agent | v2.4.2，UDP 8888 |
| Control Gateway | `~/ros2_px4_ws/src/drone_control_gateway` |
| Gateway stable predecessor | `e88b16459b812cf59d824e6c7afbe9de8db1d725` |
| Gateway current commit | `09776d3d234ccebabb5c86cf7aed7be1026bfe30` |

## Stage 1 Control Gateway 实现边界

`drone_control_gateway` 是独立 ROS 2 package，未修改 PX4-Autopilot、
px4_msgs、px4_ros_com 或 Micro-XRCE-DDS-Agent。包含：

- domain-level `Intent.msg`；
- 与 ROS/PX4 无关的 `IntentMapper`；
- `control_gateway_node` PX4 发布适配层；
- 参数 YAML、launch、README 与 mapper 单元测试。

冻结 Intent：`MOVE_FORWARD`、`MOVE_BACKWARD`、`MOVE_LEFT`、`MOVE_RIGHT`、
`ASCEND`、`DESCEND`、`YAW_LEFT`、`YAW_RIGHT`、`HOVER`。
ARM、TAKEOFF、LAND 与模式切换不在 Gateway 节点中，节点启动不自动起飞。

坐标契约为 PX4 local world NED，不是 body-relative：

- FORWARD = `+X / North`，BACKWARD = `-X / South`；
- RIGHT = `+Y / East`，LEFT = `-Y / West`；
- ASCEND = `-Z`，DESCEND = `+Z`；
- YAW_RIGHT = 正 NED yaw-rate（俯视顺时针），YAW_LEFT = 负 yaw-rate；
- HOVER = `[0, 0, 0] m/s` 且 yaw-rate `0 rad/s`。

`OffboardControlMode.velocity=true`，10 Hz heartbeat；TrajectorySetpoint 中
position/acceleration/jerk/absolute yaw 显式为 NaN，velocity 与 yawspeed 为
有限控制值。选择 yawspeed 是因为左右转是持续方向/速率意图，不是绝对航向。
默认前向/侧向/垂直速度为 0.8/0.8/0.5 m/s，默认 yaw-rate 0.35 rad/s；
最大水平/垂直速度 1.0/0.7 m/s，最大 yaw-rate 0.6 rad/s，timeout 0.5 s。

## 构建与实际验证

- `colcon build --packages-select drone_control_gateway`：PASS。
- `colcon test` / `colcon test-result --verbose`：6 tests，0 errors，0 failures
  （其中 5 个 mapper/lease gtest case）。
- 启动安全：Gateway 启动时 PX4 为 DISARMED；输出速度 `[0,0,0]`，未发出
  ARM/OFFBOARD/TAKEOFF/LAND 指令。
- HOVER/timeout：每次短租约 Intent 停止后约 0.5 s 自动回零速度。
- RIGHT：NED y `0.047 → 1.931 m`。
- LEFT：NED y `1.931 → 0.092 m`。
- ASCEND：NED z `-2.76 → -3.97 m`。
- DESCEND：NED z `-3.97 → -2.74 m`。
- FORWARD：NED x `0.077 → 2.020 m`（测试时机头约 -2.9 rad，证明是 world-frame）。
- BACKWARD：NED x `2.020 → 0.050 m`。
- YAW_RIGHT：setpoint `+0.35 rad/s`，实际 heading `-2.891 → -1.829 rad`，
  俯视顺时针。
- YAW_LEFT：setpoint `-0.35 rad/s`，实际 heading `-1.802 → -2.827 rad`，
  俯视逆时针。
- clamp：请求 99 m/s 时，在线 setpoint 实测水平为 `+1.0 m/s`、上升为
  `-0.7 m/s`；请求 99 rad/s 时 yaw-rate 被 clamp 为 `+0.6 rad/s`。
  随后 timeout 同时回 `[0,0,0]` 与 `0 rad/s`。
- Offboard loss：`COM_OF_LOSS_T=1.0 s`，`COM_OBL_RC_ACT=0`。停止 Gateway
  后捕获到 `armed=2, nav_state=14, failsafe=false` 转为
  `armed=2, nav_state=5 (AUTO_RTL), failsafe=true`，随后安全落地/解锁。

## 问题与解决

1. 地面已 ARM 时发送 ASCEND 不会绕过 PX4 takeoff 状态机，飞机保持地面。
   这是预期安全行为；测试采用独立 `px4-commander takeoff` 起飞，再显式切入
   Offboard，未向 Gateway 添加自动起飞逻辑。
2. 单次 CLI 发布后再 echo 会错过 0.5 s 命令租约。改为并行记录
   `/fmu/in/trajectory_setpoint`，确认 active setpoint 与 timeout 回零。
3. 高频 Intent 初版会逐条打印 INFO。最终版按 seq/intent 去重，仅保留状态变化，
   不影响 10 Hz 输入续租。
4. `TrajectorySetpoint.yawspeed` 是 NED +Z 轴角速度；PX4 v1.17 源码与
   Gazebo/PX4 实际 heading 共同确认正值为俯视顺时针。采用 yawspeed、保持
   absolute yaw=NaN，timeout 时显式归零，避免把方向 Intent 锁成绝对航向。

## 尚未完成 / 不应误判为完成

- Stage 1 已 PASS，控制层冻结；不继续无目的扩展飞控接口。
- Gateway 仍不负责 TAKEOFF/LAND；后续若扩展，必须保持独立安全接口。
- Stage 3 MediaPipe Pose/Quality/Normalization 已实现并等待人工验证；Stage 4 Gesture、
  Stage 5 Tracking/Ownership、TCN、ST-GCN 仍未开始。
- Windows 除路线/状态文档外，仅按备份策略保存 Stage 3 quality/normalization 等重要自主
  算法源码；远端环境、模型、第三方仓库、build/install/log、ULog 和 rosbag 未迁回。

## PRE-STAGE-5 Operator Ownership Research（2026-09-10）

任务性质是 Stage 5 前独立技术预研，不代表 Stage 5 Runtime 已启动。已在远端
`~/drone_vision_refs/operator_lock` 对六个现有、干净的冻结仓库完成静态源码审计；
未重新 clone、未安装依赖、未执行模型、未修改参考仓库：

| 仓库 | 冻结 commit |
|---|---|
| BoxMOT | `857628343860db1ea48ea50db7a73c25b7a3be13` |
| BoT-SORT | `251985436d6712aaf682aaaf5f71edb4987224bd` |
| ByteTrack | `d1bf0191adff59bc8fcfeaa0b33d3d1642552a99` |
| deep-person-reid | `f8cd150fdf77e8d9e1ed143b7f308c2c609ded50` |
| opencv_zoo | `47534e27c9851bb1128ccc0102f1145e27f23f98` |
| rknn_model_zoo | `bad6c7334531becaf90a561988519b7bec34d0ab` |

研究结论：**HIGH feasibility / CONDITIONAL_GO**。Global MOT 的首 baseline 采用
BoT-SORT family，同时以 StrongSORT、DeepOCSORT、OccluBoost 做 A/B，以 ByteTrack
做无 ReID 下界；任何 Tracker 的 `track_id` 都不得直接代表 operator session 或
控制权。必须自主开发 session-level `OperatorOwnershipManager`、保守 ReID Gallery、
竞争候选 margin、时空/双目硬门控与拒绝优先 Safety FSM。

原始 BoT-SORT 冻结源码因 ReID 成员初始化顺序、`np.float` 和旧依赖栈问题，不原样
集成；TrackerNano 没有身份 embedding/gallery，只可作为未来可选 shadow ROI，不是
ownership 权威。第一版架构不强制 Dedicated SOT，先验证更小的：

```text
Global MOT + Operator ReID Gallery + geometry/stereo gates + Safety FSM
```

BoxMOT 是最佳统一研究/评测台，但许可证为 AGPL-3.0，若进入发布 Runtime 必须先明确
合规策略。OSNet 适合输出人体 embedding，但不得以单一 cosine 阈值授权；YuNet/SFace
只作可选正面证据/冲突证据。详细报告：

- `docs/research/OPERATOR_LOCK_OPEN_SOURCE_AUDIT.md`
- `docs/research/OPERATOR_OWNERSHIP_FEASIBILITY_V1.md`

远端额外观察：系统 apt OpenCV 为 4.5.4；Python `cv2` 当前因旧 OpenCV 与用户态
NumPy 2.2.6 ABI 不匹配而 import 失败。本轮遵守静态审计边界未修复、未升级。该问题
留到 Stage 2 正式环境规划中处理，不为兼容 TrackerNano 擅自改栈。

Windows `D:\FlyCommanderByVision` 当前不是 Git worktree（无 `.git`），因此本轮只能
落盘 Markdown，不能真实产生或报告 Windows Git commit。未伪造 commit，也未 push。

## Stage 2 Stereo Camera Calibration Tool（2026-09-14）

正式硬件事实已冻结：深圳德创信双目 RGB USB Camera，`0bda:5883` / `uvcvideo`，
唯一 Video Capture 节点为 `/dev/video0`；输出是 2560×960 MJPEG Side-by-Side，
左半幅为物理 LEFT、右半幅为物理 RIGHT，每目原始分辨率 1280×960。USB 3.0
SuperSpeed，声明 60 FPS，长时间实测约 58.7 FPS；工具使用 monotonic timestamp，
不写死 `dt=1/60`。

正式工具已创建：

- 远端：`~/drone_stage2/stereo_calibration`
- Windows 文档/重要源码备份：`D:\FlyCommanderByVision\stereo_calibration`

实现边界：

- `stereo_calibration.py`：V4L2 实时预览、双侧 FOUND/PAIR VALID、FPS/计数/按键；
- S/Space 对当前原始同一 SBS 帧重新做双侧 full-resolution SB 检测后才保存；
- pair 以 PNG 保存到独立 UTC session，含 LEFT/RIGHT/FULL 与原子 metadata；
- D 为可恢复删除，移动到 session `deleted/`；
- C 只从磁盘重读数据，不使用实时内存角点；
- `calibrateCameraExtended` 分别求左右内参，再用
  `stereoCalibrateExtended + CALIB_FIX_INTRINSIC` 求 R/T/E/F；
- `stereoRectify + initUndistortRectifyMap + remap` 输出校正预览；
- 用 `undistortPoints(R,P)` 计算校正角点纵向误差 mean/median/p95/max；
- 输出 OpenCV FileStorage `calibration.yaml`、`calibration.npz`、人类可读报告和
  独立 validation report；异常 view 仅报告，不静默剔除；
- V1 使用 pinhole 5 参数 radial/tangential 模型。102° 不是自动切换 fisheye 的依据；
  只有实体数据出现边缘系统残差、径向非单调或校正失败时才启动独立模型对照实验；
- 对称棋盘只做左右 row/column basis 几何一致性验证，冲突即拒绝，不自动翻转角点。

研究依据记录在 `stereo_calibration/CALIBRATION_RESEARCH.md`。其中明确区分 OpenCV
官方定义与项目工程 heuristic，不宣称存在 OpenCV 官方统一 RMS 合格线。

远端 `~/venvs/drone_stage2`（OpenCV 4.10.0 / NumPy 1.26.4）验证：

- `compileall`：PASS；
- `unittest`：11 tests PASS；
- 覆盖 SB 双侧/单侧识别、ordering mismatch、45 mm object points、session/metadata、
  可恢复删除、空数据拒绝、OpenCV Extended API、65 mm 合成几何、完整合成 PNG
  session、YAML/NPZ round-trip、rectified previews 和独立 validation；
- 真实 `/dev/video0` 短时 smoke：协商 2560×960@60，连续 5/5 帧读取并正确拆为
  LEFT/RIGHT 1280×960，结束后已 release；
- 不存在相机路径错误提示：PASS；空 captures 标定拒绝：PASS。

首轮实体候选标定（session `20260914_051811_UTC`）已完成 30 对且 0 对磁盘拒绝：
LEFT RMS 0.09045 px、RIGHT RMS 0.08114 px、Stereo RMS 0.17590 px；估计 baseline
64.8068 mm（相对标称 65 mm 差约 0.3%）；rectified vertical error mean 0.09342 px、
p95 0.23581 px、max 0.69305 px。六张 rectified preview 的已覆盖区域肉眼水平对齐良好。

本候选结果状态为 `SUSPICIOUS`，不得作为最终 Stage 2 参数验收：30 组棋盘角点实际
纵向只覆盖 y=499–751 px，棋盘中心仅覆盖 y=554–613 px，画面上半部和外角没有获得
足够约束；左目五参数模型在未覆盖的传感器最外角出现径向映射非单调。Stereo
per-view 异常集中于 pair 17、18、26、27、28、29、30；另有 2~14、6~20 两组近重复
姿态。该结论只描述首轮候选结果；随后已通过第二轮全画幅数据、Run A/B 对照和实体
距离验证完成 Stage 2 收口，不再将首轮参数作为当前默认参数。

第二轮实体候选标定（session `20260914_092939_UTC`）完成 42 对、0 对磁盘拒绝，
状态提升为 `WARNING`：LEFT/RIGHT RMS 0.09285/0.09126 px、Stereo RMS 0.23666 px、
baseline 64.8846 mm、vertical epipolar mean/median/p95/max 分别为
0.22946/0.18188/0.60849/1.55579 px。角点覆盖显著改善到约 x=65–1180、
y=152–938 px，左右径向模型全传感器采样单调，六张标准 preview 的水平对齐肉眼良好。
警告来自个别 view：pair 4 的右目清晰度最低且棋盘近距离贴边，pair 27 的 stereo
per-view error 最高；pair 4/27 也是极线误差主要贡献者。与首轮相比 K 仅变化约
1.5–1.9 px、baseline 仅变化 0.078 mm、R 差约 0.154°，但 Tz 从 -4.61 mm 变为
+4.83 mm，正式验收前仍需用独立数据确认外参方向稳定性。

同一 session 已增加显式 exclusion 对照能力：CLI `--exclude-pairs` 只过滤内存角点，
不修改 captures；`--output-tag` 创建独立目录并拒绝覆盖；preflight/report/YAML/NPZ
均记录 `excluded_pair_indices`，独立 validator 会按相同排除集复读原图。11 tests PASS。
Run A 使用全部 42 对；Run B 显式排除 pair 4、27 后使用 40 对。B 相对 A：LEFT/
RIGHT/Stereo RMS 从 0.09285/0.09126/0.23666 降为 0.09172/0.08617/0.15717 px；
epipolar mean/p95/max 从 0.22946/0.60849/1.55579 降为
0.16704/0.43912/1.01111 px；baseline 从 64.88463 变为 64.44372 mm；R 差
0.10164°，Tz 从 +4.8283 变为 +2.3132 mm。共同 40 对公平评估中 B 的 mean/p95
同样下降，但 max 略升。两套均保持径向单调、状态均为 WARNING；经人工工程决策，
Run B 被指定为后续开发默认参数，但不改写成学术意义上的最终标定。
pair 34 原图清晰、完整且清晰度高于中位数，不满足条件，故未生成 Run C。原始
LEFT/RIGHT/FULL 数量仍为 42/42/42，metadata SHA-256 前后均为
`e61cda539c8f9cd34dce59897294d9e34ca2fea70bb93ff546eff9e495d7eedc`。

### Stage 2 最终收口（2026-09-14）

当前默认参数文件：

`~/drone_stage2/stereo_calibration/stereo_calibration_output/20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml`

Run B 使用 40 对有效观测，明确记录 `excluded_pair_indices=[4,27]`；LEFT/RIGHT/
Stereo RMS 为 0.09172/0.08617/0.15717 px，baseline 64.4437 mm，T 为
`[-64.401, -0.348, +2.313] mm`，vertical epipolar mean/p95/max 为
0.1670/0.4391/1.0111 px，左右径向诊断均保持单调。原始 42 对 LEFT/RIGHT/FULL
captures 未被删除、移动或改写。

Run B 驱动的实时验证链已打通：rectify maps → `remap` → StereoSGBM → CV_16S
disparity `/16.0` → Q 重投影 → 7×7 邻域 median Z。纹理目标卷尺实测如下：

| 标称距离 | 实测结果 | 相对情况 |
|---:|---:|---:|
| 0.5 m | 511–523 mm，均值约 517 mm | 约 +2%～+4% |
| 1.0 m | 982 / 1003 / 986 mm | 约 0.98–1.00 m |
| 1.5 m | 均值约 1478.6 mm | 约 -1.4% |
| 2.0 m | 均值约 1968 mm | 约 -1.6% |
| 2.5 m | 约 2475 mm | 约 -1.0% |

距离随真实距离单调增长，未发现 disparity ×16、长度单位 ×1000、Q 符号或左右目交换
错误。120 s 相机稳定性实测平均 58.76 FPS、平均帧间隔 17.02 ms、读取失败 0，未见
持续超过 50 ms 的卡顿；原生 V4L2 复测为 58.71–58.77 FPS。

因此 Stage 2 Gate 记为 PASS。此 PASS 表示 Stage 3 所需的工程几何底座已成立，不表示
深度达到生产级：Run B 仍是开发期参数；跨 session 的 Tz 比 Tx/baseline 更敏感；当前
深度证据主要来自静态纹理目标；低纹理、反光、遮挡边界、重复纹理和动态人体仍是已知
风险；同一 UVC SBS 帧已确认，但尚未单独证明双传感器曝光级硬件同步。这些事项记录为
后续风险，不阻塞 Stage 3。

Stage 2 到此冻结：不继续采棋盘、不删除旧 captures、不启动 fisheye 对照、不调优
StereoSGBM、不做 CUDA/ROS 2 depth/点云扩展。完整交接见
`STAGE2_STEREO_CAMERA_FINAL.md`。

## Stage 3 Pose + Quality + Normalization（2026-09-14）

正式工程已创建：

- 远端：`~/drone_stage3_pose`
- 独立环境：`~/venvs/drone_stage3`
- Windows 重要自主算法备份：`D:\FlyCommanderByVision\drone_stage3_pose`

Stage 2 源码、captures、Run B 参数和 `~/venvs/drone_stage2` 均未修改；复核时 Stage 2
环境仍为 OpenCV 4.10.0 / NumPy 1.26.4，Run B YAML SHA-256 为
`9730eb49d136d426b84846319c7c3fecaf73dbd74be3f38ac4c376b40843e174`。

V1 backend 为官方 MediaPipe Tasks Pose Landmarker Full 1.0.1、CPU/XNNPACK、VIDEO
mode；OpenCV contrib 固定 4.10.0.84，NumPy 固定 1.26.4。Full model SHA-256 为
`4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad`。
输入链固定为 `/dev/video0` 2560×960 SBS → physical LEFT 1280×960 → Run B
rectification → BGR→RGB → Pose。RIGHT/depth 不参与 V1 主推理。

实现了 backend-neutral `PoseBackend`、`PoseFrame.poses[]`、`PersonPoseV1`、带明确原因的
`PoseQualityV1` 和 `NormalizedSkeletonV1`。canonical joints 覆盖 nose、左右 shoulder/
elbow/wrist/hip/knee/ankle；left/right 永远是操作者解剖学左右。`local_detection_id`
只表示当前帧数组索引，不是 track/operator identity。canonical confidence 是
`min(visibility,presence)` 的项目保守派生值；阈值均为配置化工程 heuristic。

Normalization 只消除二维平移和统一尺度：center 为髋中点，scale 为肩宽与肩中点—髋
中点躯干长度的均值；不旋转、不镜像。输出有效 normalized joints、主要 bone 的
dx/dy/length、左右 elbow angle 与 upper-arm image angle（degree）；缺失依赖和退化
scale 显式 invalid，不填假 0。

远端验证：compile PASS；12/12 deterministic tests PASS（含空格键非阻塞 3 秒单次拍摄）；官方 Full backend 初始化与
空白图 no-person PASS；错误 camera path 明确拒绝；真实 `/dev/video0` 使用 Run B 连续
处理 120/120 帧并 clean release。120 帧性能：有效同步 pipeline 44.97 FPS；Pose inference
mean/median/p95 11.86/11.00/16.81 ms；total processing 24.53/19.59/29.12 ms。无人值守
画面没有确认到真人，故没有伪造 real-human Pose PASS。

当前 Stage 3 状态为 `IMPLEMENTED / MANUAL VALIDATION PENDING`，不是 PASS。需要操作员
完成正面、自然垂手、解剖学左右/双手举起、画面左右移动、约 0.8/1.5/2.0 m、遮挡、
部分出画和无人场景，并用 snapshot JSON / `normalization_debug.py` 比较归一化差异。
Stage 4 Gesture 和 Stage 5 Tracking/Ownership 未实现。

### Stage 3 最终收口（2026-09-15）

操作员已用真实 `/dev/video0` 和 Run B rectified LEFT 完成全部人工场景：正面与自然垂手、
解剖学左手/右手/双手举起、双臂平举、画面左中右、约 0.8/1.5/2.0 m、手臂部分遮挡、
身体部分出画和无人。原始 snapshot PNG/overlay/JSON 保留在远端
`~/drone_stage3_pose/outputs/snapshots`，未删除或改写，也未批量复制到 Windows。

真人有效场景 quality score 为 0.9946–0.9995；举手样本关键关节覆盖率 100%，最低
canonical confidence 为 0.9570–0.9872。中央到画面左/右的原始关节位移约 280/178 px，
normalized joint RMSE 为 0.179/0.090，平均角度差 3.77/3.58 度；约 0.8 m 到 1.5/2.0 m
的原始关节位移约 112/204 px，normalized joint RMSE 为 0.057/0.115，平均角度差
3.35/2.84 度。这些是项目工程比较值，不冒充 MediaPipe 官方 PASS 阈值。

无人样本以 `NO_POSE` 拒绝；右臂遮挡样本以缺失 elbow/wrist 拒绝；三次顶部/右侧真实
出画均以 `HEAVY_TRUNCATION + LOW_CONFIDENCE + MISSING_ARMS` 拒绝，且不生成有效
NormalizedSkeleton。另有一张被椅子严重遮挡但仍被模型高置信度补全的样本被 Quality
V1 接受，保留为 hard negative/已知局限；不同身高真人泛化尚未实测。

最新真实操作运行持续约 31 分钟、处理 62,962 帧；camera read mean/median/p95 为
8.58/7.57/13.99 ms，pose inference 为 13.82/11.60/25.06 ms，total processing 为
24.09/21.09/39.45 ms，有效同步 pipeline FPS 33.75。12/12 deterministic tests PASS。
PersonPoseV1、PoseQualityV1 和 NormalizedSkeletonV1 接口冻结。因此 **Stage 3 Gate = PASS**。
此结论不包含 Gesture 分类、Tracking 或 Operator Ownership；这些仍属于后续 Stage。

## Stage 4 Gesture Baseline（2026-09-16）

### 2026-09-23 新 session 样本审计

NUC `~/drone_stage4_gesture/datasets/20260923_051541_UTC` 有 26 段 / 5,061 帧。
按操作员确认，将误标的 `sequence_0017_HOVER`、`sequence_0018_HOVER` 显式重标为
UNKNOWN（含逐帧 ground truth），原件保存在 `relabel_backup/`，改动哈希记录于
`relabel_audit.jsonl`，没有删除原始数据。新 session 标签分布为 LEFT 4、RIGHT 3、
ASCEND 3、DESCEND 3、HOVER 3、UNKNOWN 10，无空段或混合标签。

冻结算法重算：16/16 合法序列至少一次正确稳定确认，未见错误合法标签；10/10 负样本
序列、0/3385 负样本帧产生稳定合法误触。五类序列级 precision/recall 在这个小样本上
均为 1.0，但每类仅 3–4 段，不能外推为 Gate PASS。确认延迟通常 300–344 ms，
`sequence_0013_DESCEND` 为 978 ms；8/16 合法段最长持续稳定正确识别不足 1.5 s。
整段帧级合法拒绝率约 48.2%，但含准备/过渡帧，未标注动作起止，不能当作保持动作的
recall。报告在 `drone_stage4_gesture/reports/heldout_20260923_051541_recomputed.json`。

合并开发 Pilot 后，现有合法段 LEFT 7、其余各 6，负样本 17；按项目最低目标还缺
14 段合法动作（LEFT 2，其余各 3）。新段人体中心仍主要在画面中部，未证实画面左/右
覆盖；JSONL 未记录实际距离，也不含原视频，不能独立检查模糊/姿势质量。建议补足
位置和距离矩阵、每段稳定保持约 2–3 秒后再人工验收。Stage 4 仍为 **IN PROGRESS**，
不提前进入 Stage 5 或飞控闭环。

### Stage 4 实现与基线（2026-09-16）

Stage 4 已正式启动，当前状态为 **IMPLEMENTED / REAL GESTURE DATASET PENDING**，尚未
标记 Gate PASS。新工程位于远端 `~/drone_stage4_gesture`，重要自主算法和文档同步到
Windows `D:\FlyCommanderByVision\drone_stage4_gesture`。未修改已冻结 Stage 3 源码、
模型、Run B 参数或原始 snapshots。

Gesture V1 冻结为五个刻意动作：解剖学左臂水平伸直 + 对侧垂手为 LEFT，右侧镜像为
RIGHT，双臂直线上举/Y 形为 ASCEND，双臂直线向外斜下/倒 Y 为 DESCEND，双上臂水平
外展且双肘约 90 度、前臂向上的“门柱形”为 HOVER。自然垂手与 T-Pose 均为 UNKNOWN；
T-Pose 明确保留给可能的 Stage 5 Operator 授权，V1 不含 ACTIVATE。

实现了只消费 `NormalizedSkeletonV1` 的 Geometry Recognizer、完整性/置信度/非有限值
拒绝、规则互斥检查、`GestureCandidateV1`、300 ms/4-frame confirm 和 120 ms/2-frame
release 的轻量 Temporal Stabilizer。INVALID 立即清除 temporal 状态；状态不包含人员
身份或 session ownership。另有真实相机 UI、三秒单张采集、带 ground-truth 的 JSONL
序列录制、snapshot/sequence 评估器及 confusion matrix、per-class precision/recall、
legal rejection、false-trigger、confirmation latency 输出。

NUC compile PASS、12/12 deterministic tests PASS；真实 `/dev/video0` 经 Run B、MediaPipe、
Stage 3 Quality/Normalize、Stage 4 Rules/Temporal 的 60 帧和 30 帧 headless smoke 均完成并
clean release。回放19个 Stage 3真人样本：1个 ASCEND正确，12个自然/过渡/T-Pose为
UNKNOWN，6个无人/遮挡/截断为INVALID，合法控制误触0；其中Stage 3曾接受的椅子遮挡
hard negative被Stage 4更严格的双臂置信度门拒绝。

上述回放没有 LEFT/RIGHT/DESCEND/HOVER 真人正样本，不能给出五类正式 precision/
recall，也不能把 Stage 4 写成 PASS。下一步只采集 `STAGE4_DATASET_PLAN.md` 定义的平衡
真人动作与 hard-negative 序列，之后以正式指标人工验收；当前未进入 Tracking、ReID、
Operator Lock、ROS 2/PX4 Intent 或飞行闭环。

### Stage 4 首轮真人 Pilot（2026-09-16）

远端 session `datasets/20260916_101001_UTC` 当前有22段：LEFT/RIGHT/ASCEND/DESCEND/HOVER
各3段，UNKNOWN 7段，无空文件和混合标签。两段误标为 HOVER 的负样本已通过审计工具
重标为 UNKNOWN；原始内容保存在 `relabel_backup/`，前后哈希与路径记录于
`relabel_audit.jsonl`，未静默改写或删除证据。

初始0.35 DESCEND外展阈值在 `sequence_0019_UNKNOWN` 对自然垂手边界姿态产生一次稳定
误确认和4帧release grace；观测到双腕最小侧向外展约0.351–0.362 body-scale。阈值扫查
表明0.40是已测试的最小修正：保留15/15合法序列确认，同时消除该负样本的全部5帧
误触。已将阈值改为0.40并新增回归测试，当前14/14 tests PASS。

用当前代码重算后，五类在开发Pilot上均为3/3序列确认，序列级precision/recall均为
1.0；0/7负样本序列、0/3197负样本帧产生稳定合法标签；从首个正确raw match到确认约
300–339 ms。由于该Pilot参与了阈值调优，这些不是held-out最终指标，Stage 4仍为
`IMPLEMENTED / REAL GESTURE DATASET PENDING`。

最低Gate集合按此前给操作员的60段执行：每类动作3个距离×3次=9段，共45段；负样本
15段。现有22段对应每类每距离1次及7个负样本；还需新增30段合法动作（每个距离补画面
左/右各一次）和8段多样负样本。这38段作为不参与调参的held-out集合。

### Stage 4 侧边位置补录审计（2026-09-23）

最新 `20260923_051541_UTC` session 又增 `0027`–`0048` 共22段合法动作/1490帧；
冻结算法重算后20/22段确认标签。LEFT、RIGHT、HOVER各4/4，ASCEND 4/5，
DESCEND 4/5。异常为 `0039_ASCEND`（132帧内无ASCEND，曾稳定输出5帧DESCEND，
骨架双臂向下，疑似误标/动作录错）和 `0040_DESCEND`（82帧仅3帧raw DESCEND，
无稳定确认；79帧失败于右腕外展，归一化中位数0.398，略低于现有0.40门槛）。
`0041`–`0044` DESCEND正常稳定确认。两段均未改标签或删除，原始JSONL仍在NUC。

合并Pilot后总计70段：合法53段（LEFT 11、RIGHT 10、ASCEND 11、DESCEND 11、
HOVER 10），UNKNOWN 17，数量已超过工程最低60段；但没有存储实际距离标签，
`0039`/`0040`需人工确认或重录，不能仅凭总数判定采集矩阵完整。最新机器报告：
`drone_stage4_gesture/reports/new_20260923_0027_0048_recomputed.json`。
Stage 4 Gate **仍待验收**；不因异常样本放宽规则或静默剔除。

### Stage 4 操作员授权排除（2026-09-23）

操作员明确要求从有效集移除上述两段。已将 `0039_ASCEND`、`0040_DESCEND` 移至同一
session 的 `excluded/`，未删除、未改标签，移动前后SHA-256一致；排除原因和哈希见
`drone_stage4_gesture/reports/EXCLUSIONS_20260923.md`。重算新增有效补录20/20段正确
稳定确认，当前session有效46段；合并Pilot共68段，合法51段（LEFT 11、其余各10），
UNKNOWN 17段。51/51合法段至少一次正确确认，17段负样本/6582帧无稳定合法误触。
有效集报告在 `drone_stage4_gesture/reports/stage4_active_pilot_plus_20260923_excluding_0039_0040.json`。
此前70段/53合法及20/22指标保留为排除前诊断记录，不再作为当前有效集口径。
Pilot曾参与调参、实际距离未存元数据、视频/姿势质量仍待核实，Stage 4 Gate尚未PASS。

### Stage 4 正式最终验收（2026-09-23）

完整决策与限制见 `drone_stage4_gesture/STAGE4_FINAL_VALIDATION_REPORT.md`。NUC 冻结代码
14/14单元测试PASS；70个JSONL文件全部可解析，时间戳/帧号单调，无空文件、低于1秒
文件或精确哈希重复。Pilot/Development为22段，冻结后Validation原始集48段（含两段
`excluded/`），操作员筛选集46段。新session保存的预测与冻结代码回放一致；Pilot因
此前0.35→0.40调参存在历史预测差异，不算独立验收集。

**Gate主口径必须使用原始Validation 48段**：合法36/38序列正确确认；LEFT 8/8、
RIGHT 7/7、ASCEND 7/8、DESCEND 7/8、HOVER 7/7；UNKNOWN 10/10段、3385帧均无
稳定合法误触。`0039_ASCEND` 出现5帧稳定DESCEND（疑似采集标签/动作不符，原视频
无法核对），`0040_DESCEND` 未稳定确认（无已证实采集故障，不能仅因模型失败而
从Gate证据排除）。操作员筛选集36/36合法正确只是对照，不替代36/38；仅暂排
`0039` 的敏感性分析为36/37。T-Pose独立标注快照1张为UNKNOWN，自然垂手快照9张
均UNKNOWN；held-out负样本没有子类型标签，不能伪称完成专项时序统计。确认延迟
36个成功样本mean 337.6ms / median 318ms / p95 341ms；可测release仅4次，最大
152ms。Stage 3椅子遮挡被Quality接受，但Stage 4拒为INVALID，保留纵深防护证据。

最终结论：**Stage 4 — CONDITIONAL PASS / ISSUE FOUND**。五动作、接口与基本安全拒绝
具备真人数据支撑，但原始集有一次跨类稳定确认、一次DESCEND漏识，且T-Pose/自然
垂手子类时序与release覆盖有限。没有改规则、阈值、原始文件或ground truth；
不把Stage 4标为无条件PASS，不启动Stage 5、ROS2/PX4或真机闭环。

### Stage 4 操作员最终确认与 Gate 收口（2026-09-23）

操作员后续明确确认 `0039_ASCEND`、`0040_DESCEND` 在录制时目标动作本身未正确完成，
属于 **OPERATOR RECORDING ERROR / INVALID GROUND TRUTH**，而非仅因识别失败就排除。
两段继续保存在 `excluded/`，未物理删除，原始诊断48段和哈希永久留档；原视频未保存，
无法独立视觉复核，排除依据明确归于操作员确认。最终有效held-out为46段：合法
36/36确认，UNKNOWN 10/10段、3385帧零稳定合法误触，合法→合法互串0。
mean/median/p95确认延迟337.6/318/341 ms。排除前可测release4次，其中1次来自
`0039`；最终有效集可测release仅3次，此覆盖限制继续保留。Pilot22段只作开发历史。
最终状态改为 **Stage 4 PASS / Gesture V1 FROZEN**；`GestureCandidateV1`、五动作、
UNKNOWN、DESCEND阈值0.40和300ms/4帧确认、120ms/2帧释放、INVALID立即reset均冻结。
详情见 `drone_stage4_gesture/STAGE4_FINAL_VALIDATION_REPORT.md`。Stage 5 可启动视觉
Tracking/Ownership/Unknown Reject，但仍不得接PX4/Gazebo/真机飞行。

### Stage 5 独立视觉基线（2026-09-23）

NUC 新建 `/home/sentinel/drone_stage5_operator`，Windows 保存自研源码于
`drone_stage5_operator/`。没有修改 Stage 3/4 冻结源码、PX4/ROS2 或第三方参考仓库，
也没有安装/升级依赖。实现多人体姿态接入、临时全局 MOT ID、T-Pose 时序授权、
与 track ID 分离的 Operator Session、保守失踪/歧义/超时拒绝、仅操作手手势授权、
逐帧 JSONL 和可视化。`TrackedPersonV1` 与 `AuthorizedGestureV1` 已定义。

本机实际 backend 是 OpenCV Kalman + 两阶段 IoU 匹配；不是官方 BoT-SORT/ByteTrack。
ReID Gallery 当前采用弱 HSV 躯干颜色描述子，不是 OSNet。原因是当前 Stage 3 venv
没有成熟 MOT/神经 ReID 运行依赖，也无可用 OSNet 权重。相似衣着和交叉遮挡时
**不能声称可靠身份保持**；不把该结果接飞控。

NUC 自动化测试 19/19 PASS；真实 `/dev/video0` 无人房间 12 帧 headless 冒烟通过；
不存在相机 `/dev/video999` 返回 fail-closed、零帧授权。尚无真人双人、交叉、
遮挡、重获身份性能数据，故 Stage 5 Gate 保持未通过。
源码当前为 2026-09-23 工作区快照，NUC Stage 5 目录尚无 Git commit。
问题与解决：原参考仓库依赖/权重不具备直接运行条件，先采用明确标注的轻量
视觉基线并保留接口；下一步采集双人对抗场景、测量误授权与 ID switch，
再决定是否引入成熟 MOT/ReID。详见 `drone_stage5_operator/STAGE5_DESIGN_V1.md`
和 `drone_stage5_operator/STAGE5_VALIDATION_REPORT.md`。

### 项目工程化与私有备份（2026-09-24）

依据 NUC 原目录进行资产盘点，建立独立 `~/FlyCommanderByVision` 复制品；原 Stage
工程与数据未移动。Windows 当前文档可访问，优先同步最新状态文档。集成仓库收录
Stage 1–5 自研源码、测试、设计及小型报告、Run B YAML；排除原始采集、视频、
JSONL 数据集、模型权重、第三方源码和构建缓存。状态与远端备份核验以
`REPOSITORY_SYNC_REPORT.md` 为准，不因仓库整理改变任何 Stage Gate。
