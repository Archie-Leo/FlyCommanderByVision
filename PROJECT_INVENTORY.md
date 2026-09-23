# Project source inventory

Audited 2026-09-24 against `sentinel-S600` and the accessible Windows workspace. This inventory precedes integration copying. Paths below are original NUC directories; none are to be moved or deleted.

| Source path | Component / Stage | Ownership | Integration action | Large or sensitive material | Verified status |
|---|---|---|---|---|---|
| `~/ros2_px4_ws/src/drone_control_gateway` | ROS 2 Control Gateway / 1 | Project-owned | Copy source, launch, config, message, tests, README; do not copy `.git` or build | No; ignore `__pycache__` | Git HEAD `09776d3d234ccebabb5c86cf7aed7be1026bfe30`; Stage 1 PASS |
| `~/drone_stage2/stereo_calibration` | stereo capture/calibration/validation / 2 | Project-owned | Copy `.py`, tests, research/README; copy only Run B YAML and small formal reports | 443 MB original; captures, PNGs, sessions, NPZ and runtime output stay outside Git | Stage 2 PASS; Run B is development default |
| `~/drone_stage2/stereo_depth_validation` | stereo depth validation / 2 | Project-owned | Copy source, tests, README/notes | No bulky source | Stage 2 PASS |
| `~/drone_stage3_pose` | pose, quality, skeleton normalization / 3 | Project-owned except MediaPipe model/dependency | Copy source, tests, requirements, design/research/validation docs; not model or outputs | 95 MB original; `.task`, captures/snapshots/outputs excluded | PASS / V1 FROZEN |
| `~/drone_stage4_gesture` | frozen gesture baseline / 4 | Project-owned | Copy source, tests, protocol, evaluation, final audit reports; exclude datasets | 69 MB original; JSONL sessions, captures/videos excluded | PASS / Gesture V1 FROZEN; 0039/0040 retained as excluded invalid ground truth |
| `~/drone_stage5_operator` | visual ownership baseline / 5 | Project-owned | Copy source, tests, design/validation docs; exclude runs | Runtime JSONL stays outside Git | IMPLEMENTED / MANUAL VALIDATION PENDING, not PASS |
| `D:\FlyCommanderByVision` | project-level docs and selected source backup | Project-owned | Compare with NUC and use current Windows status/checklist/research docs; do not overwrite newer Windows documents | Local contact sheets/staging excluded | Accessible; includes latest Stage 4/5 status |
| `~/PX4-Autopilot`, `px4_msgs`, `px4_ros_com`, Micro-XRCE-DDS-Agent | flight stack / 0-1 | Third party | Commit/reference only; no repository copy | Large upstream repositories | PX4 v1.17.0; `px4_ros_com` `86e9aeb20e55a4673fa8a9f1c29ea06a6c5ad1af`; Agent v2.4.2 |
| `~/drone_vision_refs/operator_lock/{BoT-SORT,boxmot,ByteTrack,deep-person-reid,opencv_zoo,rknn_model_zoo}` | Stage 5 research | Third party | URLs/commits/licenses/research conclusions only | Large upstream repositories and possible weights | Source audit complete; none is bundled into Stage 5 runtime |

The original Control Gateway's `e88b16459b812cf59d824e6c7afbe9de8db1d725` is a stable predecessor, not the integration repository's history. Original Stage 1 working tree has only an untracked launch `__pycache__`; no source change was observed. No claim is made that copied files preserve independent source Git history.

Data remain at their original NUC paths. `DATASETS.md` will index them. The integration repository must exclude raw image/video/JSONL data, models, third-party trees, caches, ROS bags and ULogs. Copy-only consolidation cannot certify data quality beyond the existing Stage reports.
