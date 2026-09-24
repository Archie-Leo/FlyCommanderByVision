# Third-party notices — frozen NUC baseline

This repository contains original project glue/source, not the complete
third-party trees or model binaries. Each upstream remains separately
licensed. Pin exact versions before deployment; consult the upstream license
and seek legal review before redistributing a combined product.

| Dependency | Baseline | License / boundary |
|---|---|---|
| [BoxMOT](https://github.com/mikel-brostrom/boxmot) native BoT-SORT | `857628343860db1ea48ea50db7a73c25b7a3be13` | AGPL-3.0 in checked-out `LICENSE`; used as separately built external source, not vendored. Runtime/distribution compliance review required. |
| [deep-person-reid](https://github.com/KaiyangZhou/deep-person-reid) OSNet architecture | `f8cd150fdf77e8d9e1ed143b7f308c2c609ded50` | MIT source license; upstream hosted checkpoint is downloaded separately, not redistributed. |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | Python `1.0.1` | Apache-2.0 source/runtime; official model bundle downloaded separately. |
| [OpenCV](https://github.com/opencv/opencv) | `opencv-contrib-python 4.10.0.84` | Apache-2.0 project; installed dependency. |
| [PX4-Autopilot](https://github.com/PX4/PX4-Autopilot) | `v1.17.0` | BSD-3-Clause; external flight stack, not vendored. |
| [px4_msgs](https://github.com/PX4/px4_msgs) | `v1.17.0` | External ROS interface package, not vendored. |
| [Micro-XRCE-DDS-Agent](https://github.com/eProsima/Micro-XRCE-DDS-Agent) | `v2.4.2` | External DDS bridge, not vendored. |

The current Stage 5 V2 runtime **does** load BoxMOT native BoT-SORT and
OSNet; older Stage 5 V1/initial repo documents describing only Kalman/IoU
and HSV are historical, not the V2.2 runtime. The model-weight license and
any AGPL obligations are not resolved merely by this notice. Do not vendor
or publish the binaries as a shortcut. See `third_party/README.md` for
reference repositories and `models/manifest.json` for exact model hashes.
