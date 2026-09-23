from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from gesture.evaluation import classification_metrics
from gesture import GeometryGestureRecognizer, TemporalStabilizer


LEGAL = {"LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER"}


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate recorded Stage 4 temporal sequences")
    parser.add_argument("sequences", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Re-run current geometry/temporal code from stored NormalizedSkeletonV1",
    )
    args = parser.parse_args()

    truth, predicted, sequence_reports = [], [], []
    for path in args.sequences:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not records:
            continue
        sequence_truth = records[0]["ground_truth_label"]
        if any(record["ground_truth_label"] != sequence_truth for record in records):
            raise ValueError(f"mixed ground truth labels in {path}")
        start_ms = records[0]["timestamp_ms"]
        first_correct_stable = None
        first_correct_raw = None
        false_trigger_frames = 0
        stable_label_counts = Counter()
        recognizer = GeometryGestureRecognizer() if args.recompute else None
        temporal = TemporalStabilizer() if args.recompute else None
        for record in records:
            if args.recompute:
                raw_value = recognizer.recognize(record.get("normalized_skeleton"))
                stable_value = temporal.update(raw_value)
                raw, stable = raw_value.to_dict(), stable_value.to_dict()
            else:
                stable = record["gesture_stable"]
                raw = record["gesture_raw"]
            if stable["stable"] and stable["label"] in LEGAL:
                guess = stable["label"]
            elif raw["label"] == "INVALID":
                guess = "INVALID"
            else:
                guess = "UNKNOWN"
            truth.append(sequence_truth)
            predicted.append(guess)
            if guess in LEGAL:
                stable_label_counts[guess] += 1
            if sequence_truth in LEGAL and raw["label"] == sequence_truth and first_correct_raw is None:
                first_correct_raw = record["timestamp_ms"]
            if sequence_truth in LEGAL and guess == sequence_truth and first_correct_stable is None:
                first_correct_stable = record["timestamp_ms"]
            if sequence_truth in {"UNKNOWN", "INVALID"} and guess in LEGAL:
                false_trigger_frames += 1
        sequence_reports.append({
            "path": str(path),
            "truth": sequence_truth,
            "frames": len(records),
            "duration_ms": records[-1]["timestamp_ms"] - start_ms,
            "confirmation_from_sequence_start_ms": None if first_correct_stable is None else first_correct_stable - start_ms,
            "confirmation_from_first_correct_raw_ms": None if first_correct_stable is None or first_correct_raw is None else first_correct_stable - first_correct_raw,
            "false_trigger_frames": false_trigger_frames,
            "stable_label_counts": dict(stable_label_counts),
            "confirmed_expected_label": sequence_truth in stable_label_counts if sequence_truth in LEGAL else None,
            "wrong_stable_labels": {
                label: count for label, count in stable_label_counts.items() if label != sequence_truth
            },
        })

    report = classification_metrics(truth, predicted)
    per_gesture_sequence = {}
    for label in sorted(LEGAL):
        positive = [item for item in sequence_reports if item["truth"] == label]
        tp = sum(bool(item["stable_label_counts"].get(label)) for item in positive)
        fn = len(positive) - tp
        fp = sum(
            bool(item["stable_label_counts"].get(label))
            for item in sequence_reports if item["truth"] != label
        )
        latencies = [
            item["confirmation_from_first_correct_raw_ms"] for item in positive
            if item["confirmation_from_first_correct_raw_ms"] is not None
        ]
        per_gesture_sequence[label] = {
            "support_sequences": len(positive),
            "tp_sequences": tp,
            "fp_sequences": fp,
            "fn_sequences": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "confirmation_from_first_correct_raw_ms": {
                "count": len(latencies),
                "mean": statistics.fmean(latencies) if latencies else None,
                "median": statistics.median(latencies) if latencies else None,
                "p95": percentile(latencies, 0.95),
                "max": max(latencies) if latencies else None,
            },
        }
    legal_sequences = [item for item in sequence_reports if item["truth"] in LEGAL]
    negative_sequences = [item for item in sequence_reports if item["truth"] in {"UNKNOWN", "INVALID"}]
    negative_frames = sum(item["frames"] for item in negative_sequences)
    negative_false_frames = sum(item["false_trigger_frames"] for item in negative_sequences)
    report["sequence_level"] = {
        "per_gesture": per_gesture_sequence,
        "legal_sequence_count": len(legal_sequences),
        "legal_sequence_success_rate": (
            sum(bool(item["confirmed_expected_label"]) for item in legal_sequences) / len(legal_sequences)
            if legal_sequences else None
        ),
        "negative_sequence_count": len(negative_sequences),
        "negative_false_trigger_sequence_count": sum(bool(item["stable_label_counts"]) for item in negative_sequences),
        "negative_false_trigger_sequence_rate": (
            sum(bool(item["stable_label_counts"]) for item in negative_sequences) / len(negative_sequences)
            if negative_sequences else None
        ),
        "negative_frame_count": negative_frames,
        "negative_false_trigger_frame_count": negative_false_frames,
        "negative_false_trigger_frame_rate": negative_false_frames / negative_frames if negative_frames else None,
    }
    report["frame_metric_note"] = (
        "Top-level frame metrics include the operator's neutral/setup/transition interval. "
        "Use sequence_level for intended-gesture confirmation and false-trigger acceptance."
    )
    report["recomputed_with_current_code"] = args.recompute
    report["sequences"] = sequence_reports
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(text)


if __name__ == "__main__":
    main()
