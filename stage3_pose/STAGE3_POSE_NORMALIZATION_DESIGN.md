# PersonPose V1 and NormalizedSkeleton V1

## Coordinate and semantic contract

- Source image: rectified physical LEFT, 1280×960 BGR8.
- Origin: top-left; +x right; +y down.
- `left_*`/`right_*`: subject anatomical side.
- Inference and geometry are never horizontally mirrored.
- `local_detection_id`: per-frame array index only; never identity.
- Angles: degrees.
- Missing/invalid values serialize as `null` plus `valid=false`, never fake zero geometry.

## PersonPose V1

```json
{
  "schema_version": "PersonPoseV1",
  "timestamp_ms": 0,
  "frame_id": 0,
  "local_detection_id": 0,
  "image_width": 1280,
  "image_height": 960,
  "bbox_xyxy": [0.0, 0.0, 0.0, 0.0],
  "pose_score": 0.0,
  "backend_name": "mediapipe_pose_landmarker_full",
  "joints": {
    "left_wrist": {
      "name": "left_wrist",
      "x_px": 0.0,
      "y_px": 0.0,
      "x_norm_image": 0.0,
      "y_norm_image": 0.0,
      "confidence": 0.0,
      "visibility": 0.0,
      "presence": 0.0,
      "valid": false,
      "derived": false
    }
  }
}
```

`pose_score` 是 canonical joints confidence 的 median 派生值，不冒充模型原生整体分数。

## PoseQuality V1

```json
{
  "schema_version": "PoseQualityV1",
  "valid": false,
  "score": 0.0,
  "reasons": ["MISSING_RIGHT_WRIST"],
  "key_joint_coverage": 0.0,
  "bbox_coverage": 0.0,
  "truncation_flags": [],
  "confidence_summary": {"min": 0.0, "median": 0.0, "mean": 0.0}
}
```

Reasons 可包括 `NO_POSE`、`LOW_CONFIDENCE`、`MISSING_SHOULDERS`、`MISSING_HIPS`、
`MISSING_ARMS`、具体 `MISSING_*`、`TOO_SMALL`、`HEAVY_TRUNCATION`、
`INVALID_GEOMETRY`。全部阈值由配置集中管理。

## NormalizedSkeleton V1

```json
{
  "schema_version": "NormalizedSkeletonV1",
  "timestamp_ms": 0,
  "frame_id": 0,
  "local_detection_id": 0,
  "body_center_px": [0.0, 0.0],
  "body_scale_px": 1.0,
  "joints": {},
  "bones": {},
  "angles_deg": {},
  "quality": {},
  "source_pose_score": 0.0,
  "image_size": [1280, 960],
  "valid": false,
  "invalid_reasons": []
}
```

Center：`pelvis = (left_hip + right_hip)/2`。  
Scale：`(shoulder_width + distance(neck,pelvis))/2`。  
Joint：`(joint_px - pelvis_px)/scale_px`。

Derived `neck` 与 `pelvis` 明确保留 `derived=true`。主要 bones：左右 upper/lower arm、
shoulder line、hip line、左右 shoulder→hip。角度：左右 elbow angle 和左右 upper-arm
image angle。任何依赖的 joint 无效时，相应 feature 为 `valid=false`。

Stage 4 只能依赖此 canonical JSON/dataclass，不得访问 MediaPipe 原始 landmark 对象。
