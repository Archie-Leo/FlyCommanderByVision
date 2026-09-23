# Stage 2 Stereo Depth Validation Notes

日期：2026-09-14  
目标：只验证 Run B provisional calibration 的实时 rectification、StereoSGBM、Q→3D
和 0.5–2.0 m 实测量级；不重新标定，不代表最终深度算法或 Stage 3。

## 冻结输入

- Calibration：`20260914_092939_UTC__run_B_exclude_0004_0027/calibration.yaml`
- `excluded_pair_indices = [4, 27]`
- 1280×960 per eye；物理左目为 SBS 左半幅，物理右目为右半幅
- object points 的方格尺寸为 45.0 mm，故 T、baseline 和由同一 Q 重投影的 XYZ
  使用同一物理尺度，即 mm。程序同时显示 mm 与 m，但不修改 calibration 文件。

## OpenCV 4.10 官方依据

1. [Camera Calibration and 3D Reconstruction](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html)
   - `initUndistortRectifyMap(K,D,R1/P1)` 为左右相机分别生成联合去畸变和校正 map；
     `remap` 使用这些 map 得到极线水平的图像。
   - `reprojectImageTo3D(disparity,Q)` 使用 `stereoRectify` 输出的 4×4 Q，结果位于第一
     相机的 rectified coordinate system。
   - 官方明确：StereoBM/StereoSGBM 的 16-bit signed disparity 在用于
     `reprojectImageTo3D` 前，应除以 16 并转为浮点。
2. [StereoSGBM class](https://docs.opencv.org/4.10.0/d2/d85/classcv_1_1StereoSGBM.html)
   - `numDisparities` 必须大于零且能被 16 整除；`blockSize` 为奇数且至少 1；
     `P2 > P1`。官方示例给出 `P1=8*channels*blockSize^2`、
     `P2=32*channels*blockSize^2` 作为合理起点。
   - StereoMatcher 的 `DISP_SHIFT=4`、`DISP_SCALE=16` 对应 4-bit fractional disparity。
3. [Remapping tutorial](https://docs.opencv.org/4.10.0/d1/da0/tutorial_remap.html)
   - `remap` 用 map_x/map_y 对源图执行几何映射，本工具使用线性插值。

## 实现决策

- 直接读取已保存的 `K_left/D_left/K_right/D_right/R1/R2/P1/P2/Q`，不调用
  `stereoCalibrate` 或 `stereoRectify`。
- 原始 `CV_16S` disparity 始终先做 `astype(float32)/16.0`。用于显示的彩色图只是
  固定范围可视化，绝不用于测距。
- `disparity <= minDisparity`、非有限 XYZ、`Z<=0` 或超出工程显示范围的样本无效。
  OpenCV 的 `handleMissingValues=true` 会把最小 disparity 映射到很大的 Z；本工具不用
  该行为代替有效性判断，而是先建立显式 mask。
- 点击使用 7×7 邻域，至少 5 个有效样本，报告 median disparity 和 median Z。
- 第一版灰度 StereoSGBM 参数：minDisparity=0、numDisparities=128、blockSize=5、
  P1=200、P2=800、disp12MaxDiff=1、uniquenessRatio=10、speckleWindowSize=100、
  speckleRange=2、preFilterCap=63、MODE_SGBM_3WAY。这些是工程起点，不是 OpenCV
  官方最优参数或验收阈值。按 Run B 的 rectified focal length 与 64.44 mm baseline，
  0.5–2.0 m 的预期 disparity 约为 94–23 px，位于 0–127 px 搜索范围内。
- 卷尺 ground truth 由人工输入；程序不会声称自动获得真实距离。

## 验收解释

本工具只检查距离单调性、量级、符号和单位，以及中央纹理区域的实际误差。SGBM 在
无纹理、反光、重复纹理、遮挡边界处产生无效/错误 disparity 属于已知工程风险；单个
漂亮点击值不能证明生产可用。真实 0.5/1.0/1.5/2.0 m 测量必须由操作员完成并写入 CSV。
