"""Typed ABI-v2 adapter for frozen BoxMOT native BoT-SORT; no BoxMOT Python stack."""
from __future__ import annotations

import ctypes as c
from pathlib import Path
import time

import numpy as np

F = c.POINTER(c.c_float)
I = c.POINTER(c.c_int64)
U = c.POINTER(c.c_uint8)


class Config(c.Structure):
    _fields_ = [(name, c.c_float) for name in
                ("track_high_thresh", "track_low_thresh", "new_track_thresh")]
    _fields_ += [("track_buffer", c.c_int)]
    _fields_ += [(name, c.c_float) for name in
                 ("match_thresh", "proximity_thresh", "appearance_thresh",
                  "second_match_thresh", "unconfirmed_match_thresh", "unconfirmed_emb_scale")]
    _fields_ += [("cmc_method", c.c_char_p), ("frame_rate", c.c_int),
                 ("fuse_first_associate", c.c_int), ("use_embeddings", c.c_int),
                 ("max_obs", c.c_int), ("asso_func", c.c_char_p)]


class Detections(c.Structure):
    _fields_ = [("abi_version", c.c_int32), ("geometry", F), ("scores", F),
                ("class_ids", I), ("detection_indices", I), ("embeddings", F),
                ("rows", c.c_int64), ("geometry_cols", c.c_int32),
                ("embedding_cols", c.c_int32)]


class Image(c.Structure):
    _fields_ = [("data", U), ("rows", c.c_int32), ("cols", c.c_int32),
                ("channels", c.c_int32)]


class Tracks(c.Structure):
    _fields_ = [("abi_version", c.c_int32), ("geometry", F), ("scores", F),
                ("track_ids", I), ("class_ids", I), ("detection_indices", I),
                ("rows", c.c_int64), ("geometry_cols", c.c_int32)]


class BotSortTrackerAdapter:
    def __init__(self, library_path: Path, frame_rate=30):
        path = Path(library_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"BoxMOT native BoT-SORT library missing: {path}")
        self.lib = c.CDLL(str(path))
        self.lib.boxmot_botsort_create.argtypes = [c.POINTER(Config)]
        self.lib.boxmot_botsort_create.restype = c.c_void_p
        self.lib.boxmot_botsort_destroy.argtypes = [c.c_void_p]
        self.lib.boxmot_botsort_update_v2.argtypes = [c.c_void_p, c.POINTER(Detections),
                                                     c.POINTER(Image), c.POINTER(c.POINTER(Tracks))]
        self.lib.boxmot_botsort_update_v2.restype = c.c_int
        self.lib.boxmot_botsort_result_free_v2.argtypes = [c.POINTER(Tracks)]
        self.lib.boxmot_botsort_last_error.restype = c.c_char_p
        cfg = Config(.6296855, .1014393, .6246494, 40, .7722224, .6084298,
                     .6188819, .2879508, .4114801, 2.5445206, b"sof",
                     frame_rate, 1, 1, 50, b"iou")
        self.handle = self.lib.boxmot_botsort_create(c.byref(cfg))
        if not self.handle:
            raise RuntimeError(self._error())
        self.last_latency_ms = 0.0

    def _error(self):
        raw = self.lib.boxmot_botsort_last_error()
        return raw.decode(errors="replace") if raw else "unknown BoT-SORT error"

    def close(self):
        if self.handle:
            self.lib.boxmot_botsort_destroy(self.handle)
            self.handle = None

    def update(self, image, boxes, scores, embeddings):
        if not self.handle:
            raise RuntimeError("BoT-SORT adapter closed")
        n = len(boxes)
        boxes = np.ascontiguousarray(np.asarray(boxes, dtype=np.float32).reshape(n, 4))
        scores = np.ascontiguousarray(np.asarray(scores, dtype=np.float32).reshape(n))
        indices = np.arange(n, dtype=np.int64)
        classes = np.zeros(n, dtype=np.int64)
        if n:
            embeddings = np.ascontiguousarray(np.asarray(embeddings, dtype=np.float32))
            if embeddings.ndim != 2 or embeddings.shape[0] != n or embeddings.shape[1] == 0:
                raise ValueError("Every tracked person requires an OSNet embedding")
            if not np.isfinite(embeddings).all():
                raise ValueError("Nonfinite OSNet embedding")
        else:
            embeddings = np.empty((0, 0), np.float32)
        image = np.ascontiguousarray(image, dtype=np.uint8)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("BoT-SORT CMC image must be BGR HxWx3")
        det = Detections(2, boxes.ctypes.data_as(F), scores.ctypes.data_as(F),
                         classes.ctypes.data_as(I), indices.ctypes.data_as(I),
                         embeddings.ctypes.data_as(F) if n else F(), n, 4,
                         embeddings.shape[1])
        im = Image(image.ctypes.data_as(U), image.shape[0], image.shape[1], 3)
        result = c.POINTER(Tracks)()
        start = time.perf_counter()
        ok = self.lib.boxmot_botsort_update_v2(self.handle, c.byref(det), c.byref(im), c.byref(result))
        self.last_latency_ms = (time.perf_counter()-start)*1000.0
        if not ok:
            if result:
                self.lib.boxmot_botsort_result_free_v2(result)
            raise RuntimeError(self._error())
        if not result:
            raise RuntimeError("BoT-SORT returned no result")
        try:
            out = result.contents
            if out.abi_version != 2 or out.geometry_cols != 4 or out.rows < 0:
                raise RuntimeError("Invalid BoT-SORT ABI result")
            rows = []
            for i in range(out.rows):
                rows.append({"bbox_xyxy": tuple(float(out.geometry[i*4+j]) for j in range(4)),
                             "confidence": float(out.scores[i]),
                             "track_id": int(out.track_ids[i]),
                             "detection_index": int(out.detection_indices[i])})
            return rows
        finally:
            self.lib.boxmot_botsort_result_free_v2(result)
