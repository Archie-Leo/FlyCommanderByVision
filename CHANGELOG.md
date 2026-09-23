# Changelog

此文件记录已验证阶段，不为复制后的集成仓库伪造原始 Git 历史；详细日期及来源见各阶段报告。

- Stage 1：独立 ROS 2 `drone_control_gateway`、Intent 映射和 SITL 验证；源工程当前 commit `09776d3d234ccebabb5c86cf7aed7be1026bfe30`，稳定前身 `e88b16459b812cf59d824e6c7afbe9de8db1d725`。
- 2026-09-14，Stage 2：双目采集、标定 Run B、极线校正和静态深度验证；Run B 为开发默认。
- 2026-09-15，Stage 3：Pose、质量判定、骨架归一化 V1 冻结。
- 2026-09-23，Stage 4：五手势协议、几何规则、时序稳定器和评估；Pilot 上将 DESCEND outward threshold 0.35→0.40，最终有效 held-out 46 段验收 PASS。0039/0040 经操作员确认录制错误，原始诊断与 exclusion 历史保留。
- 2026-09-23，Stage 5：多人视觉授权基线实现；真人双人验证待完成。
- 2026-09-24：仅工程化整理为独立主仓库副本、文档和私人备份准备；无算法调整。
