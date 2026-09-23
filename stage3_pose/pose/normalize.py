from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from config import NormalizationConfig
from .types import (
    AngleFeature,
    BoneFeature,
    JointObservation,
    NormalizedJoint,
    NormalizedSkeleton,
    PersonPose,
    PoseQuality,
)


BONES = {
    "left_upper_arm": ("left_shoulder", "left_elbow"),
    "left_lower_arm": ("left_elbow", "left_wrist"),
    "right_upper_arm": ("right_shoulder", "right_elbow"),
    "right_lower_arm": ("right_elbow", "right_wrist"),
    "shoulder_line": ("left_shoulder", "right_shoulder"),
    "hip_line": ("left_hip", "right_hip"),
    "left_torso": ("left_shoulder", "left_hip"),
    "right_torso": ("right_shoulder", "right_hip"),
}


def _point(joint: Optional[JointObservation]) -> Optional[Tuple[float, float]]:
    if not joint or not joint.valid or joint.x_px is None or joint.y_px is None:
        return None
    if not math.isfinite(joint.x_px) or not math.isfinite(joint.y_px):
        return None
    return float(joint.x_px), float(joint.y_px)


def _midpoint(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


class SkeletonNormalizer:
    def __init__(self, config: NormalizationConfig):
        self.config = config

    def normalize(self, pose: PersonPose, quality: PoseQuality) -> NormalizedSkeleton:
        base = dict(
            timestamp_ms=pose.timestamp_ms,
            frame_id=pose.frame_id,
            local_detection_id=pose.local_detection_id,
            quality=quality,
            source_pose_score=pose.pose_score,
            image_size=(pose.image_width, pose.image_height),
        )
        if not quality.valid:
            return NormalizedSkeleton(
                body_center_px=None, body_scale_px=None, joints={}, bones={}, angles_deg={},
                valid=False, invalid_reasons=list(quality.reasons), **base
            )

        ls = _point(pose.joints.get("left_shoulder"))
        rs = _point(pose.joints.get("right_shoulder"))
        lh = _point(pose.joints.get("left_hip"))
        rh = _point(pose.joints.get("right_hip"))
        if None in (ls, rs, lh, rh):
            return NormalizedSkeleton(
                body_center_px=None, body_scale_px=None, joints={}, bones={}, angles_deg={},
                valid=False, invalid_reasons=["MISSING_NORMALIZATION_ANCHORS"], **base
            )

        neck = _midpoint(ls, rs)
        pelvis = _midpoint(lh, rh)
        shoulder_width = _distance(ls, rs)
        torso_length = _distance(neck, pelvis)
        scale = (shoulder_width + torso_length) / 2.0
        if not math.isfinite(scale) or scale < self.config.min_scale_px:
            return NormalizedSkeleton(
                body_center_px=pelvis, body_scale_px=None, joints={}, bones={}, angles_deg={},
                valid=False, invalid_reasons=["INVALID_BODY_SCALE"], **base
            )

        source = dict(pose.joints)
        source["neck"] = self._derived_joint("neck", neck, pose)
        source["pelvis"] = self._derived_joint("pelvis", pelvis, pose)
        joints: Dict[str, NormalizedJoint] = {}
        for name, joint in source.items():
            point = _point(joint)
            if point is None:
                joints[name] = NormalizedJoint(name, None, None, joint.confidence, False, joint.derived)
            else:
                joints[name] = NormalizedJoint(
                    name=name,
                    x=(point[0] - pelvis[0]) / scale,
                    y=(point[1] - pelvis[1]) / scale,
                    confidence=joint.confidence,
                    valid=True,
                    derived=joint.derived,
                )

        bones = {name: self._bone(name, start, end, joints) for name, (start, end) in BONES.items()}
        angles = {
            "left_elbow_angle": self._three_point_angle(
                "left_elbow_angle", joints, "left_shoulder", "left_elbow", "left_wrist"
            ),
            "right_elbow_angle": self._three_point_angle(
                "right_elbow_angle", joints, "right_shoulder", "right_elbow", "right_wrist"
            ),
            "left_upper_arm_image_angle": self._vector_angle(
                "left_upper_arm_image_angle", joints, "left_shoulder", "left_elbow"
            ),
            "right_upper_arm_image_angle": self._vector_angle(
                "right_upper_arm_image_angle", joints, "right_shoulder", "right_elbow"
            ),
        }
        return NormalizedSkeleton(
            body_center_px=pelvis,
            body_scale_px=scale,
            joints=joints,
            bones=bones,
            angles_deg=angles,
            valid=True,
            invalid_reasons=[],
            **base,
        )

    @staticmethod
    def _derived_joint(name, point, pose):
        x, y = point
        return JointObservation(
            name=name, x_px=x, y_px=y, x_norm_image=x / pose.image_width,
            y_norm_image=y / pose.image_height, confidence=None, valid=True, derived=True
        )

    @staticmethod
    def _bone(name, start_name, end_name, joints):
        start, end = joints.get(start_name), joints.get(end_name)
        if not start or not end or not start.valid or not end.valid:
            return BoneFeature(name, start_name, end_name, None, None, None, False)
        dx, dy = end.x - start.x, end.y - start.y
        return BoneFeature(name, start_name, end_name, dx, dy, math.hypot(dx, dy), True)

    def _three_point_angle(self, name, joints, a_name, vertex_name, c_name):
        a, b, c = joints.get(a_name), joints.get(vertex_name), joints.get(c_name)
        if not a or not b or not c or not (a.valid and b.valid and c.valid):
            return AngleFeature(name, None, False)
        v1 = (a.x - b.x, a.y - b.y)
        v2 = (c.x - b.x, c.y - b.y)
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < self.config.epsilon or n2 < self.config.epsilon:
            return AngleFeature(name, None, False)
        cosine = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        return AngleFeature(name, math.degrees(math.acos(cosine)), True)

    def _vector_angle(self, name, joints, start_name, end_name):
        start, end = joints.get(start_name), joints.get(end_name)
        if not start or not end or not start.valid or not end.valid:
            return AngleFeature(name, None, False)
        dx, dy = end.x - start.x, end.y - start.y
        if math.hypot(dx, dy) < self.config.epsilon:
            return AngleFeature(name, None, False)
        return AngleFeature(name, math.degrees(math.atan2(dy, dx)), True)

