# Stage 2 Stereo Camera Final Handoff

> 日期：2026-09-14（Asia/Shanghai）  
> 状态：**PASS — Stage 3 engineering foundation**  
> 参数等级：**DEVELOPMENT/PROVISIONAL**，不是生产级或学术最终标定

## 1. 收口结论

Stage 2 已完成真实双目相机的数据入口确认、持续采集、无损数据集、单目/双目标定、
rectification、极线误差、disparity、Q→3D 和实体卷尺距离验证。现有证据足以进入
Stage 3 Pose + Pose Quality + Skeleton Normalization。

本次 PASS 的含义是“后续感知开发所需的相机几何链成立”，不是“所有动态场景和深度
算法均达到生产级”。Stage 2 到此冻结，不再增加棋盘采集、模型比较或深度优化工作。

## 2. 冻结硬件与数据契约

| 项目 | 冻结事实 |
|---|---|
| Camera | 深圳德创信双目 RGB USB Camera |
| Linux device | `/dev/video0`；`/dev/video1` 不是右目采集节点 |
| USB/UVC | USB 3.0 5000M，`uvcvideo` |
| Raw format | MJPEG 2560×960 Side-by-Side |
| LEFT | `frame[:, 0:1280]`，已由遮挡物理左镜头确认 |
| RIGHT | `frame[:, 1280:2560]`，已由物理实验确认 |
| Per-eye calibration resolution | 1280×960，禁止先缩到 640×480 再套用本参数 |
| Shutter/FOV | Global Shutter（标称），约 102° |
| Nominal baseline | 约 65 mm，仅作 sanity reference |
| Board | 11×8 inner corners，square size 45.0 mm |
| Camera model | OpenCV pinhole radial/tangential，5 distortion coefficients |

相机 120 s 实测平均 58.76 FPS、平均帧间隔 17.02 ms、读取失败 0，未见持续超过
50 ms 的卡顿；原生 V4L2 复测为 58.71–58.77 FPS。任何后续模块必须使用真实时间戳
或实测帧间隔，禁止写死 `dt=1/60`。

## 3. 标定数据与默认参数

第二轮源 session：`20260914_092939_UTC`，共 42 对全分辨率 LEFT/RIGHT/FULL PNG，
角点覆盖约 x=65–1180、y=152–938 px。原始 captures 必须永久保留，不得因 exclusion
实验删除、移动、重命名或覆盖。

### 3.1 当前开发默认：Run B

```text
~/drone_stage2/stereo_calibration/stereo_calibration_output/
20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml
```

- `excluded_pair_indices = [4, 27]`，只在重算时过滤；原始 42 对不变；
- valid pairs：40；
- LEFT RMS：0.09172 px；
- RIGHT RMS：0.08617 px；
- Stereo RMS：0.15717 px；
- baseline：64.4437 mm；
- T：`[-64.401, -0.348, +2.313] mm`；
- rectified vertical error mean/p95/max：0.1670/0.4391/1.0111 px；
- 左右径向映射工程诊断：monotonic。

Run B 相比全 42 对 Run A，Stereo RMS 下降约 33.6%，极线 mean 下降约 27.2%，p95
下降约 27.8%；共同保留的 40 对上 aggregate 同样改善。K、R、Tx、baseline 保持较
稳定，Tz 对子集较敏感。Run B 的选用是人工工程决策，不是程序按“最漂亮 RMS”自动
选择，也不消除重新采集独立 session 检查外参重复性的价值。

### 3.2 保留的历史结果

- 首轮 30 对 `20260914_051811_UTC`：覆盖不足且左目外角径向诊断非单调，状态
  `SUSPICIOUS`，不得作为默认参数；
- Run A 全 42 对：baseline 64.8846 mm，Stereo RMS 0.23666 px，vertical p95
  0.6085 px，保留作对照；
- pair 34 经原图与清晰度检查没有明显问题，因此未生成条件 Run C；
- exclusion 前后 LEFT/RIGHT/FULL 均为 42/42/42，metadata SHA-256 保持
  `e61cda539c8f9cd34dce59897294d9e34ca2fea70bb93ff546eff9e495d7eedc`。

## 4. Rectification 与深度实测

验证工具：`~/drone_stage2/stereo_depth_validation`。它只读 Run B 参数，执行：

