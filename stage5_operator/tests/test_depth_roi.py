import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from stage5_v2.depth import (DepthCacheEntry, INVALID_DEPTH, PersonDepthV1,
    RunBSGBMConfig, StereoPersonDepthAdapter, stereo_roi_bounds,
    summarize_torso_depth, torso_bounds)


class GeometryTests(unittest.TestCase):
    def test_torso_and_clamp(self):
        self.assertEqual(torso_bounds((0, 0, 100, 100), 100, 100), (32, 22, 68, 62))
        self.assertIsNone(torso_bounds((10, 10, 10, 100), 100, 100))
        self.assertIsNone(torso_bounds((float('nan'), 0, 100, 100), 100, 100))
        bounds = stereo_roi_bounds((-20, -10, 300, 500), 1280, 960, RunBSGBMConfig())
        self.assertEqual(bounds[0], 0)
        self.assertGreaterEqual(bounds[1], 0)
        self.assertTrue(0 < bounds[2] <= 1280 and 0 < bounds[3] <= 960)

    def test_search_margin_covers_disparity(self):
        cfg = RunBSGBMConfig()
        bbox = (400, 100, 800, 900)
        x0, y0, x1, y1 = stereo_roi_bounds(bbox, 1280, 960, cfg)
        torso = torso_bounds(bbox, 1280, 960)
        self.assertGreaterEqual(torso[0]-x0, cfg.num_disparities)
        self.assertLess(x0, torso[0])
        self.assertGreater(x1, torso[2])
        self.assertTrue(y0 <= torso[1] < torso[3] <= y1)

    def test_too_small_and_invalid_disparity(self):
        self.assertIsNone(stereo_roi_bounds((3, 3, 4, 4), 1280, 960, RunBSGBMConfig()))
        points = np.zeros((100, 100, 3), np.float32)
        valid = np.zeros((100, 100), bool)
        self.assertFalse(summarize_torso_depth(points, valid, (0, 0, 100, 100)).available)
        points[:] = np.nan
        valid[:] = True
        self.assertFalse(summarize_torso_depth(points, valid, (0, 0, 100, 100)).available)

    def test_median_aggregation(self):
        points = np.zeros((100, 100, 3), np.float32)
        points[:, :, 2] = 1500
        points[25, 35, 2] = 4000
        valid = np.ones((100, 100), bool)
        value = summarize_torso_depth(points, valid, (0, 0, 100, 100))
        self.assertEqual(value.depth_m, 1.5)


class CacheTests(unittest.TestCase):
    def make_adapter(self):
        adapter = StereoPersonDepthAdapter.__new__(StereoPersonDepthAdapter)
        adapter.depth_rate_hz = 5.
        adapter.depth_max_age_ms = 500
        adapter.size = (1280, 960)
        adapter._cache = {}
        adapter.last_profile_ms = {}
        adapter.last_roi_shapes = []
        adapter.last_latency_ms = 0.
        adapter.last_depth_ages_ms = []
        adapter.last_depth_sources = []
        adapter.last_depth_valid = []
        adapter.last_updated_count = 0
        adapter.calls = []
        def fake_roi(left, right, boxes, *, rectified_left=None):
            adapter.calls.append(list(boxes))
            values = [PersonDepthV1(float(box[0]/100), 1., 1., 0., (0., 0., float(box[0]/100)))
                      for box in boxes]
            return rectified_left, values
        adapter.process_roi = fake_roi
        return adapter

    def test_rate_fresh_stale_track_binding_and_reordering(self):
        adapter = self.make_adapter()
        frame = np.zeros((960, 1280, 3), np.uint8)
        boxes = [(100, 100, 300, 700), (600, 100, 800, 700)]
        _, first = adapter.process_tracked(frame, frame, boxes, [11, 22],
            frame_id=1, timestamp_ms=1000, rectified_left=frame)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(adapter.calls[0]), 2)
        _, second = adapter.process_tracked(frame, frame, boxes[::-1], [22, 11],
            frame_id=2, timestamp_ms=1030, rectified_left=frame)
        self.assertEqual([p.depth_m for p in second], [first[1].depth_m, first[0].depth_m])
        self.assertEqual(adapter.last_depth_sources, [1, 1])
        self.assertEqual(len(adapter.calls), 1)
        for entry in adapter._cache.values():
            object.__setattr__(entry, 'computed_timestamp_ms', entry.computed_timestamp_ms-600)
        _, third = adapter.process_tracked(frame, frame, boxes[::-1], [22, 11],
            frame_id=3, timestamp_ms=1600, rectified_left=frame)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.last_depth_sources, [3, 3])
        self.assertTrue(all(v.available for v in third))

    def test_track_change_cannot_inherit_depth(self):
        adapter = self.make_adapter()
        frame = np.zeros((960, 1280, 3), np.uint8)
        box = (100, 100, 300, 700)
        adapter.process_tracked(frame, frame, [box], [1], frame_id=1,
                                timestamp_ms=1000, rectified_left=frame)
        adapter.process_tracked(frame, frame, [box], [2], frame_id=2,
                                timestamp_ms=1030, rectified_left=frame)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.last_depth_sources, [2])

    def test_absent_track_and_large_box_jump_force_update(self):
        adapter = self.make_adapter()
        frame = np.zeros((960, 1280, 3), np.uint8)
        box = (100, 100, 300, 700)
        adapter.process_tracked(frame, frame, [box], [1], frame_id=1,
                                timestamp_ms=1000, rectified_left=frame)
        adapter.process_tracked(frame, frame, [], [], frame_id=2,
                                timestamp_ms=1030, rectified_left=frame)
        self.assertEqual(adapter._cache, {})
        adapter.process_tracked(frame, frame, [box], [1], frame_id=3,
                                timestamp_ms=1060, rectified_left=frame)
        self.assertEqual(len(adapter.calls), 2)
        adapter.process_tracked(frame, frame, [(700,100,900,700)], [1], frame_id=4,
                                timestamp_ms=1090, rectified_left=frame)
        self.assertEqual(len(adapter.calls), 3)
        self.assertEqual(adapter.last_depth_sources, [4])
    def test_invalid_update_fails_closed(self):
        adapter = self.make_adapter()
        frame = np.zeros((960, 1280, 3), np.uint8)
        adapter.process_roi = lambda left, right, boxes, *, rectified_left=None: (rectified_left, [INVALID_DEPTH]*len(boxes))
        _, values = adapter.process_tracked(frame, frame, [(100, 100, 300, 700)], [1],
                                           frame_id=1, timestamp_ms=1000, rectified_left=frame)
        self.assertFalse(values[0].available)
        self.assertEqual(adapter.last_depth_valid, [False])
        with self.assertRaises(ValueError):
            adapter.process_tracked(frame, frame, [(0,0,1,1)]*2, [1,1],
                                    frame_id=2, timestamp_ms=1100, rectified_left=frame)


