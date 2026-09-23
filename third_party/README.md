# Third-party references (not vendored)

The following repositories are references/dependencies, **not** project-owned source bundled here. Commits and URLs were read from local NUC checkouts on 2026-09-24. Licenses below are from checkout `LICENSE` headers where checked; verify again before redistribution or integration.

| Project | URL | Commit/version | License / use |
|---|---|---|---|
| PX4-Autopilot | https://github.com/PX4/PX4-Autopilot | v1.17.0 | Flight stack; external runtime |
| px4_ros_com | https://github.com/PX4/px4_ros_com | `86e9aeb20e55a4673fa8a9f1c29ea06a6c5ad1af` | Official offboard reference; not bundled |
| px4_msgs | https://github.com/PX4/px4_msgs | v1.17.0 | ROS 2 interface; not bundled |
| Micro-XRCE-DDS-Agent | https://github.com/eProsima/Micro-XRCE-DDS-Agent | v2.4.2 | External bridge |
| BoT-SORT | https://github.com/NirAharon/BoT-SORT | `251985436d6712aaf682aaaf5f71edb4987224bd` | MIT; research only |
| BoxMOT | https://github.com/mikel-brostrom/boxmot | `857628343860db1ea48ea50db7a73c25b7a3be13` | AGPL-3.0; research only, compliance review required for runtime |
| ByteTrack | https://github.com/FoundationVision/ByteTrack | `d1bf0191adff59bc8fcfeaa0b33d3d1642552a99` | MIT; research only |
| deep-person-reid | https://github.com/KaiyangZhou/deep-person-reid | `f8cd150fdf77e8d9e1ed143b7f308c2c609ded50` | MIT; OSNet research only, no usable weights bundled |
| opencv_zoo | https://github.com/opencv/opencv_zoo | `47534e27c9851bb1128ccc0102f1145e27f23f98` | Apache-2.0; research only |
| rknn_model_zoo | https://github.com/airockchip/rknn_model_zoo | `bad6c7334531becaf90a561988519b7bec34d0ab` | Apache-2.0; future deployment reference |

OpenCV and MediaPipe are installed dependencies, not vendored. The Stage 5 tracker is a small original Kalman/IoU fallback, **not** an official BoT-SORT or ByteTrack integration. Stage 5 Gallery uses HSV clothing histograms, **not** OSNet. See `docs/research/` for the source audit and feasibility limits.
