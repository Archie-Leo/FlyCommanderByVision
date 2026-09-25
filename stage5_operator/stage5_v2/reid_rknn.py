"""RK3576 OSNet x0.25 inference with the same crop contract as Torch Stage5."""
from __future__ import annotations

import hashlib
from pathlib import Path
import threading
import time

import numpy as np

from .reid import normalize_feature, prepare_crop

VALIDATED_RK3576_SHA256 = "b84b904776a33ad3f2dcbd5d113b0e820ae55c236d18a4ad327703fa472ed0f5"


class RKNNOSNetEmbedder:
    dimension = 512

    def __init__(self, model_path: Path, expected_sha256: str | None = None, runtime_factory=None):
        path = Path(model_path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size < 100_000:
            raise FileNotFoundError(f"RKNN OSNet model missing/invalid: {path}")
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 and self.sha256.lower() != expected_sha256.lower():
            raise ValueError(f"RKNN OSNet SHA256 mismatch: {self.sha256}")
        if runtime_factory is None:
            from rknnlite.api import RKNNLite
            runtime_factory = RKNNLite
        self.runtime = runtime_factory()
        self.lock = threading.Lock()
        self.last_latency_ms = 0.0
        self.last_preprocess_ms = 0.0
        self.last_inference_ms = 0.0
        self.last_postprocess_ms = 0.0
        try:
            if self.runtime.load_rknn(str(path)) != 0:
                raise RuntimeError("RKNN OSNet model load failed")
            if self.runtime.init_runtime() != 0:
                raise RuntimeError("RKNN OSNet runtime initialization failed")
        except BaseException:
            self.close()
            raise

    def extract(self, frame, bbox):
        with self.lock:
            if self.runtime is None:
                raise RuntimeError("RKNN OSNet runtime closed")
            start = time.perf_counter()
            data, quality = prepare_crop(frame, bbox)
            prep_end = time.perf_counter()
            self.last_preprocess_ms = (prep_end - start) * 1000
            self.last_inference_ms = 0.0
            self.last_postprocess_ms = 0.0
            self.last_latency_ms = self.last_preprocess_ms
            if data is None:
                return None, quality
            # RKNN model expects normalized float32 NHWC, batch size one.
            tensor = np.ascontiguousarray(data[None], dtype=np.float32)
            infer_start = time.perf_counter()
            outputs = self.runtime.inference(inputs=[tensor])
            infer_end = time.perf_counter()
            self.last_inference_ms = (infer_end - infer_start) * 1000
            if outputs is None or len(outputs) != 1:
                raise RuntimeError("RKNN OSNet returned no unique feature output")
            try:
                feature = normalize_feature(outputs[0], self.dimension)
            except ValueError as exc:
                raise RuntimeError(f"RKNN OSNet invalid feature: {exc}") from exc
            end = time.perf_counter()
            self.last_postprocess_ms = (end - infer_end) * 1000
            self.last_latency_ms = (end - start) * 1000
            return feature, quality

    def close(self):
        with self.lock:
            runtime, self.runtime = self.runtime, None
            if runtime is not None:
                runtime.release()
