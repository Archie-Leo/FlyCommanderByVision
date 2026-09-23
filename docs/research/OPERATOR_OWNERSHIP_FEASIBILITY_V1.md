# Operator Ownership Feasibility V1

> 结论日期：2026-09-10  
> 决策：**HIGH feasibility / CONDITIONAL_GO**  
> 本文是 Stage 5 前设计约束，不代表 Stage 5 已启动或通过。

## 1. 最终判断

基于冻结的 BoxMOT、BoT-SORT、ByteTrack、deep-person-reid、OpenCV Zoo 与 RKNN Model Zoo，实现类似 DJI / HOVERAir 的以下竞赛目标，技术可行性评为 **HIGH**：

- 用户通过显式流程绑定唯一 Operator；
- 常规运动与短遮挡期间保持身份；
- MOT 轨迹重编号后可基于 session memory 保守恢复；
- 旁观者做动作不产生控制 Intent；
- 完全不确定时拒绝/HOVER，而不是自动换人。

工程状态仍是 **CONDITIONAL_GO**，因为 HIGH 依赖以下条件：

1. 最终双目相机数据上证明 Wrong Operator Command 为 0；
2. OSNet 在目标视角和 RK3576 部署形态下通过 embedding parity、延迟和阈值标定；
3. 全局 Tracker 在实拍交叉、遮挡和相机运动数据上完成 A/B；
4. Ownership Manager 的状态与 Gesture/Intent 通路实施硬隔离；
5. BoxMOT 若进入分发 Runtime，先完成 AGPL 合规决策。

全遮挡时间无限、人物高度相似且无 face/3D/显式再确认时，任何纯视觉系统都不能证明身份。产品目标应是“缺证据就拒绝”，不是承诺所有情况下无缝跟回。

---

## 2. 采用的系统分层

```text
Stereo FramePacket
   │ left/right image + monotonic timestamp + calibration id
   ▼
Person/Pose Detector ───────────────┐
   │ person bbox / keypoints / conf │
   ├──────────────► OSNet Encoder   │
   │                 │ normalized embedding
   ▼                 ▼              │
Global MOT ◄────────────────────────┘
   │ TrackObservation[]
   │ (track_id is ephemeral)
   ▼
OperatorOwnershipManager
   ├─ OperatorTargetMemory / ReID Gallery
   ├─ track continuity and candidate competition
   ├─ temporal + kinematic gates
   ├─ stereo 3D continuity gate
   ├─ optional face evidence
   └─ explicit acquire/release protocol
   │
   ├─ OperatorObservation + ownership_state
   └─ authorization = true only in LOCKED_HIGH
                 │
                 ▼
Pose Normalize → Gesture → Unknown Reject
                 │
                 ▼
Safety FSM → IntentMessage → frozen Control Gateway

Optional SOT:
Global MOT loss ─► SOT shadow ROI proposal ─► OwnershipManager evidence only
                                              (never authority)
```

接口纪律：

- Global MOT 负责“画面里有哪些连续轨迹”；
- Ownership Manager 负责“哪一个轨迹当前可以代表本次 operator session”；
- Safety FSM 负责“当前证据是否足以允许任何控制意图”；
- Gesture 模块永远不接收所有人的 pose，只接收已授权 OperatorObservation；
- Control Gateway 保持 Stage 1 冻结，不感知 Tracker、ReID 或相机。

---

## 3. Tracker 决策

### 3.1 首轮候选

| 角色 | 候选 | 决策 |
|---|---|---|
| Global MOT 首 baseline | BoT-SORT family | GO：移动相机 GMC + ReID + 两阶段关联 |
| 身份型对照 | StrongSORT | GO for A/B：多样本 NN gallery；不能直接当 ownership |
| 运动/遮挡对照 | DeepOCSORT | GO for A/B |
| 当前新候选 | OccluBoost | GO for offline A/B；AGPL/新实现条件限制 |
| 无 ReID 下界 | ByteTrack | GO as baseline only |
| 原始 BoT-SORT Runtime | 固定 2022 repo | NO_GO：源码与依赖问题 |
| Dedicated TrackerNano | OpenCV NanoTrack | DEFER/OPTIONAL：只可 shadow，不是身份组件 |

