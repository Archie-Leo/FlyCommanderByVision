import unittest
from types import SimpleNamespace

import numpy as np

from stage5_v2.pipeline import Stage5PipelineV2


class DepthSchedulingTests(unittest.TestCase):
    def test_untracked_pose_does_not_run_disparity(self):
        class Depth:
            last_latency_ms = 0.0
            def __init__(self):
                self.box_calls = []
            def process(self, left, right, boxes, *, rectified_left=None):
                self.box_calls.append(list(boxes))
                return rectified_left, []
        class Embedder:
            last_latency_ms = 0.0
            def extract(self, frame, box):
                return (1.0, 0.0, 0.0), 1.0
        class Tracker:
            last_latency_ms = 0.0
            def update(self, frame, boxes, scores, vectors):
                return []
        depth = Depth()
        pipeline = Stage5PipelineV2(Tracker(), Embedder(), depth)
        frame = np.zeros((96, 128, 3), dtype=np.uint8)
        pose = SimpleNamespace(bbox_xyxy=(10, 10, 60, 80), pose_score=.9)
        pose_frame = SimpleNamespace(poses=[pose], timestamp_ms=100, frame_id=1)
        _, people, _, _ = pipeline.process(frame, frame, pose_frame,
            SimpleNamespace(), SimpleNamespace(), rectified_left=frame)
        self.assertEqual(people, [])
        self.assertEqual(depth.box_calls, [[], []])


if __name__ == "__main__":
    unittest.main()
