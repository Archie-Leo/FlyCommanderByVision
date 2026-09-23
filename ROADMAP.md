# Roadmap

详细验收条目保留在 `无人机视觉交互_执行清单_Stage0-9.md`，这里仅索引当前 Gate，不替代原清单。

| Stage | Gate | 下一步 |
|---|---|---|
| 0 环境 | PASS | 保持已验证版本 |
| 1 控制层 | PASS / frozen | 不修改 PX4 内部飞控 |
| 2 双目 | PASS / geometry baseline frozen | Run B 是开发默认，不等于所有环境的正式计量认证 |
| 3 Pose | PASS / V1 FROZEN | 保持接口稳定 |
| 4 Gesture | PASS / V1 FROZEN | 不补采、不调阈值；T-Pose 保留给 Stage 5 |
| 5 Tracking + Ownership | IMPLEMENTED / MANUAL VALIDATION PENDING | 完成下列真人双人 Gate；不得提前标 PASS |
| 6 视觉→仿真控制闭环 | NOT STARTED | Stage 5 Gate 通过后再计划 |
| 7–9 | NOT STARTED | 依原执行清单推进 |

Stage 5 真人 Gate：A 不做 T-Pose 时保持 `WAIT_OPERATOR`；A T-Pose 至少 600 ms 且 6 帧后才可锁定；A 的合法稳定手势可授权、B 的任何手势不可授权；A 离场立即失权，B 不继承旧 session；A 返回只能保守重获或重新授权；两人交叉、遮挡、相似衣着时必须正确保持 A 或拒绝，不得错误授权 B。记录真人视频（经同意）、逐帧 JSONL、false authorization、ID switch、重获时延与拒绝率。当前没有第二个人的真人 Gate 证据。
