"""Bounded preview handoff checks; no camera, ROS, or PX4 required."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.preview_stage4_web import PreviewState, run_render


class PreviewStateTests(unittest.TestCase):
    def test_latest_analysis_overwrites_old_and_keeps_timestamp(self):
        state = PreviewState()
        for frame_id in range(1000):
            state.publish_analysis((frame_id,), {
                "frame_id": frame_id,
                "capture_timestamp_monotonic_ns": frame_id * 1000,
            })
        self.assertEqual(state.analysis, (999,))
        self.assertEqual(state.analysis_sequence, 1000)
        self.assertEqual(state.status["frame_id"], 999)
        self.assertEqual(state.status["capture_timestamp_monotonic_ns"], 999000)
        self.assertFalse(hasattr(state, "queue"))

    def test_display_timestamp_is_not_replaced_by_new_analysis(self):
        state = PreviewState()
        state.publish_analysis((1,), {"frame_id": 1,
                                      "capture_timestamp_monotonic_ns": 1000})
        state.publish(b"jpeg", {"display_frame_id": 1,
                                "capture_timestamp_monotonic_ns": 1000,
                                "frame_ready_monotonic_ns": 2000,
                                "host_receipt_age_at_publish_ms": 0.001})
        state.publish_analysis((2,), {"frame_id": 2,
                                      "capture_timestamp_monotonic_ns": 3000})
        self.assertEqual(state.jpeg_capture_ns, 1000)
        self.assertEqual(state.status["capture_timestamp_monotonic_ns"], 3000)
        self.assertEqual(state.status["display_capture_timestamp_monotonic_ns"], 1000)
        self.assertEqual(state.sequence, 1)

    def test_render_exception_does_not_stop_vision(self):
        state = PreviewState()
        args = SimpleNamespace(display_mirror=False, display_width=640)
        with patch("scripts.preview_stage4_web.draw_panel", side_effect=RuntimeError("render failed")):
            thread = threading.Thread(target=run_render, args=(args, state))
            thread.start()
            state.publish_analysis((None, None, None, None, None,
                                    0, 0, 0, 0, 1000), {"frame_id": 1})
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(state.render_error, "render failed")
        self.assertFalse(state.stopped)


if __name__ == "__main__":
    unittest.main()
