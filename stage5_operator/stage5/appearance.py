from __future__ import annotations

import math
from collections import deque
from typing import Optional, Sequence


def normalize_embedding(values: Optional[Sequence[float]]) -> Optional[tuple[float, ...]]:
    if values is None or len(values) == 0:
        return None
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in vector):
        return None
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector) if norm > 1e-9 else None


def cosine(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> Optional[float]:
    if a is None or b is None or len(a) != len(b):
        return None
    return float(sum(x * y for x, y in zip(a, b)))


class OperatorReIDGallery:
    """Bounded session-owned appearance memory. Its score never grants authority alone."""

    def __init__(self, capacity: int = 12, model_version: str = "hsv_torso_hist_v1"):
        self.entries: deque[tuple[float, ...]] = deque(maxlen=capacity)
        self.model_version = model_version

    def score(self, embedding: Optional[Sequence[float]]) -> Optional[float]:
        candidate = normalize_embedding(embedding)
        if candidate is None or not self.entries:
            return None
        scores = [cosine(candidate, item) for item in self.entries]
        return max(score for score in scores if score is not None) if scores else None

    def update(self, embedding: Optional[Sequence[float]], *, locked_high: bool,
               crop_quality: float, unique_candidate: bool) -> bool:
        if not locked_high or not unique_candidate or crop_quality < 0.75:
            return False
        candidate = normalize_embedding(embedding)
        if candidate is None or (self.entries and len(candidate) != len(self.entries[0])):
            return False
        existing = self.score(candidate)
        if existing is not None and (existing < 0.82 or existing > 0.998):
            return False
        self.entries.append(candidate)
        return True

    def clear(self) -> None:
        self.entries.clear()


def torso_histogram(frame, bbox) -> tuple[Optional[tuple[float, ...]], float]:
    """Weak HSV appearance proxy, not OSNet nor a biometric identity model."""
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    box_width, box_height = x2 - x1, y2 - y1
    xa = max(0, int(x1 + 0.20 * box_width))
    xb = min(width, int(x1 + 0.80 * box_width))
    ya = max(0, int(y1 + 0.15 * box_height))
    yb = min(height, int(y1 + 0.65 * box_height))
    if xb - xa < 16 or yb - ya < 24:
        return None, 0.0
    crop = frame[ya:yb, xa:xb]
    if crop.size == 0:
        return None, 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [12, 6], [0, 180, 0, 256]).reshape(-1)
    vector = normalize_embedding(hist.tolist())
    quality = min(1.0, (xb - xa) / 40.0, (yb - ya) / 60.0)
    if x1 < 3 or y1 < 3 or x2 > width - 3 or y2 > height - 3:
        quality *= 0.5
    if float(np.std(crop)) < 5.0:
        quality *= 0.5
    return vector, float(quality)
