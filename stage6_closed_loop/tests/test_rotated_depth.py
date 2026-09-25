import unittest
from types import SimpleNamespace

import numpy as np

from stage6.rotated_depth import RotatedDepthAdapter


class RotatedDepthTests(unittest.TestCase):
    def test_upright_box_maps_back_to_original_stereo(self):
        class Source:
            maps = (object(), object())
            calibration_path = "run_b.yaml"
            last_latency_ms = 3.0
            def process(self, left, right, boxes, *, rectified_left=None):
                self.boxes = boxes
                self.rectified_left = rectified_left
                return rectified_left, ["depth"] * len(boxes)

        source = Source()
        adapter = RotatedDepthAdapter(source)
        original = np.zeros((96, 128, 3), dtype=np.uint8)
        upright = original.copy()
        adapter.prepare(original, upright)
        image, depths = adapter.process(original, original,
                                        [(10, 20, 50, 70)], rectified_left=upright)
        self.assertIs(image, upright)
        self.assertIs(source.rectified_left, original)
        self.assertEqual(source.boxes, [(78, 26, 118, 76)])
        self.assertEqual(depths, ["depth"])
        self.assertEqual(adapter.last_latency_ms, 3.0)

    def test_unprepared_frame_fails_closed(self):
        source = SimpleNamespace(maps=(None, None), calibration_path="run_b.yaml")
        adapter = RotatedDepthAdapter(source)
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        with self.assertRaises(RuntimeError):
            adapter.process(frame, frame, [], rectified_left=frame)


if __name__ == "__main__":
    unittest.main()
