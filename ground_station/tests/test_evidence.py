from __future__ import annotations

import unittest

from ground_station.evidence import competition_state
from ground_station.protocol import empty_snapshot


class EvidenceMappingTests(unittest.TestCase):
    def packet(self):
        packet = empty_snapshot()
        packet["ai_age_ms"] = 40
        return packet

    def test_wait_operator_and_px4_placeholder(self):
        state = competition_state(self.packet(), 10)
        self.assertEqual(state["perception"][0], "NO PERSON")
        self.assertEqual(state["authorization"][:2], ("WAITING", "WAIT_OPERATOR"))
        self.assertEqual(state["gesture"][0], "--")
        self.assertEqual(state["safety"][0], "SAFE HOVER")
        self.assertEqual(state["px4"][0], "DISCONNECTED")

    def test_candidate_does_not_inherit_old_gesture(self):
        packet = self.packet()
        packet["people_count"] = 1
        packet["operator"].update(kind="candidate", track_id=5, bbox=[1, 2, 3, 4])
        packet["gesture"]["stable"] = "RIGHT"
        state = competition_state(packet, 10)
        self.assertEqual(state["perception"][0], "CANDIDATE #5")
        self.assertEqual(state["authorization"][0], "WAITING")
        self.assertEqual(state["gesture"][0], "--")

    def test_locked_right_uses_stage6_decision(self):
        packet = self.packet()
        packet["people_count"] = 1
        packet["operator"].update(state="LOCKED_HIGH", kind="operator",
                                  track_id=3, bbox=[1, 2, 3, 4])
        packet["gesture"]["stable"] = "RIGHT"
        packet["stage6"].update(intent="MOVE_RIGHT", valid=True,
                                reason="VALID_GESTURE", lease="ACTIVE")
        state = competition_state(packet, 10)
        self.assertEqual(state["perception"][0], "PERSON #3")
        self.assertEqual(state["authorization"][:2], ("AUTHORIZED", "LOCKED_HIGH"))
        self.assertEqual(state["gesture"][0], "RIGHT")
        self.assertEqual(state["safety"][0], "MOVE_RIGHT")
        self.assertEqual(state["control_safety"], "READY")
        self.assertEqual(state["lease"], "ACTIVE")

    def test_operator_lost_is_safe_hover_and_clears_gesture(self):
        packet = self.packet()
        packet["people_count"] = 1
        packet["operator"].update(state="OPERATOR_LOST", kind=None, track_id=None)
        packet["gesture"]["stable"] = "RIGHT"
        packet["stage6"].update(intent="HOVER", valid=False, reason="OPERATOR_LOST")
        state = competition_state(packet, 10)
        self.assertEqual(state["perception"][0], "NO OPERATOR")
        self.assertEqual(state["authorization"][0], "OPERATOR LOST")
        self.assertEqual(state["gesture"][0], "--")
        self.assertEqual(state["safety"][0], "SAFE HOVER")
        self.assertFalse(state["person_visible"])

    def test_invalid_non_hover_does_not_claim_motion_allowed(self):
        packet = self.packet()
        packet["stage6"].update(intent="MOVE_RIGHT", valid=False,
                                reason="VISION_COMMAND_TIMEOUT")
        state = competition_state(packet, 10)
        self.assertEqual(state["safety"][0], "INHIBITED")
        self.assertEqual(state["control_intent"], "MOVE_RIGHT")

    def test_metadata_and_video_loss_are_independent(self):
        packet = self.packet()
        stale = competition_state(packet, 600, video_ready=True)
        self.assertEqual(stale["perception"][0], "METADATA LOST")
        self.assertEqual(stale["gesture"][0], "--")
        self.assertFalse(stale["person_visible"])
        video_lost = competition_state(packet, 10, video_ready=False)
        self.assertEqual(video_lost["perception"][0], "NO PERSON")
        self.assertEqual(video_lost["system"], "VIDEO LOST")
        self.assertEqual(video_lost["px4"][0], "DISCONNECTED")

    def test_stale_ai_cannot_show_old_operator(self):
        packet = self.packet()
        packet["ai_age_ms"] = 900
        packet["operator"].update(state="LOCKED_HIGH", kind="operator",
                                  track_id=3, bbox=[1, 2, 3, 4])
        state = competition_state(packet, 10)
        self.assertEqual(state["perception"][0], "AI STALE")
        self.assertEqual(state["gesture"][0], "--")
        self.assertFalse(state["person_visible"])


if __name__ == "__main__":
    unittest.main()
