"""Offscreen checks for guarded flight command buttons and confirmation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from ground_station.app import Console
from ground_station.config import load_config
from ground_station.protocol import empty_snapshot


class Video:
    def snapshot(self):
        return None, 0, None, {"frames": 0, "restarts": 0}

    def close(self):
        pass


class Metadata:
    def __init__(self):
        self.packet = empty_snapshot()
        self.packet["ai_age_ms"] = 40
        self.packet["px4"] = {"connected": True, "status_age_ms": 40,
            "mode": "POSCTL", "nav_state": 2, "armed": False, "failsafe": False,
            "preflight": True, "landed": True, "local_position_valid": True}

    def snapshot(self):
        return self.packet, 10, {"received": 1, "gaps": 0, "invalid": 0, "hz": 20.0}

    def close(self):
        pass


class Command:
    def __init__(self):
        self.data = {"bridge_state": "READY", "enabled": True,
                     "takeoff_height_verified": True, "transaction_state": "IDLE"}
        self.requests = []

    def snapshot(self):
        return self.data

    def request(self, command):
        self.requests.append(command)

    def close(self):
        pass


class CommandUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_buttons_confirmation_busy_and_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config()
            config["metrics_jsonl"] = Path(tmp) / "metrics.jsonl"
            metadata, client = Metadata(), Command()
            station = Console(config, video=Video(), metadata=metadata, command_client=client)
            try:
                station.last_ui_at = 0
                station.refresh()
                self.assertTrue(station.flight_buttons["TAKEOFF 1.2m"].isEnabled())
                metadata.packet["px4"]["preflight"] = False
                station.last_ui_at = 0
                station.refresh()
                self.assertFalse(station.flight_buttons["TAKEOFF 1.2m"].isEnabled())
                self.assertIn("PRECHECK FAILED", station.transaction_value.text())
                metadata.packet["px4"]["preflight"] = True
                station.last_ui_at = 0
                station.refresh()
                self.assertFalse(station.flight_buttons["LAND"].isEnabled())
                self.assertFalse(station.flight_buttons["ARM"].isEnabled())
                self.assertFalse(station.flight_buttons["RETURN"].isEnabled())
                with patch("ground_station.app.QMessageBox.question",
                           return_value=QMessageBox.StandardButton.No):
                    station._request_flight("TAKEOFF")
                self.assertEqual(client.requests, [])
                with patch("ground_station.app.QMessageBox.question",
                           return_value=QMessageBox.StandardButton.Yes):
                    station._request_flight("TAKEOFF")
                self.assertEqual(client.requests, ["TAKEOFF"])
                client.data["transaction_state"] = "TAKING_OFF"
                station.last_ui_at = 0
                station.refresh()
                self.assertFalse(station.flight_buttons["TAKEOFF 1.2m"].isEnabled())
                self.assertIn("TAKING_OFF", station.transaction_value.text())
                client.data["transaction_state"] = "COMPLETE"
                metadata.packet["px4"].update(armed=True, landed=False)
                station.last_ui_at = 0
                station.refresh()
                self.assertTrue(station.flight_buttons["LAND"].isEnabled())
                client.data = None
                station.last_ui_at = 0
                station.refresh()
                self.assertFalse(station.flight_buttons["LAND"].isEnabled())
            finally:
                station.close()


if __name__ == "__main__":
    unittest.main()
