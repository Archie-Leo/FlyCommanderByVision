from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CountdownState:
    display_seconds: Optional[int]
    capture_due: bool


class CaptureCountdown:
    """Non-blocking one-shot countdown driven by monotonic timestamps."""

    def __init__(self, duration_seconds: float = 3.0):
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        self.duration_ns = int(duration_seconds * 1_000_000_000)
        self.deadline_ns: Optional[int] = None

    @property
    def active(self) -> bool:
        return self.deadline_ns is not None

    def start(self, now_ns: int) -> bool:
        if self.active:
            return False
        self.deadline_ns = int(now_ns) + self.duration_ns
        return True

    def update(self, now_ns: int) -> CountdownState:
        if self.deadline_ns is None:
            return CountdownState(None, False)
        remaining_ns = self.deadline_ns - int(now_ns)
        if remaining_ns > 0:
            return CountdownState(max(1, math.ceil(remaining_ns / 1_000_000_000)), False)
        self.deadline_ns = None
        return CountdownState(None, True)

