from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ground_station.px4_telemetry import read_px4_snapshot


class Px4TelemetryTests(unittest.TestCase):
    def test_missing_and_stale_never_retain_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "px4.json"
            self.assertFalse(read_px4_snapshot(path, now_ns=4_000_000_000)["connected"])
            path.write_text(json.dumps({"status": {
                "received_monotonic_ns": 1_000_000_000, "mode": "OFFBOARD",
                "armed": True, "failsafe": False}, "position": None}))
            stale = read_px4_snapshot(path, now_ns=4_000_000_000)
            self.assertFalse(stale["connected"])
            self.assertIsNone(stale["mode"])
            self.assertIsNone(stale["armed"])

    def test_status_fresh_position_missing_then_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "px4.json"
            data = {"status": {"received_monotonic_ns": 1_000_000_000,
                               "mode": "AUTO_LOITER", "nav_state": 4,
                               "armed": False, "failsafe": False},
                    "position": None}
            path.write_text(json.dumps(data))
            current = read_px4_snapshot(path, now_ns=1_200_000_000)
            self.assertTrue(current["connected"])
            self.assertIsNone(current["local_position_valid"])
            data["position"] = {"received_monotonic_ns": 1_100_000_000,
                                "xy_valid": True, "z_valid": True,
                                "v_xy_valid": True, "v_z_valid": True,
                                "x": 1, "y": 2, "z": -3,
                                "vx": 0.1, "vy": 0.2, "vz": 0.3}
            path.write_text(json.dumps(data))
            current = read_px4_snapshot(path, now_ns=1_200_000_000)
            self.assertTrue(current["local_position_valid"])
            self.assertEqual(current["position"], {"x": 1.0, "y": 2.0, "z": -3.0})


if __name__ == "__main__":
    unittest.main()
