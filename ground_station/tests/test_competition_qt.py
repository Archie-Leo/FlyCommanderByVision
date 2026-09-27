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
                self.assertFalse(station.engineering.isVisible())
                self.assertTrue(all(not button.isEnabled()
                                    for button in station.flight_buttons.values()))
                station.toggle_engineering()
                self.app.processEvents()
                self.assertTrue(station.engineering.isVisible())
                self.assertEqual(station.engineering.fields["People count"].text(), "0")
                self.assertEqual(station.engineering.fields["Stage6 intent"].text(), "HOVER")
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
            finally:
                station.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
