import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cv2

from camera.rectification import LeftRectifier
from config import NormalizationConfig, QualityConfig
from countdown import CaptureCountdown
from pose.mediapipe_backend import MEDIAPIPE_TO_CANONICAL, canonical_confidence
from pose.normalize import SkeletonNormalizer
from pose.quality import PoseQualityEvaluator
from pose.types import JointObservation, PersonPose


BASE = {
    "nose": (500, 150),
    "left_shoulder": (430, 300), "right_shoulder": (570, 300),
    "left_elbow": (390, 410), "right_elbow": (620, 390),
    "left_wrist": (330, 470), "right_wrist": (690, 420),
    "left_hip": (455, 530), "right_hip": (545, 530),
    "left_knee": (455, 700), "right_knee": (545, 700),
    "left_ankle": (450, 880), "right_ankle": (550, 880),
}


def make_pose(dx=0.0, dy=0.0, scale=1.0, low=None):
    joints = {}
    for name, (x, y) in BASE.items():
        px, py = x * scale + dx, y * scale + dy
        confidence = 0.2 if name == low else 0.95
        joints[name] = JointObservation(
            name, px, py, px / 1280, py / 960, confidence,
            visibility=confidence, presence=confidence, valid=True
        )
    xs = [j.x_px for j in joints.values()]
    ys = [j.y_px for j in joints.values()]
    return PersonPose(1, 1, 0, 1280, 960, (min(xs), min(ys), max(xs), max(ys)), 0.95, "synthetic", joints)


class PoseCoreTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = PoseQualityEvaluator(QualityConfig())
        self.normalizer = SkeletonNormalizer(NormalizationConfig())

    def normalized(self, pose):
        quality = self.evaluator.evaluate(pose)
        self.assertTrue(quality.valid, quality.reasons)
        result = self.normalizer.normalize(pose, quality)
        self.assertTrue(result.valid, result.invalid_reasons)
        return result

    def assert_same_joints(self, a, b, places=8):
        for name in BASE:
            self.assertAlmostEqual(a.joints[name].x, b.joints[name].x, places=places)
            self.assertAlmostEqual(a.joints[name].y, b.joints[name].y, places=places)

    def test_translation_invariance(self):
        self.assert_same_joints(self.normalized(make_pose()), self.normalized(make_pose(dx=210, dy=-80)))

    def test_scale_invariance(self):
        self.assert_same_joints(self.normalized(make_pose()), self.normalized(make_pose(scale=1.5)))

    def test_translation_and_scale_invariance(self):
        self.assert_same_joints(self.normalized(make_pose()), self.normalized(make_pose(dx=100, dy=50, scale=0.75)))

    def test_anatomical_left_right_are_not_collapsed(self):
        original_pose = make_pose()
        swapped_pose = make_pose()
        for left, right in (("left_shoulder", "right_shoulder"),
                            ("left_elbow", "right_elbow"), ("left_wrist", "right_wrist")):
            swapped_pose.joints[left], swapped_pose.joints[right] = (
                replace(swapped_pose.joints[right], name=left),
                replace(swapped_pose.joints[left], name=right),
            )
        original = self.normalized(original_pose)
        swapped = self.normalized(swapped_pose)
        self.assertNotAlmostEqual(original.joints["left_wrist"].x, original.joints["right_wrist"].x)
        self.assertNotAlmostEqual(original.joints["left_wrist"].x, swapped.joints["left_wrist"].x)
        self.assertEqual(MEDIAPIPE_TO_CANONICAL["left_shoulder"], 11)
        self.assertEqual(MEDIAPIPE_TO_CANONICAL["right_shoulder"], 12)

    def test_zero_scale_is_invalid_without_nan(self):
        pose = make_pose()
        for name in ("left_shoulder", "right_shoulder", "left_hip", "right_hip"):
            pose.joints[name].x_px = 500
            pose.joints[name].y_px = 500
        quality = self.evaluator.evaluate(pose)
        result = self.normalizer.normalize(pose, quality)
        self.assertFalse(result.valid)
        self.assertIn("INVALID_BODY_SCALE", result.invalid_reasons)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_missing_low_confidence_joint_rejected(self):
        quality = self.evaluator.evaluate(make_pose(low="right_wrist"))
        self.assertFalse(quality.valid)
        self.assertIn("MISSING_RIGHT_WRIST", quality.reasons)

    def test_no_pose_rejected(self):
        self.assertEqual(self.evaluator.evaluate(None).reasons, ["NO_POSE"])

    def test_bones_and_angles_are_geometry(self):
        result = self.normalized(make_pose())
        self.assertTrue(result.bones["left_upper_arm"].valid)
        self.assertGreater(result.bones["left_upper_arm"].length, 0)
        self.assertTrue(result.angles_deg["left_elbow_angle"].valid)
        self.assertGreaterEqual(result.angles_deg["left_elbow_angle"].degrees, 0)
        self.assertLessEqual(result.angles_deg["left_elbow_angle"].degrees, 180)

    def test_confidence_rule(self):
        self.assertEqual(canonical_confidence(0.8, 0.6), 0.6)
        self.assertEqual(canonical_confidence(0.8, None), 0.8)
        self.assertIsNone(canonical_confidence(None, None))

    def test_serialization(self):
        result = self.normalized(make_pose())
        payload = json.loads(json.dumps(result.to_dict(), allow_nan=False))
        self.assertEqual(payload["schema_version"], "NormalizedSkeletonV1")
        self.assertEqual(payload["joints"]["pelvis"]["derived"], True)

    def test_calibration_loader_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.yaml"
            fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_WRITE | cv2.FILE_STORAGE_FORMAT_YAML)
            fs.write("image_width", 1280)
            fs.write("image_height", 960)
            import numpy as np
            fs.write("K_left", np.array([[650., 0., 640.], [0., 650., 480.], [0., 0., 1.]]))
            fs.write("D_left", np.zeros((1, 5)))
            fs.write("R1", np.eye(3))
            fs.write("P1", np.array([[650., 0., 640., 0.], [0., 650., 480., 0.], [0., 0., 1., 0.]]))
            fs.release()
            rectifier = LeftRectifier(path)
            self.assertEqual((rectifier.width, rectifier.height), (1280, 960))

    def test_three_second_capture_countdown_is_non_blocking_and_one_shot(self):
        timer = CaptureCountdown(3.0)
        self.assertTrue(timer.start(10_000_000_000))
        self.assertFalse(timer.start(10_000_000_001))
        self.assertEqual(timer.update(10_000_000_000).display_seconds, 3)
        self.assertEqual(timer.update(11_100_000_000).display_seconds, 2)
        self.assertEqual(timer.update(12_100_000_000).display_seconds, 1)
        due = timer.update(13_000_000_000)
        self.assertTrue(due.capture_due)
        self.assertIsNone(due.display_seconds)
        self.assertFalse(timer.update(13_000_000_001).capture_due)


if __name__ == "__main__":
    unittest.main()
