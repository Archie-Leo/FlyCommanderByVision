# Stage 2 双目相机标定研究与设计

本文定义深圳德创信双目 RGB USB Camera 的正式标定流程。硬件事实、棋盘物理参数和验收目标均按实测冻结；算法选择以 OpenCV 4.x 官方文档和 NUC 上 OpenCV 4.10.0 Python API 实测为依据。

## 1. 冻结事实

| 项目 | 冻结值 |
|---|---|
| 主机系统 | Ubuntu 22.04 / Python 3.10 |
| 虚拟环境 | `~/venvs/drone_stage2` |
| OpenCV / NumPy | 4.10.0 / 1.26.4（已在 NUC 复核） |
| 相机 | `/dev/video0`，UVC `0bda:5883`，USB 3.0 SuperSpeed |
| 原始流 | MJPEG，2560×960，声明 60 FPS，实测约 58.7 FPS |
| 左右关系 | 左半幅是物理 LEFT；右半幅是物理 RIGHT |
| 单目原始尺寸 | 1280×960；检测、保存、计算均不得预缩放 |
| 快门 / 标称 FOV | Global Shutter / 约 102° |
| 棋盘 | 11×8 个内角点，即 `patternSize=(11,8)` |
| 方格边长 | 45.0 mm |
| 标称 baseline | 约 65 mm，仅 sanity reference，不是优化约束 |

帧间隔来自 `time.monotonic_ns()` 的实际采集时间，程序不写死 `1/60`。

## 2. 官方定义与直接结论

### 2.1 Pattern size 与物点尺度

OpenCV 将 chessboard `patternSize` 定义为每行、每列的**内角点数**，顺序为 `(columns, rows)`。本板因此是 `(11, 8)`，共 88 点。[^1]

平面物点定义为：

```text
(col * 45.0, row * 45.0, 0.0) mm
```

OpenCV 的投影模型对物点坐标单位本身没有偏好；平移向量与 object points 使用同一尺度。因此使用 mm 后，单目 view 的 `tvec`、双目 `T` 和 `norm(T)` 都解释为 mm。不能先以 1.0 建模再把 baseline 猜测成 mm。

### 2.2 角点检测器

OpenCV 说明 `findChessboardCornersSB` 使用 sector-based 方法，直接返回亚像素位置；官方文档还说明其结果比传统检测后 `cornerSubPix` 更适合高精度标定。[^2] NUC 上 OpenCV 4.10.0 已实测存在 Python API：

```text
cv2.findChessboardCornersSB(image, patternSize[, flags])
```

V1 因此使用 SB：

- 实时预览在**原始 1280×960 灰度图**上检测，但节流到可配置周期，避免 UI 因每帧双路 SB 检测失去响应；
- 保存按键会对该按键对应的原始复合帧重新执行完整 SB 检测；
- 离线标定从 PNG 重读后再次执行完整 SB 检测；
- 不对 SB 结果重复调用 `cornerSubPix`；
- 若未来增加传统 detector fallback，只有传统 `findChessboardCorners` 结果才执行 `cornerSubPix`，且结果来源必须写入 metadata。

`CALIB_CB_NORMALIZE_IMAGE | CALIB_CB_EXHAUSTIVE | CALIB_CB_ACCURACY` 用于保存/离线重检；这些 flag 的含义来自官方文档，但选择该组合是工程决策，不是 OpenCV 强制要求。[^2]

### 2.3 单目与双目标定顺序

V1 使用：

```text
LEFT calibrateCameraExtended
RIGHT calibrateCameraExtended
            ↓
stereoCalibrateExtended + CALIB_FIX_INTRINSIC
            ↓
stereoRectify + initUndistortRectifyMap + remap
```

`calibrateCameraExtended` 除总体 RMS 外还返回每个 view 的 RMS 和参数标准差，适合显式报告异常 view。官方将返回值定义为总体 RMS reprojection error，并把 `perViewErrors` 定义为每个 pattern view 的 RMS。[^3]

OpenCV 明确说明：双目标定若同时优化大量内外参可能因高维和输入噪声偏离正确解；当每个相机内参可分别高质量估计时，建议先单目标定，再以 `CALIB_FIX_INTRINSIC` 只估计 `R/T/E/F`。[^4] 因此不在 stereo 阶段重新“自由优化”左右内参。

`stereoCalibrateExtended` 在 NUC 4.10.0 的 Python binding 已实测可用，并提供 stereo per-view error；它要求显式传入初始 `R`、`T` 参数位置，程序会传单位阵和以标称基线为量级的初值，但在未设置 `CALIB_USE_EXTRINSIC_GUESS` 时该初值不构成约束。最终 baseline 始终由 `norm(T)` 得到，不强制为 65 mm。

### 2.4 Rectification

`stereoRectify` 输出：

