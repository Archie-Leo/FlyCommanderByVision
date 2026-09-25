#!/usr/bin/env python3
"""On-device OSNet crop equivalence and integrated RKNN latency evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_operator"))
from stage5_v2.reid import OSNetEmbedder
from stage5_v2.reid_rknn import RKNNOSNetEmbedder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--mot-root", type=Path, help="existing MOT17-mini sequence root with gt/gt.txt and img1")
    parser.add_argument("--torchreid-root", type=Path, default=Path.home() / "fcv_third_party/deep-person-reid")
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "models/reid/osnet_x0_25_msmt17.pth")
    parser.add_argument("--rknn-model", type=Path, default=ROOT / "models/reid/rk3576/osnet_x0_25_msmt17_fp16.rknn")
    parser.add_argument("--iterations", type=int, default=220)
    args = parser.parse_args()
    paths = sorted(args.images.rglob("*.jpg"))
    if not paths and args.mot_root is None:
        raise SystemExit("BLOCKED_REAL_PERSON_DATA: no JPG images")
    torch = OSNetEmbedder(args.checkpoint, args.torchreid_root)
    rknn = RKNNOSNetEmbedder(args.rknn_model)
    names, torch_vectors, rknn_vectors, cosines = [], [], [], []
    crops = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is not None:
            frame = cv2.copyMakeBorder(image, 4, 4, 4, 4, cv2.BORDER_REFLECT_101)
            crops.append((path.name, frame, (4, 4, 4 + image.shape[1], 4 + image.shape[0])))
    if args.mot_root is not None:
        root = args.mot_root.expanduser()
        for line in (root / "gt/gt.txt").read_text().splitlines():
            values = line.split(",")
            if len(values) < 9 or int(values[7]) != 1 or float(values[8]) < .5:
                continue
            frame_id, person_id = int(values[0]), int(values[1])
            path = root / "img1" / f"{frame_id:06d}.jpg"
            if not path.is_file():
                continue
            image = cv2.imread(str(path))
            if image is None:
                continue
            x, y, w, h = map(float, values[2:6])
            crops.append((f"{root.name}:{frame_id}:{person_id}", image, (x, y, x+w, y+h)))
            if len(crops) >= 40:
                break
    try:
        for name, frame, bbox in crops:
            a, qa = torch.extract(frame, bbox)
            b, qb = rknn.extract(frame, bbox)
            if a is None or b is None:
                continue
            av, bv = np.asarray(a), np.asarray(b)
            names.append(name)
            torch_vectors.append(av)
            rknn_vectors.append(bv)
            cosines.append(float(np.dot(av, bv)))
        if not cosines:
            raise SystemExit("BLOCKED_REAL_PERSON_DATA: no usable crops")
        pairwise_agreement = None
        if len(cosines) >= 2:
            ta, ra = np.stack(torch_vectors), np.stack(rknn_vectors)
            tri = np.triu_indices(len(cosines), 1)
            torch_pairs, rknn_pairs = (ta @ ta.T)[tri], (ra @ ra.T)[tri]
            pairwise_agreement = {"pearson": float(np.corrcoef(torch_pairs, rknn_pairs)[0, 1]),
                                  "max_abs_delta": float(np.max(np.abs(torch_pairs-rknn_pairs)))}
        _, frame, bbox = crops[0]
        timing = {key: [] for key in ("preprocess_ms", "inference_ms", "postprocess_ms", "total_ms")}
        for _ in range(args.iterations):
            embedding, _ = rknn.extract(frame, bbox)
            if embedding is None:
                raise RuntimeError("Benchmark crop rejected")
            for key, value in (("preprocess_ms", rknn.last_preprocess_ms),
                               ("inference_ms", rknn.last_inference_ms),
                               ("postprocess_ms", rknn.last_postprocess_ms),
                               ("total_ms", rknn.last_latency_ms)):
                timing[key].append(value)
        result = {
            "samples": len(cosines), "filenames": names,
            "cosine": {"mean": float(np.mean(cosines)), "min": float(np.min(cosines)),
                       "p5": float(np.percentile(cosines, 5)), "p50": float(np.median(cosines))},
            "pairwise_similarity_agreement": pairwise_agreement,
            "iterations": args.iterations,
            "latency": {key: {"mean_ms": float(np.mean(values)),
                              "p95_ms": float(np.percentile(values, 95))}
                        for key, values in timing.items()},
            "throughput_fps": 1000 / float(np.mean(timing["total_ms"])),
            "torch_sha256": torch.sha256,
            "rknn_sha256": rknn.sha256,
        }
        print(json.dumps(result, indent=2))
    finally:
        torch.close()
        rknn.close()


if __name__ == "__main__":
    main()
