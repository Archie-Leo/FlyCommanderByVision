from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Sequence

from .types import GestureLabel


LEGAL = [
    GestureLabel.LEFT.value,
    GestureLabel.RIGHT.value,
    GestureLabel.ASCEND.value,
    GestureLabel.DESCEND.value,
    GestureLabel.HOVER.value,
]
REJECT = [GestureLabel.UNKNOWN.value, GestureLabel.INVALID.value]


def classification_metrics(truth: Sequence[str], predicted: Sequence[str]) -> Dict[str, Any]:
    if len(truth) != len(predicted):
        raise ValueError("truth and predicted lengths differ")
    labels = LEGAL + REJECT
    matrix = {actual: {guess: 0 for guess in labels} for actual in labels}
    for actual, guess in zip(truth, predicted):
        if actual not in matrix or guess not in matrix[actual]:
            raise ValueError(f"unsupported label pair: {actual}/{guess}")
        matrix[actual][guess] += 1

    per_class: Dict[str, Dict[str, Any]] = {}
    for label in LEGAL:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in labels if other != label)
        fn = sum(matrix[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        per_class[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "support": sum(matrix[label].values()),
        }

    legal_total = sum(sum(matrix[label].values()) for label in LEGAL)
    rejected_legal = sum(matrix[label][reject] for label in LEGAL for reject in REJECT)
    negative_total = sum(sum(matrix[label].values()) for label in REJECT)
    false_triggers = [
        {"index": i, "truth": actual, "predicted": guess}
        for i, (actual, guess) in enumerate(zip(truth, predicted))
        if actual in REJECT and guess in LEGAL
    ]
    return {
        "sample_count": len(truth),
        "labels": labels,
        "confusion_matrix": matrix,
        "per_gesture": per_class,
        "legal_action_rejection_rate": rejected_legal / legal_total if legal_total else None,
        "negative_sample_count": negative_total,
        "false_trigger_count": len(false_triggers),
        "false_triggers": false_triggers,
        "exact_accuracy": sum(a == b for a, b in zip(truth, predicted)) / len(truth) if truth else None,
        "threshold_note": "Metrics are dataset measurements; no official universal PASS threshold is claimed.",
    }

