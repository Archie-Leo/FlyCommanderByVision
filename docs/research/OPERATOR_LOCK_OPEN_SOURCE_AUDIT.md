# Operator Lock 开源仓库源码审计

> 审计日期：2026-09-10（Asia/Shanghai）  
> 任务性质：`PRE-STAGE-5 OPERATOR OWNERSHIP RESEARCH`  
> 方法：在远端 NUC 对冻结 commit 做完全静态源码阅读；未安装依赖、未运行模型、未修改仓库。  
> 边界：Stage 0 = PASS，Stage 1 = PASS，Stage 2 等待最终双目相机；本轮未启动 Stage 5 Runtime。

## 0. 核心结论

最终判断是：**技术可行性 HIGH，工程决策 CONDITIONAL_GO**。

这里的 HIGH 不是说某个现成 Tracker 能直接实现 DJI / HOVERAir 级“操作手所有权”，而是六个仓库已经覆盖了成熟的检测关联、Kalman、GMC、ReID embedding、轻量人脸和 RKNN 部署路径；剩余工作可以集中在本项目真正独有的 `OperatorOwnershipManager`、拒绝策略、状态机和双目几何约束上。

关键结论如下：

1. **Track ID 不是 Operator ID，更不是控制权。** 所有被审计的 MOT 都允许轨迹删除、新建和重新编号；其目标函数是总体跟踪指标，而不是“宁可拒绝也绝不把控制权给错人”。
2. **没有一个 Tracker 单独满足产品目标。** 若只比较“锁定后的身份记忆结构”，StrongSORT 最接近，因为它维护 nearest-neighbor feature gallery；若比较无人机移动相机下的全局跟踪底座，BoT-SORT 更合适，因为它明确把 GMC、Kalman、IoU 和 ReID 结合。最终控制权仍必须由独立 Ownership Manager 决定。
3. **第一版不把 Dedicated SOT 放在必选路径。** 推荐先做 `Global MOT + session-level ReID Gallery + 时空/双目硬门控 + Safety FSM`。SOT 只作为可消融的短检测空洞证据源，永远不能自行授予或转移 ownership。
4. **原始 BoT-SORT 仓库不能原样集成。** 冻结源码的 ReID 初始化路径存在确定的成员初始化顺序问题，并使用 `np.float`；其旧依赖栈也与当前环境不匹配。应复用算法和调用链，不复用原仓库运行时。
5. **BoxMOT 是最佳统一研究台，但不是默认发布依赖。** 当前版本封装完整、接口清楚、对比方便；许可证是 AGPL-3.0，若直接链接或分发需先确认项目能履行相应义务。默认策略是把它用于离线基准与读源码，运行时再作许可证决策。

---

## 1. 仓库与冻结点

所有目录均已存在且工作树干净，本轮没有重新 clone。

| 优先级 | 仓库 | 冻结 commit | 日期 | License | 本轮结论 |
|---|---|---:|---:|---|---|
| P0 | BoxMOT | `857628343860db1ea48ea50db7a73c25b7a3be13` | 2026-09-09 | AGPL-3.0 | 统一研究/评测台；发布前做许可证决策 |
| P0 | BoT-SORT | `251985436d6712aaf682aaaf5f71edb4987224bd` | 2022-10-30 | MIT | 算法依据；否定原样运行时集成 |
| P0 | deep-person-reid | `f8cd150fdf77e8d9e1ed143b7f308c2c609ded50` | 2026-01-09 | MIT | OSNet 训练/导出参考；Runtime 只带导出模型 |
| P1 | ByteTrack | `d1bf0191adff59bc8fcfeaa0b33d3d1642552a99` | 2022-12-11 | MIT | 无 ReID 的速度/消融 baseline |
| P1 | opencv_zoo | `47534e27c9851bb1128ccc0102f1145e27f23f98` | 2026-05-28 | Apache-2.0；YuNet 子目录 MIT | 可选 face positive evidence |
| P2 | rknn_model_zoo | `bad6c7334531becaf90a561988519b7bec34d0ab` | 2025-04-09 | Apache-2.0 | RK3576 pose 部署证据；OSNet 仍需单独验证 |