- `R1/R2`：从各自未校正相机坐标到各自校正坐标的旋转；
- `P1/P2`：校正坐标中的 3×4 投影矩阵；
- `Q`：disparity-to-depth 的 4×4 映射矩阵；
- `validPixROI1/2`：校正图中的有效像素区域。[^5]

V1 使用 `CALIB_ZERO_DISPARITY`，使校正视图主点具有相同像素坐标。`alpha=0.0` 作为默认值，含义是裁切/缩放到无黑边的有效像素；同时允许 CLI 改为 `[0,1]`，便于后续权衡视场保留。这个默认值是工程选择，含义由 OpenCV 官方定义。[^5]

`initUndistortRectifyMap` 生成 undistortion+rectification maps，`remap` 对原图应用映射。[^6] maps 不写入正式 YAML/NPZ，以避免大文件；后续 Runtime 根据 K/D/R/P 和目标尺寸确定性重建。

### 2.5 Reprojection 与 epipolar error

OpenCV 将 reprojection error 作为参数精度估计：用 `projectPoints` 把物点投回图像，再与检测角点比较；越接近 0 越好。[^7] OpenCV **没有规定一个对所有相机都适用的 RMS 合格线**。报告保留总体 RMS、每 view RMS、median/MAD outlier 提示，不用诸如“RMS < 0.5 是官方 PASS”这样的伪标准。

校正后的理想水平双目对应点具有相同纵坐标。V1 用 `undistortPoints(..., R=R1/2, P=P1/2)` 直接把原始角点转换到 rectified pixel coordinates，再统计所有对应角点的：

```text
abs(y_left_rectified - y_right_rectified)
mean / median / p95 / max  [pixel]
```

这是由 rectification 几何直接导出的项目验收指标，不宣称为 OpenCV 官方规定的阈值。它比在 remapped raster 上重新检测角点更直接，也避免二次检测误差混入变换验证。

## 3. Pinhole 与 fisheye 决策

OpenCV calib3d 默认模型是 pinhole projection 加 radial/tangential distortion；默认 5 参数顺序是 `(k1,k2,p1,p2,k3)`。官方还指出真实镜头的 radial distortion 应当单调、双射；非单调估计应视为 calibration failure。[^1]

OpenCV fisheye namespace 使用不同的角度模型：`theta=atan(r)`，再用四个系数构成 `theta_d`。[^8] “102°”只描述视场，并不能单独决定投影模型；厂家写“无畸变”也不能把 D 强制为 0。

V1 选择标准 pinhole 5 参数模型，理由是：

1. 102° 尚不证明是 fisheye projection；
2. 商品描述称低畸变/无畸变，先用较少自由度模型更可解释；
3. 最终由边缘覆盖数据、per-view residual、radial monotonicity、rectified preview 和 vertical epipolar error 判断模型是否充分。

触发独立 fisheye 对照实验的证据包括：边缘出现系统性 residual、pinhole radial 映射非单调、畸变系数/焦距明显不合理、校正边缘严重拉伸或 epipolar error 在边缘系统升高。若触发，必须生成独立报告与独立输出目录，不静默比较两个 RMS 后自动挑低者；不同模型的目标函数和参数量不同，单个 RMS 也不足以做模型选择。

## 4. 对称棋盘的左右角点顺序

标准无 marker 棋盘没有可观测的绝对角点 ID。`findChessboardCornersSBWithMeta` 只有在带 marker 的专用 pattern 上才能明确 pattern origin；本实体板不是 marker board，程序不得凭运气翻转数组。[^2]

V1 采用“验证并拒绝，不自动重排”：

1. 分别从 row-major 角点计算平均 row basis 和 column basis；
2. 检查 LEFT/RIGHT 的 row basis 方向点积、column basis 方向点积均为正；
3. 检查两个 basis 的有向面积符号一致；
4. 任一方向相反时标记 `ORDERING_MISMATCH`，该 pair 不保存/不进入标定；
5. 不执行 `corners[::-1]` 之类碰运气的修正。

这一检查依赖已验证事实：左右半幅来自同一水平安装、图像方向相同的双目模组。它是项目的几何 sanity check，不是 OpenCV 官方 API 保证。若未来仍出现不可消除的对称歧义，应改用带唯一 marker 的 calibration target，而不是隐藏重排。

## 5. 数据采集与程序拆分

采用三程序加统一入口：

```text
stereo_calibration/
├── stereo_calibration.py        # 正式交互入口；实时预览，C 从磁盘标定
├── 01_capture_stereo.py         # 仅采集入口
├── 02_calibrate_stereo.py       # 对指定/latest session 离线标定
├── 03_validate_calibration.py   # 重载参数和原始数据重新验证
├── calibration/
│   ├── config.py
│   ├── capture.py
│   ├── checkerboard.py
│   ├── dataset.py
│   ├── calibrator.py
│   ├── validation.py
│   └── io_utils.py
├── captures/<session>/
│   ├── left/
│   ├── right/
│   ├── full/
│   └── metadata.json
├── stereo_calibration_output/<session>/
│   ├── calibration.yaml
│   ├── calibration.npz
│   ├── calibration_report.txt
│   └── rectified_previews/
├── tests/
├── CALIBRATION_RESEARCH.md
├── README.md
└── requirements.txt
```

