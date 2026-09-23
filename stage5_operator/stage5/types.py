from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence


BBox = tuple[float, float, float, float]


def finite_bbox(box: Sequence[float]) -> bool:
    try:
        return len(box) == 4 and all(math.isfinite(float(x)) for x in box) and float(box[2]) > float(box[0]) and float(box[3]) > float(box[1])
    except (TypeError, ValueError, OverflowError):
        return False


@dataclass
class DetectionV1:
    timestamp_ms: int
    frame_id: int
    bbox_xyxy: BBox
    pose: Any
    pose_quality: Any
    skeleton: Any
    confidence: float
    embedding: Optional[tuple[float, ...]] = None
    crop_quality: float = 0.0


@dataclass
class TrackedPersonV1:
    timestamp_ms: int
    frame_id: int
    track_id: int
    bbox_xyxy: BBox
    pose: Any
    pose_quality: Any
    skeleton: Any
    tracker_confidence: float
    age_frames: int
    lost_frames: int
    embedding: Optional[tuple[float, ...]] = None
    crop_quality: float = 0.0
    source_backend: str = "kalman_iou_two_pass_v1"
    schema_version: str = "TrackedPersonV1"

    def log_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "timestamp_ms": self.timestamp_ms,
            "frame_id": self.frame_id, "track_id": self.track_id,
            "bbox_xyxy": list(self.bbox_xyxy), "tracker_confidence": self.tracker_confidence,
            "age_frames": self.age_frames, "lost_frames": self.lost_frames,
            "pose_quality_valid": bool(getattr(self.pose_quality, "valid", False)),
            "crop_quality": self.crop_quality, "embedding_available": self.embedding is not None,
            "source_backend": self.source_backend,
        }


@dataclass
class AuthorizedGestureV1:
    timestamp_ms: int
    frame_id: int
    operator_session_id: Optional[str]
    current_track_id: Optional[int]
    authorization_state: str
    gesture: str = "UNKNOWN"
    gesture_confidence: float = 0.0
    valid: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    ownership_score: float = 0.0
    schema_version: str = "AuthorizedGestureV1"

    def to_dict(self) -> dict:
        value = asdict(self)
        if not math.isfinite(self.gesture_confidence) or not math.isfinite(self.ownership_score):
            raise ValueError("non-finite authorization score")
        if self.valid and (not self.operator_session_id or self.current_track_id is None or self.gesture not in
                           {"LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER"}):
            raise ValueError("invalid authorized gesture invariant")
        return value