许可证结论只用于工程筛选，不构成法律意见。

---

## 2. BoxMOT 如何封装四种 Tracker

### 2.1 统一构造与输入边界

当前 BoxMOT 不是简单地复制四个仓库，而是通过三层接口统一它们：

```text
TrackerSpec
  │ tracker_type / backend / config / reid
  ▼
registry.py
  │ 声明 capabilities：frame、embeddings、geometry、backend
  ▼
factory.py::create_tracker()
  │ 校验配置与能力，延迟 import Python/C++ 实现
  ▼
Tracker protocol / BaseTracker.update()
  │ Detections + Frame + timestamp
  ▼
BotSort | ByteTrack | StrongSort | DeepOcSort
```

具体依据：

- [`_tracker_exports.py`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/_tracker_exports.py) 是实现 manifest；BoT-SORT、ByteTrack、StrongSORT、DeepOCSORT 都映射到 BoxMOT 自己的类。
- [`registry.py`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/registry.py) 记录每类 Tracker 是否接受/要求 frame、embeddings，是否支持 native backend 与 AABB/OBB。
- [`factory.py`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/factory.py#L133) 以 `TrackerSpec` 构造实例，并拒绝能力不匹配的配置。
- [`protocols.py`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/protocols.py) 把更新边界统一为 `update(detections, frame, timestamp_s)`。

NumPy AABB 输入仍支持 `(x1,y1,x2,y2,confidence,class)`；canonical `Detections` 还能携带 embeddings。ReID 可由上游提供，也可由 `LiveReIDMixin` 延迟建立 encoder；该 mixin 会对输出作 L2 normalization。对本项目更合理的是：**Detector、EmbeddingEncoder、MOT 分离，预计算 embedding 显式传入**，便于 RKNN/NPU 调度和数值验证。

### 2.2 四种实现的实际差异

| Tracker | 第一次关联 | 低分框第二次关联 | Appearance | 相机运动 | 身份记忆边界 | 对本项目定位 |
|---|---|---|---|---|---|---|
| ByteTrack | active+lost 与高分框 IoU | 有，IoU | 无 | 无 | track buffer | 速度/无 ReID baseline |
| BoT-SORT | Kalman + GMC 后，IoU/score 与 gated ReID 取更优成本 | 有，IoU | EMA + 最多 50 个 feature，但关联主要用 smooth feature | 有 | 当前 track/lost pool | 移动相机全局 MOT 首选 baseline |
| StrongSORT | appearance matching cascade + Kalman gating；后接 IoU fallback | 无 ByteTrack 式低分框两段法 | NearestNeighborDistanceMetric gallery，`nn_budget` 限长 | 有 | active confirmed track；删除后会被 metric prune | 身份型对照组；仍非 session operator gallery |
| DeepOCSORT | 自适应 appearance + observation-centric motion association | 自身 rematch/OCR | EMA embedding | sparse optical flow CMC | max_age 内 track | 非线性运动/遮挡对照组 |

关键源码：[`BotSort`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/box/botsort/tracker.py)、[`StrongSort`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/box/strongsort/tracker.py)、[`DeepOcSort`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/box/deepocsort/tracker.py)、[`ByteTrack`](https://github.com/mikel-brostrom/boxmot/blob/857628343860db1ea48ea50db7a73c25b7a3be13/boxmot/trackers/box/bytetrack/tracker.py)。

BoxMOT 自带的当前 MOT17 ablation 里，OccluBoost 的 HOTA/MOTA/IDF1 为 71.10/78.50/85.28，BoT-SORT 为 69.68/78.23/82.33，StrongSORT 为 68.05/76.19/80.76，DeepOCSORT 为 67.95/75.83/80.54，ByteTrack 为 67.68/78.04/79.16。该排名只能说明应把 OccluBoost 纳入离线 A/B，**不能直接外推到低机位/移动无人机/少人数/拒绝优先场景**。SportsMOT 中相对排名又不同，进一步说明必须使用本项目数据验证。

### 2.3 哪一个最适合“已锁定 Operator 后长期保持身份”

答案分两层：

- **只在四种 Tracker 内比较身份记忆结构：StrongSORT 最接近。** 它用每轨迹多样本 nearest-neighbor gallery，而不是只依赖单个 EMA embedding。
- **作为移动无人机的 Global MOT：BoT-SORT 是更稳妥的第一 baseline。** GMC 是明确的一等组件，且两阶段关联能利用低置信检测维持轨迹。
- **产品最终答案：都不够。** StrongSORT 的 gallery 随 active track 生命周期维护，轨迹删除后会 prune；BoT-SORT 的 feature deque/EMA 也属于 track，而不属于“本次会话中已授权的那个人”。二者都不会实现“发生歧义时冻结控制权且禁止自动换人”。

因此推荐 A/B：BoT-SORT、StrongSORT、DeepOCSORT、OccluBoost，加 ByteTrack 无 ReID 下界；选出的 Global MOT 上方始终放独立的 Operator Ownership Manager。

---

## 3. BoT-SORT 实际调用链

### 3.1 一帧数据流

原始仓库 [`BoTSORT.update`](https://github.com/NirAharon/BoT-SORT/blob/251985436d6712aaf682aaaf5f71edb4987224bd/tracker/bot_sort.py#L230-L430) 的执行顺序是：

```text
detector output
  ├─ 过滤 < track_low_thresh
  ├─ high detections ── FastReID inference ── normalized feature
  └─ low detections

tracked + lost -> strack_pool
  -> STrack.multi_predict()                 [Kalman predict]
  -> GMC.apply(frame, dets)                 [估计 2x3 camera warp]
  -> STrack.multi_gmc(pool, warp)           [warp mean/covariance]
  -> IoU distance + detection-score fusion
  -> ReID cosine distance
  -> proximity gate + appearance gate
  -> min(IoU cost, ReID cost)
  -> linear assignment
       ├─ Tracked: update()
       └─ Lost: re_activate(new_id=False)    [refind，保留 track id]

unmatched active + low detections
  -> IoU-only second association
  -> unmatched active -> mark_lost()

unconfirmed + remaining high detections
  -> gated IoU/ReID association
  -> unmatched unconfirmed -> removed

remaining detection -> activate new track
lost age > max_time_lost -> removed
deduplicate tracked/lost -> output
```

### 3.2 ReID

[`FastReIDInterface.inference`](https://github.com/NirAharon/BoT-SORT/blob/251985436d6712aaf682aaaf5f71edb4987224bd/fast_reid/fast_reid_interfece.py#L76-L150) 对高分框裁剪、RGB 转换、resize、batch inference，并在 postprocess 中 L2 normalize。关联时 [`embedding_distance`](https://github.com/NirAharon/BoT-SORT/blob/251985436d6712aaf682aaaf5f71edb4987224bd/tracker/matching.py#L128-L143) 以 track 的 `smooth_feat` 和 detection 的 `curr_feat` 计算 cosine distance。

`proximity_thresh` 实际作用在 IoU distance 上：距离超过阈值时，ReID 候选被置为无效；`appearance_thresh` 则限制 embedding distance。前者过严会阻断大位移/遮挡后的正确 ReID，过松会允许远处候选；后者过严会碎片化，过松会串人。它们必须在本项目相机视角、帧率和运动速度上联合标定。

### 3.3 Kalman 与 GMC

`STrack.multi_predict()` 对 active+lost pool 批量 Kalman predict。随后 [`GMC.apply`](https://github.com/NirAharon/BoT-SORT/blob/251985436d6712aaf682aaaf5f71edb4987224bd/tracker/gmc.py#L8-L78) 可选择 ORB、SIFT、ECC、sparse optical flow 或预计算文件。估出的 2×3 affine warp 通过 `multi_gmc` 同时作用到 state mean 和 covariance。

这对飞行相机非常有价值，但不是身份信号。低纹理、运动模糊、大面积动态前景、滚动快门或估计突变都可能让 GMC 错。工程上需要输出 GMC quality/inlier 指标；质量差时退化到无 GMC 或扩大不确定度，绝不能因为 warp 给出的空间接近就转移 ownership。

### 3.4 lost / refind

高分框第一次关联把 lost track 也加入 pool；匹配后 `re_activate(..., new_id=False)`，即 refind 并保留 ID。未匹配 active 经第二次低分框 IoU 关联仍失败才 `mark_lost()`；超出 `max_time_lost` 才 `mark_removed()`。

这是“短遮挡恢复”的成熟实现，但 removed 后没有 session-level 认证记忆，且 lost pool 内的错误匹配仍会把原 ID 给旁观者。产品层必须在 MOT 输出之外再审查候选身份。

### 3.5 明确否定原始 BoT-SORT 原样集成

冻结 commit 的 [`STrack.__init__`](https://github.com/NirAharon/BoT-SORT/blob/251985436d6712aaf682aaaf5f71edb4987224bd/tracker/bot_sort.py#L20-L43) 在传入 feature 时先调用 `update_features()`，之后才创建 `self.features` 和 `self.alpha`。而 `update_features()` 会立即访问这两个成员，因此 ReID 路径存在确定的初始化顺序缺陷。源码还多处使用现代 NumPy 已移除的 `np.float`。README 所列 Python 3.7、Torch 1.11/CUDA 11.3、torchvision 0.12 等也属于旧栈。

结论：**复用论文、状态机和关联数学；不要直接把该仓库塞入正式 Runtime，也不要为迁就它降级/替换当前环境。**

---

## 4. ByteTrack 的边界

原始 [`BYTETracker.update`](https://github.com/FoundationVision/ByteTrack/blob/d1bf0191adff59bc8fcfeaa0b33d3d1642552a99/yolox/tracker/byte_tracker.py) 的价值是两阶段关联：先把 high detections 与 active+lost tracks 做 IoU matching，再用 low-confidence detections 挽救未匹配 active tracks。这样能利用被遮挡时置信度下降但位置仍合理的检测。

它没有 appearance embedding、face、3D 或 session identity。两人交叉、相邻、相似尺度或检测抖动时，空间关联无法证明身份。结论：

- 适合测量“ReID 到底带来多少收益”的 baseline；
- 适合算力极紧且单人/少交叉的降级模式；
- **不适合独自承担 Operator Ownership。**

---

## 5. OSNet embedding 与 Operator ReID Gallery

### 5.1 embedding 如何得到

deep-person-reid 的 [`FeatureExtractor`](https://github.com/KaiyangZhou/deep-person-reid/blob/f8cd150fdf77e8d9e1ed143b7f308c2c609ded50/torchreid/utils/feature_extractor.py#L13-L152) 接受路径、NumPy 图像或 Tensor：

```text
person bbox crop
  -> RGB/PIL
  -> Resize(256, 128)
  -> ToTensor
  -> ImageNet mean/std Normalize
  -> OSNet eval forward
  -> B x D tensor
  -> 我们显式 L2 normalize
```

[`OSNet`](https://github.com/KaiyangZhou/deep-person-reid/blob/f8cd150fdf77e8d9e1ed143b7f308c2c609ded50/torchreid/models/osnet.py#L297-L431) 默认 `feature_dim=512`，eval 时返回 feature vector。它没有在 model forward 内保证 L2 normalization，因此 gallery 写入和查询前必须统一归一化。`osnet_x0_25` 的通道规模是轻量版本；仓库 Model Zoo 给出的典型输入是 256×128、512-D。

### 5.2 可以维护 Gallery，但应由我们维护

可以，推荐定义 session-level `OperatorTargetMemory`，而不是把某个 Track 的 deque 当 Gallery：

```text
OperatorTargetMemory
├─ operator_session_id              # 不等于 track_id
├─ gallery[]
│  ├─ normalized_embedding[512]
│  ├─ timestamp / source_track_id
│  ├─ bbox / crop_quality / visibility
│  ├─ viewpoint_bucket              # front/side/back 或聚类桶
│  └─ model_version
├─ face_gallery[]                   # 可选，独立阈值
├─ last_confirmed_2d / velocity
├─ last_confirmed_3d / covariance   # 双目到货后
└─ state / confidence / ambiguity
```

Gallery 更新必须是保守写入：只有 `LOCKED_HIGH`、裁剪质量足够、无遮挡或低遮挡、与现有 gallery 一致且没有近似旁观者时才加入；容量受限并保留多视角代表，不做无界 FIFO。候选分数可用 top-k/medoid/quality-weighted similarity，不能让一个污染样本永久接管 gallery。

### 5.3 OSNet 的限制

- 公共行人 ReID 数据与近距离人机交互、俯视/仰视、运动模糊、同服装域存在 domain gap。
- 衣服相似或换装时，body ReID 不是生物身份认证。
- 裁剪不完整、背面、遮挡会显著改变 embedding。
- 仓库导出脚本虽然支持 ONNX opset 12 和 dynamic batch，但注释中的输出维度与 x0.25 实际 512-D 不一致，且简化流程是旧 API 风格；导出后必须逐样本做 PyTorch/ONNX cosine parity，再做 RKNN FP/INT8 parity。

所以 OSNet 是主要 appearance evidence，不是单独的授权器。

---

## 6. OpenCV Zoo、TrackerNano 与 RKNN

### 6.1 YuNet + SFace

OpenCV Zoo 的 [YuNet](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet) 可作为轻量人脸检测，[SFace](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_recognition_sface) 通过 `alignCrop → feature → match` 提供 face embedding。仓库 demo 的 cosine/L2 阈值来自通用 benchmark，不应直接当作零误授权阈值。

Face 的正确角色是：清晰正脸时提供**额外正证据或冲突否决**。背身、远距、口罩或模糊时“没有 face”不能等同于 operator lost，更不能因此换人。

### 6.2 明确否定 TrackerNano 作为身份方案

OpenCV 官方把 [TrackerNano](https://docs.opencv.org/4.12.0/d8/d69/classcv_1_1TrackerNano.html) 定义为约 1.9 MB、backbone+neckhead 的轻量 DNN 单目标 tracker；API 是 `init(bbox)`、`update(bbox)` 和 `getTrackingScore()`。源码在 init 时从初始 crop 提取 template feature，此后搜索最可能位置并返回 box/score；它没有人物 embedding gallery、跨轨重识别、旁观者对比或授权状态。

因此：

- 它能在 detector 短暂漏检时提供一个位置假设；
- 它会追随外观相近区域，发生漂移时没有能力证明身份；
- 当前 NUC 的 apt OpenCV 是 4.5.4，本轮 Python `cv2` 又因 NumPy 2.2.6 与旧 ABI 不匹配而无法 import；用户要求不安装依赖，本轮未修复；
- **V1 不把 TrackerNano 放进关键路径。** 将来若升级/独立构建 OpenCV，必须单独审批，且只能作为 `SHADOW_TRACKING` 证据源。

### 6.3 RKNN model zoo

冻结仓库的 [YOLOv8 pose 示例](https://github.com/airockchip/rknn_model_zoo/tree/bad6c7334531becaf90a561988519b7bec34d0ab/examples/yolov8_pose) 已明确支持 RK3576、INT8、1×3×640×640；仓库 benchmark 报告的 66.8 FPS 是单核 `rknn_run` 推理口径，不能当作 detector+pre/post+tracking 的端到端 FPS。

它证明的是 pose/detection 的成熟 RKNN 调用路径：Toolkit2 转换、量化 dataset、`rknn_init/query/inputs_set/run/outputs_get` 和独立后处理。**它没有证明 OSNet 能无损转 RKNN**；OSNet 的 ONNX 算子兼容、静态 shape、量化漂移和余弦阈值必须另做 Gate。

---

## 7. 已成熟、无需重写的部分

以下组件应直接复用实现思想、接口或经过许可证审查的代码，不自行发明：

- Kalman predict/update 与 track lifecycle 基本机制；
- Hungarian/LAP assignment、IoU、cosine distance、Kalman gating；
- ByteTrack high/low confidence 两阶段关联；
- BoT-SORT 的 GMC + motion + gated appearance 组合；
- StrongSORT 的 bounded nearest-neighbor gallery 思路；
- OSNet 的人体 embedding backbone、训练与导出工具链；
- YuNet/SFace 的 face detection/alignment/embedding API；
- RKNN 的模型转换和 C/C++ inference API 模式；
- MOT 指标：HOTA、IDF1、MOTA、ID switches，以及速度/延迟统计。

“复用成熟”不等于“把所有 Python 仓库嵌入正式 ROS Runtime”。研究依赖与部署依赖应继续分开。

---

## 8. 必须由我们开发的 Operator Ownership Manager

这些能力在六个仓库中都不存在或不满足安全语义：

1. **显式 acquisition/release protocol**：谁、何时、通过何种手势/交互获得或释放权力。
2. **Operator session identity**：独立于 MOT `track_id`，track 重编号时 session 不变。
3. **保守 Gallery**：质量门控、多视角记忆、防污染、model version、生命周期和擦除。
4. **竞争候选与 margin**：不仅判断“像不像 operator”，还要判断第一名是否显著优于第二名。
5. **hard negative / bystander memory**：对容易混淆的旁观者记录负证据，但不做永久身份数据库。
6. **时空硬门控**：速度、位置、遮挡时间、双目深度/3D 连续性；不可能的跳变直接拒绝。
7. **不确定性语义**：`uncertain -> HOVER/REJECT`，绝不解释为“切给当前最像的人”。
8. **Safety FSM**：控制资格与视觉跟踪解耦，低置信/超时/冲突都撤销 Gesture 输出许可。
9. **审计日志**：每次 acquisition、降级、lost、reacquire、release 和 reject 的证据与阈值。
10. **项目数据评测与阈值标定**：尤其优化 Wrong Operator Acceptance，而非只优化 IDF1。

NVIDIA 当前 DeepStream tracker 的产品化设计证明了这条方向：它同时使用 Late Activation、Shadow Tracking、ReID gallery、motion/ReID 阈值和 lost-target re-association，并且 shadow data 默认不下发，因为它可能不可靠。[官方说明](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvtracker.html#target-re-association) 不是本项目可直接部署的 RK3576 方案，但为“分层、延迟确认、低置信不授权”提供了成熟先例。

---

## 9. 更简单、可靠的推荐组合

原设想：

```text
Global MOT + Dedicated SOT + ReID Gallery + optional Face + future Stereo 3D + Safety FSM
```

建议收敛为：

```text
Person/Pose detections
        │
        ├── OSNet embeddings
        ▼
Global MOT (A/B 后定；BoT-SORT 为首 baseline)
        │ tracks are observations, not authority
        ▼
OperatorOwnershipManager
  ├── session ReID Gallery
  ├── candidate margin + hard negatives
  ├── temporal/motion gates
  ├── stereo 3D gate (相机到货后)
  ├── optional YuNet/SFace positive/conflict evidence
  └── explicit acquire/release + reject
        ▼
Safety FSM ── only LOCKED_HIGH may forward gesture
        ▼
Gesture / Unknown Reject / Intent

Optional SOT shadow observer
  └── only proposes ROI during short detector gaps;
      cannot acquire, reacquire, switch, or authorize.
```

这比默认并行维护 MOT 与 SOT 更简单：消除了两套 box 冲突、SOT 漂移接管和额外模型部署风险。若后续消融表明 SOT 显著提高短漏检恢复且不增加误授权，再作为可关闭模块加入。

OccluBoost 值得作为 BoxMOT 内的额外 A/B 候选，因为其当前实现包含更长生命周期、late/tentative confirmation、ReID recovery 和 graveyard/reassociation 等接近产品需求的机制；但它仍是 track manager，不是 authority manager，且受 BoxMOT AGPL 与项目数据未验证限制，不能直接定为 Runtime 答案。

---

## 10. 不推荐项

| 方案 | 判断 | 源码依据/原因 | 替代 |
|---|---|---|---|
| 原始 BoT-SORT 仓库原样运行 | NO | ReID 初始化顺序缺陷、`np.float`、旧依赖栈 | 当前 BoxMOT 作研究对照；运行时做薄实现/许可证审查后的稳定实现 |
| ByteTrack 独立承担 ownership | NO | 仅 motion/IoU，无 appearance/session identity | 用作 baseline；上层 Gallery + Ownership Manager |
| TrackerNano 作为身份锁 | NO | 仅 bbox+tracking score，无 ReID/候选对比；会漂移 | 可选 shadow ROI，永不授权 |
| OSNet cosine 单阈值授权 | NO | domain gap、相似服装、坏 crop；无竞争 margin/时空约束 | 多证据 hard gate + gallery + reject |
| SFace 必须每帧成功 | NO | 背身/距离/遮挡时不可用 | optional positive/conflict evidence |
| 直接按公开 MOT 排名选型 | NO | 数据域和安全目标不同 | 自采双目场景 A/B，主指标 Wrong Operator Acceptance |
| 直接把 BoxMOT 带入闭源发布 | CONDITIONAL | AGPL-3.0 合规边界 | 先法律/发布策略确认；研究与 Runtime 分离 |

---

## 11. Stage 5 前验证矩阵

以下是相机到货后应先录制的可回放数据集，不是本轮已完成测试。

| ID | 场景 | 预期 | 核心指标 | 失败判定 |
|---|---|---|---|---|
| T01 | 单人正面静止/走动 | 稳定 LOCKED_HIGH | lock continuity、P95 latency | 无遮挡频繁降级/换 ID |
| T02 | 两人分离，旁观者做手势 | 仅 operator 可输出 | Wrong Operator Command = 0 | 任一旁观者指令被接受 |
| T03 | 两人正面交叉 | 不串人；不确定则降级 | wrong ownership、IDSW、reject duration | ownership 转移给 B |
| T04 | 相似衣服交叉 | 优先拒绝 | FAR/Wrong Operator Acceptance | 无 margin 仍自动 reacquire |
| T05 | 0.5/1/2/5 s 遮挡 | 短遮挡恢复，长遮挡 lost | reacquire recall/time | 遮挡后错绑旁观者 |
| T06 | operator 出画，B 留场 | OPERATOR_LOST/HOVER | lost latency | 自动把 B 当 operator |
| T07 | operator 从不同位置返场 | gated reacquire | TAR@zero-WOA、time | 仅凭最高 ReID 就接受 |
| T08 | 背身/侧身/正脸变化 | gallery 多视角稳定 | intra/inter similarity margin | gallery 被坏 crop 污染 |
| T09 | 快速机动/运动模糊 | GMC 可降级、不误绑 | GMC inlier/quality、WOA | 错 GMC 导致 authority switch |
| T10 | detector 连续漏检 | 进入 shadow/low/lost | state transition timing | 低置信仍输出 Gesture |
| T11 | 深度接近/远离/相互遮挡 | 3D 连续性 gate 生效 | 3D innovation、depth validity | 不可能 3D 跳变被接受 |
| T12 | face 可见/不可见/冲突 | 可见时加证据，不可见不中断 | face availability、conflict reject | face 缺失触发换人 |
| T13 | 录像回放重复运行 | 决策可复现 | deterministic event diff | 同输入 ownership 事件漂移 |
| T14 | 模型/相机 timeout | Safety FSM HOVER/REJECT | fault-to-safe latency | timeout 后仍发非 HOVER Intent |
| T15 | 长时间 30–60 min 多人 | 内存有界、无 gallery 漂移 | RSS、gallery size、WOA | 内存增长或身份逐渐污染 |

建议 Gate 不是只看 HOTA/IDF1：

- `Wrong Operator Command Count = 0` 为硬门槛；
- `Wrong Ownership Acceptance Rate` 是第一身份指标；
- 在该硬门槛下最大化 reacquire recall、缩短 reacquire time；
- 同时记录 HOTA、IDF1、IDSW、fragmentation、detector recall、P50/P95 latency、端到端 FPS；
- 阈值必须划分 train/calibration/test 场景，不能在最终测试片段上调参。

---

## 12. 风险登记

| 风险 | 严重度 | 当前状态 | 缓解 |
|---|---:|---|---|
| 相似衣服造成 body ReID 混淆 | 高 | 未实测 | competitive margin、face/3D、拒绝优先、项目数据微调 |
| Gallery 污染 | 高 | 需自研 | 仅 LOCKED_HIGH 写入、质量门、容量/多样性、回滚 |
| MOT ID switch 传播为控制权切换 | 高 | 架构可消除 | session id 与 track id 分离；Ownership Manager 二次认证 |
| 双目深度在低纹理/遮挡失效 | 中高 | 等相机 | validity/confidence gate；无深度时降级而非假定 |
| GMC 在无人机运动中估错 | 中高 | 未实测 | quality/inlier gate、IMU/视觉一致性、fallback |
| BoxMOT AGPL 发布约束 | 中高 | 已识别 | 研究/Runtime 分离，发布前许可证评审 |
| OSNet ONNX→RKNN 量化漂移 | 中 | 未实测 | static shape、FP/INT8 parity、阈值重新标定 |
| NUC OpenCV Python ABI 冲突 | 中 | 已观察、未改 | Stage 2 环境规划时修复，不在本轮安装/升级 |

---

## 13. Sources

### 冻结源码（主要证据）

- [BoxMOT at `8576283`](https://github.com/mikel-brostrom/boxmot/tree/857628343860db1ea48ea50db7a73c25b7a3be13)
- [BoT-SORT at `2519854`](https://github.com/NirAharon/BoT-SORT/tree/251985436d6712aaf682aaaf5f71edb4987224bd)
- [ByteTrack at `d1bf019`](https://github.com/FoundationVision/ByteTrack/tree/d1bf0191adff59bc8fcfeaa0b33d3d1642552a99)
- [deep-person-reid at `f8cd150`](https://github.com/KaiyangZhou/deep-person-reid/tree/f8cd150fdf77e8d9e1ed143b7f308c2c609ded50)
- [OpenCV Zoo at `47534e2`](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98)
- [RKNN Model Zoo at `bad6c73`](https://github.com/airockchip/rknn_model_zoo/tree/bad6c7334531becaf90a561988519b7bec34d0ab)

### 官方文档与论文（交叉验证）

- [BoT-SORT paper](https://arxiv.org/abs/2206.14651)
- [Deep OC-SORT paper](https://arxiv.org/abs/2302.11813)
- [OpenCV TrackerNano 4.12 API](https://docs.opencv.org/4.12.0/d8/d69/classcv_1_1TrackerNano.html)
- [OpenCV TrackerNano implementation](https://github.com/opencv/opencv/blob/4.x/modules/video/src/tracking/tracker_nano.cpp)
- [NVIDIA DeepStream NvMultiObjectTracker: shadow tracking, ReID gallery and target re-association](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvtracker.html)

