# Stage 3 Pose Research

日期：2026-09-14  
范围：MediaPipe Pose baseline、Pose Quality、2D Skeleton Normalization；不包含动作识别、
身份跟踪、Operator Ownership、3D skeleton 或飞控。

## 1. 官方资料

本设计以 Google AI Edge / MediaPipe 官方资料和官方源码为主：

1. [Pose Landmarker overview](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker)
2. [Pose Landmarker Python guide](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/python)
3. [PoseLandmarkerOptions Python API](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/PoseLandmarkerOptions)
4. [Landmark container API](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/components/containers/Landmark)
5. [Official Pose Landmarker Python source](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/vision/pose_landmarker.py)
6. [Official Full model bundle](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task)

## 2. 官方 API 与输出定义

当前 Python Tasks API 通过 `mediapipe` 包提供 `BaseOptions`、`PoseLandmarkerOptions` 和
`PoseLandmarker.create_from_options()`。它支持：

- `IMAGE`：单张同步推理；
- `VIDEO`：视频帧同步推理，要求严格递增的毫秒 timestamp；
- `LIVE_STREAM`：异步回调；backend 忙时新输入可能被忽略。

官方说明 VIDEO/LIVE_STREAM 会利用内部 tracking 降低重复检测开销。本项目 V1 使用
VIDEO：控制流确定、每个提交帧都有同步结果，便于 frame_id/timestamp 对齐。这里的内部
tracking 只属于模型执行优化；Stage 3 不导出 track_id，也不把跨帧 local_detection_id
解释为身份。

Pose Landmarker 最多输出 `num_poses` 个 pose。官方默认 1；V1 设置为 1，但外部接口仍为
`PoseFrame.poses[]`，以后可以替换多人 backend。

官方模型输出 33 个 landmark。重要索引包括 nose=0，left/right shoulder=11/12，
elbow=13/14，wrist=15/16，hip=23/24，knee=25/26，ankle=27/28。`left/right` 是人体
解剖学左右，不是屏幕左右。图像 inference 不镜像；仅允许 UI 副本镜像，V1 默认也不镜像。

Normalized landmarks 的 x/y 是相对图像宽高的坐标。Landmark API 将 visibility 定义为
landmark 可见/未被遮挡的分数（出画也视为不可见），presence 定义为 landmark 位于场景内
的分数。API 同时提醒不同模型可能返回 sigmoid 结果或 sigmoid 输入，因此项目不把它们
解释为经过安全标定的概率。

V1 canonical confidence 定义为：两者都有时取 `min(visibility, presence)`；只有一个时
取可用值；都没有时为 `None`。这是项目的保守派生规则，不是 MediaPipe 官方规则。

官方还输出 `pose_world_landmarks`。它属于 GHUM 模型估计的 backend-specific 3D，不是
Stage 2 双目测量所得的真实 camera XYZ。V1 backend 不把它放进 canonical 3D 字段。

## 3. Backend 选择

V1 选择 MediaPipe Pose Landmarker Full、CPU、VIDEO mode：

- 官方提供、33 点上肢/躯干/下肢语义完整；
- 面向设备端实时用途，x86_64 CPU 可直接运行；
- visibility/presence 可支持质量门控；
- Tasks API 支持 `num_poses`，接口可平滑扩展；
- Full 是 lite 与 heavy 之间的质量/时延折中，最终选择以 NUC 实测为准。

NUC 实际下载的 Full bundle 大小 9,398,198 bytes，SHA-256：
`4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad`。即使官方
`latest` URL 后续变化，也可用该 hash 识别本次验证的模型资产。

依赖安装在独立 `~/venvs/drone_stage3`，不触碰 `drone_stage2`。MediaPipe 1.0.1 声明依赖
`opencv-contrib-python`；因此 Stage 3 只安装固定的 contrib 4.10.0.84，而不同时安装
`opencv-python`，避免两个 wheel 争用同一个 `cv2` 模块。版本冻结在实际 smoke 通过的
版本，requirements 不使用无上限升级。若当前最新版在 Ubuntu 22.04/Python 3.10
出现 API、ABI 或长期运行问题，允许退到官方同一 Tasks API 的已验证版本并记录原因，
不能静默更换 backend。

RTMPose/MMPose 与 YOLO Pose 可作为后续替换项，但它们不是本轮实现内容。替换后端只需
把原始 schema 映射成 `PersonPose V1`；Quality 与 Normalization 不依赖 MediaPipe 对象。

## 4. Camera 输入

Stage 2 冻结输入 `/dev/video0`：2560×960 MJPEG SBS，左半幅是物理 LEFT 1280×960。
Stage 3 读取同一帧、拆 LEFT，并使用 Run B 的 `K_left/D_left/R1/P1` 做 rectification。
Pose 只消费 rectified LEFT BGR→RGB，不在 RIGHT 重复推理。host monotonic timestamp 用于
性能和 VIDEO API 排序；它不是 sensor exposure timestamp，也不写死 1/60。

## 5. Canonical schema

V1 原始 canonical joints：nose、左右 shoulder/elbow/wrist/hip/knee/ankle，共 13 个。
neck（肩中点）和
pelvis（髋中点）是明确标记的 derived joints。每个 joint 保留 pixel、image-normalized、
visibility、presence、canonical confidence、valid 和 derived。

图像坐标固定为左上角原点，x 向右、y 向下。`local_detection_id` 只是当前帧数组索引，
不是 track_id/operator_id。

## 6. Quality 与工程阈值

Quality 检查 NO_POSE、非有限值、肩/髋/手臂缺失或低置信、人体过小和严重出画。
阈值来自 `config.py`，属于可调工程 heuristic，MediaPipe/OpenCV 没有为本项目规定统一
PASS 线。拒绝结果保留 reasons、coverage、confidence summary 和 truncation flags。

## 7. Normalization 研究结论

V1 body center 为左右髋中点。body scale 为肩宽与“肩中点到髋中点躯干长度”的算术
平均；两项都必须有限且大于最小像素阈值。归一化公式：

```text
p_body = (p_pixel - pelvis_pixel) / body_scale_pixel
```

该公式消除二维全局平移和统一尺度。不做旋转、不做左右翻转，从而保留手在肩上/下、
手臂在图像左/右方向及人体解剖学左右。scale 退化时直接 INVALID，不允许除零、NaN 或
用 0 填充缺失骨段。

V1 bones 输出 dx/dy/length；elbow angle 用 shoulder-elbow-wrist 三点夹角，单位 degree，
范围 [0,180]。upper-arm image angle 使用图像坐标 `atan2(dy,dx)`，单位 degree，范围
[-180,180]。这两者都是几何量，不是 Gesture rule。

## 8. 验证边界

自动测试覆盖平移/尺度不变性、左右语义、缺失关节、零尺度、序列化、标定加载和资源
释放。真实相机 smoke 只证明 Camera→rectified LEFT→Pose 软件链可执行。正面站立、
举手、左右移动、不同距离、遮挡/出画和无人场景必须由操作员进行 MANUAL VALIDATION；
在完成前 Stage 3 状态必须是 `IMPLEMENTED / MANUAL VALIDATION PENDING`。
