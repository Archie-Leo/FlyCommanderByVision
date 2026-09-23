from __future__ import annotations

import argparse
import json
from pathlib import Path

from gesture import GeometryGestureRecognizer
from gesture.evaluation import classification_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Gesture V1 against a JSONL manifest")
    parser.add_argument("manifest", type=Path, help="JSONL records with path and label")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    recognizer = GeometryGestureRecognizer()
    records, truth, predicted = [], [], []
    for line_number, line in enumerate(args.manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        record = json.loads(line)
        path = Path(record["path"]).expanduser()
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        candidate = recognizer.recognize(snapshot.get("normalized_skeleton"))
        actual = str(record["label"]).upper()
        truth.append(actual)
        predicted.append(candidate.label.value)
        records.append({
            "line": line_number,
            "path": str(path),
            "truth": actual,
            "predicted": candidate.label.value,
            "score": candidate.score,
            "reasons": candidate.reasons,
            "group": record.get("group"),
            "notes": record.get("notes"),
        })

    report = classification_metrics(truth, predicted)
    report["records"] = records
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(text)


if __name__ == "__main__":
    main()
