"""Explicit OSNet dependency; no HSV substitution when checkpoint is unavailable."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import time

import cv2
import numpy as np


def prepare_crop(frame, bbox):
    """Return the frozen Stage5 crop quality and normalized RGB HWC input."""
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 24 or y2 - y1 < 64:
        return None, 0.0
    crop = frame[y1:y2, x1:x2]
    quality = min(1.0, (x2-x1)/100.0, (y2-y1)/200.0)
    if min(x1, y1, w-x2, h-y2) < 3:
        quality *= .5
    if cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < 15:
        quality *= .5
    if quality < .25:
        return None, float(quality)
    rgb = cv2.cvtColor(cv2.resize(crop, (128, 256)), cv2.COLOR_BGR2RGB)
    data = rgb.astype(np.float32) / 255.0
    data = (data - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
    return data, float(quality)


def normalize_feature(value, dimension=512):
    value = np.asarray(value).reshape(-1)
    if value.size != dimension or not np.isfinite(value).all():
        raise ValueError("OSNet output must be finite 512-D")
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise ValueError("OSNet output has invalid norm")
    return tuple((value / norm).tolist())


class OSNetEmbedder:
    def __init__(self, checkpoint: Path, torchreid_root: Path):
        self.checkpoint = Path(checkpoint).expanduser().resolve()
        if not self.checkpoint.is_file() or self.checkpoint.stat().st_size < 100_000:
            raise FileNotFoundError(f"Official OSNet checkpoint missing/invalid: {self.checkpoint}")
        import torch
        root = Path(torchreid_root).expanduser().resolve()
        if not (root / "torchreid" / "models" / "osnet.py").is_file():
            raise FileNotFoundError(f"deep-person-reid source missing: {root}")
        # Load the upstream OSNet architecture file directly. Its package
        # __init__ eagerly imports training datasets and unrelated deps.
        spec = importlib.util.spec_from_file_location("stage5_v2_upstream_osnet", root / "torchreid/models/osnet.py")
        if spec is None or spec.loader is None:
            raise ImportError("Cannot load upstream OSNet architecture")
        upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upstream)
        self.torch = torch
        self.model = upstream.osnet_x0_25(num_classes=1000, loss="softmax", pretrained=False)
        # Only a known official checkpoint is acceptable: torch.load uses pickle.
        # The file is user-provisioned from the official model zoo, never an unknown mirror.
        try:
            state = torch.load(self.checkpoint, map_location="cpu", weights_only=True)
        except TypeError as exc:
            raise RuntimeError("PyTorch with weights_only=True is required") from exc
        state = state.get("state_dict", state)
        own = self.model.state_dict()
        matched = {k.removeprefix("module."): v for k, v in state.items()
                   if k.removeprefix("module.") in own and own[k.removeprefix("module.")].shape == v.shape}
        if len(matched) < 100:
            raise ValueError("Checkpoint does not match OSNet x0.25 architecture")
        self.model.load_state_dict(matched, strict=False)
        self.model.eval()
        self.dimension = int(self.model.feature_dim)
        self.sha256 = hashlib.sha256(self.checkpoint.read_bytes()).hexdigest()
        self.last_latency_ms = 0.0

    def extract(self, frame, bbox):
        self.last_latency_ms = 0.0
        data, quality = prepare_crop(frame, bbox)
        if data is None:
            return None, quality
        tensor = self.torch.from_numpy(np.ascontiguousarray(data.transpose(2, 0, 1))[None])
        start = time.perf_counter()
        with self.torch.inference_mode():
            value = self.model(tensor).detach().cpu().numpy().reshape(-1)
        self.last_latency_ms = (time.perf_counter() - start) * 1000.0
        try:
            return normalize_feature(value, self.dimension), quality
        except ValueError:
            return None, 0.0

    def close(self):
        pass
