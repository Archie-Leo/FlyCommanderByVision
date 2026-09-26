"""Bounded asynchronous adapter for the existing Run B tracked ROI depth algorithm.

Only the worker calls StereoPersonDepthAdapter.process_roi. Stage5 sees a fresh,
identity-bound cached observation or the existing INVALID_DEPTH value.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import threading
import time

from stage5_v2.depth import INVALID_DEPTH, _bbox_iou
from scripts.preview_stage5_web import TrackedRotatedDepthAdapter


@dataclass(frozen=True)
class DepthTask:
    epoch: int
    session_id: str | None
    frame_id: int
    timestamp_ms: int
    submitted_ms: int
    track_ids: tuple[int, ...]
    bboxes: tuple[tuple[float, float, float, float], ...]
    source_boxes: tuple[tuple[float, float, float, float], ...]
    left_raw: object
    right_raw: object
    rectified_left: object


@dataclass(frozen=True)
class DepthResult:
    epoch: int
    session_id: str | None
    track_id: int
    bbox: tuple[float, float, float, float]
    source_frame_id: int
    source_timestamp_ms: int
    computed_timestamp_ms: int
    value: object
    quality: float
    valid: bool


class AsyncTrackedRotatedDepthAdapter(TrackedRotatedDepthAdapter):
    """Single worker, one pending task, 5 Hz per track and 500 ms freshness."""

    def __init__(self, source):
        super().__init__(source)
        self.condition = threading.Condition()
        self._pending: DepthTask | None = None
        self._latest_by_track: dict[int, DepthResult] = {}
        self._active_boxes = {}
        self._last_submit_ms = {}
        self._epoch = 0
        self._session_id = None
        self._ownership_state = None
        self._closed = False
        self._error = None
        self._replaced = 0
        self._completed = 0
        self._discarded = 0
        self._last_compute_ms = 0.0
        self._last_profile = {}
        self._last_roi_shapes = []
        self._last_depth_ages_ms = []
        self._last_depth_sources = []
        self._last_depth_valid = []
        self._last_updated_count = 0
        self._completed_samples = deque(maxlen=512)
        self._worker = threading.Thread(target=self._run, name="depth-worker", daemon=True)
        self._worker.start()

    @property
    def last_latency_ms(self):
        # Stage5's synchronous depth field measures only the adapter wait.
        return 0.0

    @property
    def last_profile_ms(self):
        with self.condition:
            return dict(self._last_profile)

    @property
    def last_roi_shapes(self):
        with self.condition:
            return list(self._last_roi_shapes)

    @property
    def last_depth_ages_ms(self):
        return list(self._last_depth_ages_ms)

    @property
    def last_depth_sources(self):
        return list(self._last_depth_sources)

    @property
    def last_depth_valid(self):
        return list(self._last_depth_valid)

    @property
    def last_updated_count(self):
        return self._last_updated_count

    def status(self):
        with self.condition:
            return {"depth_worker_tid": self._worker.native_id,
                    "depth_task_pending": int(self._pending is not None),
                    "depth_task_replaced": self._replaced,
                    "depth_completed": self._completed,
                    "depth_discarded": self._discarded,
                    "depth_compute_ms": self._last_compute_ms,
                    "depth_error": str(self._error) if self._error else None}

    def drain_samples(self):
        with self.condition:
            samples = list(self._completed_samples)
            self._completed_samples.clear()
            return samples

    def set_identity(self, session_id, ownership_state):
        with self.condition:
            entered_lost = (ownership_state in ("OPERATOR_LOST", "WAIT_OPERATOR") and
                            self._ownership_state != ownership_state)
            if session_id != self._session_id or entered_lost:
                self._epoch += 1
                self._pending = None
                self._latest_by_track.clear()
                self._last_submit_ms.clear()
            self._session_id = session_id
            self._ownership_state = ownership_state

    def process(self, left_raw, right_raw, bboxes, *, rectified_left=None):
        if bboxes:
            raise RuntimeError("Async depth requires tracked ROI boxes")
        if self._original_left is None or rectified_left is not self._analysis_left:
            raise RuntimeError("Rotated depth frame was not prepared")
        return rectified_left, []

    def process_tracked(self, left_raw, right_raw, bboxes, track_ids, *,
                        frame_id, timestamp_ms, rectified_left=None):
        if len(bboxes) != len(track_ids) or len(set(track_ids)) != len(track_ids):
            raise ValueError("Depth track IDs must be unique and align with bboxes")
        if self._original_left is None or rectified_left is not self._analysis_left:
            raise RuntimeError("Rotated depth frame was not prepared")
        height, width = rectified_left.shape[:2]
        boxes = tuple(tuple(float(v) for v in box) for box in bboxes)
        source_boxes = tuple((width-x2, height-y2, width-x1, height-y1)
                             for x1, y1, x2, y2 in boxes)
        now_ms = time.monotonic_ns() // 1_000_000
        period_ms = 1000. / self.source.depth_rate_hz
        max_age_ms = self.source.depth_max_age_ms
        results, ages, sources = [], [], []
        update_indices = []
        with self.condition:
            active = set(track_ids)
            self._active_boxes = dict(zip(track_ids, boxes))
            self._latest_by_track = {tid: value for tid, value in self._latest_by_track.items()
                                     if tid in active}
            self._last_submit_ms = {tid: value for tid, value in self._last_submit_ms.items()
                                    if tid in active}
            if self._pending and any(tid not in active for tid in self._pending.track_ids):
                self._pending = None
            for i, (tid, box) in enumerate(zip(track_ids, boxes)):
                cached = self._latest_by_track.get(tid)
                age = now_ms-cached.computed_timestamp_ms if cached else float('inf')
                fresh = bool(cached and cached.epoch == self._epoch and
                             cached.session_id == self._session_id and
                             0 <= age <= max_age_ms and
                             timestamp_ms >= cached.source_timestamp_ms and
                             _bbox_iou(cached.bbox, box) >= .25)
                results.append(cached.value if fresh else INVALID_DEPTH)
                ages.append(float(age) if fresh else None)
                sources.append(cached.source_frame_id if fresh else None)
                last_submit = self._last_submit_ms.get(tid, -float('inf'))
                if (not fresh or age >= period_ms) and now_ms-last_submit >= period_ms:
                    update_indices.append(i)
                    self._last_submit_ms[tid] = now_ms
            self._last_depth_ages_ms = ages
            self._last_depth_sources = sources
            self._last_depth_valid = [value.available for value in results]
            self._last_updated_count = len(update_indices)
            if update_indices and not self._closed and self._error is None:
                task = DepthTask(self._epoch, self._session_id, frame_id, timestamp_ms,
                    now_ms, tuple(track_ids[i] for i in update_indices),
                    tuple(boxes[i] for i in update_indices),
                    tuple(source_boxes[i] for i in update_indices),
                    left_raw, right_raw, self._original_left)
                if self._pending is not None:
                    self._replaced += 1
                self._pending = task
                self.condition.notify()
        return rectified_left, results

    def _run(self):
        while True:
            with self.condition:
                while self._pending is None and not self._closed:
                    self.condition.wait()
                if self._closed:
                    return
                task, self._pending = self._pending, None
            try:
                started = time.perf_counter()
                _, values = self.source.process_roi(task.left_raw, task.right_raw,
                    task.source_boxes, rectified_left=task.rectified_left)
                compute_ms = (time.perf_counter()-started)*1000
                computed_ms = time.monotonic_ns() // 1_000_000
                profile = dict(self.source.last_profile_ms)
                roi_shapes = list(self.source.last_roi_shapes)
            except BaseException as exc:
                with self.condition:
                    self._error = exc
                    self._pending = None
                    self._latest_by_track.clear()
                return
            with self.condition:
                self._last_compute_ms = compute_ms
                self._last_profile = profile
                self._last_roi_shapes = roi_shapes
                if self._closed or task.epoch != self._epoch or task.session_id != self._session_id:
                    self._discarded += len(task.track_ids)
                    continue
                accepted = 0
                for tid, box, value in zip(task.track_ids, task.bboxes, values):
                    current = self._active_boxes.get(tid)
                    if (current is None or _bbox_iou(box, current) < .25 or
                            computed_ms-task.timestamp_ms > self.source.depth_max_age_ms):
                        self._discarded += 1
                        continue
                    self._latest_by_track[tid] = DepthResult(task.epoch, task.session_id,
                        tid, box, task.frame_id, task.timestamp_ms, computed_ms,
                        value, value.depth_quality, value.available)
                    self._completed += 1
                    accepted += 1
                self._completed_samples.append({"source_frame_id": task.frame_id,
                    "source_timestamp_ms": task.timestamp_ms,
                    "computed_timestamp_ms": computed_ms,
                    "compute_ms": compute_ms, "accepted_tracks": accepted,
                    "profile_ms": profile})

    def close(self):
        with self.condition:
            self._closed = True
            self._pending = None
            self._latest_by_track.clear()
            self.condition.notify_all()
        self._worker.join()
