"""Offline UDP receiver and ledger tests; no ROS node or PX4 publisher."""
from __future__ import annotations

import json
import math
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest

from ground_station.command_protocol import encode
from scripts.ground_command_bridge import Bridge
from scripts.ground_command_core import Telemetry


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = b"k" * 32
        self.bridge = Bridge.__new__(Bridge)
        b = self.bridge
        b.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b.sock.bind(("127.0.0.1", 0))
        b.sock.setblocking(False)
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        b.allowed_ip, b.key = "127.0.0.1", self.key
        b.enabled, b.height_verified = True, True
        b.state_path = Path(self.tmp.name) / "state.json"
        b.command_count = 0
        b.last_status_timestamp_us = 0
        b.transaction = None
        b.db = sqlite3.connect(":memory:")
        b.db.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, command TEXT, state TEXT)")
        now = time.monotonic()
        b.t = Telemetry(status_at=now, position_at=now, land_at=now,
                        armed=False, nav_state=2, failsafe=False, preflight=True,
                        z_valid=True, vz_valid=True, z=0, vz=0, landed=True)

    def tearDown(self):
        self.bridge.sock.close()
        self.client.close()
        self.bridge.db.close()
        self.tmp.cleanup()

    def send(self, request_id, command="TAKEOFF", *, expired=False):
        now = time.time() - (20 if expired else 0)
        packet = encode({"kind": "request", "request_id": request_id,
                         "command": command, "issued_at": now, "expires_at": now + 2}, self.key)
        self.client.sendto(packet, self.bridge.sock.getsockname())
        self.bridge._receive()

    def test_duplicate_busy_expired_and_disabled(self):
        self.send("request-one")
        first = self.bridge.transaction
        self.assertEqual(first.request_id, "request-one")
        self.assertEqual(self.bridge.command_count, 0)
        self.send("request-one")
        self.assertIs(self.bridge.transaction, first)
        self.assertEqual(self.bridge.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.send("request-two")
        self.assertEqual(self.bridge.db.execute(
            "SELECT state FROM requests WHERE id='request-two'").fetchone()[0], "REJECTED")
        self.send("request-old", expired=True)
        self.assertIsNone(self.bridge.db.execute(
            "SELECT state FROM requests WHERE id='request-old'").fetchone())
        self.bridge.transaction = None
        self.bridge.enabled = False
        self.send("request-three")
        self.assertEqual(self.bridge.db.execute(
            "SELECT state FROM requests WHERE id='request-three'").fetchone()[0], "REJECTED")
        self.assertEqual(json.loads(self.bridge.state_path.read_text())["command_count"], 0)

    def test_preflight_failure_never_starts_transaction(self):
        self.bridge.t.preflight = False
        self.send("request-four")
        self.assertIsNone(self.bridge.transaction)
        self.assertEqual(self.bridge.db.execute(
            "SELECT state FROM requests WHERE id='request-four'").fetchone()[0], "REJECTED")
        self.assertEqual(self.bridge.command_count, 0)

    def test_command_message_uses_nan_for_unspecified_fields(self):
        class Msg:
            VEHICLE_CMD_COMPONENT_ARM_DISARM = 400
            VEHICLE_CMD_NAV_TAKEOFF = 22
            VEHICLE_CMD_NAV_LAND = 21

        class Publisher:
            def __init__(self):
                self.messages = []

            def publish(self, msg):
                self.messages.append(msg)

        self.bridge.VehicleCommand = Msg
        self.bridge.publisher = Publisher()
        self.bridge._publish("TAKEOFF")
        takeoff = self.bridge.publisher.messages[-1]
        self.assertEqual(takeoff.command, 22)
        self.assertTrue(all(math.isnan(getattr(takeoff, f"param{i}")) for i in range(1, 8)))
        self.bridge._publish("ARM")
        arm = self.bridge.publisher.messages[-1]
        self.assertEqual((arm.param1, arm.param2), (1.0, 0.0))
        self.assertTrue(math.isnan(arm.param7))
        self.bridge._publish("LAND")
        self.assertTrue(math.isnan(self.bridge.publisher.messages[-1].param7))


if __name__ == "__main__":
    unittest.main()
