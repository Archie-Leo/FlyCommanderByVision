# Stage 2 Stereo Calibration Tool

正式双目标定工具，适用于 `/dev/video0` 输出的 2560×960 MJPEG Side-by-Side 图像。左半幅是物理 LEFT，右半幅是物理 RIGHT；所有检测、PNG 保存和标定均使用原始单目 1280×960。

## 环境

```bash
cd ~/drone_stage2/stereo_calibration
source ~/venvs/drone_stage2/bin/activate
python -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)"
```

期望版本：OpenCV 4.10.0、NumPy 1.26.4。无需重新安装或升级。

## 一体化交互入口

```bash
python stereo_calibration.py
```

按键：

- `S` / `Space`：对当前原始帧重新执行左右 full-resolution 检测；双侧成功且顺序一致才保存；
- `C`：从当前 session 的磁盘 PNG 重新检测并标定，不使用预览内存角点；
- `D`：删除最后一组；实际移动到 session 的 `deleted/`，可恢复；
- `Q` / `ESC`：释放相机、关闭窗口并退出。

每次启动创建：

```text
captures/YYYYMMDD_HHMMSS_UTC/
├── left/pair_0001_left.png
├── right/pair_0001_right.png
├── full/pair_0001_full.png
├── deleted/
└── metadata.json
```

## 独立入口

只采集：

```bash
python 01_capture_stereo.py
```

对最新 session 标定：

```bash
python 02_calibrate_stereo.py --session latest
```

显式排除指定观测做对照实验（不会移动、删除或修改任何 capture）：

```bash
python 02_calibrate_stereo.py \
  --session 20260914_092939_UTC \
  --exclude-pairs 4 27 \
  --output-tag run_B_exclude_0004_0027
```

`--output-tag` 会创建独立输出目录，程序拒绝覆盖已有 calibration。排除编号会写入
`preflight_report.txt`、`calibration_report.txt`、`calibration.yaml` 和
`calibration.npz` 的 `excluded_pair_indices` 字段；这与损坏/检测失败的 rejected pair
分开记录，绝不静默筛除。

也可指定 session 和 rectification alpha：

```bash
python 02_calibrate_stereo.py --session 20260914_120000_UTC --alpha 0.0
```

独立复核已有输出：

```bash
python 03_validate_calibration.py stereo_calibration_output/20260914_120000_UTC
```

## 采集建议

20–30 个高质量、多样姿态 pair 通常比大量重复姿态更有意义；这是工程建议，不是 OpenCV 官方强制数量。覆盖：中心、四角/边缘、近中远距离、水平/垂直/复合倾角，同时保证整块棋盘及其外圈白边可见。

以下情况不要按 S：

- 棋盘严重模糊、过曝、反光；
- 棋盘太小，角点只占很少像素；
- 棋盘部分出画或外边界不可见；
- LEFT/RIGHT 任一侧显示 `NOT FOUND`；
- `PAIR: INVALID` 或 ordering mismatch；
- 连续多张位置、距离和倾角几乎相同；
- 标定板弯曲或方格实际尺寸不可信。

## 输出

```text
stereo_calibration_output/<session>/
├── calibration.yaml
├── calibration.npz
├── calibration_report.txt
├── preflight_report.txt
└── rectified_previews/
```

结果状态为 `INFO`、`WARNING` 或 `SUSPICIOUS`。所有数值阈值均在报告中注明是工程 heuristic；OpenCV 没有统一的 RMS 合格线。异常 view 会列出但不会静默删除。

`calibration.yaml` 是 OpenCV FileStorage YAML，后续 C++ 可直接读取，例如：

```cpp
cv::FileStorage fs("calibration.yaml", cv::FileStorage::READ);
cv::Mat K_left, D_left, R, T, Q;
fs["K_left"] >> K_left;
fs["D_left"] >> D_left;
fs["R"] >> R;
fs["T"] >> T;
fs["Q"] >> Q;
```

## 测试

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

这些测试验证程序逻辑、OpenCV 4.10 API、合成几何和序列化，不代表实体相机已经完成标定。真实 baseline、R/T 和极线误差必须由实体棋盘采集结果证明。

研究依据与阈值边界见 `CALIBRATION_RESEARCH.md`。
