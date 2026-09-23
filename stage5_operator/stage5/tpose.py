from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def field(obj: Any, name: str, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


@dataclass(frozen=True)
class TPoseCandidateV1:
    timestamp_ms: int
    frame_id: int
    track_id: int
    matched: bool
    score: float
    reasons: tuple[str, ...]
    schema_version: str = "TPoseCandidateV1"


class TPoseRecognizer:
    """Both anatomical arms straight, horizontal, and outwards from shoulder midline."""

    def recognize(self, skeleton: Any, track_id: int) -> TPoseCandidateV1:
        now = int(field(skeleton, "timestamp_ms", 0) or 0)
        frame = int(field(skeleton, "frame_id", -1) or -1)
        if skeleton is None or not field(skeleton, "valid", False) or not field(field(skeleton, "quality"), "valid", False):
            return TPoseCandidateV1(now, frame, track_id, False, 0.0, ("POSE_INVALID",))
        joints = field(skeleton, "joints", {})
        angles = field(skeleton, "angles_deg", {})
        names = ("left_shoulder", "left_elbow", "left_wrist", "right_shoulder", "right_elbow", "right_wrist")
        values = {name: field(joints, name) for name in names}
        if any(not field(joint, "valid", False) or field(joint, "confidence", 0.0) is None or
               float(field(joint, "confidence", 0.0)) < 0.65 for joint in values.values()):
            return TPoseCandidateV1(now, frame, track_id, False, 0.0, ("LOW_ARM_CONFIDENCE",))
        try:
            finite_geometry = all(math.isfinite(float(field(joint, axis))) for joint in values.values() for axis in ("x", "y"))
        except (TypeError, ValueError, OverflowError):
            finite_geometry = False
        if not finite_geometry:
            return TPoseCandidateV1(now, frame, track_id, False, 0.0, ("NONFINITE_JOINT",))
        ls, rs = values["left_shoulder"], values["right_shoulder"]
        middle = (float(field(ls, "x")) + float(field(rs, "x"))) / 2.0
        if abs(float(field(ls, "x")) - middle) < 0.2 or abs(float(field(rs, "x")) - middle) < 0.2:
            return TPoseCandidateV1(now, frame, track_id, False, 0.0, ("SHOULDER_GEOMETRY",))
        reasons = []
        margins = []
        for side in ("left", "right"):
            shoulder, elbow, wrist = (values[f"{side}_{part}"] for part in ("shoulder", "elbow", "wrist"))
            sign = 1.0 if float(field(shoulder, "x")) > middle else -1.0
            reach = sign * (float(field(wrist, "x")) - float(field(shoulder, "x")))
            elbow_reach = sign * (float(field(elbow, "x")) - float(field(shoulder, "x")))
            horizontal = abs(float(field(wrist, "y")) - float(field(shoulder, "y")))
            elbow_horizontal = abs(float(field(elbow, "y")) - float(field(shoulder, "y")))
            angle = field(angles, f"{side}_elbow_angle")
            straight = bool(field(angle, "valid", False)) and float(field(angle, "degrees", 0.0)) >= 145.0
            if reach < 0.65 or elbow_reach < 0.25 or horizontal > 0.35 or elbow_horizontal > 0.35 or not straight:
                reasons.append(f"{side.upper()}_NOT_HORIZONTAL_STRAIGHT")
            margins.append(min(1.0, max(0.0, reach / 0.9), max(0.0, 1.0 - horizontal / 0.7)))
        matched = not reasons
        return TPoseCandidateV1(now, frame, track_id, matched, min(margins) if matched else 0.0, tuple(reasons))


class TPoseTemporal:
    def __init__(self, confirm_ms: int = 600, confirm_frames: int = 6):
        self.confirm_ms = confirm_ms
        self.confirm_frames = confirm_frames
        self.pending: dict[int, tuple[int, int, int]] = {}

    def update(self, track_id: int, timestamp_ms: int, matched: bool) -> bool:
        if not matched:
            self.pending.pop(track_id, None)
            return False
        start, count, previous = self.pending.get(track_id, (timestamp_ms, 0, timestamp_ms))
        if timestamp_ms <= previous and count:
            self.pending.pop(track_id, None)
            return False
        count += 1
        self.pending[track_id] = (start, count, timestamp_ms)
        return timestamp_ms - start >= self.confirm_ms and count >= self.confirm_frames

    def retain_only(self, visible_ids: set[int]) -> None:
        for track_id in list(self.pending):
            if track_id not in visible_ids:
                self.pending.pop(track_id, None)

    def clear(self) -> None:
        self.pending.clear()
