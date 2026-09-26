# RK3576 Stage5 双目深度 ROI 性能优化

## 范围与基线

分支 `rk3576-migration`，起点 `b36cf56`。未改 Run B 标定文件（SHA256 `9730eb49d136d426b84846319c7c3fecaf73dbd74be3f38ac4c376b40843e174`）、SGBM 参数、Stage3/4 判定、Stage5 Ownership/T-Pose/ReID/MOT/Session、Stage6 或 H264。

原链路在每个有效 tracked detection 帧将 1280×960 原始双目整幅 rectification 后运行全幅 SGBM，再取人物 bbox 的 torso median。真人预览观察到深度约 1.17 s/帧、整链约 1 FPS。对板端同一静态 SBS 采样帧进行 200 次全幅 matcher 基准：平均 859.21 ms，P50 855.61 ms，P95 900.33 ms，最大 925.58 ms。单次完整全幅 adapter 耗时 947.85 ms。性能差异来自场景、调度与包含的 rectification/aggregation 范围；勿把两种时间混为同一指标。

## ROI 设计与坐标

Pose/Tracker 使用 `rectified LEFT → rotate 180°` 的 analysis 坐标。`TrackedRotatedDepthAdapter` 沿用既有 `RotatedDepthAdapter` 的 XYXY（x2/y2 exclusive）反转约定：`(W-x2, H-y2, W-x1, H-y1)`。深度仍使用**未旋转**的 Run B rectified 左右目和原 Q，保持水平 disparity 的方向；从原始 RIGHT 图只 remap ROI。边界归一化及裁剪由 `stereo_roi_bounds` 完成。

保留既有 torso 样本范围：bbox x 的 32%–68%，y 的 22%–62%；围绕该范围加可配置的 bbox 边距，默认 `depth_roi_margin_ratio=0.10`。LEFT 和 RIGHT 使用同一个原图坐标 crop，并在左缘额外留出 `minDisparity + numDisparities - 1 + blockSize` 的搜索上下文，因此不会把右目同位置的窄 bbox 错当作完整匹配窗口。ROI 小到不能满足现有 matcher 参数时返回无效深度，不缩小 `numDisparities`。只对 torso 内 disparity 做 Q 重投影；Q 的 x/y 原点加上 torso 在原 rectified 图中的起点。valid mask、depth range、median、MAD 和质量公式均沿用现有实现。

## 降频与时效

`Stage5PipelineV2` 对有 `process_tracked` 能力的深度 adapter 启用 ROI；原 Stage6 `RotatedDepthAdapter` 没有该接口，因此继续走原全幅路径，Stage6 文件未改。ROI 默认每个活跃 track 最多 5 Hz，`depth_max_age_ms=500`。缓存记录 track ID、bbox、深度值、源 frame ID、源 timestamp、计算 timestamp。仅当 track 在连续帧仍出现、bbox IoU ≥0.25、源 timestamp 不倒退、cache age ≤500 ms 时可复用；ROI 更新失败则写入 invalid，不回退到旧有效值。track 消失即清理缓存，重新出现需重新计算。每帧日志有 age、valid、源 frame ID、ROI 尺寸和拆分耗时。所有 Observation 与 Ownership API 保持原状。

## 同帧性能与准确性

同一静态 SBS fixture，已 rectified LEFT 作为输入，ROI adapter 各运行 200 次；OpenCV 原 SGBM 参数未改。完整 ROI 深度耗时包括 RIGHT ROI remap、matcher、torso 重投影/聚合，不含预先供 Pose 使用的 LEFT rectify。

| 人数 | ROI mean | P50 | P95 | max | ROI 样例 |
|---:|---:|---:|---:|---:|---|
| 1 | 137.99 ms | 137.64 ms | 140.18 ms | 158.89 ms | 529×492 |
| 2 | 190.31 ms | 187.31 ms | 210.32 ms | 216.69 ms | 529×492, 328×438 |
| 4 | 309.92 ms | 307.67 ms | 330.43 ms | 372.83 ms | 4 个独立 ROI |

1 人 ROI profile mean：准备 3.93 ms、matcher 120.49 ms、聚合 13.54 ms。bbox 映射在真人预览单独记录。与全幅 matcher 基线相比，1 人完整 ROI 耗时约 6.23 倍更快；与原真人预览的 1.17 s 相比约 8.48 倍，但测试场景与计时边界不同。

静态 fixture 4 个 bbox 的全幅深度为 `0.6350217, 0.5151829, invalid, 0.6380820 m`；ROI 为 `0.6350217, 0.5161872, invalid, 0.6432487 m`。3 个有效位置平均绝对差约 2.06 mm，中位相对差约 0.195%。invalid 仍为 invalid。这个 fixture 不是已知真值距离；真人 1/1.5/2/3 m 距离趋势尚需实测。

## 真人预览与回归

预览保持单独视觉入口 `scripts/preview_stage5_web.py`，页面 `http://192.168.1.100:8081`，不接 Stage6/PX4。60 秒第一轮约 120 个 HTTP 状态样本：`LOCKED_HIGH` 17 个采样，Operator Lock 回归通过；页面持续有画面与状态。最终版本再次在板端冒烟：页面 HTTP 200、JPEG HTTP 200（34169 bytes）、状态含按 track ID 绑定的有效深度，退出后相机空闲、8081 关闭。采样平均累计 analysis FPS 7.66（P50 7.68），Pose inference 平均 33.35 ms，LEFT rectify 平均 15.11 ms，Stage5 non-depth 平均 48.00 ms，Stage5 total 平均 78.53 ms。仅深度更新样本平均 74.74 ms，P95 132.30 ms；depth age P95 192 ms。该次现场有 1–2 人入镜，整链均值仍低于 8 FPS 最低目标，不能标记整链性能完全达标。

第二轮离开/返回测试的 140 个采样均为 `WAIT_OPERATOR`：T-Pose reasons 主要为双臂未水平伸直、Pose invalid 或肩部几何。第三轮开始重新进入 `LOCKED_HIGH`（3 个采样），离开后进入 `OPERATOR_LOST`；返回后有 10 个采样的 T-Pose 匹配成功、Pose valid、crop quality 1.0，但旧 gallery 相似度只有 0.84–0.89，低于现有 reauth 门槛 0.90。因此“离开后重新授权”未通过，原因属于原有 ReID/Ownership 规则，阈值未改。默认实验性自动重新授权为关闭状态；其门槛更高，启用也不能解决这批 0.84–0.89 相似度。第一轮有 14 个 `LOCKED_HIGH` 样本同时检测到 2 人，但缺少受控的第二人干扰动作，故只记部分通过。

自动回归：Stage3 12、Stage4 14、Stage5 109、Stage6 67 项通过。`test_depth_roi.py` 覆盖 XYXY 180°映射、clamp、ROI 搜索边距、过小 ROI、无效/nonfinite、median、同帧全幅/ROI、一人/多人 track 关联、离开后清缓存、帧率限制、失效后 fail closed。`live_operator_v2.py --no-display` 退出时只在 GUI 模式调用 `destroyAllWindows`。

## 限制与下一步

1. 不同人体尺寸使 ROI 更新耗时变化；4 人最坏静态场景约 310 ms。保持 5 Hz 深度会压低多人整链帧率。
2. 目前同步执行 ROI 深度；未上线程池。单人 ROI 均值 <150 ms，先保留简单、可审计的同步路径。
3. 真人距离趋势、第二人受控干扰、离开后重新锁定仍未通过验收；后者已经定位为旧 gallery 相似度未达现有门槛。
4. 不使用 PX4 live output；后续 Stage3→4→5→6 dry-run 集成为独立任务。