在获得项目数据前不预设最终算法赢家。若必须现在指定默认开发入口，则用 **BoT-SORT family + 外部 OSNet embeddings**，同时保持 TrackerAdapter 可替换。即使 StrongSORT 在身份型测试胜出，也只替换 Global MOT，不改变 Ownership Manager。

### 3.2 为什么不默认 `Global MOT + Dedicated SOT`

SOT 的优势是 detector 空洞时仍能提议 ROI；代价是模板漂移、与 MOT box 冲突、额外生命周期和模型部署。对“拒绝优先”的系统，lost 并安全悬停比静默漂移到旁观者更可接受。

因此 V1 的最小可靠组合是：

```text
Global MOT + persistent Operator Gallery + geometry/3D gates + Safety FSM
```

只有 T05/T10 消融证明 SOT 能显著提高正确恢复且不增加 Wrong Ownership Acceptance 时，才加入 `ShadowTrackerAdapter`。SOT 输出不能触发 `LOCKED_HIGH`，最多维持 `SHADOW_TRACKING` 或帮助 detector 指定 ROI。

---

## 4. OperatorTargetMemory

建议的核心数据结构：

```text
OperatorTargetMemory
  operator_session_id: uint64
  bound_at_ns: int64
  state: OwnershipState
  current_track_id: optional<int64>
  gallery: deque<GalleryEntry>
  face_gallery: deque<FaceEntry>          # optional
  last_confirmed_bbox: BBox2D
  last_confirmed_velocity_px_s: Vec2
  last_confirmed_xyz_m: optional<Vec3>
  xyz_covariance: optional<Matrix3>
  last_confirmed_ns: int64
  ambiguity_score: float
  consecutive_accepts: int
  consecutive_rejects: int
  model_version: string

GalleryEntry
  embedding: float32[512]                 # L2 normalized
  captured_at_ns: int64
  source_track_id: int64
  crop_quality: float
  visibility: float
  viewpoint_bucket: enum/front-side-back-or-cluster
  bbox: BBox2D
```

规则：

- Gallery 是 operator session 所有，不随 MOT track 删除；release/session reset 时清空。
- 只在 `LOCKED_HIGH` 且质量、可见度、margin、时空门控都通过时写入。
- 相近连续帧不重复写，保留多视角/多姿态代表；固定容量，例如每个视角桶 K 个，具体 K 由数据标定。
- 新 feature 同时与 positive gallery 和当前 hard-negative/bystander 候选比较。
- model version 或预处理变更后，不混用旧 Gallery。
- 任何疑似错误写入都可从日志重放并回滚；不要只保存不可审计的单个 EMA。

---

## 5. Ownership 状态机

状态定义按本次预研要求冻结为：

```text
UNBOUND
  └─ explicit acquire request ─► ACQUIRING

ACQUIRING
  ├─ N-frame stable unique candidate + quality gates ─► LOCKED_HIGH
  ├─ ambiguity/timeout ─► UNBOUND
  └─ explicit cancel ─► RELEASED

LOCKED_HIGH                       [唯一允许 Gesture 进入 Safety FSM]
  ├─ weaker evidence ─► LOCKED_LOW
  ├─ detector miss but shadow hypothesis valid ─► SHADOW_TRACKING
  ├─ no valid hypothesis ─► REACQUIRING
  └─ explicit release ─► RELEASED

LOCKED_LOW                        [禁止新控制 Gesture；默认 HOVER]
  ├─ evidence recovers ─► LOCKED_HIGH
  ├─ detector miss ─► SHADOW_TRACKING
  └─ grace timeout/conflict ─► REACQUIRING

SHADOW_TRACKING                   [仅 ROI/运动假设，不授权]
  ├─ MOT + identity reconfirmed ─► LOCKED_HIGH/LOCKED_LOW
  └─ shadow timeout/conflict ─► REACQUIRING

REACQUIRING                       [不授权]
  ├─ unique candidate passes stricter multi-frame gates ─► LOCKED_HIGH
  └─ timeout/no candidate ─► OPERATOR_LOST

OPERATOR_LOST                     [不授权；HOVER/上层安全动作]
  ├─ explicit reacquire + strict confirmation ─► ACQUIRING
  └─ release/session timeout ─► RELEASED

RELEASED
  └─ cleanup complete ─► UNBOUND
```

