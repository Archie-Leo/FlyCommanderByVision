"""Small offscreen Qt checks; run on the Windows station with PySide6 available."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ground_station.app import Console
from ground_station.config import load_config
from ground_station.protocol import empty_snapshot


class FakeVideo:
    def snapshot(self):
        return None, 0, None, {"frames": 0, "restarts": 0}

    def close(self):
        pass


class FakeMetadata:
    def __init__(self):
        self.packet = empty_snapshot()
        self.packet["ai_age_ms"] = 40
        self.age = 10

    def snapshot(self):
        return self.packet, self.age, {"received": 1, "gaps": 0,
                                 "invalid": 0, "hz": 20.0}

    def close(self):
        pass


class CompetitionQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_toggle_and_disabled_flight_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config()
            config["metrics_jsonl"] = Path(tmp) / "metrics.jsonl"
            metadata = FakeMetadata()
            station = Console(config, video=FakeVideo(), metadata=metadata)
            try:
                station.show()
                self.app.processEvents()
                station.last_ui_at = 0
                station.refresh()
                self.assertEqual(station.nodes["PX4"].value.text(), "DISCONNECTED")
                self.assertEqual(station.nodes["PERCEPTION"].value.text(), "NO PERSON")
                self.assertEqual(station.system_label.text(), "SYSTEM  ·  VIDEO LOST")
                metadata.packet["px4"] = {"connected": True, "status_age_ms": 80,
                    "mode": "AUTO_LOITER", "nav_state": 4, "armed": False,
                    "failsafe": False, "local_position_valid": False}
                metadata.packet["command"] = {"mode": "SHADOW", "fresh": True,
                    "transmitted": False, "intent": "HOVER", "velocity": [0, 0, 0],
                    "projected_intent": "MOVE_RIGHT", "projected_velocity": [0, 0.8, 0],
                    "authority": "BLOCKED", "authority_reason": "PX4_DISARMED"}
                metadata.packet["command"].update(
                    limiter=["L", 3, 0.5, 0.5, 0.3])
                station.last_ui_at = 0
                station.refresh()
                self.assertEqual(station.nodes["PX4"].value.text(), "CONNECTED")
                self.assertEqual(station.px4_card.rows["Mode"].text(), "AUTO_LOITER")
                self.assertEqual(station.px4_card.rows["Local Pos"].text(), "INVALID")
                self.assertIn("NOT TRANSMITTED", station.command_value.text())
                self.assertIn("MOVE_RIGHT", station.command_value.text())
                self.assertFalse(station.engineering.isVisible())
                self.assertTrue(all(not button.isEnabled()
                                    for button in station.flight_buttons.values()))
                station.toggle_engineering()
                self.app.processEvents()
                self.assertTrue(station.engineering.isVisible())
                self.assertEqual(station.engineering.fields["People count"].text(), "0")
                self.assertEqual(station.engineering.fields["Stage6 intent"].text(), "HOVER")
                self.assertEqual(station.engineering.fields["Command authority"].text(), "BLOCKED")
                self.assertEqual(station.nodes["SAFETY"].value.text(), "SAFE HOVER")
                self.assertEqual(station.engineering.fields["Limiter state"].text(), "LIMIT_REACHED")
                self.assertEqual(station.engineering.fields["Episode limit"].text(), "0.5 m")
                self.assertEqual(station.engineering.fields["Packet bytes"].text(), "--")
                station.toggle_engineering()
                self.app.processEvents()
                self.assertFalse(station.engineering.isVisible())
                for width, height in ((1920, 1080), (1463, 914), (1366, 768)):
                    station.resize(width, height)
                    self.app.processEvents()
                    self.assertTrue(station.system_label.isVisible())
                    self.assertTrue(station.canvas.isVisible())
                    self.assertGreater(station.canvas.width(), 480)
                    self.assertGreater(station.canvas.height(), 320)
                metadata.age = 700
                station.last_ui_at = 0
                station.refresh()
                self.assertEqual(station.nodes["PERCEPTION"].value.text(), "METADATA LOST")
                self.assertEqual(station.nodes["GESTURE"].value.text(), "--")
                self.assertEqual(station.nodes["PX4"].value.text(), "DISCONNECTED")
                self.assertNotIn("MOVE_RIGHT", station.command_value.text())
            finally:
                station.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
