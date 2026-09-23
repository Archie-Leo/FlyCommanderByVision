from __future__ import annotations

import math
import statistics
from typing import Iterable, Optional

from config import QualityConfig
from .types import JointObservation, PersonPose, PoseQuality


TORSO = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
ARMS = ("left_elbow", "right_elbow", "left_wrist", "right_wrist")
KEY_JOINTS = TORSO + ARMS


def _reliable(joint: Optional[JointObservation], threshold: float) -> bool:
    return bool(
        joint
        and joint.valid
        and joint.confidence is not None
        and math.isfinite(joint.confidence)
        and joint.confidence >= threshold
        and joint.x_px is not None
        and joint.y_px is not None
        and math.isfinite(joint.x_px)
        and math.isfinite(joint.y_px)
    )


class PoseQualityEvaluator:
    def __init__(self, config: QualityConfig):
        self.config = config

    def evaluate(self, pose: Optional[PersonPose]) -> PoseQuality:
        if pose is None:
            return PoseQuality(False, 0.0, ["NO_POSE"], 0.0, 0.0, [], self._summary([]))

        reasons = []
        threshold = self.config.joint_confidence_min
        reliable = {name: _reliable(pose.joints.get(name), threshold) for name in KEY_JOINTS}
        coverage = sum(reliable.values()) / len(KEY_JOINTS)

        if not reliable["left_shoulder"] or not reliable["right_shoulder"]:
            reasons.append("MISSING_SHOULDERS")
        if not reliable["left_hip"] or not reliable["right_hip"]:
            reasons.append("MISSING_HIPS")
        if self.config.require_both_arms and not all(reliable[name] for name in ARMS):
            reasons.append("MISSING_ARMS")
            for name in ARMS:
                if not reliable[name]:
                    reasons.append(f"MISSING_{name.upper()}")
        if coverage < self.config.min_key_joint_coverage:
            reasons.append("LOW_CONFIDENCE")

        x1, y1, x2, y2 = pose.bbox_xyxy
        finite_bbox = all(math.isfinite(v) for v in (x1, y1, x2, y2)) and x2 > x1 and y2 > y1
        if not finite_bbox:
            reasons.append("INVALID_GEOMETRY")
            bbox_area_ratio = 0.0
            bbox_height_ratio = 0.0
        else:
            cx1, cy1 = max(0.0, x1), max(0.0, y1)
            cx2, cy2 = min(float(pose.image_width), x2), min(float(pose.image_height), y2)
            bbox_area_ratio = max(0.0, cx2 - cx1) * max(0.0, cy2 - cy1) / (
                pose.image_width * pose.image_height
            )
            bbox_height_ratio = max(0.0, cy2 - cy1) / pose.image_height
            if (
                bbox_height_ratio < self.config.min_bbox_height_ratio
                or bbox_area_ratio < self.config.min_bbox_area_ratio
            ):
                reasons.append("TOO_SMALL")

        truncation_flags = self._truncation_flags(pose)
        out_count = sum(
            1
            for name in KEY_JOINTS
            if self._outside(pose.joints.get(name), pose.image_width, pose.image_height)
        )
        if out_count > self.config.max_out_of_frame_key_joints:
            reasons.append("HEAVY_TRUNCATION")

        confidences = [
            pose.joints[name].confidence
            for name in KEY_JOINTS
            if pose.joints.get(name) and pose.joints[name].confidence is not None
            and math.isfinite(pose.joints[name].confidence)
        ]
        summary = self._summary(confidences)
        median_conf = summary["median"] or 0.0
        score = max(0.0, min(1.0, 0.6 * coverage + 0.4 * median_conf))
        reasons = list(dict.fromkeys(reasons))
        return PoseQuality(
            valid=not reasons,
            score=score,
            reasons=reasons,
            key_joint_coverage=coverage,
            bbox_coverage=bbox_area_ratio,
            truncation_flags=truncation_flags,
            confidence_summary=summary,
        )

    @staticmethod
    def _outside(joint, width, height):
        return bool(
            joint
            and joint.x_px is not None
            and joint.y_px is not None
            and (joint.x_px < 0 or joint.x_px >= width or joint.y_px < 0 or joint.y_px >= height)
        )

    @staticmethod
    def _truncation_flags(pose):
        flags = []
        for name, joint in pose.joints.items():
            if joint.x_px is None or joint.y_px is None:
                continue
            if joint.x_px < 0:
                flags.append(f"{name}:LEFT")
            if joint.x_px >= pose.image_width:
                flags.append(f"{name}:RIGHT")
            if joint.y_px < 0:
                flags.append(f"{name}:TOP")
            if joint.y_px >= pose.image_height:
                flags.append(f"{name}:BOTTOM")
        return flags

    @staticmethod
    def _summary(values: Iterable[float]):
        values = list(values)
        if not values:
            return {"min": None, "median": None, "mean": None}
        return {
            "min": min(values),
            "median": statistics.median(values),
            "mean": statistics.fmean(values),
        }

