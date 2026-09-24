"""Deterministic Safety Pilot authority edges without publishing live ROS."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from px4_msgs.msg import VehicleStatus

from stage5.types import AuthorizedGestureV1
from stage6.flight_authority import FlightAuthorityGate
from stage6.intent_adapter import AuthorizedGestureIntentAdapter


OFFBOARD = VehicleStatus.NAVIGATION_STATE_OFFBOARD
HOLD = VehicleStatus.NAVIGATION_STATE_AUTO_LOITER


def gesture(ms=1000, label="RIGHT", valid=True, session="S001"):
    return AuthorizedGestureV1(ms,ms,session,2,"LOCKED_HIGH",label,.95,valid,[],.95)


def log(raw="RIGHT", stable="RIGHT"):
    return {"gesture_raw":{"label":raw},
            "gesture_stable":{"label":stable,"stable":stable != "UNKNOWN"}}


class FlightAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.gate = FlightAuthorityGate(offboard_nav_state=OFFBOARD)
        self.adapter = AuthorizedGestureIntentAdapter(300)
        self.session = SimpleNamespace(id="S001", gallery=["known-embedding"])

    def status(self, mode=OFFBOARD, *, armed=True, failsafe=False, ms=1000):
        changed = self.gate.update_status(armed=armed,nav_state=mode,
                                          failsafe=failsafe,now_ns=ms*1_000_000)
        if changed:
            self.adapter.clear(self.gate.transition_reason)
        return changed

    def release(self, start=1010):
        neutral = gesture(start,"UNKNOWN",False)
        self.assertFalse(self.gate.allow(neutral,log("UNKNOWN","UNKNOWN"),
                                         start*1_000_000)[0])
        neutral.timestamp_ms = start+160
        self.assertEqual(self.gate.allow(neutral,log("UNKNOWN","UNKNOWN"),
                                         (start+160)*1_000_000),
                         (False,"FRESH_GESTURE_READY"))

    def test_no_status_fail_closed(self):
        self.assertFalse(self.gate.allow(gesture(),log(),1_000_000_000)[0])
        self.assertEqual(self.gate.transition_reason,"PX4_STATUS_UNAVAILABLE")

    def test_armed_offboard_no_failsafe_required(self):
        self.assertFalse(self.gate.update_status(armed=False,nav_state=OFFBOARD,
                                                failsafe=False,now_ns=1))
        self.assertFalse(self.gate.enabled)
        self.status()
        self.assertTrue(self.gate.enabled)
        self.assertTrue(self.gate.require_fresh_gesture)

    def test_offboard_exit_revokes_lease_and_old_right(self):
        self.status()
        self.release()
        self.assertTrue(self.gate.allow(gesture(1200),log(),1_200_000_000)[0])
        self.adapter.ingest(gesture(1200),1200)
        self.assertTrue(self.adapter.tick(1210).valid)
        self.assertTrue(self.status(HOLD,ms=1220))
        self.assertEqual(self.adapter.tick(1220).intent,"HOVER")
        self.assertFalse(self.adapter.tick(1221).valid)
        self.assertEqual(self.gate.transition_reason,"OFFBOARD_EXIT")

    def test_session_and_gallery_unchanged_on_exit(self):
        self.status()
        self.status(HOLD,ms=1100)
        self.assertEqual(self.session.id,"S001")
        self.assertEqual(self.session.gallery,["known-embedding"])

    def test_reentry_preserves_session_without_new_tpose(self):
        self.status()
        self.status(HOLD,ms=1100)
        self.assertTrue(self.status(OFFBOARD,ms=1200))
        self.assertEqual(self.session.id,"S001")
        self.assertTrue(self.gate.require_fresh_gesture)
        self.assertEqual(self.gate.transition_reason,"OFFBOARD_ENTER_WAIT_FRESH_GESTURE")

    def test_holding_right_across_reentry_never_auto_restores(self):
        self.status()
        self.release()
        self.status(HOLD,ms=1300)
        self.status(OFFBOARD,ms=1400)
        for ms in (1410,1510,1610,1710):
            self.assertEqual(self.gate.allow(gesture(ms),log(),ms*1_000_000),
                             (False,"WAIT_FRESH_GESTURE_RELEASE"))
        self.assertTrue(self.gate.require_fresh_gesture)

    def test_release_then_new_right_renews_motion(self):
        self.status()
        self.release()
        right = gesture(1200)
        self.assertTrue(self.gate.allow(right,log(),1_200_000_000)[0])
        self.assertTrue(self.adapter.ingest(right,1200))
        self.assertEqual(self.adapter.tick(1200).intent,"MOVE_RIGHT")

    def test_transient_invalid_pose_does_not_unlock_fresh_gate(self):
        self.status()
        for ms in (1100,1300):
            self.assertFalse(self.gate.allow(gesture(ms,"UNKNOWN",False),
                                             log("INVALID","UNKNOWN"),ms*1_000_000)[0])
        self.assertTrue(self.gate.require_fresh_gesture)

    def test_neutral_must_persist_in_same_session(self):
        self.status()
        self.gate.allow(gesture(1010,"UNKNOWN",False),
                        log("UNKNOWN","UNKNOWN"),1_010_000_000)
        self.assertFalse(self.gate.allow(gesture(1170,"UNKNOWN",False,"S002"),
                                         log("UNKNOWN","UNKNOWN"),1_170_000_000)[0])
        self.assertTrue(self.gate.require_fresh_gesture)

    def test_failsafe_revokes_authority(self):
        self.status()
        self.release()
        self.assertTrue(self.status(OFFBOARD,failsafe=True,ms=1200))
        self.assertFalse(self.gate.enabled)
        self.assertEqual(self.gate.transition_reason,"PX4_FAILSAFE")

    def test_disarm_revokes_authority(self):
        self.status()
        self.release()
        self.assertTrue(self.status(OFFBOARD,armed=False,ms=1200))
        self.assertFalse(self.gate.enabled)
        self.assertEqual(self.gate.transition_reason,"PX4_DISARMED")

    def test_stale_px4_status_revokes_authority(self):
        self.status()
        self.release()
        self.assertTrue(self.gate.refresh(2_500_000_000))
        self.assertFalse(self.gate.enabled)
        self.assertEqual(self.gate.transition_reason,"PX4_STATUS_TIMEOUT")
        self.assertFalse(self.gate.allow(gesture(2500),log(),2_500_000_000)[0])

    def test_new_session_after_ready_needs_its_own_release(self):
        self.status()
        self.release()
        self.assertEqual(self.gate.allow(gesture(1200,session="S002"),log(),
                                         1_200_000_000),
                         (False,"SESSION_CHANGED_WAIT_FRESH_GESTURE"))


if __name__ == "__main__":
    unittest.main()
