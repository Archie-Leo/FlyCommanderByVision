"""Exercise the exact BoxMOT C ABI through the Stage5 adapter when built."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stage5_v2.tracker import BotSortTrackerAdapter


def test_native_botsort_tracks_across_frames():
    library = ROOT / "build/botsort/botsort_capi.so"
    if not library.is_file():
        pytest.skip("exact BoxMOT native library not built on this host")
    tracker = BotSortTrackerAdapter(library)
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    feature = np.full((1, 512), 1 / np.sqrt(512), dtype=np.float32)
    try:
        first = tracker.update(image, [(50, 30, 150, 250)], [.95], feature)
        second = tracker.update(image, [(51, 31, 151, 251)], [.95], feature)
        assert len(first) == len(second) == 1
        for row in (first[0], second[0]):
            assert set(row) == {"bbox_xyxy", "confidence", "track_id", "detection_index"}
            assert row["detection_index"] == 0
            assert row["confidence"] == pytest.approx(.95, abs=1e-6)
        assert first[0]["track_id"] == second[0]["track_id"]
    finally:
        tracker.close()
