from __future__ import annotations

import unittest

from stage5.types import AuthorizedGestureV1
from stage6.intent_adapter import AuthorizedGestureIntentAdapter, GESTURE_TO_INTENT


def event(t=1000, gesture="LEFT", valid=True, state="LOCKED_HIGH", session="session-a",
          confidence=.95, frame=None):
    return AuthorizedGestureV1(t,frame if frame is not None else t,session,2,state,
                               gesture,confidence,valid,[],.95)


class IntentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AuthorizedGestureIntentAdapter(300)

    def test_left(self):
        self.assertTrue(self.adapter.ingest(event(),1000))
        self.assertEqual(self.adapter.tick(1000).intent,"MOVE_LEFT")

    def test_right(self):
        self.adapter.ingest(event(1000,"RIGHT"),1000)
        self.assertEqual(self.adapter.tick(1000).intent,"MOVE_RIGHT")

    def test_ascend(self):
        self.adapter.ingest(event(1000,"ASCEND"),1000)
        self.assertEqual(self.adapter.tick(1000).intent,"ASCEND")

    def test_descend(self):
        self.adapter.ingest(event(1000,"DESCEND"),1000)
        self.assertEqual(self.adapter.tick(1000).intent,"DESCEND")

    def test_valid_hover(self):
        self.adapter.ingest(event(1000,"HOVER"),1000)
        result = self.adapter.tick(1000)
        self.assertEqual(result.intent,"HOVER")
        self.assertTrue(result.valid)

    def test_unknown_never_moves(self):
        self.assertFalse(self.adapter.ingest(event(gesture="UNKNOWN"),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_invalid_never_moves(self):
        self.assertFalse(self.adapter.ingest(event(valid=False),1000))
        self.assertEqual(self.adapter.tick(1000).intent,"HOVER")

    def test_release_immediately_cancels_previous_movement(self):
        self.adapter.ingest(event(),1000)
        self.assertTrue(self.adapter.tick(1010).valid)
        self.adapter.ingest(event(1020,gesture="UNKNOWN",valid=False),1020)
        self.assertFalse(self.adapter.tick(1020).valid)

    def test_stale_on_arrival(self):
        self.assertFalse(self.adapter.ingest(event(),1400))
        self.assertEqual(self.adapter.tick(1400).intent,"HOVER")

    def test_lease_expires_at_300_ms_inclusive(self):
        self.adapter.ingest(event(),1000)
        self.assertTrue(self.adapter.tick(1299).valid)
        expired = self.adapter.tick(1300)
        self.assertFalse(expired.valid)
        self.assertEqual(expired.reason,"VISION_COMMAND_TIMEOUT")

    def test_red_denied(self):
        self.assertFalse(self.adapter.ingest(event(state="LOCKED_LOW"),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_lost_denied(self):
        self.assertFalse(self.adapter.ingest(event(state="OPERATOR_LOST"),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_yellow_denied(self):
        self.assertFalse(self.adapter.ingest(event(state="WAIT_OPERATOR"),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_new_session_no_old_command_replay(self):
        self.adapter.ingest(event(),1000)
        self.adapter.ingest(event(1100,"UNKNOWN",False,session="session-b"),1100)
        self.assertFalse(self.adapter.tick(1100).valid)
        self.assertTrue(self.adapter.ingest(event(1200,"RIGHT",session="session-b"),1200))
        self.assertEqual(self.adapter.tick(1200).intent,"MOVE_RIGHT")

    def test_old_session_cannot_return_after_new_session(self):
        self.adapter.ingest(event(),1000)
        self.adapter.ingest(event(1100,"UNKNOWN",False,session="session-b"),1100)
        self.assertFalse(self.adapter.ingest(event(1200,"LEFT",session="session-a"),1200))
        self.assertFalse(self.adapter.tick(1200).valid)

    def test_auto_reauth_success_frame_no_old_gesture(self):
        self.adapter.ingest(event(),1000)
        self.adapter.ingest(event(1100,"UNKNOWN",False,session="new-session"),1100)
        self.assertEqual(self.adapter.tick(1100).intent,"HOVER")

    def test_camera_or_vision_exception_fails_closed(self):
        self.adapter.ingest(event(),1000)
        self.adapter.clear("VISION_PIPELINE_EXCEPTION")
        result=self.adapter.tick(1100)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason,"VISION_PIPELINE_EXCEPTION")

    def test_nonfinite_timestamp_rejected(self):
        self.assertFalse(self.adapter.ingest(event(float("nan")),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_future_timestamp_rejected(self):
        self.assertFalse(self.adapter.ingest(event(1100),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_out_of_order_input_clears_lease(self):
        self.adapter.ingest(event(1000),1000)
        self.assertFalse(self.adapter.ingest(event(999),1001))
        self.assertFalse(self.adapter.tick(1001).valid)

    def test_rapid_change_replaces_previous_intent(self):
        self.adapter.ingest(event(1000,"LEFT"),1000)
        self.assertEqual(self.adapter.tick(1000).intent,"MOVE_LEFT")
        self.adapter.ingest(event(1050,"RIGHT"),1050)
        self.assertEqual(self.adapter.tick(1050).intent,"MOVE_RIGHT")

    def test_no_takeoff_land_arm_mapping(self):
        self.assertEqual(set(GESTURE_TO_INTENT.values()),
                         {"MOVE_LEFT","MOVE_RIGHT","ASCEND","DESCEND","HOVER"})

    def test_bad_input_type_clears_lease(self):
        self.adapter.ingest(event(),1000)
        self.assertFalse(self.adapter.ingest({"gesture":"RIGHT"},1010))
        self.assertFalse(self.adapter.tick(1010).valid)

    def test_malformed_confidence_fails_closed(self):
        self.assertFalse(self.adapter.ingest(event(confidence="bad"),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_malformed_session_fails_closed(self):
        self.assertFalse(self.adapter.ingest(event(session=123),1000))
        self.assertFalse(self.adapter.tick(1000).valid)

    def test_timeout_cannot_exceed_gateway(self):
        with self.assertRaises(ValueError):
            AuthorizedGestureIntentAdapter(501)

    def test_no_source_never_moves(self):
        result=self.adapter.tick(1000)
        self.assertEqual(result.intent,"HOVER")
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
