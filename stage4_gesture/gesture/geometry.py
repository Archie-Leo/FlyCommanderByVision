from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import GeometryConfig
from .types import GestureCandidate, GestureLabel


REQUIRED_JOINTS = (
    "left_shoulder",
    "left_elbow",
    "left_wrist",
    "right_shoulder",
    "right_elbow",
    "right_wrist",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class _Joint:
    x: float
    y: float
    confidence: float


@dataclass(frozen=True)
class _Check:
    name: str
    passed: bool
    score: float


@dataclass(frozen=True)
class _RuleResult:
    label: GestureLabel
    matched: bool
    score: float
    failures: Tuple[str, ...]


class GeometryGestureRecognizer:
    """Deterministic V1 geometry baseline over NormalizedSkeletonV1 only."""

    def __init__(self, config: Optional[GeometryConfig] = None):
        self.config = config or GeometryConfig()

    def recognize(self, skeleton: Any) -> GestureCandidate:
        if skeleton is None or skeleton == {}:
            return GestureCandidate(
                timestamp_ms=0,
                frame_id=-1,
                local_detection_id=-1,
                label=GestureLabel.INVALID,
                score=0.0,
                reasons=["NO_NORMALIZED_SKELETON"],
                source_schema_version="UNKNOWN",
            )
        timestamp_ms = int(_field(skeleton, "timestamp_ms", 0) or 0)
        frame_id = int(_field(skeleton, "frame_id", -1) or 0)
        detection_id = int(_field(skeleton, "local_detection_id", -1) or 0)
        schema = str(_field(skeleton, "schema_version", ""))
        quality = _field(skeleton, "quality", {})
        quality_score = _field(quality, "score", None)

        base = dict(
            timestamp_ms=timestamp_ms,
            frame_id=frame_id,
            local_detection_id=detection_id,
            source_quality_score=quality_score,
            source_schema_version=schema or "UNKNOWN",
        )
        if schema != "NormalizedSkeletonV1":
            return GestureCandidate(
                label=GestureLabel.INVALID,
                score=0.0,
                reasons=["UNSUPPORTED_SOURCE_SCHEMA"],
                **base,
            )

        quality_valid = bool(_field(quality, "valid", False))
        skeleton_valid = bool(_field(skeleton, "valid", False))
        if not quality_valid or not skeleton_valid:
            reasons = list(_field(skeleton, "invalid_reasons", []) or [])
            reasons.extend(list(_field(quality, "reasons", []) or []))
            return GestureCandidate(
                label=GestureLabel.INVALID,
                score=0.0,
                reasons=["SOURCE_POSE_INVALID"] + list(dict.fromkeys(reasons)),
                **base,
            )

        joints, joint_errors = self._load_required_joints(_field(skeleton, "joints", {}))
        if joint_errors:
            return GestureCandidate(
                label=GestureLabel.INVALID,
                score=0.0,
                reasons=joint_errors,
                **base,
            )

        features = self._features(joints)
        rules = [
            self._left(features),
            self._right(features),
            self._ascend(features),
            self._descend(features),
            self._hover(features),
        ]
        matches = [rule for rule in rules if rule.matched]
        rule_scores = {rule.label.value: round(rule.score, 6) for rule in rules}
        if len(matches) > 1:
            return GestureCandidate(
                label=GestureLabel.UNKNOWN,
                score=0.0,
                matched_labels=[rule.label.value for rule in matches],
                reasons=["AMBIGUOUS_MATCH"],
                rule_scores=rule_scores,
                **base,
            )
        if not matches:
            nearest = max(rules, key=lambda rule: rule.score)
            failures = [f"NEAREST_RULE:{nearest.label.value}"]
            failures.extend(f"FAILED:{name}" for name in nearest.failures[:4])
            return GestureCandidate(
                label=GestureLabel.UNKNOWN,
                score=0.0,
                reasons=["NO_RULE_MATCH"] + failures,
                rule_scores=rule_scores,
                **base,
            )

        match = matches[0]
        return GestureCandidate(
            label=match.label,
            score=round(match.score, 6),
            matched_labels=[match.label.value],
            reasons=[f"RULE_MATCH:{match.label.value}"],
            rule_scores=rule_scores,
            **base,
        )

    def _load_required_joints(self, source: Any) -> Tuple[Dict[str, _Joint], List[str]]:
        result: Dict[str, _Joint] = {}
        errors: List[str] = []
        for name in REQUIRED_JOINTS:
            item = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
            if item is None or not bool(_field(item, "valid", False)):
                errors.append(f"MISSING_REQUIRED_JOINT:{name}")
                continue
            x, y = _field(item, "x", None), _field(item, "y", None)
            confidence = _field(item, "confidence", None)
            if x is None or y is None or not math.isfinite(float(x)) or not math.isfinite(float(y)):
                errors.append(f"INVALID_JOINT_GEOMETRY:{name}")
                continue
            if confidence is None or not math.isfinite(float(confidence)):
                errors.append(f"MISSING_JOINT_CONFIDENCE:{name}")
                continue
            if float(confidence) < self.config.min_joint_confidence:
                errors.append(f"LOW_JOINT_CONFIDENCE:{name}")
                continue
            result[name] = _Joint(float(x), float(y), float(confidence))
        return result, errors

    @staticmethod
    def _vector(a: _Joint, b: _Joint) -> Tuple[float, float, float]:
        dx, dy = b.x - a.x, b.y - a.y
        return dx, dy, math.hypot(dx, dy)

    @staticmethod
    def _angle(a: _Joint, b: _Joint, c: _Joint) -> float:
        v1, v2 = (a.x - b.x, a.y - b.y), (c.x - b.x, c.y - b.y)
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 <= 1e-9 or n2 <= 1e-9:
            return float("nan")
        cosine = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        return math.degrees(math.acos(cosine))

    def _features(self, joints: Dict[str, _Joint]) -> Dict[str, float]:
        ls, le, lw = joints["left_shoulder"], joints["left_elbow"], joints["left_wrist"]
        rs, re, rw = joints["right_shoulder"], joints["right_elbow"], joints["right_wrist"]
        l_upper = self._vector(ls, le)
        l_lower = self._vector(le, lw)
        r_upper = self._vector(rs, re)
        r_lower = self._vector(re, rw)
        l_full = self._vector(ls, lw)
        r_full = self._vector(rs, rw)
        return {
            "l_upper_dx": l_upper[0], "l_upper_dy": l_upper[1], "l_upper_len": l_upper[2],
            "l_lower_dx": l_lower[0], "l_lower_dy": l_lower[1], "l_lower_len": l_lower[2],
            "r_upper_dx": r_upper[0], "r_upper_dy": r_upper[1], "r_upper_len": r_upper[2],
            "r_lower_dx": r_lower[0], "r_lower_dy": r_lower[1], "r_lower_len": r_lower[2],
            "l_full_dx": l_full[0], "l_full_dy": l_full[1], "l_full_len": l_full[2],
            "r_full_dx": r_full[0], "r_full_dy": r_full[1], "r_full_len": r_full[2],
            "l_elbow": self._angle(ls, le, lw),
            "r_elbow": self._angle(rs, re, rw),
        }

    @staticmethod
    def _ge(name: str, value: float, threshold: float, margin: float = 0.5) -> _Check:
        score = _clamp01(0.5 + 0.5 * (value - threshold) / max(margin, 1e-9))
        return _Check(name, math.isfinite(value) and value >= threshold, score)

    @staticmethod
    def _le(name: str, value: float, threshold: float, margin: float = 0.5) -> _Check:
        score = _clamp01(0.5 + 0.5 * (threshold - value) / max(margin, 1e-9))
        return _Check(name, math.isfinite(value) and value <= threshold, score)

    @staticmethod
    def _between(name: str, value: float, low: float, high: float) -> _Check:
        if not math.isfinite(value):
            return _Check(name, False, 0.0)
        center, half = (low + high) / 2.0, (high - low) / 2.0
        score = _clamp01(1.0 - 0.5 * abs(value - center) / max(half, 1e-9))
        return _Check(name, low <= value <= high, score if low <= value <= high else 0.0)

    @staticmethod
    def _abs_le(name: str, value: float, threshold: float) -> _Check:
        magnitude = abs(value)
        score = _clamp01(1.0 - 0.5 * magnitude / max(threshold, 1e-9))
        return _Check(name, math.isfinite(value) and magnitude <= threshold, score)

    def _segments(self, f: Dict[str, float]) -> List[_Check]:
        return [
            self._ge(name, f[name], self.config.min_segment_length, 0.2)
            for name in ("l_upper_len", "l_lower_len", "r_upper_len", "r_lower_len")
        ]

    @staticmethod
    def _result(label: GestureLabel, checks: Iterable[_Check]) -> _RuleResult:
        values = list(checks)
        failures = tuple(check.name for check in values if not check.passed)
        score = sum(check.score for check in values) / len(values) if values else 0.0
        return _RuleResult(label, not failures, score, failures)

    def _left(self, f: Dict[str, float]) -> _RuleResult:
        c = self.config
        checks = self._segments(f) + [
            self._ge("left_outward_reach", f["l_full_dx"], c.single_arm_reach_x_min),
            self._abs_le("left_shoulder_height", f["l_full_dy"], c.horizontal_dy_max),
            self._ge("left_elbow_straight", f["l_elbow"], c.straight_elbow_min_deg, 35.0),
            self._ge("right_arm_down", f["r_full_dy"], c.opposite_down_y_min),
            self._abs_le("right_arm_low_lateral_drift", f["r_full_dx"], c.opposite_down_abs_x_max),
            self._ge("right_elbow_straight", f["r_elbow"], c.straight_elbow_min_deg, 35.0),
        ]
        return self._result(GestureLabel.LEFT, checks)

    def _right(self, f: Dict[str, float]) -> _RuleResult:
        c = self.config
        checks = self._segments(f) + [
            self._ge("right_outward_reach", -f["r_full_dx"], c.single_arm_reach_x_min),
            self._abs_le("right_shoulder_height", f["r_full_dy"], c.horizontal_dy_max),
            self._ge("right_elbow_straight", f["r_elbow"], c.straight_elbow_min_deg, 35.0),
            self._ge("left_arm_down", f["l_full_dy"], c.opposite_down_y_min),
            self._abs_le("left_arm_low_lateral_drift", f["l_full_dx"], c.opposite_down_abs_x_max),
            self._ge("left_elbow_straight", f["l_elbow"], c.straight_elbow_min_deg, 35.0),
        ]
        return self._result(GestureLabel.RIGHT, checks)

    def _ascend(self, f: Dict[str, float]) -> _RuleResult:
        c = self.config
        checks = self._segments(f) + [
            self._ge("left_wrist_above_shoulder", -f["l_full_dy"], c.ascend_up_y_min),
            self._ge("right_wrist_above_shoulder", -f["r_full_dy"], c.ascend_up_y_min),
            self._ge("left_elbow_straight", f["l_elbow"], c.straight_elbow_min_deg, 35.0),
            self._ge("right_elbow_straight", f["r_elbow"], c.straight_elbow_min_deg, 35.0),
        ]
        return self._result(GestureLabel.ASCEND, checks)

    def _descend(self, f: Dict[str, float]) -> _RuleResult:
        c = self.config
        checks = self._segments(f) + [
            self._ge("left_wrist_below_shoulder", f["l_full_dy"], c.descend_down_y_min),
            self._ge("right_wrist_below_shoulder", f["r_full_dy"], c.descend_down_y_min),
            self._ge("left_wrist_outward", f["l_full_dx"], c.descend_out_x_min),
            self._ge("right_wrist_outward", -f["r_full_dx"], c.descend_out_x_min),
            self._ge("left_elbow_straight", f["l_elbow"], c.straight_elbow_min_deg, 35.0),
            self._ge("right_elbow_straight", f["r_elbow"], c.straight_elbow_min_deg, 35.0),
        ]
        return self._result(GestureLabel.DESCEND, checks)

    def _hover(self, f: Dict[str, float]) -> _RuleResult:
        c = self.config
        checks = self._segments(f) + [
            self._ge("left_upper_arm_outward", f["l_upper_dx"], c.hover_upper_out_x_min),
            self._abs_le("left_upper_arm_horizontal", f["l_upper_dy"], c.horizontal_dy_max),
            self._ge("right_upper_arm_outward", -f["r_upper_dx"], c.hover_upper_out_x_min),
            self._abs_le("right_upper_arm_horizontal", f["r_upper_dy"], c.horizontal_dy_max),
            self._ge("left_forearm_up", -f["l_lower_dy"], c.hover_forearm_up_y_min),
            self._abs_le("left_forearm_vertical", f["l_lower_dx"], c.hover_forearm_abs_x_max),
            self._ge("right_forearm_up", -f["r_lower_dy"], c.hover_forearm_up_y_min),
            self._abs_le("right_forearm_vertical", f["r_lower_dx"], c.hover_forearm_abs_x_max),
            self._between("left_elbow_bent", f["l_elbow"], c.bent_elbow_min_deg, c.bent_elbow_max_deg),
            self._between("right_elbow_bent", f["r_elbow"], c.bent_elbow_min_deg, c.bent_elbow_max_deg),
        ]
        return self._result(GestureLabel.HOVER, checks)
