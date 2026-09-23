from __future__ import annotations

import math
from dataclasses import dataclass

from .types import BBox, DetectionV1, TrackedPersonV1, finite_bbox


def iou(a: BBox, b: BBox) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - overlap
    return overlap / union if union > 0 else 0.0


def _center(box: BBox) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _assign(costs: list[list[float]], max_cost: float) -> list[tuple[int, int]]:
    """Exact bounded assignment for <=6 people; maximize matches, then minimize cost."""
    if not costs or not costs[0]:
        return []
    best_key: tuple[int, float] = (1, float("inf"))
    best: list[tuple[int, int]] = []

    def walk(row: int, used: int, pairs: list[tuple[int, int]], total: float) -> None:
        nonlocal best_key, best
        if row == len(costs):
            key = (-len(pairs), total)
            if key < best_key:
                best_key, best = key, pairs.copy()
            return
        walk(row + 1, used, pairs, total)
        for col, cost in enumerate(costs[row]):
            if used & (1 << col) or not math.isfinite(cost) or cost > max_cost:
                continue
            pairs.append((row, col))
            walk(row + 1, used | (1 << col), pairs, total + cost)
            pairs.pop()

    walk(0, 0, [], 0.0)
    return best


@dataclass
class _Track:
    track_id: int
    bbox: BBox
    last_seen_ms: int
    age_frames: int = 1
    lost_frames: int = 0
    kf: object = None


class KalmanIoUTwoPassTracker:
    """Small ByteTrack-style high/low IoU baseline using OpenCV Kalman.

    It is intentionally not called BoT-SORT: no GMC or neural ReID exists in this V1.
    The tracker is observation plumbing, never an ownership authority.
    """

    def __init__(self, max_people: int = 6, max_lost_frames: int = 15,
                 high_score: float = 0.5, low_score: float = 0.2):
        self.max_people = max_people
        self.max_lost_frames = max_lost_frames
        self.high_score = high_score
        self.low_score = low_score
        self.tracks: dict[int, _Track] = {}
        self.next_id = 1

    @staticmethod
    def _make_filter(box: BBox):
        import cv2
        import numpy as np

        x, y = _center(box)
        width, height = box[2] - box[0], box[3] - box[1]
        kf = cv2.KalmanFilter(8, 4)
        kf.transitionMatrix = np.eye(8, dtype=np.float32)
        kf.measurementMatrix = np.zeros((4, 8), dtype=np.float32)
        kf.measurementMatrix[:4, :4] = np.eye(4, dtype=np.float32)
        kf.processNoiseCov = np.eye(8, dtype=np.float32) * 0.02
        kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 0.2
        kf.errorCovPost = np.eye(8, dtype=np.float32)
        kf.statePost = np.array([[x], [y], [width], [height], [0], [0], [0], [0]], dtype=np.float32)
        return kf

    @staticmethod
    def _predict(track: _Track, now_ms: int) -> BBox:
        import numpy as np

        dt = max(0.001, min(0.25, (now_ms - track.last_seen_ms) / 1000.0))
        transition = np.eye(8, dtype=np.float32)
        for index in range(4):
            transition[index, index + 4] = dt
        track.kf.transitionMatrix = transition
        state = track.kf.predict().reshape(-1)
        x, y, width, height = [float(value) for value in state[:4]]
        width, height = max(5.0, width), max(5.0, height)
        return (x - width / 2, y - height / 2, x + width / 2, y + height / 2)

    @staticmethod
    def _correct(track: _Track, detection: DetectionV1) -> None:
        import numpy as np

        box = detection.bbox_xyxy
        x, y = _center(box)
        measurement = np.array([[x], [y], [box[2] - box[0]], [box[3] - box[1]]], dtype=np.float32)
        track.kf.correct(measurement)
        track.bbox = box
        track.last_seen_ms = detection.timestamp_ms
        track.lost_frames = 0

    @staticmethod
    def _cost(predicted: BBox, detection: BBox) -> float:
        overlap = iou(predicted, detection)
        ax, ay = _center(predicted)
        bx, by = _center(detection)
        diagonal = max(20.0, math.hypot(predicted[2] - predicted[0], predicted[3] - predicted[1]))
        distance = math.hypot(ax - bx, ay - by) / diagonal
        if overlap < 0.02 and distance > 0.65:
            return float("inf")
        return 0.7 * (1.0 - overlap) + 0.3 * min(1.0, distance)

    def update(self, detections: list[DetectionV1], timestamp_ms: int) -> list[TrackedPersonV1]:
        if not isinstance(timestamp_ms, int):
            raise TypeError("timestamp_ms must be integer")
        detections = [d for d in detections if finite_bbox(d.bbox_xyxy) and math.isfinite(d.confidence)]
        detections = sorted(detections, key=lambda d: d.confidence, reverse=True)[:self.max_people]
        high = [d for d in detections if d.confidence >= self.high_score]
        low = [d for d in detections if self.low_score <= d.confidence < self.high_score]
        predicted: dict[int, BBox] = {}
        for track_id, track in self.tracks.items():
            track.age_frames += 1
            track.lost_frames += 1
            predicted[track_id] = self._predict(track, timestamp_ms)
        used_tracks: set[int] = set()
        outputs: list[TrackedPersonV1] = []

        def match_pool(pool: list[DetectionV1], spawn: bool) -> None:
            candidate_ids = [tid for tid in sorted(self.tracks) if tid not in used_tracks]
            costs = [[self._cost(predicted[tid], det.bbox_xyxy) for det in pool] for tid in candidate_ids]
            matches = _assign(costs, max_cost=0.84)
            used_detections = set()
            for row, col in matches:
                tid, det = candidate_ids[row], pool[col]
                self._correct(self.tracks[tid], det)
                used_tracks.add(tid)
                used_detections.add(col)
                outputs.append(self._output(self.tracks[tid], det))
            if spawn:
                for index, det in enumerate(pool):
                    if index in used_detections:
                        continue
                    tid = self.next_id
                    self.next_id += 1
                    track = _Track(tid, det.bbox_xyxy, det.timestamp_ms, kf=self._make_filter(det.bbox_xyxy))
                    self.tracks[tid] = track
                    used_tracks.add(tid)
                    outputs.append(self._output(track, det))

        match_pool(high, spawn=True)
        match_pool(low, spawn=False)
        for track_id in list(self.tracks):
            if self.tracks[track_id].lost_frames > self.max_lost_frames:
                del self.tracks[track_id]
        return sorted(outputs, key=lambda item: item.track_id)

    @staticmethod
    def _output(track: _Track, detection: DetectionV1) -> TrackedPersonV1:
        return TrackedPersonV1(
            timestamp_ms=detection.timestamp_ms, frame_id=detection.frame_id,
            track_id=track.track_id, bbox_xyxy=detection.bbox_xyxy,
            pose=detection.pose, pose_quality=detection.pose_quality,
            skeleton=detection.skeleton, tracker_confidence=detection.confidence,
            age_frames=track.age_frames, lost_frames=track.lost_frames,
            embedding=detection.embedding, crop_quality=detection.crop_quality,
        )
