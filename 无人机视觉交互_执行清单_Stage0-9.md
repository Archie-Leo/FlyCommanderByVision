# 无人机视觉交互系统：执行清单（Stage 0 → Stage 9）

> 用法：每完成一项，把 `[ ]` 改成 `[x]`。  
> 原则：**每个 Stage 的 Gate 没有全部 PASS，不进入下一 Stage。**  
> 总路线参考：`无人机视觉交互_开源参考与技术路线_v2_工程仿真版.md`

---

# 当前总进度

- [x] Stage 0 — 数字飞行底座
- [x] Stage 1 — ROS 2 Offboard 控制闭环
- [x] Stage 2 — 真实摄像头接入（2026-09-14 Gate PASS；开发默认 Run B）
- [x] Stage 3 — Pose + Normalize（2026-09-15 Gate PASS）
- [x] Stage 4 — Gesture Baseline（2026-09-23：PASS / Gesture V1 FROZEN；两段无效ground truth由操作员明确确认）
- [ ] Stage 5 — Tracking + Operator Lock + Unknown Reject
- [ ] Stage 6 — Real Camera Hybrid Closed Loop
- [ ] Stage 7 — 压力测试 / Fault Injection
- [ ] Stage 8 — Temporal Model Upgrade
- [ ] Stage 9 — Jetson / PX4 真机迁移

当前阻塞/旁路工作（更新至 2026-09-14）：

- [x] PRE-STAGE-5 Operator Ownership 六仓库静态源码审计
- [x] 输出 `OPERATOR_LOCK_OPEN_SOURCE_AUDIT.md`
- [x] 输出 `OPERATOR_OWNERSHIP_FEASIBILITY_V1.md`
- [x] 冻结结论为 `HIGH feasibility / CONDITIONAL_GO`
- [x] 最终双目相机到货并确认 `/dev/video0` 为 2560×960 SBS 正式数据源
- [x] Stage 2 正式双目标定工具实现并完成软件/合成数据/短时相机 smoke test
- [x] 使用实体棋盘完成参数求解、几何审核与 0.5–2.5 m 静态测距验收

> 以上预研完成不勾选 Stage 5 的实现或 Gate；未安装依赖、未修改参考仓库、未开始
> Operator Ownership Runtime。

---

# Stage 0 — 数字飞行底座

参考 V2：
- §3 主开发环境冻结建议
- §4 PX4 + Gazebo + ROS 2 工程仿真底座
- §12 时间同步
- §14 Debug / Logging / Replay
- §25 第一阶段任务优先级

## S0-01 固定开发机系统

目标：冻结 NUC / x86 正式开发环境。当前实测冻结为 Ubuntu 22.04.5 LTS，
ROS 2 Humble；不为追随文档旧建议而升级已验证稳定的系统。

执行：

```bash
lsb_release -a
uname -a
```

验收：

- [x] Ubuntu 22.04.5 LTS（当前正式冻结版本）
- [x] 网络正常
- [x] Git 正常
- [x] 磁盘空间足够
- [x] GPU 驱动/图形界面正常

> 2026-09-09 决策：保持已完成 Stage 0/官方 Offboard 实测的 Ubuntu 22.04.5 + Humble，禁止为追新破坏稳定底座。

---

## S0-02 安装 PX4 官方开发环境

执行：

```bash
cd ~
git clone https://github.com/PX4/PX4-Autopilot.git --recursive
bash ./PX4-Autopilot/Tools/setup/ubuntu.sh
```

之后：

```bash
cd ~/PX4-Autopilot
make px4_sitl
```

验收：

- [x] PX4 源码完整
- [x] setup 脚本无致命错误
- [x] `make px4_sitl` 编译成功
- [x] 记录 PX4 commit/tag（v1.17.0）

记录：

```bash
cd ~/PX4-Autopilot
git rev-parse HEAD
git status
```

---

## S0-03 启动 Gazebo X500 四轴

