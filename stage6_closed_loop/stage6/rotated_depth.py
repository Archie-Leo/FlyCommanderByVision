"""Keep stereo depth in Run B coordinates when the analysis image is uprighted."""
from __future__ import annotations


class RotatedDepthAdapter:
    def __init__(self, source):
        self.source = source
        self.maps = source.maps
        self.calibration_path = source.calibration_path
        self._original_left = None
        self._analysis_left = None

    @property
    def last_latency_ms(self):
        return self.source.last_latency_ms

    def prepare(self, original_left, analysis_left):
        if original_left.shape != analysis_left.shape:
            raise ValueError("Rotated left image shape mismatch")
        self._original_left = original_left
        self._analysis_left = analysis_left

    def process(self, left_raw, right_raw, bboxes, *, rectified_left=None):
        if self._original_left is None or rectified_left is not self._analysis_left:
            raise RuntimeError("Rotated depth frame was not prepared")
        height, width = rectified_left.shape[:2]
        source_boxes = [(width-x2, height-y2, width-x1, height-y1)
                        for x1, y1, x2, y2 in bboxes]
        _, depths = self.source.process(left_raw, right_raw, source_boxes,
                                        rectified_left=self._original_left)
        return rectified_left, depths
