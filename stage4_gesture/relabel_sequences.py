from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


LABELS = {"LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER", "UNKNOWN", "INVALID"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relabel(path: Path, new_label: str, apply: bool = False) -> dict:
    new_label = new_label.upper()
    if new_label not in LABELS:
        raise ValueError(f"unsupported label: {new_label}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"empty sequence: {path}")
    records = [json.loads(line) for line in lines]
    old_labels = {record.get("ground_truth_label") for record in records}
    if len(old_labels) != 1:
        raise ValueError(f"mixed/missing labels in {path}: {sorted(str(v) for v in old_labels)}")
    old_label = next(iter(old_labels))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
    target_name = path.name.replace(f"_{old_label}.jsonl", f"_{new_label}.jsonl")
    if target_name == path.name:
        target_name = f"{path.stem}__RELABEL_{new_label}.jsonl"
    target = path.with_name(target_name)
    backup = path.parent / "relabel_backup" / f"{path.stem}.before_{stamp}.jsonl"
    result = {
        "status": "PREVIEW" if not apply else "APPLIED",
        "original_path": str(path),
        "target_path": str(target),
        "backup_path": str(backup),
        "old_label": old_label,
        "new_label": new_label,
        "frame_count": len(records),
        "sha256_before": sha256(path),
        "timestamp_utc": stamp,
    }
    if not apply:
        return result
    if target.exists() and target.resolve() != path.resolve():
        raise FileExistsError(f"target already exists: {target}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    for record in records:
        record["ground_truth_label"] = new_label
        record["relabel"] = {
            "old_label": old_label,
            "new_label": new_label,
            "timestamp_utc": stamp,
            "reason": "operator_correction",
        }
    temporary = path.with_suffix(path.suffix + ".relabel.tmp")
    temporary.write_text(
        "".join(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(path)
    if target.resolve() != path.resolve():
        path.replace(target)
    result["sha256_after"] = sha256(target)
    with (target.parent / "relabel_audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Auditable Stage 4 sequence ground-truth correction")
    parser.add_argument("--label", required=True, choices=sorted(LABELS))
    parser.add_argument("--apply", action="store_true", help="Without this flag, only preview changes")
    parser.add_argument("sequences", type=Path, nargs="+")
    args = parser.parse_args()
    results = [relabel(path.resolve(), args.label, args.apply) for path in args.sequences]
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

