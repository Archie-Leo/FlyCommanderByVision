"""One inference to detect vision-venv/RKNN drift; no throughput benchmark."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from rknnlite.api import RKNNLite


def main() -> None:
    model = Path("/usr/share/model/RK3576/mobilenet_v1.rknn")
    if not model.is_file():
        raise FileNotFoundError(model)
    runtime = RKNNLite()
    try:
        if runtime.load_rknn(str(model)) != 0:
            raise RuntimeError("RKNN model load failed")
        if runtime.init_runtime() != 0:
            raise RuntimeError("RKNN runtime init failed")
        outputs = runtime.inference(inputs=[np.zeros((1, 224, 224, 3), dtype=np.uint8)])
        if not outputs or not all(np.isfinite(value).all() for value in outputs):
            raise RuntimeError("RKNN inference returned empty/nonfinite output")
        print("RKNN single inference: PASS; output shapes:",
              [value.shape for value in outputs])
    finally:
        runtime.release()


if __name__ == "__main__":
    main()
