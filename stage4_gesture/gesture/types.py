from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class GestureLabel(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    ASCEND = "ASCEND"
    DESCEND = "DESCEND"
    HOVER = "HOVER"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"

    @property
    def is_legal_gesture(self) -> bool:
        return self not in {GestureLabel.UNKNOWN, GestureLabel.INVALID}


@dataclass
class GestureCandidate:
    timestamp_ms: int
    frame_id: int
    local_detection_id: int
    label: GestureLabel
    score: float
    stable: bool = False
    stable_for_ms: int = 0
    matched_labels: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    rule_scores: Dict[str, float] = field(default_factory=dict)
    source_quality_score: Optional[float] = None
    source_schema_version: str = "NormalizedSkeletonV1"
    schema_version: str = "GestureCandidateV1"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["label"] = self.label.value
        return value

