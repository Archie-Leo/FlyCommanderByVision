from __future__ import annotations

import json
import math
import unittest
from types import SimpleNamespace

from stage5.appearance import OperatorReIDGallery
from stage5.ownership import OperatorOwnershipManager, OwnershipState
from stage5.tpose import TPoseRecognizer
from stage5.tracking import KalmanIoUTwoPassTracker
from stage5.types import DetectionV1, TrackedPersonV1


def skeleton(tpose=True, timestamp_ms=0, frame_id=0):
    def joint(x, y):
        return {"x": x, "y": y, "confidence": 0.99, "valid": True}
    wrist_y = -1.0 if tpose else -0.2
    return {
        "timestamp_ms": timestamp_ms, "frame_id": frame_id, "schema_version": "NormalizedSkeletonV1",
        "valid": True, "quality": {"valid": True, "score": 0.99},
        "joints": {
            "left_shoulder": joint(0.35, -1.0), "right_shoulder": joint(-0.35, -1.0),
            "left_elbow": joint(0.8, -1.0 if tpose else -0.6),
            "right_elbow": joint(-0.8, -1.0 if tpose else -0.6),
            "left_wrist": joint(1.3, wrist_y), "right_wrist": joint(-1.3, wrist_y),
        },
        "angles_deg": {"left_elbow_angle": {"valid": True, "degrees": 175.0},
                       "right_elbow_angle": {"valid": True, "degrees": 175.0}},
    }


def person(tid=3, timestamp_ms=0, frame_id=0, embedding=(1.0, 0.0, 0.0),
           bbox=(100.0, 100.0, 300.0, 500.0), tpose=True, valid=True):
    return TrackedPersonV1(
        timestamp_ms=timestamp_ms, frame_id=frame_id, track_id=tid, bbox_xyxy=bbox,
        pose=None, pose_quality=SimpleNamespace(valid=valid),
        skeleton=skeleton(tpose, timestamp_ms, frame_id), tracker_confidence=0.99,
        age_frames=10, lost_frames=0, embedding=embedding, crop_quality=0.95,
    )


def gesture(label="RIGHT", stable=True, score=0.9):
    return SimpleNamespace(label=label, stable=stable, score=score)


def locked_manager():
    manager = OperatorOwnershipManager()
    for index in range(7):
        now = index * 100
        manager.update([person(timestamp_ms=now, frame_id=index)], now, index)
    assert manager.state == OwnershipState.LOCKED_HIGH
    return manager