`stereo_calibration.py` 满足启动即预览和按 C 计算；计算函数只读磁盘 session，不使用实时内存角点。独立脚本允许相机不在场时重复调 flags、计算和验证，保证数据不随 UI 退出而丢失。

每次采集创建 UTC 时间 session。每个 pair 从同一个 2560×960 frame 拆出，保存三张 PNG 和原子更新的 metadata。保存前重新检测；任一侧失败、尺寸错误或 ordering mismatch 均拒绝。手动 S/Space 才保存，检测成功不会自动拍摄。

## 6. 结果状态与工程阈值

OpenCV 官方没有给出通用的 pair 数量、RMS、baseline 偏差或 rectified vertical error 合格线。V1 明确区分：

### 官方数学约束

- `R @ R.T ≈ I` 且 `det(R)≈1`；
- radial mapping 对真实镜头应单调/双射；
- rectified 水平 stereo 对应点应在相同行；
- K 的焦距应为正；
- stereo 左右必须观测相同 object points。

### 工程启发式（可配置，不是官方标准）

- 少于 12 个有效 pair 时拒绝求解；20–30 个覆盖画面、距离、倾角的 pair 是采集建议；
- baseline 相对 65 mm 偏差 >30% 记 `WARNING`，>50% 记 `SUSPICIOUS`；
- `||R R^T-I||_F >1e-3` 或 `|det(R)-1|>1e-3` 记 `SUSPICIOUS`；
- principal point 超出图像边界、焦距小于 0.1×宽或大于 10×宽记 `SUSPICIOUS`；
- rectified vertical p95 >1 px 记 `WARNING`，>2 px 记 `SUSPICIOUS`；
- per-view RMS 超过 `median + 3×1.4826×MAD` 标为 outlier；默认不删除；
- 采集姿态 descriptor 过近只提示重复，不自动删除。

最终状态是 `INFO`、`WARNING` 或 `SUSPICIOUS`，绝不无条件打印 PASS。原始 pairs 永久保留，异常 view 只列出；任何排除必须由未来显式参数和报告记录。

## 7. 输出兼容性

`calibration.yaml` 使用 OpenCV `FileStorage` YAML，矩阵为 `opencv-matrix`，Python/C++ 均可用 OpenCV 直接读取。`calibration.npz` 用于 Python 数值回放。两者包含图像尺寸、board 参数、K/D、R/T/E/F、R1/R2/P1/P2/Q、ROI、baseline、RMS、epipolar statistics、软件版本、时间和 source session。

验证程序必须同时：

- 读取 YAML 并检查必需 key；
- 读取 NPZ 并与 YAML 关键矩阵比较；
- 重建 rectify maps；
- 从 source session 重读 PNG 和重检角点；
- 生成带水平线的 previews；
- 重新计算 epipolar statistics 与 sanity checks。

## 8. 测试边界

无需实体相机可完成：API presence、空 captures、缺 pair/坏图/错尺寸、单边角点拒绝、ordering mismatch、目录/metadata、合成投影 calibration、YAML/NPZ round-trip、validation report、camera open failure、异常释放路径。

实体相机与棋盘仍必须由人工完成：真实角点覆盖、左右镜头稳定、真实 R/T、约 65 mm baseline、真实 rectification 和 epipolar error。软件 smoke test 不能冒充实体标定完成。

## Sources

[^1]: OpenCV. [Camera Calibration and 3D Reconstruction — pinhole model, patternSize and distortion validity](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html). OpenCV 4.10.0 documentation.
[^2]: OpenCV. [findChessboardCorners and findChessboardCornersSB](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html). OpenCV 4.10.0 documentation.
[^3]: OpenCV. [calibrateCamera / calibrateCameraExtended](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html). OpenCV 4.10.0 documentation.
[^4]: OpenCV. [stereoCalibrate / stereoCalibrateExtended](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html). OpenCV 4.10.0 documentation.
[^5]: OpenCV. [stereoRectify](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html). OpenCV 4.10.0 documentation.
[^6]: OpenCV. [initUndistortRectifyMap](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html) and [remap](https://docs.opencv.org/4.10.0/da/d54/group__imgproc__transform.html). OpenCV 4.10.0 documentation.
[^7]: OpenCV. [Camera Calibration tutorial — Re-projection Error](https://docs.opencv.org/4.10.0/dc/dbb/tutorial_py_calibration.html). OpenCV 4.10.0 documentation.
[^8]: OpenCV. [Fisheye camera model](https://docs.opencv.org/4.10.0/db/d58/group__calib3d__fisheye.html). OpenCV 4.10.0 documentation.
