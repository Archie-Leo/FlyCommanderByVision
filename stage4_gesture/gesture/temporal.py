from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .config import TemporalConfig
from .types import GestureCandidate, GestureLabel


class TemporalStabilizer:
    """Short-horizon gesture debouncer; deliberately contains no identity state."""

    def __init__(self, config: Optional[TemporalConfig] = None):
        self.config = config or TemporalConfig()
        self.reset()

    def reset(self) -> None:
        self._last_timestamp_ms: Optional[int] = None
        self._pending_label: Optional[GestureLabel] = None
        self._pending_since_ms: Optional[int] = None
        self._pending_frames = 0
        self._stable_label: Optional[GestureLabel] = None
        self._stable_since_ms: Optional[int] = None
        self._release_since_ms: Optional[int] = None
        self._release_frames = 0

    def update(self, raw: GestureCandidate) -> GestureCandidate:
        now = raw.timestamp_ms
        if self._last_timestamp_ms is not None and now < self._last_timestamp_ms:
            self.reset()
            self._last_timestamp_ms = now
            return replace(
                raw,
                label=GestureLabel.INVALID,
                score=0.0,
                stable=False,
                stable_for_ms=0,
                matched_labels=[],
                reasons=["NON_MONOTONIC_TIMESTAMP"],
            )
        self._last_timestamp_ms = now

        if raw.label == GestureLabel.INVALID:
            self.reset()
            self._last_timestamp_ms = now
            return replace(raw, stable=False, stable_for_ms=0)

        if raw.label.is_legal_gesture:
            self._release_since_ms = None
            self._release_frames = 0
            if self._stable_label == raw.label:
                stable_start = now if self._stable_since_ms is None else self._stable_since_ms
                stable_for = max(0, now - stable_start)
                return replace(raw, stable=True, stable_for_ms=stable_for)

            if self._stable_label is not None and self._stable_label != raw.label:
                self._stable_label = None
                self._stable_since_ms = None
                self._pending_label = None

            if self._pending_label != raw.label:
                self._pending_label = raw.label
                self._pending_since_ms = now
                self._pending_frames = 1
            else:
                self._pending_frames += 1

            pending_start = now if self._pending_since_ms is None else self._pending_since_ms
            elapsed = max(0, now - pending_start)
            if elapsed >= self.config.confirm_ms and self._pending_frames >= self.config.min_confirm_frames:
                self._stable_label = raw.label
                self._stable_since_ms = now
                self._pending_label = None
                self._pending_since_ms = None
                self._pending_frames = 0
                return replace(raw, stable=True, stable_for_ms=0, reasons=raw.reasons + ["TEMPORAL_CONFIRMED"])
            return replace(
                raw,
                label=GestureLabel.UNKNOWN,
                score=0.0,
                stable=False,
                stable_for_ms=0,
                reasons=raw.reasons + ["TEMPORAL_CONFIRMING"],
            )

        self._pending_label = None
        self._pending_since_ms = None
        self._pending_frames = 0
        if self._stable_label is None:
            return replace(raw, stable=False, stable_for_ms=0)

        if self._release_since_ms is None:
            self._release_since_ms = now
            self._release_frames = 1
        else:
            self._release_frames += 1
        elapsed = max(0, now - self._release_since_ms)
        if elapsed >= self.config.release_ms and self._release_frames >= self.config.min_release_frames:
            self._stable_label = None
            self._stable_since_ms = None
            self._release_since_ms = None
            self._release_frames = 0
            return replace(raw, stable=False, stable_for_ms=0, reasons=raw.reasons + ["TEMPORAL_RELEASED"])

        return replace(
            raw,
            label=self._stable_label,
            stable=True,
            stable_for_ms=max(
                0,
                now - (now if self._stable_since_ms is None else self._stable_since_ms),
            ),
            reasons=raw.reasons + ["TEMPORAL_RELEASE_GRACE"],
        )