执行：

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500
```

验收：

- [x] Gazebo Harmonic 正常打开（8.15.0）
- [x] 场景中出现 X500
- [x] PX4 shell 正常运行
- [x] 仿真无持续报错
- [x] 四轴初始状态稳定

---

## S0-04 安装并连接 QGroundControl

安装官方稳定 AppImage，并给予执行权限：

```bash
chmod +x QGroundControl-*.AppImage
./QGroundControl-*.AppImage
```

验收：

- [x] QGC 正常启动
- [x] QGC 自动发现 PX4 SITL
- [x] 能看到 Vehicle 状态
- [x] 能看到飞行模式/位置/高度
- [x] 无关键 Preflight Error

---

## S0-05 用 PX4 自己控制起飞/降落

PX4 shell：

```text
commander takeoff
```

观察数秒后：

```text
commander land
```

验收：

- [x] X500 正常解锁
- [x] 正常起飞
- [x] 能稳定悬停
- [x] 正常降落
- [x] 无异常翻滚/失控

> 到这里证明 PX4 + Gazebo 本身是好的，暂时与我们的代码无关。

---

## S0-06 安装 ROS 2 Humble

当前正式环境使用 ROS 2 Humble；按已验证环境保持冻结。

验证：

```bash
source /opt/ros/humble/setup.bash
ros2 --help
printenv ROS_DISTRO
```

预期：

```text
humble
```

验收：

- [x] ROS 2 Humble 可用
- [x] `ros2` CLI 可用
- [x] `colcon` 可用
- [x] 每次新终端可以正确 source

---

## S0-07 建立 PX4 ROS 2 workspace

执行：

```bash
mkdir -p ~/ros2_px4_ws/src
cd ~/ros2_px4_ws/src
git clone https://github.com/PX4/px4_msgs.git
```

注意：

> `px4_msgs` 必须与 PX4 固件消息定义匹配。正式冻结版本时，要一起 pin。

编译：

```bash
cd ~/ros2_px4_ws
source /opt/ros/humble/setup.bash
colcon build
source install/setup.bash
```

验证：

```bash
ros2 interface show px4_msgs/msg/VehicleStatus
```

验收：

- [x] workspace 编译成功
- [x] `px4_msgs` 可读取（v1.17.0）
- [x] 无 message version mismatch

---

## S0-08 安装并运行 Micro XRCE-DDS Agent

Humble 当前冻结环境使用已验证的 Agent v2.4.2：

```bash
cd ~/ros2_px4_ws/src
git clone -b v2.4.2 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git

cd ~/ros2_px4_ws
source /opt/ros/humble/setup.bash
colcon build
source install/setup.bash
```

启动：

```bash
MicroXRCEAgent udp4 -p 8888
```

再启动：

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500
```

另一个终端：

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
ros2 topic list
```

验收：

- [x] Agent 与 PX4 Client 建立连接（v2.4.2）
- [x] 出现 `/fmu/...` PX4 topics
- [x] ROS 2 能读取 PX4 Vehicle 状态
- [x] ROS 2 ↔ PX4 通信持续稳定

---

## S0-09 时间同步

参考 V2 §12。

安装 Gazebo/ROS 2 bridge：

```bash
sudo apt install ros-humble-ros-gzharmonic
```

桥接 `/clock`：

```bash
ros2 run ros_gz_bridge parameter_bridge /clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock
```

要求：

- ROS 2 仿真节点：`use_sim_time=true`
- PX4 端按官方指南处理 `UXRCE_DDS_SYNCT`
- Frame / Pose / Intent / VehicleState 后续全部带 timestamp

验收：

- [x] ROS 2 `/clock` 有数据
- [x] ROS2/Gazebo 时间一致
- [x] PX4 时间策略已记录
- [x] 后续节点知道统一时间源是谁

---

## S0-10 日志能力

先确认三套日志链：

```text
ROS 2  → rosbag2
PX4    → ULog
地面站 → QGroundControl
```

至少测试：

```bash
ros2 bag record -a
```

短暂记录后停止，并验证：

```bash
ros2 bag info <bag目录>
```

验收：

- [x] rosbag2 可记录
- [x] rosbag2 可读取
- [x] PX4 ULog 能生成
- [x] QGC 可查看基本飞行状态

---

## Stage 0 Gate

全部通过才能打勾：

- [x] Gazebo X500 正常生成
- [x] PX4 SITL 正常启动
- [x] QGroundControl 正常连接
- [x] ROS 2 能看到 PX4 topics
- [x] ROS 2 ↔ PX4 通信正常
- [x] ARM / DISARM 正常
- [x] TAKEOFF 正常
- [x] HOVER 正常
- [x] LAND 正常
- [x] 仿真时间可以统一
- [x] rosbag2 / ULog 可记录

**Gate PASS → Stage 0 完成。**

---

# Stage 1 — ROS 2 Offboard 控制闭环

参考 V2：
- §5 ROS 2 Offboard 控制闭环
- §11 Control Gateway / Drone Adapter
- §13 ROS 2 通信原则

## S1-01 跑通官方 Offboard Example

执行：

```bash
cd ~/ros2_px4_ws/src
git clone https://github.com/PX4/px4_ros_com.git