不可违反的 invariant：

1. 任意时刻最多一个 `operator_session_id`；
2. 只有 `LOCKED_HIGH` 可设置 `authorization=true`；
3. `track_id` 改变不会自动改变 operator session，也不会自动通过 reacquire；
4. `uncertain`, `tie`, `conflict`, `timeout`, `invalid depth` 均不能触发 ownership switch；
5. `SHADOW_TRACKING` 的 bbox 不进入 Gesture 控制路径；
6. 新 candidate 的最高分不等于足够可信，必须同时通过绝对阈值和竞争 margin；
7. release 后历史 feature 不再参与下一次 acquisition，除非未来产品明确设计跨会话身份功能。

---

## 6. 证据融合与拒绝规则

不建议一开始训练黑盒融合器。V1 使用可解释 hard gates + calibrated score：

```text
hard gates:
  person class valid
  detection/crop quality valid
  time since last confirmation within state-specific horizon
  2D kinematics physically plausible
  stereo 3D innovation plausible when depth valid
  no strong face conflict when face available

soft evidence:
  S_reid     = gallery nearest/top-k similarity
  S_motion   = Kalman/track continuity score
  S_iou      = spatial overlap/track association evidence
  S_depth    = stereo 3D continuity score, only when valid
  S_face     = optional positive evidence
  Q          = crop/visibility/detector quality

candidate_score = calibrated_combination(...)
accept only if:
  candidate_score >= threshold_for_state
  AND score(best) - score(second_best) >= ambiguity_margin
  AND stable for required consecutive observations/time
  AND all hard gates pass
```

`REACQUIRING` 阈值必须高于 `LOCKED_HIGH` 内连续维持阈值，因为重新绑定比保持同轨更危险。缺失 face 或 invalid depth 时，不把对应分数记为 0；应标记 evidence unavailable，并提高其他证据要求或拒绝。

首轮不在文档里拍脑袋冻结具体 cosine/时间阈值。阈值必须由 T01–T15 calibration split 决定，并把 `Wrong Operator Acceptance = 0` 作为约束，再优化召回。

---

## 7. 成熟组件与自主模块边界

### 直接复用/适配

- 检测/pose 模型和 RKNN inference pattern；
- MOT 的 Kalman、association、lost/refind、GMC；
- OSNet backbone 与训练/导出方法；
- YuNet/SFace 可选 face pipeline；
- 标准 MOT 与延迟指标。

### 必须自主开发

- `OperatorOwnershipManager`；
- session-level `OperatorTargetMemory` 与 Gallery anti-contamination；
- acquisition/release/reacquire protocol；
- candidate competition、ambiguity margin、hard negatives；
- stereo 3D continuity/uncertainty gate；
- Safety FSM ownership guard；
- ownership event schema、回放评测与 fault injection；
- 以零误授权为第一约束的阈值标定。

这是项目的主要创新点，不应埋进某个 Tracker fork。

---

## 8. 未来 Stage 5 建议文件边界

以下只是实施蓝图，本轮不创建源码：

