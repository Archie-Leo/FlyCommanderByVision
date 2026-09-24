"""Stage 6 ROS dry-run transport timing and SIGINT lease-revocation checks."""
from __future__ import annotations

import io
import json
import signal
import time
import unittest
from pathlib import Path

import rclpy
from drone_control_gateway.msg import Intent
from px4_msgs.msg import VehicleStatus
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions

from stage5.types import AuthorizedGestureV1
from stage6.ros_intent_node import AuthorizedIntentPublisher, DRY_RUN_TOPIC
from stage6.flight_authority import FlightAuthorityGate
from stage6.run_context import RunContext


class RosIntentSafetyTests(unittest.TestCase):
    def setUp(self):
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self.event_log = io.StringIO()
        self.run_context = RunContext(Path("."))
        self.publisher = AuthorizedIntentPublisher(event_log=self.event_log,
                                                   run_context=self.run_context)
        self.observer = Node("stage6_gate6b_safety_observer")
        self.received = []
        self.observer.create_subscription(
            Intent, DRY_RUN_TOPIC,
            lambda message: self.received.append(
                (time.monotonic_ns() // 1_000_000, message.intent,
                 message.valid, message.reason)), 10)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.publisher)
        self.executor.add_node(self.observer)
        self.spin_for(.12)

    def tearDown(self):
        self.executor.shutdown()
        self.observer.destroy_node()
        self.publisher.destroy_node()
        rclpy.try_shutdown()

    def spin_for(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=.01)

    def submit_right(self):
        now_ms = time.monotonic_ns() // 1_000_000
        authorized = AuthorizedGestureV1(
            now_ms, 1, "gate6b-test-session", 2, "LOCKED_HIGH", "RIGHT",
            .95, True, [], .95)
        self.assertTrue(self.publisher.submit(authorized, {
            "ownership_state": "LOCKED_HIGH",
            "authorized_gesture": {"valid": True, "gesture": "RIGHT"},
        }))
        return now_ms

    def test_timeout_received_within_350_ms(self):
        submitted_ms = self.submit_right()
        self.spin_for(.55)
        records = [json.loads(line) for line in self.event_log.getvalue().splitlines()]
        self.assertTrue(any(x["intent"] == "MOVE_RIGHT" and x["ros_output_valid"]
                            for x in records))
        timed_out = [x for x in self.received
                     if x[3] == "VISION_COMMAND_TIMEOUT"]
        self.assertTrue(timed_out, "No timeout HOVER received on ROS topic")
        self.assertEqual(timed_out[0][1:4], ("HOVER", False,
                                            "VISION_COMMAND_TIMEOUT"))
        self.assertLessEqual(timed_out[0][0] - submitted_ms, 350)
        self.assertFalse(any(x[2] and x[1] == "MOVE_RIGHT"
                             for x in self.received
                             if x[0] >= timed_out[0][0]))

    def test_intent_log_has_monotonic_alignment_and_transport_fields(self):
        self.submit_right()
        self.spin_for(.09)
        records = [json.loads(line) for line in self.event_log.getvalue().splitlines()]
        movement = next(row for row in records if row["intent"] == "MOVE_RIGHT"
                        and row["valid"])
        for key in ("host_monotonic_ns","run_elapsed_ms","operator_session_id",
                    "intent_seq","reason","input_age_ms","topic",
                    "requested_speed_m_s","requested_yaw_rate_rad_s"):
            self.assertIn(key,movement)
        self.assertGreaterEqual(movement["run_elapsed_ms"],0)

    def test_sigint_final_ros_message_is_safe_hover(self):
        self.submit_right()
        self.spin_for(.11)
        self.assertTrue(any(x[1] == "MOVE_RIGHT" and x[2]
                            for x in self.received))
        try:
            signal.raise_signal(signal.SIGINT)
        except KeyboardInterrupt:
            self.publisher.stop_and_publish("VISION_STOPPED")
        self.spin_for(.12)
        self.assertEqual(self.received[-1][1:4],
                         ("HOVER", False, "VISION_STOPPED"))
        records = [json.loads(line) for line in self.event_log.getvalue().splitlines()]
        self.assertEqual((records[-1]["intent"], records[-1]["ros_output_valid"],
                          records[-1]["control_reason"]),
                         ("HOVER", False, "VISION_STOPPED"))

    def test_px4_authority_edges_on_isolated_dry_run_transport(self):
        # Exercise the production callback/timer/lease path without ever
        # publishing to the Gateway topic or starting a PX4 vehicle.
        self.publisher.flight_gate = FlightAuthorityGate(
            offboard_nav_state=VehicleStatus.NAVIGATION_STATE_OFFBOARD,
            neutral_release_ms=0)

        def status(nav_state):
            message = VehicleStatus()
            message.arming_state = VehicleStatus.ARMING_STATE_ARMED
            message.nav_state = nav_state
            message.failsafe = False
            self.publisher._on_px4_status(message)

        def submit(label, valid):
            now_ms = time.monotonic_ns() // 1_000_000
            value = AuthorizedGestureV1(now_ms,now_ms,"gate6b-test-session",2,
                                        "LOCKED_HIGH",label,.95,valid,[],.95)
            state = {"ownership_state":"LOCKED_HIGH",
                     "operator_session_id":"gate6b-test-session",
                     "gesture_raw":{"label":label},
                     "gesture_stable":{"label":label,"stable":label != "UNKNOWN"},
                     "authorized_gesture":{"valid":valid,"gesture":label}}
            return self.publisher.submit(value,state)

        status(VehicleStatus.NAVIGATION_STATE_OFFBOARD)
        self.assertFalse(submit("RIGHT",True))
        self.assertFalse(submit("UNKNOWN",False))
        self.assertFalse(submit("UNKNOWN",False))
        self.assertTrue(submit("RIGHT",True))
        self.spin_for(.08)
        self.assertTrue(any(x[1] == "MOVE_RIGHT" and x[2] for x in self.received))
        status(VehicleStatus.NAVIGATION_STATE_AUTO_LOITER)
        self.spin_for(.04)
        self.assertEqual(self.received[-1][1:4],("HOVER",False,"OFFBOARD_EXIT"))
        self.assertEqual(self.publisher.adapter._session_id,"gate6b-test-session")
        status(VehicleStatus.NAVIGATION_STATE_OFFBOARD)
        self.assertFalse(submit("RIGHT",True))
        self.spin_for(.04)
        self.assertFalse(self.received[-1][2])
        self.assertFalse(submit("UNKNOWN",False))
        self.assertFalse(submit("UNKNOWN",False))
        self.assertTrue(submit("RIGHT",True))


if __name__ == "__main__":
    unittest.main()
