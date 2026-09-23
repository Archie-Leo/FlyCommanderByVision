"""Read-only Stage 4 Gate audit of frozen sequence recordings.

Run from the NUC project root. This script does not edit recordings or rules.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from gesture import GeometryGestureRecognizer, TemporalStabilizer


LEGAL = ("LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER")
LABELS = (*LEGAL, "UNKNOWN", "INVALID")


def stats(values):
    if not values:
        return {"count": 0, "min": None, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.95
    low, high = math.floor(position), math.ceil(position)
    p95 = ordered[low] if low == high else ordered[low] * (high - position) + ordered[high] * (position - low)
    return {
        "count": len(values), "min": min(values), "mean": statistics.fmean(values),
        "median": statistics.median(values), "p95": p95, "max": max(values),
    }


def stable_label(value):
    return value["label"] if value["stable"] and value["label"] in LEGAL else None


def inspect_sequence(path, project_root):
    result = {"path": str(path.relative_to(project_root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    records = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception as error:
                    raise ValueError(f"line {line_number}: {error}") from error
    except Exception as error:
        result["error"] = str(error)
        return result
    result["frames"] = len(records)
    if not records:
        result["error"] = "empty sequence"
        return result
    labels = Counter(str(record.get("ground_truth_label")) for record in records)
    result["ground_truth_counts"] = dict(labels)
    if len(labels) != 1 or next(iter(labels)) not in LABELS:
        result["error"] = "mixed or unsupported ground-truth labels"
        return result
    result["truth"] = next(iter(labels))
    try:
        times = [int(record["timestamp_ms"]) for record in records]
        frames = [int(record["frame_id"]) for record in records]
    except Exception as error:
        result["error"] = f"invalid timestamp/frame_id: {error}"
        return result
    result["duration_ms"] = times[-1] - times[0]
    result["timestamp_nonincreasing_pairs"] = sum(b <= a for a, b in zip(times, times[1:]))
    result["frame_id_nonincreasing_pairs"] = sum(b <= a for a, b in zip(frames, frames[1:]))
    result["too_short_under_1000_ms"] = result["duration_ms"] < 1000
    if result["timestamp_nonincreasing_pairs"] or result["frame_id_nonincreasing_pairs"]:
        result["error"] = "non-increasing timestamp/frame_id"
        return result
    recognizer, temporal = GeometryGestureRecognizer(), TemporalStabilizer()
    raw_values, stable_values, guesses = [], [], []
    recorded_raw_mismatch = recorded_stable_mismatch = 0
    for record in records:
        raw_value = recognizer.recognize(record.get("normalized_skeleton"))
        raw = raw_value.to_dict()
        stable = temporal.update(raw_value).to_dict()
        raw_values.append(raw)
        stable_values.append(stable)
        guesses.append(stable_label(stable) or ("INVALID" if raw["label"] == "INVALID" else "UNKNOWN"))
        recorded_raw_mismatch += raw["label"] != record.get("gesture_raw", {}).get("label")
        previous = record.get("gesture_stable", {})
        recorded_stable_mismatch += (stable["label"], stable["stable"]) != (previous.get("label"), previous.get("stable"))
    result["recorded_raw_label_mismatch_frames"] = recorded_raw_mismatch
    result["recorded_stable_state_mismatch_frames"] = recorded_stable_mismatch
    result["raw_counts"] = dict(Counter(item["label"] for item in raw_values))
    result["stable_legal_counts"] = dict(Counter(guess for guess in guesses if guess in LEGAL))
    result["stable_invalid_frames"] = guesses.count("INVALID")
    result["stable_unknown_frames"] = guesses.count("UNKNOWN")
    result["false_legal_frame_counts"] = {
        label: sum(guess == label for guess in guesses) for label in LEGAL
    } if result["truth"] in ("UNKNOWN", "INVALID") else {}
    result["wrong_legal_frame_counts"] = {
        label: sum(guess == label for guess in guesses) for label in LEGAL if label != result["truth"]
    } if result["truth"] in LEGAL else {}
    result["wrong_legal_frame_counts"] = {k: v for k, v in result["wrong_legal_frame_counts"].items() if v}
    result["wrong_legal_confirm_events"] = sum(
        guesses[i] in LEGAL and guesses[i] != result["truth"]
        and (i == 0 or guesses[i - 1] != guesses[i])
        for i in range(len(guesses))
    ) if result["truth"] in LEGAL else 0
    stable_legal = Counter(guess for guess in guesses if guess in LEGAL)
    result["sequence_prediction"] = (
        sorted(stable_legal, key=lambda label: (-stable_legal[label], LABELS.index(label)))[0]
        if stable_legal else ("INVALID" if result["truth"] == "INVALID" else "UNKNOWN")
    )
    result["multiple_stable_legal_labels"] = len(stable_legal) > 1
    result["confirmation_from_first_correct_raw_ms"] = None
    result["confirmation_from_sequence_start_ms"] = None
    result["longest_correct_stable_run_frames"] = 0
    result["longest_correct_stable_run_ms"] = 0
    result["correct_stable_fraction_whole_sequence"] = None
    if result["truth"] in LEGAL:
        target = result["truth"]
        first_raw = next((i for i, item in enumerate(raw_values) if item["label"] == target), None)
        first_stable = next((i for i, guess in enumerate(guesses) if guess == target), None)
        if first_stable is not None:
            result["confirmation_from_sequence_start_ms"] = times[first_stable] - times[0]
            if first_raw is not None:
                result["confirmation_from_first_correct_raw_ms"] = times[first_stable] - times[first_raw]
        result["correct_stable_fraction_whole_sequence"] = guesses.count(target) / len(guesses)
        run_start = None
        for i, guess in enumerate((*guesses, None)):
            if guess == target and run_start is None:
                run_start = i
            elif guess != target and run_start is not None:
                end = i - 1
                result["longest_correct_stable_run_frames"] = max(result["longest_correct_stable_run_frames"], i - run_start)
                result["longest_correct_stable_run_ms"] = max(result["longest_correct_stable_run_ms"], times[end] - times[run_start])
                run_start = None
    releases, censored = [], 0
    for i in range(1, len(records)):
        prior = stable_label(stable_values[i - 1])
        if not prior or raw_values[i - 1]["label"] != prior or raw_values[i]["label"] == prior:
            continue
        for j in range(i, len(records)):
            if stable_label(stable_values[j]) != prior:
                releases.append({"label": prior, "latency_ms": times[j] - times[i], "from_invalid": raw_values[i]["label"] == "INVALID"})
                break
            if raw_values[j]["label"] == prior:
                censored += 1
                break
        else:
            censored += 1
    result["release_samples"] = releases
    result["censored_release_attempts"] = censored
    centers = [r.get("normalized_skeleton", {}).get("body_center_px") for r in records if isinstance(r.get("normalized_skeleton"), dict)]
    center_x = [float(c[0]) for c in centers if isinstance(c, list) and len(c) == 2 and c[0] is not None]
    result["median_body_center_x_px"] = statistics.median(center_x) if center_x else None
    return result


def aggregate(sequences, name):
    valid = [item for item in sequences if "error" not in item]
    matrix = {truth: {guess: 0 for guess in LABELS} for truth in LABELS}
    for item in valid:
        matrix[item["truth"]][item["sequence_prediction"]] += 1
    per_class = {}
    for label in LEGAL:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        support = sum(matrix[label].values())
        per_class[label] = {
            "support": support, "tp": tp, "fp": fp, "fn": support - tp,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / support if support else None,
        }
    legal = [item for item in valid if item["truth"] in LEGAL]
    negative = [item for item in valid if item["truth"] in ("UNKNOWN", "INVALID")]
    release_samples = [sample for item in valid for sample in item["release_samples"]]
    legal_cross_frames = {f"{src}->{dst}": sum(item["wrong_legal_frame_counts"].get(dst, 0) for item in legal if item["truth"] == src)
                          for src in LEGAL for dst in LEGAL if src != dst}
    return {
        "name": name, "sequence_count": len(sequences), "evaluable_sequence_count": len(valid),
        "class_counts": dict(Counter(item["truth"] for item in valid)),
        "sequence_confusion_matrix": matrix, "per_legal_gesture": per_class,
        "overall_legal_sequence_accuracy": sum(matrix[label][label] for label in LEGAL) / len(legal) if legal else None,
        "legal_sequence_count": len(legal), "negative_sequence_count": len(negative),
        "negative_frames": sum(item["frames"] for item in negative),
        "negative_false_legal_frames": {truth: {label: sum(item["false_legal_frame_counts"].get(label, 0) for item in negative if item["truth"] == truth)
                                                for label in LEGAL} for truth in ("UNKNOWN", "INVALID")},
        "negative_false_legal_sequences": {truth: {label: sum(item["false_legal_frame_counts"].get(label, 0) > 0 for item in negative if item["truth"] == truth)
                                                   for label in LEGAL} for truth in ("UNKNOWN", "INVALID")},
        "legal_to_legal_frames": {k: v for k, v in legal_cross_frames.items() if v},
        "legal_to_legal_confirm_events": sum(item["wrong_legal_confirm_events"] for item in legal),
        "legal_to_legal_affected_sequences": [item["path"] for item in legal if item["wrong_legal_confirm_events"]],
        "confirmation_from_first_correct_raw_ms": stats([item["confirmation_from_first_correct_raw_ms"] for item in legal
                                                          if item["confirmation_from_first_correct_raw_ms"] is not None]),
        "confirmation_from_sequence_start_ms": stats([item["confirmation_from_sequence_start_ms"] for item in legal
                                                       if item["confirmation_from_sequence_start_ms"] is not None]),
        "release_latency_ms_measurable_only": stats([sample["latency_ms"] for sample in release_samples]),
        "release_invalid_immediate_count": sum(sample["from_invalid"] and sample["latency_ms"] == 0 for sample in release_samples),
        "censored_release_attempts": sum(item["censored_release_attempts"] for item in valid),
        "longest_correct_stable_run_ms": stats([item["longest_correct_stable_run_ms"] for item in legal]),
        "correct_stable_fraction_whole_sequence": stats([item["correct_stable_fraction_whole_sequence"] for item in legal]),
    }


def main():
    root = Path(__file__).resolve().parents[1]
    pilot = sorted((root / "datasets/20260916_101001_UTC").glob("sequence_*.jsonl"))
    session = root / "datasets/20260923_051541_UTC"
    active = sorted(session.glob("sequence_*.jsonl"))
    excluded = sorted((session / "excluded").glob("sequence_*.jsonl"))
    paths = pilot + active + excluded
    records = {str(path.relative_to(root)): inspect_sequence(path, root) for path in paths}
    hashes = Counter(item["sha256"] for item in records.values())
    pilot_rows = [records[str(p.relative_to(root))] for p in pilot]
    active_rows = [records[str(p.relative_to(root))] for p in active]
    excluded_rows = [records[str(p.relative_to(root))] for p in excluded]
    output = {
        "frozen_code": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [root / "gesture/config.py", root / "gesture/geometry.py", root / "gesture/temporal.py", root / "gesture/types.py"]},
        "cohort_paths": {"pilot": [r["path"] for r in pilot_rows], "validation_active": [r["path"] for r in active_rows],
                         "validation_operator_excluded": [r["path"] for r in excluded_rows]},
        "integrity": {"total_files": len(paths), "parsed": sum("error" not in r for r in records.values()),
                      "corrupted_or_invalid": [r["path"] + ": " + r["error"] for r in records.values() if "error" in r],
                      "empty": sum(r.get("error") == "empty sequence" for r in records.values()),
                      "under_1000_ms": [r["path"] for r in records.values() if r.get("too_short_under_1000_ms")],
                      "duplicate_sha256_groups": sum(count > 1 for count in hashes.values()),
                      "recorded_raw_label_mismatch_frames": sum(r.get("recorded_raw_label_mismatch_frames", 0) for r in records.values()),
                      "recorded_stable_state_mismatch_frames": sum(r.get("recorded_stable_state_mismatch_frames", 0) for r in records.values())},
        "cohorts": {
            "pilot_development": aggregate(pilot_rows, "Pilot / development"),
            "validation_raw": aggregate(active_rows + excluded_rows, "Held-out raw after operator-confirmed relabel of 0017/0018"),
            "validation_excluding_0039_only": aggregate(active_rows + [r for r in excluded_rows if "0040_DESCEND" in r["path"]], "Sensitivity: 0039 excluded, 0040 retained"),
            "validation_operator_curated": aggregate(active_rows, "Operator-curated comparison; 0040 exclusion unverified"),
        },
        "sequences": list(records.values()),
        "limitations": ["No per-frame action onset/offset truth", "No per-sequence physical distance metadata",
                        "No source video in JSONL; visual blur/form cannot be reviewed", "UNKNOWN subtype not encoded in sequence JSONL"],
    }
    target = root / "reports/stage4_final_audit_metrics.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    for key, cohort in output["cohorts"].items():
        print(key, cohort["sequence_count"], cohort["class_counts"], cohort["overall_legal_sequence_accuracy"],
              cohort["legal_to_legal_confirm_events"])
    print("integrity", output["integrity"])


if __name__ == "__main__":
    main()
