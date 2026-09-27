from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from flight_evidence.core import SessionStore, boot_id
from flight_evidence.board_recorder import BoardRecorder, fresh_px4
from ground_station.blocker import last_blocker
from ground_station.flight_button_policy import evaluate_buttons
from ground_station.session_recorder import GroundSessionRecorder


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / "sessions", {"boot_id": "boot-test"}, max_sessions=3)
        self.addCleanup(lambda: self.store.close() if self.store.active else None)

    def events(self):
        self.store.events_file.flush()
        return [json.loads(line) for line in
                (self.store.root / self.store.active / "events.jsonl").read_text().splitlines()]

    def test_01_unchanged_event_suppressed(self):
        self.assertTrue(self.store.event("px4", "PX4_STATE_CHANGED", new_state="POSCTL"))
        self.assertFalse(self.store.event("px4", "PX4_STATE_CHANGED", new_state="POSCTL"))

    def test_02_state_change_recorded(self):
        self.store.event("px4", "PX4_STATE_CHANGED", new_state="POSCTL")
        self.store.event("px4", "PX4_STATE_CHANGED", new_state="OFFBOARD")
        self.assertEqual([e["new_state"] for e in self.events() if e["event"] == "PX4_STATE_CHANGED"],
                         ["POSCTL", "OFFBOARD"])

    def test_03_boot_id_in_event(self):
        self.store.event("system", "BOOT_START")
        self.assertEqual(self.events()[-1]["boot_id"], "boot-test")

    def test_04_real_boot_id_shape(self):
        self.assertTrue(boot_id())

    def test_05_session_id_unique(self):
        first = self.store.active
        self.store.close()
        second = self.store.start("NEXT")
        self.assertNotEqual(first, second)

    def test_06_takeoff_bridge_disabled(self):
        takeoff, _ = evaluate_buttons({"bridge_state": "READY", "enabled": False,
                                       "transaction_state": "IDLE"}, self.px4())
        self.assertEqual((takeoff["result"], takeoff["primary_reason"]),
                         ("BLOCKED", "BRIDGE_DISABLED"))

    def test_07_takeoff_preflight_failed(self):
        px4 = self.px4(preflight=False)
        takeoff, _ = evaluate_buttons(self.feedback(), px4)
        self.assertEqual(takeoff["primary_reason"], "PREFLIGHT_FAILED")

    def test_08_wait_fresh_gesture_recorded(self):
        self.store.event("flight_authority", "FLIGHT_AUTHORITY_CHANGED", new_state="BLOCKED",
                         reason="WAIT_FRESH_GESTURE_RELEASE")
        self.assertEqual(self.events()[-1]["reason"], "WAIT_FRESH_GESTURE_RELEASE")

    def test_09_heading_invalid_recorded(self):
        self.store.event("gateway", "GATEWAY_OUTPUT_CHANGED", reason="HEADING_INVALID")
        self.assertEqual(self.events()[-1]["reason"], "HEADING_INVALID")

    def test_10_episode_distance_limit_recorded(self):
        self.store.event("safety_limiter", "SAFETY_BLOCK", reason="EPISODE_DISTANCE_LIMIT")
        self.assertEqual(self.events()[-1]["reason"], "EPISODE_DISTANCE_LIMIT")

    def test_11_authority_granted_recorded(self):
        self.store.event("flight_authority", "FLIGHT_AUTHORITY_CHANGED", new_state="GRANTED")
        self.assertEqual(self.store.summary["last_authority"], "GRANTED")

    def test_12_gateway_nonzero_output_recorded(self):
        self.store.event("gateway", "GATEWAY_OUTPUT_CHANGED", new_state="DESCEND",
                         details={"ned_velocity": [0, 0, .2]})
        self.assertEqual(self.store.summary["last_gateway_output"]["ned_velocity"], [0, 0, .2])

    def test_13_ros_tx_state_recorded(self):
        self.store.event("ros", "ROS_TX_STATE_CHANGED", new_state="PUBLISHING",
                         details={"publisher_active": True})
        self.assertTrue(self.store.summary["ros_tx"]["publisher_active"])

    def test_14_transaction_ack_recorded(self):
        self.store.event("command_bridge", "TAKEOFF_ACK_ACCEPTED", details={"px4_ack": 0})
        self.assertEqual(self.events()[-1]["details"]["px4_ack"], 0)

    def test_15_telemetry_about_ten_hz(self):
        self.assertTrue(self.store.telemetry({"armed": True}, now=10))
        self.assertFalse(self.store.telemetry({"armed": True}, now=10.05))
        self.assertTrue(self.store.telemetry({"armed": True}, now=10.10))
        self.store.telemetry_file.flush()
        with (self.store.root / self.store.active / "telemetry.csv").open(newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 2)

    def test_16_close_generates_summary(self):
        name = self.store.active
        self.store.close()
        self.assertEqual(json.loads((self.store.root / name / "session_summary.json").read_text())
                         ["session_id"], name)

    def test_17_write_failure_is_fail_open(self):
        original = self.store.events_file
        class Failing:
            def write(self, _):
                raise OSError("disk full")
        self.store.events_file = Failing()
        self.assertFalse(self.store.event("stage6", "INTENT_CHANGED", new_state="DESCEND"))
        self.assertTrue(self.store.degraded)
        self.store.events_file = original

    def test_18_reboot_does_not_resume_old_session(self):
        first = self.store.active
        for handle in (self.store.events_file, self.store.system_file, self.store.telemetry_file):
            handle.close()
        self.store.active = None
        second = SessionStore(self.store.root, {"boot_id": "new-boot"})
        self.addCleanup(second.close)
        self.assertNotEqual(first, second.active)
        summary = json.loads((self.store.root / first / "session_summary.json").read_text())
        self.assertEqual(summary["end_reason"], "RECORDER_RESTART_OR_REBOOT")

    def test_19_last_blocker_from_real_button_eval(self):
        takeoff, _ = evaluate_buttons({"bridge_state": "READY", "enabled": False,
                                       "transaction_state": "IDLE"}, self.px4())
        self.assertEqual(last_blocker({"px4_details": self.px4(), "control_intent": "HOVER"}, takeoff),
                         "TAKEOFF · BRIDGE_DISABLED")

    def test_20_last_blocker_from_authority_reason(self):
        takeoff, _ = evaluate_buttons(self.feedback(), self.px4())
        self.assertEqual(last_blocker({"px4_details": self.px4(), "control_intent": "DESCEND",
                                       "command_details": {"authority": "BLOCKED",
                                           "authority_reason": "WAIT_FRESH_GESTURE_RELEASE"}}, takeoff),
                         "MOVEMENT · WAIT_FRESH_GESTURE_RELEASE")

    def test_21_duplicate_system_events_suppressed(self):
        recorder = BoardRecorder(self.root / "board", self.root, b"a" * 32,
                                 gs_ip="127.0.0.1", session_port=0, local_port=0)
        self.addCleanup(recorder.store.close)
        self.addCleanup(recorder.gs_sock.close)
        self.addCleanup(recorder.local_sock.close)
        recorder._state("system", "CAMERA_READY", "CAMERA_LOST", True)
        recorder._state("system", "CAMERA_READY", "CAMERA_LOST", True)
        recorder.store.system_file.flush()
        path = recorder.store.root / recorder.store.active / "system_events.jsonl"
        self.assertEqual(sum('"event":"CAMERA_READY"' in line for line in path.read_text().splitlines()), 1)

    def test_22_no_false_px4_rx_confirmation(self):
        self.store.event("ros", "ROS_TX_STATE_CHANGED", new_state="PUBLISHING",
                         details={"publisher_active": True})
        self.store.event("px4", "COMMAND_EFFECT_NOT_OBSERVED", reason="NO_DIRECTIONAL_RESPONSE_IN_WINDOW")
        name = self.store.active
        self.store.close()
        summary = json.loads((self.store.root / name / "session_summary.json").read_text())
        self.assertEqual(summary["primary_failure_layer"], "UNKNOWN")
        self.assertEqual(summary["px4_rx_direct_evidence"], "unavailable_online")

    def test_23_retention_never_deletes_active(self):
        for _ in range(4):
            self.store.close()
            self.store.start("NEXT")
        self.assertTrue((self.store.root / self.store.active).exists())
        self.assertLessEqual(len([p for p in self.store.root.iterdir() if p.is_dir()]), 4)

    def test_24_ground_station_event_log_automatic(self):
        client = GroundSessionRecorder(self.root / "windows", "127.0.0.1", b"a" * 32, port=9)
        client.event("TAKEOFF_BUTTON_EVAL", reason="BRIDGE_DISABLED")
        client.close()
        files = list((self.root / "windows").glob("pending_*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertIn("BRIDGE_DISABLED", files[0].read_text())

    def test_25_fresh_readonly_control_mode_is_distinct_from_rx(self):
        sample = fresh_px4({"control_mode": {"received_monotonic_ns": 900,
                                              "offboard_enabled": True,
                                              "velocity_enabled": True}}, now_ns=1000)
        self.assertTrue(sample["control_mode"]["offboard_enabled"])
        self.assertNotIn("trajectory_setpoint_rx_confirmed", sample)

    def test_26_reauthorized_counts_as_operator_locked(self):
        self.store.event("stage5", "OPERATOR_REAUTHORIZED", new_state="LOCKED_HIGH")
        self.assertTrue(self.store.summary["operator_locked"])

    def test_27_idle_session_has_no_invented_failure(self):
        name = self.store.active
        self.store.close()
        summary = json.loads((self.store.root / name / "session_summary.json").read_text())
        self.assertEqual(summary["takeoff_result"], "NOT_ATTEMPTED")
        self.assertEqual(summary["land_result"], "NOT_ATTEMPTED")
        self.assertEqual(summary["primary_failure_layer"], "UNKNOWN")

    def test_28_mock_vision_to_ros_evidence_chain(self):
        gateway_path = self.root / "gateway.json"
        recorder = BoardRecorder(self.root / "chain", self.root, b"a" * 32,
                                 gs_ip="127.0.0.1", session_port=0, local_port=0,
                                 gateway_file=gateway_path)
        self.addCleanup(recorder.store.close)
        self.addCleanup(recorder.gs_sock.close)
        self.addCleanup(recorder.local_sock.close)
        recorder.metadata = {
            "operator": {"state": "LOCKED_HIGH", "track_id": 4},
            "gesture": {"raw": "DESCEND", "stable": "DESCEND"},
            "stage6": {"intent": "DESCEND", "valid": True, "lease": True},
            "command": {"authority": "GRANTED", "authority_reason": "READY"}}
        recorder.vision_evidence = {"gesture_confidence": .91, "authorized_valid": True}
        gateway_path.write_text(json.dumps({"monotonic_ns": time.monotonic_ns(),
            "intent": "DESCEND", "authority": "GRANTED", "velocity": [0, 0, .2],
            "ros_published": True, "tx_count": 10,
            "last_publish_monotonic_ns": time.monotonic_ns(),
            "offboard_mode_flags": {"position": False, "velocity": True},
            "safety_limiter_state": "PASS", "limiter_reason": "NONE"}))
        recorder._vision()
        recorder._gateway()
        recorder._telemetry()
        recorder.store.events_file.flush()
        recorder.store.telemetry_file.flush()
        path = recorder.store.root / recorder.store.active
        names = [json.loads(line)["event"] for line in (path / "events.jsonl").read_text().splitlines()]
        for name in ("OPERATOR_LOCKED_HIGH", "GESTURE_CONFIRMED", "INTENT_CHANGED",
                     "FLIGHT_AUTHORITY_CHANGED", "GATEWAY_OUTPUT_CHANGED", "ROS_TX_STATE_CHANGED"):
            self.assertIn(name, names)
        with (path / "telemetry.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[-1]["gateway_vz"], "0.2")

    def test_29_lock_only_session_closes_after_safe_idle(self):
        px4_path = self.root / "px4.json"
        now = time.monotonic_ns()
        px4_path.write_text(json.dumps({
            "status": {"received_monotonic_ns": now, "armed": False,
                       "mode": "POSCTL", "nav_state": 2, "failsafe": False},
            "land": {"received_monotonic_ns": now, "landed": True}}))
        recorder = BoardRecorder(self.root / "idle", self.root, b"a" * 32,
                                 gs_ip="127.0.0.1", session_port=0, local_port=0,
                                 px4_file=px4_path)
        self.addCleanup(recorder.store.close)
        self.addCleanup(recorder.gs_sock.close)
        self.addCleanup(recorder.local_sock.close)
        old = recorder.store.active
        recorder.flight_active_seen = True
        recorder.safe_since = time.monotonic() - 9
        recorder.poll_once()
        self.assertNotEqual(old, recorder.store.active)
        summary = json.loads((recorder.store.root / old / "session_summary.json").read_text())
        self.assertEqual(summary["end_reason"], "LANDED_DISARMED_STABLE")

    @staticmethod
    def px4(**changes):
        return {"connected": True, "armed": "False", "failsafe": "False",
                "preflight": True, "landed": True, "local_position_valid": "VALID",
                "z_valid": True, "vz_valid": True, **changes}

    @staticmethod
    def feedback():
        return {"bridge_state": "READY", "enabled": True, "transaction_state": "IDLE",
                "takeoff_height_verified": True}


if __name__ == "__main__":
    unittest.main()
