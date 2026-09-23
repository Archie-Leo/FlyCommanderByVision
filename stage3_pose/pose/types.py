from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class JointObservation:
    name: str
    x_px: Optional[float]
    y_px: Optional[float]
    x_norm_image: Optional[float]
    y_norm_image: Optional[float]
    confidence: Optional[float]
    visibility: Optional[float] = None
    presence: Optional[float] = None
    valid: bool = False
    derived: bool = False


@dataclass
class PersonPose:
    timestamp_ms: int
    frame_id: int
    local_detection_id: int
    image_width: int
    image_height: int
    bbox_xyxy: Tuple[float, float, float, float]
    pose_score: Optional[float]
    backend_name: str
    joints: Dict[str, JointObservation]
    schema_version: str = "PersonPoseV1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PoseFrame:
    timestamp_ms: int
    frame_id: int
    image_width: int
    image_height: int
    poses: List[PersonPose]
    backend_name: str
    inference_latency_ms: float
    schema_version: str = "PoseFrameV1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PoseQuality:
    valid: bool
    score: float
    reasons: List[str]
    key_joint_coverage: float
    bbox_coverage: float
    truncation_flags: List[str]
    confidence_summary: Dict[str, Optional[float]]
    schema_version: str = "PoseQualityV1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedJoint:
    name: str
    x: Optional[float]
    y: Optional[float]
    confidence: Optional[float]
    valid: bool
    derived: bool = False


@dataclass
class BoneFeature:
    name: str
    start_joint: str
    end_joint: str
    dx: Optional[float]
    dy: Optional[float]
    length: Optional[float]
    valid: bool


@dataclass
class AngleFeature:
    name: str
    degrees: Optional[float]
    valid: bool


@dataclass
class NormalizedSkeleton:
    timestamp_ms: int
    frame_id: int
    local_detection_id: int
    body_center_px: Optional[Tuple[float, float]]
    body_scale_px: Optional[float]
    joints: Dict[str, NormalizedJoint]
    bones: Dict[str, BoneFeature]
    angles_deg: Dict[str, AngleFeature]
    quality: PoseQuality
    source_pose_score: Optional[float]
    image_size: Tuple[int, int]
    valid: bool
    invalid_reasons: List[str] = field(default_factory=list)
    schema_version: str = "NormalizedSkeletonV1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

