# RK3576 Stage5 Auto Reauthorize 真人验收

## 实现审计

基线：`rk3576-migration`、`b4bf43d`，板端工作区开始时干净。现有算法位于 `stage5_operator/stage5_v2/ownership.py`，配置 `Settings.auto_reauthorize_enabled=False`。`stage5_operator/live_operator_v2.py` 已有 `--auto-reauthorize`；此前 `scripts/preview_stage5_web.py` 未接入该开关。此次仅给网页入口接同名开关、显示已有诊断、将现有 Stage5 frame record 可选写入 JSONL；未改 Ownership/Gallery/ReID/Session 算法或阈值。

首次授权必须 T-Pose，Stage5 `Gallery.seed` 至少需要 6 个相容 embedding。LOST 后的自动路径不需要 T-Pose：`_reauthorize` 保留手动 T-Pose 分支，若仍为 `OPERATOR_LOST`，再运行 `_auto_reauthorize`。自动路径的现有复合门槛包括 Gallery max ≥0.94、top-k mean ≥0.92、≥2 个 sample match（sample 门槛 0.90）、fused score ≥0.88、margin ≥0.15、有效 Pose/crop/连续性、连续 ≥8 帧且 ≥700 ms，单次确认 timeout 1500 ms。与手动 LOST T-Pose 使用的 0.90 ReID 门槛不同。任一门槛失败回到 `OPERATOR_LOST`，不授权 gesture。成功创建**新 Session ID**并绑定当前 track，旧 Gallery 在 LOST/确认期间保留；成功后 Gallery 延迟更新 1000 ms。track ID 变化允许。

网页增加 `--auto-reauthorize`（别名 `--enable-auto-reauthorize`），默认仍关闭；与 `live_operator_v2.py` 使用相同 `Settings` 字段。`--log-jsonl` 写现有 Stage5 frame record，附加可读别名字段；路径使用 exclusive create，避免覆盖已有日志。网页状态可见旧 Session、旧 track、候选 track、Auto 状态、ReID、Gallery 最大/TopK/匹配数、第二候选、margin、确认帧数/时间、拒绝原因、新旧 Session 和按 track 对应的 depth age/valid。

## 真人测试方法和原始证据

板端使用相机 `/dev/video73`、Run B 标定、180° analysis frame、RKNN Pose/OSNet、Stage5 ROI Depth、BoT-SORT。视觉预览无 Stage6/PX4 输出。运行命令：

```bash
source scripts/env_rk3576.sh
~/venvs/fcv_stage5/bin/python3 scripts/preview_stage5_web.py \
  --host 0.0.0.0 --port 8081 --auto-reauthorize \
  --log-jsonl /tmp/fcv_auto_reauth_human_<unique>.jsonl
```

15 分钟现场日志：`/tmp/fcv_auto_reauth_human_1790409550.jsonl`（约 5953 帧，未纳入 Git）。网页实测可看状态；退出后 `/dev/video73` 空闲。只有原操作手在场，没有第二位真人，因此负样本和双人竞争不能验收。

## 单人恢复结果

首次 T-Pose → `LOCKED_HIGH` 成功。随后观察到 4 次 **`OPERATOR_LOST → AUTO_REAUTHORIZE_CONFIRMING → LOCKED_HIGH`**，均无再次 T-Pose，均创建新 Session；第 1、3、4 次自动恢复时 MOT track 改变。

| 自动成功次序 | 旧 track → 新 track | 最终 Gallery max | 最后连续确认段至成功 | Gallery size |
|---:|---|---:|---:|---:|
| 1 | 1 → 13 | 0.9623 | 791 ms | 16 |
| 2 | 13 → 13 | 0.9683 | 993 ms | 16 |
| 3 | 13 → 18 | 0.9636 | 1145 ms | 16 |
| 4 | 18 → 24 | 0.9524 | 1110 ms | 16 |

连续确认段至成功：均值 1009.75 ms，中位数 1051.5 ms，最大 1145 ms。这是**算法首次连续确认帧到成功**的时间，不是手机秒表或身体重新入镜至成功的 glass-to-glass 时间。另有 2 次短暂出画由既有 `REACQUIRE_CONFIRMED` 路径恢复，保留旧 Session，没有进入 `OPERATOR_LOST`；这不计为 Auto Reauthorize 成功。之后额外一次长时间 LOST 返回未成功，候选 ReID 多在 0.87–0.90，并有 `AUTO_REAUTH_IDENTITY_LOW`、`AUTO_REAUTH_LOW_CROP_QUALITY`、`AUTO_REAUTH_MARGIN_LOW`，符合原有 fail-closed 设计。故此次不能宣称所有返回必能自动成功，也没有调整阈值。

Gallery 在各次 LOST/确认/成功期间保留 16 条；成功后的旧/新 Session ID 不同。自动恢复需要的深度没有成为单独的阻断条件；LOST 候选仍按原有证据融合。未见第二人误授权，但因没有第二人，**不能**将其记为攻击性负样本通过。

## ReID 分布与阈值判断

单人现场中，筛选 `len(people)==1`、候选 track 对应有效 Pose 且 crop quality ≥0.75 的候选帧，得到 568 个探索性“原操作手场景候选”相似度：min 0.7711、mean 0.8928、median 0.8920、max 0.9789。此集合没有逐帧人工身份标注，可能混入误检，**不能当作严格正样本分布**。进入确认的 24 个高质量样本为 min 0.9409、mean 0.9522、median 0.9493、max 0.9789；544 个被拒候选 min 0.7711、mean 0.8902、median 0.8916、max 0.9398。负样本无真人数据，无法计算正负重叠或安全地建议新阈值。已有原操作手返回时低于 0.94 的失败窗口，后续若要调门槛，必须先收集第二人负样本并独立审查；本轮维持原值。

## 性能与回归

首次锁定后的 60 秒：519 帧、59.975 秒，实际处理率 8.64 FPS；Pose 平均 32.72 ms，OSNet/ReID 平均 23.26 ms，ROI Depth 每处理帧平均 13.33 ms（包括缓存命中帧），Stage5 融合平均 7.25 ms，Stage5 total 平均 56.07 ms。`LOCKED_HIGH` 帧的融合平均 7.39 ms；LOST 帧平均 6.42 ms，不显示开启后有明显额外负担，但两个状态场景不同，不能单靠此差值精确归因。完整约 15 分钟日志处理率为 6.63 FPS，包含长期 LOST、空画面和多人检测等变化场景；不作为稳定锁定状态的独立性能值。

单元/回归：Stage3 12、Stage4 14、Stage5 112、Stage6 67 全部通过；新增预览入口测试覆盖默认关闭、显式启用、成功/拒绝诊断与网页字段。已有 Stage5 自动恢复测试覆盖正负身份、Gallery、track 变化、候选切换、超时和 Session 语义。真人预览在 headless 环境运行并释放相机。

## 未完成的受控场景

- 第二人冒充、第二人 T-Pose/合法 gesture、双人竞争：现场只有原操作手，**未测**。
- 1/3/5/10 秒离场、左右移动约 0.5 m、前后移动、侧身：没有逐项受控计时和位置标记，**未测**。日志包含不同实际离场，但不能冒充这些指定 Case。
- 原操作手额外一次长时间 LOST 返回未恢复，提示真实场景下该路径非 100% 成功；阈值评审需正负标注样本。本轮停止于验收结论，不继续调参。

PX4 LIVE OUTPUT：NOT USED。
