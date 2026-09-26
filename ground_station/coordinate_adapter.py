"""Display-only mapping: rotated rectified LEFT -> rotated raw LEFT."""
from __future__ import annotations

import math


class RectifiedAnalysisToVideoDisplayAdapter:
    def __init__(self, map_x, map_y):
        if map_x.shape != map_y.shape:
            raise ValueError("rectification maps have different shapes")
        self.map_x, self.map_y = map_x, map_y
        self.height, self.width = map_x.shape

    def point(self, x, y):
        if x is None or y is None or not math.isfinite(x) or not math.isfinite(y):
            return None
        xr, yr = self.width - 1 - x, self.height - 1 - y
        if not (0 <= xr <= self.width - 1 and 0 <= yr <= self.height - 1):
            return None
        x0, y0 = int(xr), int(yr)
        x1, y1 = min(x0 + 1, self.width - 1), min(y0 + 1, self.height - 1)
        dx, dy = xr - x0, yr - y0
        def sample(mapping):
            return ((1-dx)*(1-dy)*float(mapping[y0, x0]) +
                    dx*(1-dy)*float(mapping[y0, x1]) +
                    (1-dx)*dy*float(mapping[y1, x0]) +
                    dx*dy*float(mapping[y1, x1]))
        raw_x, raw_y = sample(self.map_x), sample(self.map_y)
        if not (math.isfinite(raw_x) and math.isfinite(raw_y)):
            return None
        display_x, display_y = self.width - 1 - raw_x, self.height - 1 - raw_y
        if not (0 <= display_x < self.width and 0 <= display_y < self.height):
            return None
        return display_x, display_y

    def bbox(self, box):
        if box is None or len(box) != 4:
            return None
        x1, y1, x2, y2 = box
        if not all(math.isfinite(v) for v in box) or x2 <= x1 or y2 <= y1:
            return None
        # The source bbox is XYXY with an exclusive end; sample all four edges.
        x2, y2 = x2 - 1, y2 - 1
        xm, ym = (x1+x2)/2, (y1+y2)/2
        points = [self.point(x, y) for x, y in ((x1,y1),(xm,y1),(x2,y1),
                 (x1,ym),(x2,ym),(x1,y2),(xm,y2),(x2,y2))]
        points = [p for p in points if p is not None]
        if len(points) < 4:
            return None
        return min(p[0] for p in points), min(p[1] for p in points), \
               max(p[0] for p in points), max(p[1] for p in points)