class Stage5Tests(unittest.TestCase):
    def test_single_person_tpose_acquires_unique_session(self):
        manager = locked_manager()
        self.assertNotEqual(manager.session.operator_session_id, str(manager.session.current_track_id))
        self.assertEqual(manager.session.current_track_id, 3)

    def test_no_tpose_no_operator(self):
        manager = OperatorOwnershipManager()
        for index in range(8):
            now = index * 100
            manager.update([person(timestamp_ms=now, frame_id=index, tpose=False)], now, index)
        self.assertEqual(manager.state, OwnershipState.WAIT_OPERATOR)
        self.assertIsNone(manager.session)

    def test_single_tpose_frame_cannot_authorize(self):
        manager = OperatorOwnershipManager()
        manager.update([person()], 0, 0)
        self.assertEqual(manager.state, OwnershipState.ACQUIRING)
        self.assertFalse(manager.authorize(3, gesture(), 0, 0).valid)

    def test_repeated_tpose_does_not_create_session(self):
        manager = locked_manager()
        original = manager.session.operator_session_id
        for index in range(7, 11):
            now = index * 100
            manager.update([person(timestamp_ms=now, frame_id=index)], now, index)
        self.assertEqual(manager.session.operator_session_id, original)

    def test_bystander_gesture_rejected_operator_gesture_accepted(self):
        manager = locked_manager()
        a = person(timestamp_ms=700, frame_id=7)
        b = person(tid=4, timestamp_ms=700, frame_id=7, embedding=(0, 1, 0),
                   bbox=(600, 100, 800, 500))
        manager.update([a, b], 700, 7)
        self.assertFalse(manager.authorize(4, gesture("RIGHT"), 700, 7).valid)
        self.assertIn("BYSTANDER", manager.authorize(4, gesture("ASCEND"), 700, 7).reject_reasons)
        self.assertTrue(manager.authorize(3, gesture("RIGHT"), 700, 7).valid)
        self.assertTrue(manager.authorize(3, gesture("ASCEND"), 700, 7).valid)

    def test_pose_invalid_rejects(self):
        manager = locked_manager()
        manager.update([person(timestamp_ms=700, frame_id=7, valid=False)], 700, 7)
        self.assertFalse(manager.authorize(3, gesture(), 700, 7).valid)

    def test_unknown_and_invalid_gestures_reject(self):
        manager = locked_manager()
        manager.update([person(timestamp_ms=700, frame_id=7)], 700, 7)
        self.assertFalse(manager.authorize(3, gesture("UNKNOWN"), 700, 7).valid)
        self.assertFalse(manager.authorize(3, gesture("INVALID"), 700, 7).valid)
        self.assertFalse(manager.authorize(3, gesture("RIGHT", stable=False), 700, 7).valid)

    def test_operator_lost_immediately_clears_authorization(self):
        manager = locked_manager()
        manager.update([], 700, 7)
        output = manager.authorize(3, gesture(), 700, 7)
        self.assertEqual(manager.state, OwnershipState.LOST)
        self.assertFalse(output.valid)
        self.assertEqual(output.gesture, "UNKNOWN")

    def test_bystander_remaining_cannot_become_operator(self):
        manager = locked_manager()
        b = person(tid=4, timestamp_ms=700, frame_id=7, embedding=(0, 1, 0),
                   bbox=(600, 100, 800, 500))
        manager.update([b], 700, 7)
        self.assertFalse(manager.authorize(4, gesture(), 700, 7).valid)
        self.assertEqual(manager.session.current_track_id, 3)

    def test_candidate_ambiguity_rejects(self):
        manager = locked_manager()
        a = person(timestamp_ms=700, frame_id=7)
        b = person(tid=4, timestamp_ms=700, frame_id=7)
        manager.update([a, b], 700, 7)
        self.assertEqual(manager.state, OwnershipState.AMBIGUOUS)
        self.assertFalse(manager.authorize(3, gesture(), 700, 7).valid)

    def test_track_id_change_requires_reacquire_and_preserves_session(self):
        manager = locked_manager()
        session_id = manager.session.operator_session_id
        manager.update([person(tid=7, timestamp_ms=700, frame_id=7)], 700, 7)
        self.assertEqual(manager.state, OwnershipState.REACQUIRING)
        self.assertFalse(manager.authorize(7, gesture(), 700, 7).valid)
        for index in range(8, 13):
            now = index * 100
            manager.update([person(tid=7, timestamp_ms=now, frame_id=index)], now, index)
        self.assertEqual(manager.state, OwnershipState.LOCKED_HIGH)
        self.assertEqual(manager.session.operator_session_id, session_id)
        self.assertEqual(manager.session.current_track_id, 7)

    def test_new_person_inheriting_old_track_id_does_not_inherit_authority(self):
        manager = locked_manager()
        manager.update([person(tid=3, timestamp_ms=700, frame_id=7, embedding=(0, 1, 0))], 700, 7)
        self.assertFalse(manager.authorize(3, gesture(), 700, 7).valid)
        self.assertEqual(manager.state, OwnershipState.LOST)

    def test_timeout_requires_reacquire(self):
        manager = locked_manager()
        manager.update([person(timestamp_ms=1100, frame_id=7)], 1100, 7)
        self.assertEqual(manager.state, OwnershipState.REACQUIRING)
        self.assertFalse(manager.authorize(3, gesture(), 1100, 7).valid)

    def test_multiple_initial_tposes_ambiguous(self):
        manager = OperatorOwnershipManager()
        for index in range(7):
            now = index * 100
            manager.update([person(timestamp_ms=now, frame_id=index),
                            person(tid=4, timestamp_ms=now, frame_id=index,
                                   bbox=(600, 100, 800, 500), embedding=(0, 1, 0))], now, index)
        self.assertEqual(manager.state, OwnershipState.AMBIGUOUS)
        self.assertIsNone(manager.session)

    def test_gallery_not_polluted_before_or_without_unique_lock(self):
        gallery = OperatorReIDGallery()
        self.assertFalse(gallery.update((1, 0), locked_high=False, crop_quality=1.0, unique_candidate=True))
        self.assertTrue(gallery.update((1, 0), locked_high=True, crop_quality=1.0, unique_candidate=True))
        self.assertFalse(gallery.update((0, 1), locked_high=True, crop_quality=1.0, unique_candidate=False))
        self.assertEqual(len(gallery.entries), 1)

    def test_tpose_geometry_not_single_arm(self):
        recognizer = TPoseRecognizer()
        self.assertTrue(recognizer.recognize(skeleton(), 3).matched)
        from gesture import GeometryGestureRecognizer
        self.assertEqual(GeometryGestureRecognizer().recognize(skeleton()).label.value, "UNKNOWN")
        single = skeleton()
        single["joints"]["right_wrist"]["x"] = -0.5
        self.assertFalse(recognizer.recognize(single, 3).matched)

    def test_tpose_rejects_malformed_geometry(self):
        pose = skeleton()
        pose["joints"]["left_wrist"]["x"] = None
        self.assertFalse(TPoseRecognizer().recognize(pose, 3).matched)

    def test_serialization_rejects_nan_and_does_not_emit_nan(self):
        manager = locked_manager()
        manager.update([person(timestamp_ms=700, frame_id=7)], 700, 7)
        output = manager.authorize(3, gesture(score=float("nan")), 700, 7)
        self.assertFalse(output.valid)
        json.dumps(output.to_dict(), allow_nan=False)

    def test_tracker_produces_ephemeral_ids_not_authority(self):
        tracker = KalmanIoUTwoPassTracker()
        detections = [DetectionV1(0, 0, (100, 100, 300, 500), None, SimpleNamespace(valid=True),
                                  skeleton(), 0.9, (1, 0, 0), 0.9)]
        one = tracker.update(detections, 0)
        self.assertEqual(len(one), 1)
        detections[0].timestamp_ms = 100
        detections[0].frame_id = 1
        two = tracker.update(detections, 100)
        self.assertEqual(two[0].track_id, one[0].track_id)
        self.assertEqual(two[0].schema_version, "TrackedPersonV1")


if __name__ == "__main__":
    unittest.main()