```text
vision_runtime/
├─ include/vision_runtime/tracking/
│  ├─ tracker_adapter.hpp
│  ├─ track_observation.hpp
│  └─ tracker_config.hpp
├─ include/vision_runtime/identity/
│  ├─ embedding_encoder.hpp
│  ├─ operator_target_memory.hpp
│  ├─ reid_gallery.hpp
│  └─ identity_evidence.hpp
├─ include/vision_runtime/ownership/
│  ├─ ownership_state.hpp
│  ├─ operator_ownership_manager.hpp
│  └─ ownership_policy.hpp
├─ include/vision_runtime/geometry/
│  ├─ stereo_person_localizer.hpp
│  └─ spatiotemporal_gate.hpp
├─ include/vision_runtime/safety/
│  └─ ownership_guard.hpp
├─ src/tracking/
│  └─ <selected_tracker>_adapter.cpp
├─ src/identity/
│  ├─ osnet_encoder.cpp
│  └─ reid_gallery.cpp
├─ src/ownership/
│  └─ operator_ownership_manager.cpp
├─ src/geometry/
│  ├─ stereo_person_localizer.cpp
│  └─ spatiotemporal_gate.cpp
├─ config/
│  ├─ tracker.yaml
│  └─ ownership_policy.yaml
└─ test/
   ├─ test_ownership_fsm.cpp
   ├─ test_gallery_contamination.cpp
   ├─ test_candidate_ambiguity.cpp
   ├─ test_track_id_change.cpp
   ├─ test_stereo_gate.cpp
   └─ replay_ownership_scenarios.cpp

evaluation/operator_ownership/
├─ scenario_manifest.schema.json
├─ ownership_event.schema.json
├─ evaluate_mot.py
├─ evaluate_ownership.py
└─ reports/
```

模块命名最终应服从当时已有 workspace 结构；不为迎合此草案重构 Stage 0/1。

---

## 9. 双目相机到货后的第一批任务

Stage 2 的第一任务仍应是相机接入和可重复数据，不是直接写 Stage 5 Runtime：

1. 冻结左右相机序列号、分辨率、帧率、曝光/增益和硬件/驱动时间戳语义；
2. 完成左右帧同步统计，记录 skew 的 P50/P95/max；
3. 完成 intrinsic/extrinsic/rectification 标定及 reprojection error；
4. 对 1–8 m（按最终镜头有效范围调整）的站立人体测量 depth validity、bias、P95 error；
5. 录制 T01–T15 原始双目数据，连同 calibration id、时间戳和场景标签保存为可回放集；
6. 先离线跑 tracker/OSNet A/B，再决定 Stage 5 Runtime 的 TrackerAdapter。

明确不做：不用临时海康工业相机替代最终双目继续推进，不在本轮安装 OpenCV/模型依赖，不把参考仓库拷回 Windows，不修改冻结 Control Gateway。

---

## 10. Gate 与停止条件

### CONDITIONAL_GO 转 GO

- 双目 Stage 2 数据质量 Gate 通过；
- T02/T03/T04/T06/T07 中 Wrong Operator Command 均为 0；
- 录像重放一致且所有 ownership transition 有日志依据；
- OSNet ONNX/RKNN embedding parity 满足经标定的误差预算；
- 端到端 P95 latency/FPS 满足控制链预算；
- Tracker 选型由本项目数据而非公开榜单决定；
- Runtime 许可证方案明确。

### 应暂停或降级的条件

- 相似衣服场景在合理拒绝率下仍出现误授权；
- Gallery 在长时间测试中持续污染；
- 双目无效时系统默认为“通过”而非“证据缺失”；
- Tracker/SOT 能绕过 Ownership Manager 直接喂给 Gesture；
- 为兼容旧仓库需要破坏已冻结环境。

若发生上述问题，优先收紧到显式重新 acquisition、要求 face/交互 challenge 或更长确认窗口，而不是放松阈值追求看起来连续的跟踪。

---

## 11. 一句话路线

> 用成熟 MOT 保持轨迹，用 OSNet/可选 face/未来双目提供证据，用自主 Operator Ownership Manager 管理会话身份与拒绝，用 Safety FSM 保证只有 `LOCKED_HIGH` 的唯一操作手能产生控制意图；不确定时 HOVER，而不是换人。

详细源码证据、许可证、否定项和测试矩阵见 [OPERATOR_LOCK_OPEN_SOURCE_AUDIT.md](./OPERATOR_LOCK_OPEN_SOURCE_AUDIT.md)。