class StereoConsistencyTests(unittest.TestCase):
    def test_full_vs_roi_same_torso_depth(self):
        rng = np.random.default_rng(42)
        height, width, shift = 240, 320, 16
        gray = rng.integers(30, 220, (height, width), dtype=np.uint8)
        left = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        right = np.zeros_like(left)
        right[:, :width-shift] = left[:, shift:]
        xx, yy = np.meshgrid(np.arange(width, dtype=np.float32),
                             np.arange(height, dtype=np.float32))
        adapter = StereoPersonDepthAdapter.__new__(StereoPersonDepthAdapter)
        adapter.size = (width, height)
        adapter.cfg = RunBSGBMConfig()
        adapter.matcher = cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=128, blockSize=5, P1=200, P2=800,
            disp12MaxDiff=1, preFilterCap=63, uniquenessRatio=10,
            speckleWindowSize=100, speckleRange=2,
            mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        q = np.array([[1.,0.,0.,-160.], [0.,1.,0.,-120.],
                      [0.,0.,0.,1000.], [0.,0.,1./65.,0.]])
        adapter.cal = {"Q": q}
        adapter.maps = ((xx, yy), (xx, yy))
        adapter.depth_roi_margin_ratio = .15
        adapter.last_profile_ms = {}
        adapter.last_roi_shapes = []
        bbox = (170, 20, 295, 230)
        _, full = adapter.process(left, right, [bbox], rectified_left=left)
        _, roi = adapter.process_roi(left, right, [bbox], rectified_left=left)
        self.assertTrue(full[0].available)
        self.assertTrue(roi[0].available)
        self.assertLess(abs(full[0].depth_m-roi[0].depth_m), .02)
        self.assertAlmostEqual(roi[0].depth_m, 4.0625, delta=.1)

    def test_matcher_failure_is_invalid(self):
        class FailingMatcher:
            def compute(self, *_):
                raise cv2.error('matcher failed')
        adapter = StereoPersonDepthAdapter.__new__(StereoPersonDepthAdapter)
        adapter.size = (320, 240)
        adapter.cfg = RunBSGBMConfig()
        adapter.matcher = FailingMatcher()
        xx, yy = np.meshgrid(np.arange(320, dtype=np.float32),
                             np.arange(240, dtype=np.float32))
        adapter.maps = ((xx, yy), (xx, yy))
        adapter.cal = {"Q": np.eye(4)}
        adapter.depth_roi_margin_ratio = .15
        adapter.last_profile_ms = {}
        adapter.last_roi_shapes = []
        frame = np.zeros((240, 320, 3), np.uint8)
        _, values = adapter.process_roi(frame, frame, [(170, 20, 295, 230)],
                                        rectified_left=frame)
        self.assertFalse(values[0].available)

    def test_rotated_xyxy_mapping_and_guard(self):
        from scripts.preview_stage5_web import TrackedRotatedDepthAdapter
        class Source:
            maps = (None, None)
            calibration_path = 'run_b.yaml'
            last_latency_ms = 0.
            def process_tracked(self, left, right, boxes, ids, **kwargs):
                self.boxes, self.ids, self.kwargs = boxes, ids, kwargs
                return kwargs['rectified_left'], [INVALID_DEPTH for _ in boxes]
        source = Source()
        adapter = TrackedRotatedDepthAdapter(source)
        original = np.zeros((96, 128, 3), np.uint8)
        upright = original.copy()
        adapter.prepare(original, upright)
        image, values = adapter.process_tracked(original, original,
            [(10,20,50,70),(-5,-3,20,30)], [3,4], frame_id=7,
            timestamp_ms=1234, rectified_left=upright)
        self.assertIs(image, upright)
        self.assertEqual(source.boxes, [(78,26,118,76),(108,66,133,99)])
        self.assertEqual(source.ids, [3,4])
        self.assertIs(source.kwargs['rectified_left'], original)
        with self.assertRaises(RuntimeError):
            adapter.process_tracked(original, original, [], [], frame_id=8,
                                    timestamp_ms=1240, rectified_left=original)


if __name__ == '__main__':
    unittest.main()