```text
R1/R2/P1/P2/Q
  → initUndistortRectifyMap + remap
  → StereoSGBM CV_16S disparity
  → disparity.astype(float32) / 16.0
  → reprojectImageTo3D(Q)
  → invalid mask + 7×7 median Z
```

纹理目标卷尺结果：

| Ground truth | Observed Z | Approx. error |
|---:|---:|---:|
| 0.5 m | 511–523 mm，均值约 517 mm | +2%～+4% |
| 1.0 m | 982 / 1003 / 986 mm | -1.8%～+0.3% |
| 1.5 m | 均值约 1478.6 mm | -1.4% |
| 2.0 m | 均值约 1968 mm | -1.6% |
| 2.5 m | 约 2475 mm | -1.0% |

结果随真实距离单调增长，量级、符号和单位正确；未发现 disparity ×16、长度单位
×1000、Q 符号或左右目交换错误。该结果只验证静态纹理目标上的 baseline pipeline，
不把当前约 9 FPS 的 CPU SGBM 当作最终深度方案。

## 5. Stage 2 Gate

- [x] `/dev/video0` 枚举、USB 3.0/UVC 与 MJPEG 2560×960 SBS 确认；
- [x] LEFT/RIGHT 与物理镜头映射确认；
- [x] 约 58.7 FPS 连续采集稳定性确认；
- [x] 同一复合帧的初始双目同步链确认；
- [x] 全画幅 pinhole 单目/双目标定和 65 mm baseline sanity check；
- [x] rectification preview 与 vertical epipolar statistics；
- [x] disparity `/16.0`、Q→3D/Z 的符号和单位；
- [x] 0.5–2.5 m 静态纹理目标测距 sanity check。

**Stage 2 Gate：PASS。**

## 6. Stage 3 输入接口

Stage 3 主链固定为：

```text
Stereo SBS Camera
  → Rectified LEFT RGB
  → Person / Pose
  → keypoints + confidence
  → Pose Quality Gate
  → Skeleton Normalization
  → NormalizedSkeleton
```

首版只在 rectified LEFT RGB 上运行完整 Pose，不在左右目各运行一套 Pose。RIGHT 和
depth 是可选增强，不得成为首版 2D Pose 的启动阻塞。

建议 Stage 3 的帧接口至少携带：

- `frame_id` 与 monotonic `timestamp_ns`；
- `calibration_id = 20260914_092939_UTC__run_B_exclude_0004_0027`；
- `left_rectified_bgr`：1280×960 BGR8；
- 可选 `right_rectified_bgr`：1280×960 BGR8；
- 可选 `depth_mm`：与 rectified LEFT 对齐的 float32；
- 可选 `depth_valid_mask`：无效深度必须显式标记，不能以 0 冒充真实距离。

Pose 模型可以内部 resize/letterbox，但输出关键点必须能无歧义映射回 1280×960 的
rectified LEFT 像素坐标，并保留每个关键点 confidence。NormalizedSkeleton 应保留源
frame_id/timestamp，便于后续 temporal recognizer、Operator Ownership 和安全 FSM
拒绝过期或错帧数据。

RIGHT/depth 后续可用于：人机距离、Operator 3D consistency、空间 gating 和 3D
keypoints。深度采样应使用有效性 mask 与局部稳健统计；低纹理或遮挡导致深度无效时，
系统应降级到 2D 结果或 UNKNOWN，而不是生成虚假 3D 证据。

## 7. 已知限制与后续风险

- Run B 是当前开发默认，不是生产级最终参数；
- Tz 在不同 session/子集间比 Tx 和 baseline 更敏感；高精度或安全关键部署前应重新
  采集独立均匀数据确认外参重复性；
- 当前距离验证以静态纹理目标为主，动态人体、衣物低纹理、反光、重复纹理、遮挡边界
  和运动模糊尚未覆盖；
- 同一 UVC composite frame 提供左右图，但尚无独立证据证明双传感器曝光级硬件同步；
- 当前 StereoSGBM 是验证 baseline，不提前做参数调优、CUDA、ROS 2 depth 或点云；
- 以上限制在 Stage 3 作为质量/有效性条件处理，不重新打开 Stage 2 范围。

## 8. 冻结与保留策略

必须保留所有原始 captures、metadata、首轮结果、Run A、Run B、报告、NPZ/YAML 和
rectified previews。不得为了整理磁盘删除历史数据。本轮不修改相机、标定或深度代码。

下一项工作只进入 Stage 3：Pose + Pose Quality + Skeleton Normalization。
