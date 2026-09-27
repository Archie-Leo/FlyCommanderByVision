"""Read-only bootstrap checks; no PX4 hardware or publisher is created."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "fcv_boot", Path(__file__).resolve().parents[1] / "fcv_boot.py")
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


class BootChecks(unittest.TestCase):
    def test_sample_fresh(self):
        import time
        message = SimpleNamespace(timestamp=time.time_ns() // 1000, timestamp_sample=0)
        self.assertTrue(boot.sample_fresh((message, time.monotonic_ns())))
        message.timestamp -= 3_000_000
        self.assertFalse(boot.sample_fresh((message, time.monotonic_ns())))

    @unittest.skipIf(os.name == "nt", "Linux /dev path semantics")
    def test_camera_rejects_wrong_usb_identity(self):
        with patch.dict(boot.os.environ, {"CAMERA": "/dev/video73"}), \
             patch.object(boot.Path, "resolve", return_value=Path("/dev/video73")), \
             patch.object(boot, "props", return_value={"ID_VENDOR_ID": "aaaa", "ID_MODEL_ID": "5883"}):
            with self.assertRaisesRegex(RuntimeError, "unexpected camera"):
                boot.camera_device()

    @unittest.skipIf(os.name == "nt", "Linux /dev path semantics")
    def test_serial_rejects_wrong_usb_identity(self):
        with patch.dict(boot.os.environ, {"PX4_SERIAL": "/dev/ttyUSB0"}), \
             patch.object(boot.Path, "resolve", return_value=Path("/dev/ttyUSB0")), \
             patch.object(boot, "props", return_value={"ID_VENDOR_ID": "1a86", "ID_MODEL_ID": "xxxx"}):
            with self.assertRaisesRegex(RuntimeError, "unexpected PX4"):
                boot.serial_device()

    @unittest.skipIf(os.name == "nt", "Linux /dev path semantics")
    def test_camera_requires_capture_mode(self):
        result = SimpleNamespace(returncode=0, stdout="'MJPG' Size: Discrete 1280x480", stderr="")
        with patch.dict(boot.os.environ, {"CAMERA": "/dev/video73"}), \
             patch.object(boot.Path, "resolve", return_value=Path("/dev/video73")), \
             patch.object(boot, "props", return_value={"ID_VENDOR_ID": "0bda", "ID_MODEL_ID": "5883"}), \
             patch.object(boot, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "not MJPG"):
                boot.camera_device()


if __name__ == "__main__":
    unittest.main()
