from __future__ import annotations

from collections import deque

from stage5.appearance import normalize_embedding
from .evidence import similarity


class OperatorReIDGalleryV2:
    """Session-scoped OSNet gallery; update only after high-quality GREEN evidence."""

    def __init__(self, capacity=16, model_version="osnet_x0_25_msmt17"):
        self.entries = deque(maxlen=capacity)
        self.model_version = model_version

    def score(self, embedding):
        candidate = normalize_embedding(embedding)
        if candidate is None or not self.entries:
            return None
        return max(similarity(candidate, item) for item in self.entries)

    def statistics(self, embedding, *, top_k=3, match_threshold=.90):
        """Read-only multi-sample evidence; never let one lucky match suffice."""
        candidate = normalize_embedding(embedding)
        if candidate is None or not self.entries or top_k < 1:
            return None
        values = [similarity(candidate, item) for item in self.entries]
        if any(value is None for value in values):
            return None
        values.sort(reverse=True)
        chosen = values[:min(top_k, len(values))]
        return {"max": values[0], "topk_mean": sum(chosen)/len(chosen),
                "match_count": sum(value >= match_threshold for value in values),
                "sample_count": len(values)}

    def update(self, embedding, *, green, confidence, crop_quality, occlusion,
               margin, conflict, motion_pass, depth_pass):
        if not (green and confidence >= .85 and crop_quality >= .75 and
                occlusion <= .25 and margin >= .10 and not conflict and
                motion_pass and depth_pass):
            return False
        candidate = normalize_embedding(embedding)
        if candidate is None or (self.entries and len(candidate) != len(self.entries[0])):
            return False
        prior = self.score(candidate)
        if prior is not None and (prior < .78 or prior > .998):
            return False
        self.entries.append(candidate)
        return True

    def seed(self, embeddings):
        values = [normalize_embedding(value) for value in embeddings]
        values = [value for value in values if value is not None]
        if len(values) < 6 or len({len(value) for value in values}) != 1:
            return False
        if min(similarity(values[0], value) for value in values[1:]) < .78:
            return False
        self.entries.clear()
        self.entries.extend(values[-self.entries.maxlen:])
        return True
