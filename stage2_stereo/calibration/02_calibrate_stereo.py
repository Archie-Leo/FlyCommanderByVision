#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import replace

from calibration.calibrator import CalibrationError, print_result_summary, run_calibration
from calibration.config import CalibrationConfig
from calibration.dataset import resolve_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate a saved full-resolution stereo capture session.")
    parser.add_argument("--session", default="latest", help="Session name/path or 'latest' (default).")
    parser.add_argument("--min-pairs", type=int, default=12, help="Engineering minimum valid pair count; not an OpenCV official threshold.")
    parser.add_argument("--alpha", type=float, default=0.0, help="stereoRectify alpha in [0,1].")
    parser.add_argument(
        "--exclude-pairs",
        nargs="*",
        type=int,
        default=[],
        metavar="INDEX",
        help="Explicit pair indices to exclude from optimization, e.g. --exclude-pairs 4 27. Captures are never modified.",
    )
    parser.add_argument(
        "--output-tag",
        help="Required label for a comparison run; output becomes <session>__<tag> and existing results are never overwritten.",
    )
    args = parser.parse_args()
    if args.min_pairs < 4:
        parser.error("--min-pairs must be at least 4; 20-30 varied pairs remain the collection recommendation.")
    if not 0.0 <= args.alpha <= 1.0:
        parser.error("--alpha must be between 0 and 1.")
    if any(index <= 0 for index in args.exclude_pairs):
        parser.error("--exclude-pairs indices must be positive integers.")
    if len(args.exclude_pairs) != len(set(args.exclude_pairs)):
        parser.error("--exclude-pairs contains duplicate indices.")
    if args.output_tag and not re.fullmatch(r"[A-Za-z0-9_.-]+", args.output_tag):
        parser.error("--output-tag may contain only letters, digits, dot, underscore, and hyphen.")
    config = replace(CalibrationConfig(), min_valid_pairs=args.min_pairs, rectify_alpha=args.alpha)
    try:
        session = resolve_session(args.session)
        output_name = f"{session.root.name}__{args.output_tag}" if args.output_tag else None
        output_dir = run_calibration(
            session,
            config,
            excluded_pair_indices=args.exclude_pairs,
            output_name=output_name,
        )
        print_result_summary(output_dir)
        return 0
    except (FileNotFoundError, ValueError, CalibrationError) as exc:
        print(f"CALIBRATION REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
