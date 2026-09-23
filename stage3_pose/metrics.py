from __future__ import annotations

import statistics
from typing import Dict, Iterable, List

import numpy as np


class RuntimeMetrics:
    def __init__(self):
        self.values: Dict[str, List[float]] = {
            "camera_read_ms": [], "pose_inference_ms": [], "normalization_ms": [],
            "total_processing_ms": [], "frame_interval_ms": []
        }
        self._last_timestamp_ns = None

    def add_frame_timestamp(self, timestamp_ns: int):
        if self._last_timestamp_ns is not None:
            self.values["frame_interval_ms"].append((timestamp_ns - self._last_timestamp_ns) / 1e6)
        self._last_timestamp_ns = timestamp_ns

    def add(self, name: str, value: float):
        self.values[name].append(float(value))

    def report(self):
        result = {}
        for name, values in self.values.items():
            if not values:
                result[name] = {"count": 0, "mean": None, "median": None, "p95": None}
                continue
            result[name] = {
                "count": len(values), "mean": statistics.fmean(values),
                "median": statistics.median(values), "p95": float(np.percentile(values, 95))
            }
        intervals = self.values["frame_interval_ms"]
        result["camera_fps_from_intervals"] = 1000.0 / statistics.fmean(intervals) if intervals else None
        return result

