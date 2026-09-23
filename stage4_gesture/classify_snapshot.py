from __future__ import annotations

import argparse
import json
from pathlib import Path

from gesture import GeometryGestureRecognizer


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify saved Stage 3 snapshot JSON files")
    parser.add_argument("snapshots", type=Path, nargs="+")
    args = parser.parse_args()
    recognizer = GeometryGestureRecognizer()
    output = {}
    for path in args.snapshots:
        data = json.loads(path.read_text(encoding="utf-8"))
        skeleton = data.get("normalized_skeleton")
        output[str(path)] = recognizer.recognize(skeleton).to_dict()
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
