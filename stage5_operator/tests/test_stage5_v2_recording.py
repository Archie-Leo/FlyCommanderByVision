from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from stage5_v2.recording import DiagnosticRecorder


class DiagnosticRecordingTests(unittest.TestCase):
    def test_recorded_video_timestamps_and_lossless_sbs_are_paired(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = DiagnosticRecorder(Path(tmp), raw_stereo=True, playback_fps=12)
            annotated = np.full((48, 64, 3), 70, np.uint8)
            raw = np.arange(48 * 128 * 3, dtype=np.uint8).reshape(48, 128, 3)
            clip = recorder.start(annotated, {"calibration_path": "/runB/calibration.yaml"})
            for index in range(3):
                recorder.write(annotated, raw, {"frame_id": index, "ui_state": "RED",
                                                "authorized_gesture": {"valid": False}},
                               1_000_000_000 + index * 90_000_000)
            summary = recorder.stop(reason="USER_STOP")
            self.assertEqual(summary["status"], "COMPLETE")
            manifest = json.loads((clip / "manifest.json").read_text())
            self.assertEqual(manifest["frames_written"], 3)
            self.assertEqual(manifest["video_playback_fps"], 12)
            self.assertEqual(manifest["calibration_path"], "/runB/calibration.yaml")
            entries = [json.loads(line) for line in (clip / "frames.jsonl").read_text().splitlines()]
            self.assertEqual([entry["recording_frame_index"] for entry in entries], [0, 1, 2])
            self.assertEqual(entries[1]["capture_monotonic_ns"] - entries[0]["capture_monotonic_ns"], 90_000_000)
            self.assertEqual(entries[2]["diagnostics"]["frame_id"], 2)
            self.assertTrue(np.array_equal(cv2.imread(str(clip / entries[0]["raw_sbs_png"])), raw))
            video = cv2.VideoCapture(str(clip / "annotated.avi"))
            count = 0
            while video.read()[0]:
                count += 1
            video.release()
            self.assertEqual(count, 3)

    def test_multiple_clips_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = DiagnosticRecorder(Path(tmp))
            frame = np.zeros((48, 64, 3), np.uint8)
            first = recorder.start(frame, {})
            recorder.write(frame, frame, {"frame_id": 1}, 1)
            recorder.stop()
            second = recorder.start(frame, {})
            recorder.write(frame, frame, {"frame_id": 2}, 2)
            recorder.stop()
            self.assertNotEqual(first, second)
            self.assertEqual(len(recorder.clips), 2)
            self.assertFalse((first / "raw_sbs_png").exists())

    def test_invalid_data_rejected_without_nan_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = DiagnosticRecorder(Path(tmp))
            frame = np.zeros((48, 64, 3), np.uint8)
            clip = recorder.start(frame, {})
            with self.assertRaises(ValueError):
                recorder.write(frame, frame, {"bad": float("nan")}, 100)
            recorder.stop(reason="ERROR")
            self.assertEqual((clip / "frames.jsonl").read_text(), "")


if __name__ == "__main__":
    unittest.main()
