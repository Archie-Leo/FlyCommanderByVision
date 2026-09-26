"""Runtime-only asynchronous depth scheduling and identity checks."""
from dataclasses import replace
import threading
import time

import numpy as np

from scripts.async_depth import AsyncTrackedRotatedDepthAdapter
from stage5_v2.depth import PersonDepthV1


class Source:
    depth_rate_hz = 5.
    depth_max_age_ms = 500
    calibration_path = 'fixture'

    def __init__(self, gate=None):
        self.maps = (None, None)
        self.gate = gate
        self.started = threading.Event()
        self.calls = 0
        self.last_profile_ms = {}
        self.last_roi_shapes = []

    def process_roi(self, left, right, boxes, *, rectified_left):
        self.calls += 1
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(2)
        self.last_profile_ms = {'total_depth': 30.}
        self.last_roi_shapes = [(20, 20) for _ in boxes]
        return rectified_left, [PersonDepthV1(1.5, .9, .8, .1, (0., 0., 1.5)) for _ in boxes]


def frame():
    return np.zeros((96, 128, 3), np.uint8)


def submit(adapter, image, track=7, frame_id=1):
    adapter.prepare(image, image)
    return adapter.process_tracked(image, image, [(15., 15., 70., 80.)], [track],
        frame_id=frame_id, timestamp_ms=time.monotonic_ns()//1_000_000,
        rectified_left=image)[1][0]


def wait_until(predicate, timeout=1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.005)
    return False


def test_depth_compute_does_not_block_perception_and_fresh_result_is_bound():
    gate = threading.Event()
    source = Source(gate)
    adapter = AsyncTrackedRotatedDepthAdapter(source)
    image = frame()
    try:
        adapter.set_identity('session-a', 'LOCKED_HIGH')
        started = time.perf_counter()
        assert not submit(adapter, image).available
        assert (time.perf_counter()-started)*1000 < 20
        assert source.started.wait(1)
        assert adapter.status()['depth_task_pending'] == 0
        gate.set()
        assert wait_until(lambda: adapter.status()['depth_completed'] == 1)
        result = submit(adapter, image, frame_id=2)
        assert result.available and result.depth_m == 1.5
        assert adapter.last_depth_sources == [1]
        assert adapter.last_depth_ages_ms[0] <= 500
        with adapter.condition:
            entry = adapter._latest_by_track[7]
            adapter._latest_by_track[7] = replace(entry,
                computed_timestamp_ms=entry.computed_timestamp_ms-501)
        assert not submit(adapter, image, frame_id=3).available
    finally:
        gate.set()
        adapter.close()


def test_late_result_cannot_cross_session_or_missing_track():
    gate = threading.Event()
    source = Source(gate)
    adapter = AsyncTrackedRotatedDepthAdapter(source)
    image = frame()
    try:
        adapter.set_identity('old-session', 'LOCKED_HIGH')
        submit(adapter, image)
        assert source.started.wait(1)
        adapter.set_identity('new-session', 'LOCKED_HIGH')
        gate.set()
        assert wait_until(lambda: adapter.status()['depth_discarded'] >= 1)
        assert not submit(adapter, image, frame_id=2).available
        assert wait_until(lambda: adapter.status()['depth_completed'] >= 1)
        assert submit(adapter, image, frame_id=3).available
        adapter.prepare(image, image)
        adapter.process_tracked(image, image, [], [], frame_id=4,
            timestamp_ms=time.monotonic_ns()//1_000_000, rectified_left=image)
        assert not submit(adapter, image, frame_id=5).available
    finally:
        gate.set()
        adapter.close()


def test_pending_task_is_latest_only_and_worker_failure_clears_depth():
    gate = threading.Event()
    source = Source(gate)
    adapter = AsyncTrackedRotatedDepthAdapter(source)
    image = frame()
    try:
        adapter.set_identity('session-a', 'LOCKED_HIGH')
        submit(adapter, image, frame_id=1)
        assert source.started.wait(1)
        time.sleep(.205)
        submit(adapter, image, frame_id=2)
        time.sleep(.205)
        submit(adapter, image, frame_id=3)
        assert adapter.status()['depth_task_pending'] == 1
        assert adapter.status()['depth_task_replaced'] >= 1
        gate.set()
        assert wait_until(lambda: source.calls >= 2)
        assert wait_until(lambda: adapter.status()['depth_completed'] >= 2)
        assert submit(adapter, image, frame_id=4).available
        assert adapter.last_depth_sources == [3]
    finally:
        gate.set()
        adapter.close()

    class FailingSource(Source):
        def process_roi(self, left, right, boxes, *, rectified_left):
            raise RuntimeError('depth failure')

    failing = AsyncTrackedRotatedDepthAdapter(FailingSource())
    try:
        failing.set_identity('session-a', 'LOCKED_HIGH')
        assert not submit(failing, image).available
        assert wait_until(lambda: failing.status()['depth_error'] is not None)
        assert not submit(failing, image, frame_id=2).available
        assert failing.status()['depth_completed'] == 0
    finally:
        failing.close()
