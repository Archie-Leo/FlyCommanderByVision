#!/usr/bin/env python3
"""Log-driven ReID window audit; NOT a raw-frame or full auto-FSM replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def longest(rows, threshold, *, require_quality=False):
    run = []
    best = (0, 0)
    for row in rows:
        valid = (row[2] >= threshold and
                 (not require_quality or (row[3] >= .75 and row[4])))
        if valid and (not run or (row[1] == run[-1][1] and
                                  0 < row[0]-run[-1][0] <= 500)):
            run.append(row)
        elif valid:
            run = [row]
        else:
            run = []
        if run:
            best = max(best, (len(run), run[-1][0]-run[0][0]))
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    rows = []
    with args.jsonl.open(encoding="utf-8") as source:
        for line in source:
            frame = json.loads(line)
            if frame.get("ownership_state") != "OPERATOR_LOST":
                continue
            for check in frame.get("acquisition_checks", []):
                value = check.get("old_gallery_similarity")
                if value is not None:
                    rows.append((frame["timestamp_ms"], check["track_id"],
                                 value, check.get("crop_quality", 0.),
                                 check.get("pose_valid", False)))
    print(f"LOST reauthorization checks: {len(rows)}")
    print("Threshold | frames at/above | longest run frames | longest run ms")
    for threshold in (.90, .92, .94, .95, .96):
        count = sum(row[2] >= threshold for row in rows)
        frames, duration = longest(rows, threshold)
        print(f"{threshold:.2f}      | {count:15} | {frames:18} | {duration:14}")
    quality_frames, quality_ms = longest(rows, .94, require_quality=True)
    print(f"0.94 + crop>=.75 + pose valid: {quality_frames} frames / {quality_ms} ms")
    print("ReID-only 8-frame/700-ms window:",
          "EXISTS" if longest(rows, .94)[0] >= 8 and longest(rows, .94)[1] >= 700 else "NONE")
    print("Full auto-reapproval: NOT DETERMINABLE from this V2.1 JSONL; "
          "top-k gallery statistics, independent long-term fused score, and "
          "candidate competition were not logged.")


if __name__ == "__main__":
    main()