cd ~/ros2_px4_ws
source /opt/ros/humble/setup.bash
colcon build
source install/setup.bash
```

确保：

1. QGC 已连接
2. MicroXRCEAgent 已运行
3. PX4 + Gazebo 已运行

然后：

```bash
ros2 run px4_ros_com offboard_control
```

验收：

- [x] 自动 ARM
- [x] 切入 OFFBOARD
- [x] 上升到目标高度
- [x] 稳定悬停
- [x] ROS2 node 停止后 PX4 有可预测安全行为

---

## S1-02 Control Gateway V1 / V1.1

2026-09-09 按最新实际任务完成。远端包：
`~/ros2_px4_ws/src/drone_control_gateway`，Git commit：
`e88b16459b812cf59d824e6c7afbe9de8db1d725`。

2026-09-10 在该稳定点之上完成 V1.1，commit：
`09776d3d234ccebabb5c86cf7aed7be1026bfe30`，未 squash V1 稳定点。

V1 只做 Intent → PX4 NED world-frame velocity setpoint，支持
`MOVE_FORWARD / MOVE_BACKWARD / MOVE_LEFT / MOVE_RIGHT / ASCEND / DESCEND /
YAW_LEFT / YAW_RIGHT / HOVER`。ARM、TAKEOFF、LAND、
飞行模式切换与普通方向控制分离，节点启动不会自动起飞。

验收：

- [x] IntentMessage 与 PX4 message 发布逻辑分离
- [x] Gateway 启动不 ARM、不 TAKEOFF，默认 HOVER
- [x] HOVER 发布 `[0, 0, 0]` NED 速度
- [x] `MOVE_RIGHT = +Y/East`、`MOVE_LEFT = -Y/West` 实飞方向正确
- [x] `ASCEND = -Z`、`DESCEND = +Z` 实飞高度方向正确
- [x] `MOVE_FORWARD = +X/North`、`MOVE_BACKWARD = -X/South` 实飞方向正确
- [x] `YAW_RIGHT = +yaw-rate`、`YAW_LEFT = -yaw-rate` 实际旋转正确
- [x] 0.5 s command timeout 自动 HOVER
- [x] 水平/垂直速度配置化并分别 clamp 至 1.0/0.7 m/s
- [x] yaw-rate 配置化并 clamp 至 0.6 rad/s
- [x] `OffboardControlMode.velocity=true` 且 heartbeat 10 Hz 持续发布
- [x] position/acceleration/jerk/absolute yaw 未控制字段显式设为 NaN；yawspeed 有限
- [x] Gateway 停止后 PX4 原生 Offboard failsafe 生效
- [x] 不修改 PX4、px4_msgs、px4_ros_com、Micro-XRCE-DDS-Agent
- [x] `colcon build` 与 tests（6 tests，0 failures）全部通过

实测轨迹（PX4 NED）：RIGHT 时 y `0.047 → 1.931 m`，LEFT 后 y 回到
`0.092 m`；ASCEND 时 z `-2.76 → -3.97 m`，DESCEND 后 z 回到
`-2.74 m`。停止 Gateway 后，状态由 OFFBOARD（14）转为 AUTO_RTL（5），
`failsafe=true`，随后安全落地。当前参数 `COM_OF_LOSS_T=1.0 s`、
`COM_OBL_RC_ACT=0`；实际无 RC 场景的最终动作以 PX4 状态机观测为准。

V1.1 实测：FORWARD 时 x `0.077 → 2.020 m`，BACKWARD 后 x 回到
`0.050 m`；YAW_RIGHT 使 heading `-2.891 → -1.829 rad`（俯视顺时针），
YAW_LEFT 使 heading `-1.802 → -2.827 rad`（俯视逆时针）。yaw-rate 超限请求
在线 clamp 为 0.6 rad/s，timeout 后回 0；heartbeat-loss 仍为
OFFBOARD（14）→ AUTO_RTL（5），`failsafe=true`。

---

## S1-03 Gateway 扩展项（原清单，V1 已由 S1-02 覆盖）

工程目录参考 V2 §20：

```text
control/
├─ gateway.py / gateway.cpp
├─ frame_transform.*
├─ px4_sim_adapter.*
└─ adapter_base.*
```

当前冻结版支持：

```text
HOVER
MOVE_FORWARD
MOVE_BACKWARD
MOVE_LEFT
MOVE_RIGHT
ASCEND
DESCEND
YAW_LEFT
YAW_RIGHT
```

映射到：

```text
position / velocity setpoint
```

验收：

- [x] Intent 与 PX4 message 隔离
- [x] 视觉侧不 import PX4 内部控制代码
- [x] 最大速度有限幅
- [x] 最大 yaw rate 有限幅
- [x] command timeout 已定义

> yaw 不属于 S1-02 V1，故最大 yaw rate 不提前实现或伪造完成状态。

---

## S1-04 坐标系单元测试

参考 V2 §5.2。

明确：

```text
ROS 常见：ENU
PX4：NED
body frame
world frame
```

做最简单的方向验证：

- [x] +X 实际方向正确
- [x] +Y 实际方向正确
- [x] +Z / 高度方向正确
- [x] yaw 正方向正确
- [x] RIGHT 不会变 LEFT
- [x] ASCEND 不会变 DESCEND

---

## S1-05 测试 Offboard failsafe

测试：

```text
飞行中
↓
停止 Control Gateway / 停止 Offboard heartbeat
↓
观察 PX4
```

验收：

- [x] PX4 不会无限执行最后一条危险指令
- [x] failsafe 行为明确（实测 OFFBOARD → AUTO_RTL）
- [x] 行为记录到日志

---

## Stage 1 Gate

- [x] ROS2 可独立控制 X500
- [x] 起飞（S1-01 官方 Baseline；与 Gateway 普通方向控制分离）
- [x] 悬停
- [x] XYZ 移动
- [x] yaw
- [x] 降落（PX4 commander/failsafe 路径）
- [x] 坐标系全部验证
- [x] Offboard loss 行为验证
- [x] Gateway API 冻结第一版

**Gate PASS → Stage 1 完成。**

**2026-09-10：Stage 1 Gate PASS，控制层冻结，下一阶段为 Stage 2。**

---

# Stage 2 — 真实摄像头

参考 V2：
- §6 Hardware-Camera-in-Simulation
- §9 M1 Camera / Frame Source
- §20 perception/camera

## S2-01 USB Camera 打开

使用 OpenCV 或 ROS camera 节点。

输出统一：

```text
FramePacket
timestamp
frame_id
image
width
height
source_id
```

验收：

- [x] 稳定读取真实摄像头（长期实测约 58.7 FPS；正式工具另完成连续 5 帧 smoke）
- [ ] timestamp 正确
- [ ] frame_id 单调递增
- [x] FPS 可统计（实时 UI 基于实际 monotonic frame interval，不写死 1/60）
- [ ] 丢帧可统计

> 120 s 读取稳定性已验证：平均 58.76 FPS、平均 17.02 ms、读取失败 0，未见持续
> 超过 50 ms 卡顿。独立硬件 timestamp、frame_id/drop telemetry 属于后续接口增强，
> 不据此虚构传感器曝光级同步结论，也不阻塞本次 Stage 2 Gate。

## S2-CAL 正式双目标定工具

2026-09-14 创建于远端 `~/drone_stage2/stereo_calibration`，Windows 保存重要源码与
研究文档备份 `stereo_calibration/`。

- [x] OpenCV 4.x 官方资料研究与 `CALIBRATION_RESEARCH.md`
- [x] 11×8 内角点、45.0 mm object points 与 mm 单位契约
- [x] 原始 2560×960 SBS 严格拆分为 LEFT/RIGHT 1280×960
- [x] `findChessboardCornersSB` full-resolution 检测与角点可视化
- [x] S/Space 人工保存；任一侧失败或 ordering mismatch 时拒绝
- [x] UTC session、无损 LEFT/RIGHT/FULL PNG、metadata 与可恢复 D 删除
- [x] C 从磁盘重读并验证 pairing、尺寸、损坏、双侧角点和 metadata
- [x] 左右 `calibrateCameraExtended` 与 per-view error
- [x] `stereoCalibrateExtended + CALIB_FIX_INTRINSIC` 输出 R/T/E/F
- [x] `baseline_mm = norm(T)`，65 mm 仅作 sanity reference
- [x] `stereoRectify` 输出 R1/R2/P1/P2/Q/ROI
- [x] rectify maps、remap、水平极线 preview
- [x] rectified corner vertical error mean/median/p95/max
- [x] OpenCV YAML、NPZ、report、独立 validation round-trip
- [x] R、baseline、K/D、径向单调性、per-view outlier、重复姿态工程检查
- [x] 远端 OpenCV 4.10.0 / NumPy 1.26.4：11 tests PASS
- [x] 真实相机短时打开、5/5 帧、尺寸拆分与 release smoke PASS
- [x] 首轮 30 对实体候选标定及报告审计（`20260914_051811_UTC`）
- [x] 首轮 baseline 64.8068 mm、vertical p95 0.2358 px、六张 preview 已核对
- [x] 第二轮 42 对覆盖上/中/下与边缘，角点约 x=65–1180、y=152–938 px
- [x] 第二轮左右径向模型单调，结果由 `SUSPICIOUS` 提升为 `WARNING`
- [x] 第二轮 baseline 64.8846 mm、vertical p95 0.6085 px、六张 preview 已核对
- [x] 复核 pair 4/27 并以显式 exclusion 生成 Run B；未删除或改写原始 captures
- [ ] 用独立新 session 确认外参重复性，尤其 Tz（记录为后续精度风险，不阻塞 Stage 3）
- [x] 新增显式 `--exclude-pairs`、`--output-tag`、排除记录和防覆盖机制
- [x] Run A 全 42 对、Run B 排除 4/27 的对照标定与独立 revalidation
- [x] Run B 相比 A 降低 stereo RMS 与 epipolar mean/p95；K/R 较稳定，Tz 变化已记录
- [x] pair 34 人工/清晰度检查无明显质量问题，按条件不生成 Run C
- [x] 排除实验后 captures 仍为 42/42/42，metadata SHA-256 前后一致
- [x] 实体第二轮采集 42 组多位置/距离/倾角 pair（数量为工程选择，非官方阈值）
- [x] 实体 LEFT/RIGHT/stereo RMS 与 per-view error 审核
- [x] 实体 `norm(T)` 与约 65 mm 物理 baseline sanity check
- [x] 实体 rectified previews 人工审核
- [x] 实体 vertical epipolar error 指标审核

> 实体标定与几何审核已完成。开发默认使用 Run B 的标准 pinhole 5 参数模型；当前
> 不启动 fisheye 对照。Run B 保持 `DEVELOPMENT/PROVISIONAL` 标签，不能包装成
> OpenCV 官方认证或生产级最终参数。

## S2-DEPTH Run B 实时测距验证工具

- [x] Run B calibration.yaml 只读加载并核对 excluded `[4,27]`
- [x] R1/R2/P1/P2/Q 构建 1280×960 rectify maps，无重新标定
- [x] StereoSGBM 合法参数集中配置，CV_16S disparity 明确 `/16.0`
- [x] `reprojectImageTo3D`、无效 disparity/Z 过滤、7×7 median 点击测距
- [x] R 输入人工 ground truth、L 无 GT 记录、S 快照、CSV 输出
- [x] 远端 3 tests PASS；真实 `/dev/video0` 单帧 smoke、remap/SGBM/Q PASS
- [x] 卷尺 0.5/1.0/1.5/2.0/2.5 m 纹理目标实测，距离单调且误差约在 ±4% 内

工具：`~/drone_stage2/stereo_depth_validation`。本项只证明工程测距链和量级正确，
不代表 SGBM 已完成生产级调优。

## S2-02 Camera 与飞行仿真同时运行（转 Stage 6）

同时开：

```text
Gazebo + PX4 + QGC
ROS2
USB Camera
```

验收：

- [ ] 不互相卡死
- [ ] CPU/GPU/内存有余量
- [ ] Camera 延迟稳定
- [ ] rosbag 能同时记录 Camera + PX4 状态

> 本节是完整 Hardware-Camera-in-Simulation 闭环集成，不再作为 Stage 2 Camera
> Geometry Gate 的阻塞项；保留原任务并在 Stage 6 执行，不在本轮提前勾选。

## Stage 2 Gate

- [x] `/dev/video0`、USB 3.0/UVC、2560×960 MJPEG SBS 与物理 LEFT/RIGHT 映射确认
- [x] 120 s 连续采集约 58.7 FPS，读取失败 0
- [x] 同一 SBS frame 的初始双目同步与无损 session 数据集保存/复算链确认
- [x] 42 对全画幅 pinhole 标定、Run A/B 对照、rectification 与极线误差验证
- [x] Run B baseline/T/R/K/D 合理，径向单调；作为 Stage 3 开发默认参数
- [x] StereoSGBM disparity `/16.0`、Q→3D/Z 与 0.5–2.5 m 静态纹理目标实测通过

**2026-09-14：Stage 2 Gate PASS。** 后续进入 Stage 3；不继续扩大 Stage 2。
S2-02 中 Gazebo/PX4/ROS 2 的完整混合闭环归入 Stage 6，不作为本次相机几何底座
收口的阻塞项。

---

# Stage 3 — Pose + Normalize

参考 V2：
- §9 M2 Person / Pose
- §9 M4 Pose Normalization
- §18 Pose / Tracking 技术选择

## S3-01 MediaPipe Pose Baseline

先用成熟方案快速拿到人体关键点。

目标：

```text
Camera
↓
PersonPose
↓
Skeleton overlay
```

验收：

- [x] 正面稳定
- [x] 举手稳定
- [x] 左右移动稳定
- [x] 不同距离可用
- [x] 每帧输出 canonical confidence/visibility/presence/valid
- [x] MediaPipe Tasks Pose Landmarker Full backend 与 canonical PersonPose 解耦
- [x] Run B rectified physical LEFT 为唯一 V1 Pose 主输入；未在 RIGHT 重复推理
- [x] `poses[]` 接口保留；`local_detection_id` 明确不是 track_id/operator_id
- [x] 无人输出 `poses=[]` / `NO_POSE`，不伪造检测

## S3-02 NormalizedSkeleton

实现：

```text
body center
shoulder / torso scale
joint angle
bone vector
quality
```

验收：

- [x] 合成骨架整体平移，normalized feature deterministic invariant
- [x] 合成骨架统一缩放及平移+缩放，normalized feature deterministic invariant
- [x] 真人左右移动，normalized feature 基本不变
- [x] 真人距离变化，normalized feature 基本不变
- [ ] 不同身高影响降低
- [x] anatomical left/right 不因 normalization 合并或翻转
- [x] body center/scale、normalized joints、bones、angles 和 quality V1 已实现
- [x] missing/low confidence 与 zero-scale 显式拒绝，无 NaN/crash

## Stage 3 Gate

- [x] Camera → Pose → Normalize 持续实时运行（约 31 分钟 / 62,962 帧）
- [x] Pose FPS / latency 有 NUC 120-frame 正式软件记录
- [x] PersonPose V1 接口冻结
- [x] NormalizedSkeleton V1 接口冻结
- [x] 自动测试 12/12 PASS；真实 Camera/Run B rectification/推理/clean release smoke PASS
- [x] 真人 Pose/Quality/Normalization 场景清单全部通过

**2026-09-15：Stage 3 Gate PASS。** 真人完成正面/自然垂手、解剖学左手/右手/双手举起、
画面左中右、约 0.8/1.5/2.0 m、手臂遮挡、身体出画和无人场景。位置对照 normalized
joint RMSE 为 0.090–0.179，距离对照为 0.057–0.115（工程比较值，不是官方阈值）；
无人、低质量手臂遮挡及三次越界截断均被 Quality Gate 拒绝。椅子严重遮挡但模型仍以
较高置信度补全的一张额外样本保留为已知 hard negative；不同身高真人泛化仍未实测。

---

# Stage 4 — Gesture Baseline

参考 V2：
- §9 M5 Gesture / Action Recognizer
- §17 IITK
- §21 Stage 4

## S4-01 先定义动作协议

第一版只选 4–6 个高区分度动作。

例如：

```text
RIGHT
LEFT
ASCEND
DESCEND
HOVER
ACTIVATE
```

验收：

- [x] Gesture V1 五动作语义冻结；T-Pose 保留给 Stage 5，V1 不含 ACTIVATE
- [x] 解剖学 LEFT/RIGHT 使用单侧水平直臂 + 对侧垂手，规则互斥
- [x] 仅使用肩/肘/腕和归一化几何，不依赖小手指动作

## S4-02 几何规则 Baseline

输入：

```text
NormalizedSkeleton
```

输出：

```text
GestureCandidate
```

- [x] `GestureCandidateV1` 接口冻结
- [x] Geometry rules、完整性/互斥拒绝、UNKNOWN/INVALID 显式输出
- [x] 300 ms/4-frame confirm、120 ms/2-frame release；INVALID 立即清状态
- [x] 输入严格为 `NormalizedSkeletonV1`，未读取 MediaPipe 原始对象

## S4-03 建第一版测试集

每个动作：

- 多次重复
- 不同距离
- 不同角度
- 不同动作幅度

输出：

- [x] confusion matrix（序列级原始集与筛选集）
- [x] precision/recall（五类、支持数与原始/筛选口径）
- [x] false trigger samples（含 UNKNOWN、INVALID 支持限制与合法互串事件）

已完成软件与初始回放底座：14/14 tests PASS；19 个 Stage 3 真人样本得到 1 个正确
ASCEND、12 个正确 UNKNOWN、6 个正确 INVALID，false legal trigger=0。由于 LEFT/RIGHT/
DESCEND/HOVER 尚无真人正样本，该结果不得作为 Stage 4 Gate 指标；需按
`STAGE4_DATASET_PLAN.md` 采集平衡序列数据集。

2026-09-16 真人Pilot已录22段：五类各3段、UNKNOWN 7段。初始DESCEND外展阈值0.35
在一个负样本中产生一次确认和4帧释放缓冲；显式修订为0.40后，用当前代码重算得到
15/15合法序列正确确认、0/7负样本序列和0/3197负样本帧误触。该Pilot参与过调参，
只能作为开发集；最低60段Gate集合还需新增30段合法动作和8段held-out负样本。

2026-09-23 新 session `20260923_051541_UTC` 录得26段；经操作员确认，误标HOVER的
`sequence_0017`、`sequence_0018` 已审计重标为UNKNOWN，原件备份、哈希留痕，未删除。
新 session 当前合法16段、UNKNOWN 10段；冻结算法重算为16/16合法段至少一次正确
稳定确认、10/10负样本段（3385帧）零稳定合法误触。正式 JSON 报告包含混淆矩阵、
逐类precision/recall、拒绝率和确认延迟。样本每类仅3–4段，且画面左右位置、实际
距离、长时间持续识别质量尚未充分验证；不能勾选 Stage 4 Gate。结合Pilot，负样本
17段已达数量目标，但合法动作仍缺14段（LEFT 2，其余四类各3）。

2026-09-23 后续侧边位置补录 `0027`–`0048` 增22段合法动作；合并Pilot后共70段，
其中合法53段、负样本17段，数量目标已达。冻结算法重算新增段为20/22正确稳定
确认。`0039_ASCEND` 实际骨架双臂向下，曾稳定判DESCEND 5帧，疑似录制标签/动作
不符；`0040_DESCEND` 因右腕外展中位数0.398低于当前0.40门槛而未稳定确认。
两段原始文件未动，需人工确认或重录；实际距离未编码，位置×距离矩阵仍待核实。
不能静默剔除/改标，也不能据此勾选Stage 4 Gate。

随后操作员明确授权将两段从有效集移出：已移至同session `excluded/`，原数据与
SHA-256保留，排除记录见 `drone_stage4_gesture/reports/EXCLUSIONS_20260923.md`。
有效集重算：新增补录20/20段正确确认；合并Pilot共68段（合法51、负样本17），
51/51合法段至少一次正确确认、17段负样本/6582帧零稳定合法误触。排除前报告仍
保留作诊断，当前指标以 `stage4_active_pilot_plus_20260923_excluding_0039_0040.json`
为准。数量目标已达，但距离元数据、图像质量和完整位置×距离矩阵尚待核实；Gate不勾选。

## Stage 4 Gate

- [x] 4–6 Gesture 基本可用（五类真人有效held-out均正确确认，限制见最终报告）
- [x] 有正式指标而不是“感觉挺准”
- [x] GestureCandidate 接口冻结

2026-09-23 正式决策：**CONDITIONAL PASS / ISSUE FOUND**。正式报告为
`drone_stage4_gesture/STAGE4_FINAL_VALIDATION_REPORT.md`；Pilot 22段只作开发历史，
冻结后原始验证48段为Gate主口径：合法36/38段正确、UNKNOWN 10/10段及3385帧零
稳定合法误触；`0039_ASCEND` 有5帧稳定DESCEND，`0040_DESCEND` 无稳定确认。
操作员筛选集46段/合法36/36只作敏感性对照；`0040` 无已证实的采集违规，不能因
识别失败而合法排除。T-Pose/自然垂手专项时序、release覆盖仍有限；不调参、不补采、
不进入Stage 5或飞控闭环。此前“只以排除后有效集为准”的进度记述已被本结论覆盖。

操作员后续明确确认 `0039_ASCEND`、`0040_DESCEND` 为目标动作未正确执行的录制
错误/无效ground truth，两段保留在 `excluded/` 并永久记账，不能把依据写成
“模型错所以删样本”。最终有效held-out 46段：合法36/36、UNKNOWN 10/10段及3385帧
零稳定合法误触、合法互串0；排除前48段/合法36/38继续作为原始诊断。可测release
原始4次、最终有效3次，覆盖不足保留限制。**Stage 4 Gate = PASS / Gesture V1 FROZEN**；
五动作、GestureCandidateV1、Geometry V1、Temporal V1、DESCEND 0.40均冻结。
Stage 5可以开始视觉Ownership V1，但不接ROS2/PX4/Gazebo/真机。

---

# Stage 5 — Tracking + Operator Lock + Unknown Reject

2026-09-23 状态：独立视觉基线已实现，19/19 合成测试与 12 帧空场相机冒烟通过；
真人双人、交叉、遮挡和误授权指标尚未验证。Stage 5 Gate 保持 [ ]。
实现与限制见 `drone_stage5_operator/STAGE5_DESIGN_V1.md`、
`drone_stage5_operator/STAGE5_VALIDATION_REPORT.md`。目前的 Kalman+IoU tracker
与 HSV 颜色 Gallery 不是正式 BoT-SORT/OSNet，禁止推断产品级身份可靠性。

参考 V2：
- §9 M3 Tracking + Operator Lock
- §9 M6 Temporal Stabilizer + Unknown Reject
- §9 M7 Safety FSM
- §15 Fault Injection
- §24 Competition Innovation Points

## S5-01 Tracking

Baseline：

```text
YOLO Pose + ByteTrack
或
Pose + 独立 Tracker
```

验收：

- [ ] 两个人可分 ID
- [ ] 交叉时记录 ID switch
- [ ] 短暂遮挡可恢复

## S5-02 Operator Lock

实现：

```text
Acquire
Lock
Maintain
Release
Lost
Reacquire
```

验收：

- [ ] A 是 operator
- [ ] B 做 Gesture 不生效
- [ ] A 离场进入 lost
- [ ] 可重新获取控制权

## S5-03 Temporal Stabilizer

至少：

```text
sliding window
stable_ms
confidence threshold
timeout
```

## S5-04 Unknown Reject

必须支持：

```text
UNKNOWN
INVALID
TIMEOUT
```

验收：

- [ ] 挠头不触发
- [ ] 随便挥手不触发
- [ ] 转身不触发
- [ ] 低置信度不触发
- [ ] 非 operator 不触发

## Stage 5 Gate

- [ ] 多人可用
- [ ] 控制权唯一
- [ ] UNKNOWN 可拒绝
- [ ] Vision timeout 有安全路径
- [ ] Safety FSM V1 完成

---

# Stage 6 — Real Camera Hybrid Closed Loop

参考 V2：
- §1 主架构
- §6 真实摄像头混合仿真
- §26 第一阶段正式验收场景

正式链路：

```text
真人
↓
USB Camera
↓
Pose
↓
Tracking
↓
Operator Lock
↓
Gesture
↓
Unknown Reject
↓
Safety FSM
↓
Intent
↓
Control Gateway
↓
ROS2
↓
PX4
↓
Gazebo X500
```

## S6-01 单人闭环

- [ ] RIGHT → X500 右移
- [ ] LEFT → X500 左移
- [ ] ASCEND → 上升
- [ ] DESCEND → 下降
- [ ] HOVER → 悬停

## S6-02 多人闭环

- [ ] A 获取 operator
- [ ] B 做 RIGHT → 不动
- [ ] A 做 RIGHT → 右移
- [ ] A 消失 → HOVER

## S6-03 延迟记录

至少记录：

```text
Frame timestamp
Gesture timestamp
Intent timestamp
PX4 setpoint timestamp
Vehicle response
```

验收：

- [ ] gesture → intent latency
- [ ] intent → setpoint latency
- [ ] end-to-end latency
- [ ] operator lost → hover latency

## Stage 6 Gate — 第一重大里程碑

- [ ] 真实人可以稳定控制 Gazebo X500
- [ ] 非 operator 无法控制
- [ ] UNKNOWN 不控制
- [ ] operator lost 安全
- [ ] 日志可完整回放

**到这里：项目主架构成立。**

---

# Stage 7 — 自动化压力测试 / Fault Injection

参考 V2：
- §8 Gazebo Actor
- §14 Logging / Replay
- §15 Fault Injection
- §16 指标体系

测试矩阵：

## 人

- [ ] 1 人
- [ ] 2 人
- [ ] 3 人
- [ ] 人员交叉
- [ ] 部分遮挡
- [ ] operator 离场
- [ ] bystander gesture attack

## Camera

- [ ] frame drop
- [ ] low FPS
- [ ] delay
- [ ] disconnect

## Vision

- [ ] pose lost
- [ ] tracker ID switch
- [ ] false gesture
- [ ] low confidence

## Communication

- [ ] ROS node crash
- [ ] intent timeout
- [ ] Offboard heartbeat loss

## Flight

- [ ] wind
- [ ] position disturbance
- [ ] sensor / failsafe scenario

## 自动回归

- [ ] Gazebo Actor 测试框架
- [ ] 固定动作 sequence
- [ ] scenario runner
- [ ] 每次提交可重复跑关键场景

## Stage 7 Gate

- [ ] 已形成标准测试矩阵
- [ ] 安全失败样本全部有记录
- [ ] 系统不是只在“理想演示”下工作

---

# Stage 8 — Temporal Model Upgrade

参考 V2：
- §9 M5
- §19 时序动作识别研究环境
- §21 Stage 8

比较路线：

```text
Rules
↓
Rules + Sliding Window
↓
TCN
↓
ST-GCN / PoseC3D
```

## S8-01 自采 Skeleton Sequence 数据集

单位：

```text
T × J × (x,y,confidence)
```

- [ ] label spec
- [ ] train/val/test split
- [ ] invalid/unknown samples
- [ ] dataset version

## S8-02 TCN

- [ ] Train
- [ ] Offline metrics
- [ ] Export ONNX
- [ ] Runtime integration
- [ ] Hybrid closed-loop comparison

## S8-03 ST-GCN / PoseC3D 对比

只作为候选。

只有同时满足：

```text
识别更好
+
False Acceptance 更低
+
闭环延迟可接受
```

才替换 baseline。

## Stage 8 Gate

- [ ] 新模型闭环优于旧模型
- [ ] 不是只看离线 Accuracy
- [ ] Runtime 不依赖完整研究框架

---

# Stage 9 — Jetson / PX4 真机迁移

参考 V2：
- §22 SITL / HITL / Real Flight
- §23 真机迁移什么不应该变
- §25 P3

## S9-01 Vision Runtime 导出

```text
PyTorch
↓
ONNX
↓
TensorRT
```

- [ ] Pose 模型部署
- [ ] Gesture 模型部署
- [ ] FPS 达标
- [ ] latency 达标

## S9-02 Jetson + Camera

- [ ] 真摄像头稳定
- [ ] Runtime 稳定
- [ ] ROS2 / 网络通信稳定

## S9-03 PX4 真飞控

保持：

```text
Pose
Tracking
Operator Lock
Gesture
Unknown Reject
Safety FSM
Intent
Control Gateway API
```

只替换：

```text
PX4 SITL/Gazebo
↓
PX4 real firmware / real quadrotor
```

## S9-04 真机渐进开放

顺序：

- [ ] HOVER
- [ ] LEFT / RIGHT
- [ ] ASCEND / DESCEND
- [ ] yaw
- [ ] TAKEOFF / LAND
- [ ] 更复杂行为

## Stage 9 Gate

- [ ] 真机完成安全闭环
- [ ] SITL 和真机接口基本一致
- [ ] 比赛部署版本冻结

---

# 第一阶段总验收场景

必须完整跑通：

```text
Gazebo X500 正常悬停
        ↓
真实 Camera 看到 A / B
        ↓
A 获取 operator
        ↓
B 做 RIGHT
        ↓
系统拒绝
        ↓
A 做 RIGHT
        ↓
稳定确认
        ↓
MOVE_RIGHT
        ↓
PX4 Offboard
        ↓
X500 右移
        ↓
A 离场
        ↓
operator lost
        ↓
HOVER / safety path
```

- [ ] 全链路 PASS

到这里之后，项目工作重点从“搭架构”转为“提高算法和系统指标”。
