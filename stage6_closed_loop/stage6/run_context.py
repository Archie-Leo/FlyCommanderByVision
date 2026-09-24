"""One monotonic time origin and JSON-safe evidence helpers for a Gate 6C run."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def json_safe(value, path=""):
    """Replace nonfinite numeric values with null, explicitly listing their paths."""
    bad = []
    if isinstance(value, float):
        if not math.isfinite(value):
            return None, [path or "$" ]
        return value, bad
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key], found = json_safe(item, f"{path}.{key}" if path else str(key))
            bad.extend(found)
        return result, bad
    if isinstance(value, (list, tuple)):
        result = []
        for index, item in enumerate(value):
            clean, found = json_safe(item, f"{path}[{index}]")
            result.append(clean)
            bad.extend(found)
        return result, bad
    return value, bad


def write_jsonl(handle, record: dict):
    clean, bad = json_safe(record)
    if bad:
        clean["nonfinite_fields"] = bad
    handle.write(json.dumps(clean, ensure_ascii=False, allow_nan=False) + "\n")
    handle.flush()


class RunContext:
    def __init__(self, run_dir: Path, *, t0_monotonic_ns: int | None = None,
                 started_utc: str | None = None):
        self.run_dir = Path(run_dir)
        self.t0_monotonic_ns = (time.monotonic_ns() if t0_monotonic_ns is None
                                else t0_monotonic_ns)
        if self.t0_monotonic_ns <= 0:
            raise ValueError("t0_monotonic_ns must be positive")
        self.started_utc = started_utc or utc_now()

    def stamp(self, timestamp_ns: int | None = None) -> dict:
        now = time.monotonic_ns() if timestamp_ns is None else timestamp_ns
        if now < self.t0_monotonic_ns:
            raise ValueError("timestamp precedes run start")
        return {"host_monotonic_ns": now,
                "run_elapsed_ms": (now - self.t0_monotonic_ns) / 1_000_000}

    def manifest(self, **fields):
        record = {"schema_version": "Stage6Gate6CRunV1",
                  "started_utc": self.started_utc,
                  "t0_monotonic_ns": self.t0_monotonic_ns,
                  "time_alignment": "host monotonic receive/capture time; PX4 timestamp is a separate clock domain",
                  **fields}
        clean, bad = json_safe(record)
        if bad:
            clean["nonfinite_fields"] = bad
        (self.run_dir / "run_manifest.json").write_text(
            json.dumps(clean, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8")
        return clean
