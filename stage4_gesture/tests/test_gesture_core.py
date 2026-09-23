from __future__ import annotations

import math
import json
import tempfile
import unittest
from pathlib import Path

from gesture import GeometryGestureRecognizer, GestureLabel, TemporalConfig, TemporalStabilizer
from gesture.evaluation import classification_metrics
from relabel_sequences import relabel


def joint(x, y, confidence=0.99, valid=True):
    return {"name": "", "x": x, "y": y, "confidence": confidence, "valid": valid, "derived": False}


def skeleton(label="UNKNOWN", timestamp_ms=0):
    ls, rs = (0.45, -1.2), (-0.45, -1.2)
    poses = {
        "UNKNOWN": {
            "left_elbow": (0.47, -0.55), "left_wrist": (0.50, 0.10),
            "right_elbow": (-0.47, -0.55), "right_wrist": (-0.50, 0.10),
        },
        "LEFT": {
            "left_elbow": (1.10, -1.2), "left_wrist": (1.75, -1.2),
            "right_elbow": (-0.47, -0.55), "right_wrist": (-0.50, 0.10),
        },
        "RIGHT": {
            "left_elbow": (0.47, -0.55), "left_wrist": (0.50, 0.10),
            "right_elbow": (-1.10, -1.2), "right_wrist": (-1.75, -1.2),
        },
        "ASCEND": {
            "left_elbow": (0.55, -1.90), "left_wrist": (0.65, -2.60),
            "right_elbow": (-0.55, -1.90), "right_wrist": (-0.65, -2.60),
        },
        "DESCEND": {
            "left_elbow": (0.80, -0.55), "left_wrist": (1.15, 0.10),
            "right_elbow": (-0.80, -0.55), "right_wrist": (-1.15, 0.10),
        },
        "HOVER": {
            "left_elbow": (1.10, -1.20), "left_wrist": (1.10, -1.85),
            "right_elbow": (-1.10, -1.20), "right_wrist": (-1.10, -1.85),
        },
        "T_POSE": {
            "left_elbow": (1.10, -1.20), "left_wrist": (1.75, -1.20),
            "right_elbow": (-1.10, -1.20), "right_wrist": (-1.75, -1.20),
        },
    }[label]
    joints = {
        "left_shoulder": joint(*ls),
        "right_shoulder": joint(*rs),
        **{name: joint(*xy) for name, xy in poses.items()},
    }
    return {
        "timestamp_ms": timestamp_ms,
        "frame_id": timestamp_ms,
        "local_detection_id": 0,
        "joints": joints,
        "quality": {"valid": True, "score": 0.99, "reasons": []},
        "valid": True,
        "invalid_reasons": [],
        "schema_version": "NormalizedSkeletonV1",
    }


class GestureCoreTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = GeometryGestureRecognizer()

    def test_five_legal_gestures(self):
        for name in ("LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER"):
            with self.subTest(name=name):
                result = self.recognizer.recognize(skeleton(name))
                self.assertEqual(result.label, GestureLabel(name))
                self.assertGreaterEqual(result.score, 0.5)

    def test_natural_arms_down_is_unknown(self):
        self.assertEqual(self.recognizer.recognize(skeleton("UNKNOWN")).label, GestureLabel.UNKNOWN)

    def test_borderline_natural_down_does_not_trigger_descend(self):
        value = skeleton("UNKNOWN")
        value["joints"].update({
            "left_elbow": joint(0.63, -0.65),
            "left_wrist": joint(0.81, -0.10),
            "right_elbow": joint(-0.63, -0.65),
            "right_wrist": joint(-0.81, -0.10),
        })
        result = self.recognizer.recognize(value)
        self.assertEqual(result.label, GestureLabel.UNKNOWN)
        self.assertIn("FAILED:left_wrist_outward", result.reasons)

    def test_t_pose_is_reserved_unknown(self):
        result = self.recognizer.recognize(skeleton("T_POSE"))
        self.assertEqual(result.label, GestureLabel.UNKNOWN)
        self.assertNotIn("LEFT", result.matched_labels)
        self.assertNotIn("RIGHT", result.matched_labels)

    def test_anatomical_left_right_are_distinct(self):
        left = self.recognizer.recognize(skeleton("LEFT"))
        right = self.recognizer.recognize(skeleton("RIGHT"))
        self.assertEqual(left.label, GestureLabel.LEFT)
        self.assertEqual(right.label, GestureLabel.RIGHT)

    def test_invalid_source_is_rejected(self):
        value = skeleton("LEFT")
        value["valid"] = False
        value["quality"]["valid"] = False
        value["invalid_reasons"] = ["HEAVY_TRUNCATION"]
        result = self.recognizer.recognize(value)
        self.assertEqual(result.label, GestureLabel.INVALID)
        self.assertIn("HEAVY_TRUNCATION", result.reasons)

    def test_missing_skeleton_is_rejected(self):
        result = self.recognizer.recognize(None)
        self.assertEqual(result.label, GestureLabel.INVALID)
        self.assertIn("NO_NORMALIZED_SKELETON", result.reasons)

    def test_low_confidence_required_joint_is_invalid(self):
        value = skeleton("LEFT")
        value["joints"]["left_wrist"]["confidence"] = 0.2
        result = self.recognizer.recognize(value)
        self.assertEqual(result.label, GestureLabel.INVALID)
        self.assertIn("LOW_JOINT_CONFIDENCE:left_wrist", result.reasons)

    def test_non_finite_joint_is_invalid(self):
        value = skeleton("LEFT")
        value["joints"]["left_wrist"]["x"] = math.nan
        self.assertEqual(self.recognizer.recognize(value).label, GestureLabel.INVALID)

    def test_temporal_confirmation_and_release(self):
        stabilizer = TemporalStabilizer(TemporalConfig(confirm_ms=300, min_confirm_frames=4, release_ms=100, min_release_frames=2))
        for timestamp in (0, 100, 200):
            raw = self.recognizer.recognize(skeleton("LEFT", timestamp))
            self.assertFalse(stabilizer.update(raw).stable)
        confirmed = stabilizer.update(self.recognizer.recognize(skeleton("LEFT", 300)))
        self.assertTrue(confirmed.stable)
        self.assertEqual(confirmed.label, GestureLabel.LEFT)
        grace = stabilizer.update(self.recognizer.recognize(skeleton("UNKNOWN", 350)))
        self.assertTrue(grace.stable)
        released = stabilizer.update(self.recognizer.recognize(skeleton("UNKNOWN", 450)))
        self.assertFalse(released.stable)
        self.assertEqual(released.label, GestureLabel.UNKNOWN)

    def test_invalid_clears_temporal_state_immediately(self):
        stabilizer = TemporalStabilizer(TemporalConfig(confirm_ms=0, min_confirm_frames=1))
        stable = stabilizer.update(self.recognizer.recognize(skeleton("ASCEND", 10)))
        self.assertTrue(stable.stable)
        bad = skeleton("ASCEND", 20)
        bad["quality"]["valid"] = False
        bad["valid"] = False
        output = stabilizer.update(self.recognizer.recognize(bad))
        self.assertEqual(output.label, GestureLabel.INVALID)
        self.assertFalse(output.stable)

    def test_non_monotonic_timestamp_rejected(self):
        stabilizer = TemporalStabilizer()
        stabilizer.update(self.recognizer.recognize(skeleton("LEFT", 100)))
        result = stabilizer.update(self.recognizer.recognize(skeleton("LEFT", 99)))
        self.assertEqual(result.label, GestureLabel.INVALID)
        self.assertIn("NON_MONOTONIC_TIMESTAMP", result.reasons)

    def test_metrics_count_false_triggers_and_rejection(self):
        metrics = classification_metrics(
            ["LEFT", "RIGHT", "UNKNOWN", "INVALID"],
            ["LEFT", "UNKNOWN", "ASCEND", "INVALID"],
        )
        self.assertEqual(metrics["false_trigger_count"], 1)
        self.assertEqual(metrics["legal_action_rejection_rate"], 0.5)
        self.assertEqual(metrics["per_gesture"]["LEFT"]["precision"], 1.0)

    def test_relabel_is_auditable_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sequence_0017_HOVER.jsonl"
            source.write_text(
                "".join(json.dumps({"ground_truth_label": "HOVER", "frame_id": i}) + "\n" for i in range(3)),
                encoding="utf-8",
            )
            preview = relabel(source, "UNKNOWN", apply=False)
            self.assertEqual(preview["status"], "PREVIEW")
            self.assertTrue(source.exists())
            result = relabel(source, "UNKNOWN", apply=True)
            target = Path(result["target_path"])
            self.assertTrue(target.exists())
            self.assertTrue(Path(result["backup_path"]).exists())
            labels = {json.loads(line)["ground_truth_label"] for line in target.read_text().splitlines()}
            self.assertEqual(labels, {"UNKNOWN"})
            self.assertTrue((Path(directory) / "relabel_audit.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
