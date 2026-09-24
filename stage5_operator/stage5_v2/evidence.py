from __future__ import annotations

import math
from dataclasses import dataclass, field


WEIGHTS = {"reid": .40, "depth": .20, "motion": .15, "mot": .10,
           "pose": .10, "hsv": .05}


@dataclass(frozen=True)
class Evidence:
    value: float = 0.0
    reliability: float = 0.0
    available: bool = False

    def __post_init__(self):
        if not math.isfinite(self.value) or not math.isfinite(self.reliability):
            raise ValueError("nonfinite evidence")
        if not 0 <= self.value <= 1 or not 0 <= self.reliability <= 1:
            raise ValueError("evidence outside [0,1]")

    def to_dict(self):
        return {"score": self.value, "reliability": self.reliability,
                "available": self.available}


@dataclass
class CandidateEvidence:
    track_id: int
    reid: Evidence = field(default_factory=Evidence)
    depth: Evidence = field(default_factory=Evidence)
    motion: Evidence = field(default_factory=Evidence)
    mot: Evidence = field(default_factory=Evidence)
    pose: Evidence = field(default_factory=Evidence)
    hsv: Evidence = field(default_factory=Evidence)
    hard_gates: dict[str, bool] = field(default_factory=dict)
    fused_raw: float = 0.0
    fused_normalized: float = 0.0
    effective_weight: float = 0.0

    @property
    def accepted(self):
        return bool(self.hard_gates) and all(self.hard_gates.values())

    def fuse(self, weights=WEIGHTS):
        self.effective_weight = sum(weights[k] * getattr(self, k).reliability
                                    for k in weights if getattr(self, k).available)
        self.fused_raw = sum(weights[k] * getattr(self, k).reliability * getattr(self, k).value
                             for k in weights if getattr(self, k).available)
        self.fused_normalized = self.fused_raw / self.effective_weight if self.effective_weight else 0.0
        return self.fused_normalized

    def to_dict(self):
        return {"track_id": self.track_id,
                **{f"S_{k}": getattr(self, k).value if getattr(self, k).available else None
                   for k in WEIGHTS},
                **{f"R_{k}": getattr(self, k).reliability if getattr(self, k).available else None
                   for k in WEIGHTS},
                "hard_gates": self.hard_gates, "hard_pass": self.accepted,
                "raw_fused_score": self.fused_raw,
                "normalized_fused_score": self.fused_normalized,
                "effective_weight": self.effective_weight}


def similarity(a, b):
    if a is None or b is None or len(a) != len(b):
        return None
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    if not math.isfinite(dot):
        return None
    return max(0.0, min(1.0, (dot + 1.0) / 2.0))
