"""Mock-only transaction checks. Never imports ROS or publishes to PX4."""
import unittest

from ground_station.command_protocol import decode, encode
from scripts.ground_command_core import Telemetry, Transaction, precheck


def ready(now=100.0, **changes):
    t = Telemetry(status_at=now, position_at=now, land_at=now,
                  armed=False, nav_state=2, failsafe=False, preflight=True,
                  z_valid=True, vz_valid=True, z=0.0, vz=0.0, landed=True)
    for key, value in changes.items():
        setattr(t, key, value)
    return t


class CommandCoreTests(unittest.TestCase):
    def test_prechecks(self):
        self.assertIsNone(precheck("TAKEOFF", ready(), 100, takeoff_height_verified=True))
        for changes, expected in [({"status_at": 0}, "PX4 DISCONNECTED"),
                                  ({"preflight": False}, "PRECHECK FAILED"),
                                  ({"failsafe": True}, "FAILSAFE ACTIVE"),
                                  ({"armed": True}, "ALREADY ARMED"),
                                  ({"z_valid": False}, "LOCAL POSITION INVALID")]:
            self.assertEqual(precheck("TAKEOFF", ready(**changes), 100,
                                     takeoff_height_verified=True), expected)
        self.assertEqual(precheck("TAKEOFF", ready(), 100,
                                 takeoff_height_verified=False), "TAKEOFF HEIGHT UNVERIFIED")

    def test_takeoff_ack_and_stabilization(self):
        t = ready()
        x = Transaction("request-0001", "TAKEOFF", 100, t.z)
        self.assertEqual(x.tick(t, 100, height_verified=True), "ARM")
        self.assertIsNone(x.tick(t, 100.1, height_verified=True))
        x.on_ack(400, 0, 100.2)
        self.assertEqual(x.state, "WAIT_ARMED")
        t.armed, t.status_at, t.land_at = True, 100.3, 100.3
        self.assertEqual(x.tick(t, 100.3, height_verified=True), "TAKEOFF")
        x.on_ack(22, 0, 100.4)
        self.assertEqual(x.state, "TAKING_OFF")
        t.nav_state = 17
        t.z, t.position_at = -1.19, 101
        x.tick(t, 101, height_verified=True)
        self.assertEqual(x.state, "STABILIZING")
        t.position_at = t.status_at = 102
        x.tick(t, 102, height_verified=True)
        self.assertEqual(x.state, "COMPLETE")
        self.assertEqual(x.sent, ["ARM", "TAKEOFF"])

    def test_arm_reject_timeout_and_no_takeoff(self):
        for result in (1, 2, 3, 4, 6):
            x = Transaction("request-0002", "TAKEOFF", 100, 0)
            x.tick(ready(), 100, height_verified=True)
            x.on_ack(400, result, 100.1)
            self.assertEqual(x.state, "FAILED")
            self.assertEqual(x.sent, ["ARM"])
        x = Transaction("request-0003", "TAKEOFF", 100, 0)
        x.tick(ready(), 100, height_verified=True)
        x.tick(ready(104), 104, height_verified=True)
        self.assertEqual(x.reason, "COMMAND TIMEOUT")

    def test_armed_confirmation_timeout(self):
        x = Transaction("request-armed-timeout", "TAKEOFF", 100, 0)
        x.tick(ready(), 100, height_verified=True)
        x.on_ack(400, 0, 100.1)
        x.tick(ready(105), 105, height_verified=True)
        self.assertEqual(x.reason, "ARMED CONFIRMATION TIMEOUT")
        self.assertEqual(x.sent, ["ARM"])

    def test_takeoff_ack_denied_height_timeout_and_telemetry_loss(self):
        for outcome in (2, 0):
            x = Transaction(f"request-takeoff-{outcome}", "TAKEOFF", 100, 0)
            t = ready()
            x.tick(t, 100, height_verified=True)
            x.on_ack(400, 0, 100.1)
            t.armed, t.status_at, t.land_at = True, 100.2, 100.2
            self.assertEqual(x.tick(t, 100.2, height_verified=True), "TAKEOFF")
            x.on_ack(22, outcome, 100.3)
            if outcome == 2:
                self.assertEqual(x.state, "FAILED")
                self.assertIn("ACK 2", x.reason)
            else:
                t.status_at = t.position_at = 126
                t.nav_state = 17
                x.tick(t, 126, height_verified=True)
                self.assertEqual(x.reason, "HEIGHT TIMEOUT")
        x = Transaction("request-loss", "TAKEOFF", 100, 0)
        t = ready()
        x.tick(t, 100, height_verified=True)
        x.on_ack(400, 0, 100.1)
        t.armed, t.status_at, t.land_at = True, 100.2, 100.2
        x.tick(t, 100.2, height_verified=True)
        x.on_ack(22, 0, 100.3)
        x.tick(t, 102, height_verified=True)
        self.assertEqual(x.reason, "PX4 DISCONNECTED")

    def test_local_z_reset_aborts_height_measurement(self):
        t = ready()
        x = Transaction("request-z-reset", "TAKEOFF", 100, 0)
        x.tick(t, 100, height_verified=True)
        x.on_ack(400, 0, 100.1)
        t.armed, t.status_at, t.land_at = True, 100.2, 100.2
        x.tick(t, 100.2, height_verified=True)
        x.on_ack(22, 0, 100.3)
        t.z_reset, t.status_at, t.position_at = 1, 100.5, 100.5
        x.tick(t, 100.5, height_verified=True)
        self.assertEqual(x.reason, "LOCAL POSITION RESET")

    def test_landing_requires_ack_landed_and_disarmed(self):
        t = ready(armed=True, landed=False, nav_state=14)  # OFFBOARD at click
        x = Transaction("request-0004", "LAND", 100, None)
        self.assertEqual(x.tick(t, 100, height_verified=True), "LAND")
        x.on_ack(21, 0, 100.1)
        self.assertEqual(x.state, "LANDING")
        t.nav_state = 18
        t.landed, t.land_at = True, 101
        x.tick(t, 101, height_verified=True)
        self.assertEqual(x.state, "WAIT_DISARM")
        t.armed, t.status_at, t.land_at = False, 102, 102
        x.tick(t, 102, height_verified=True)
        self.assertEqual(x.state, "COMPLETE")
        self.assertEqual(x.sent, ["LAND"])

    def test_land_ack_denied_and_pilot_mode_takeover(self):
        t = ready(armed=True, landed=False, nav_state=14)
        x = Transaction("request-land-denied", "LAND", 100, None)
        x.tick(t, 100, height_verified=True)
        x.on_ack(21, 2, 100.1)
        self.assertEqual(x.state, "FAILED")
        x = Transaction("request-land-mode", "LAND", 100, None)
        x.tick(t, 100, height_verified=True)
        x.on_ack(21, 0, 100.1)
        t.status_at = t.land_at = 104
        x.tick(t, 104, height_verified=True)
        self.assertEqual(x.reason, "LAND MODE LOST")

    def test_loss_aborts_without_extra_commands(self):
        t = ready(armed=True, landed=False)
        x = Transaction("request-0005", "LAND", 100, None)
        x.tick(t, 100, height_verified=True)
        x.tick(t, 102, height_verified=True)
        self.assertEqual(x.state, "ABORTED")
        self.assertEqual(x.sent, ["LAND"])

    def test_authenticated_ttl(self):
        key = b"k" * 32
        body = {"kind": "request", "issued_at": 100, "expires_at": 102,
                "command": "LAND", "request_id": "request-0006"}
        packet = encode(body, key)
        self.assertEqual(decode(packet, key, now=101), body)
        with self.assertRaises(ValueError):
            decode(packet, key, now=103)
        with self.assertRaises(ValueError):
            decode(packet, b"x" * 32, now=101)


if __name__ == "__main__":
    unittest.main()
