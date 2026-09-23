from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

from .config import CalibrationConfig, captures_root


PAIR_RE = re.compile(r"^pair_(\d{4})_(left|right|full)\.png$")


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    left: Path
    right: Path
    full: Path
    deleted: Path
    metadata: Path

    @classmethod
    def from_root(cls, root: Path) -> "SessionPaths":
        return cls(root, root / "left", root / "right", root / "full", root / "deleted", root / "metadata.json")

    def ensure(self) -> None:
        for path in (self.root, self.left, self.right, self.full, self.deleted):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class PairPaths:
    index: int
    left: Path
    right: Path
    full: Optional[Path]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_session(config: CalibrationConfig, root: Optional[Path] = None) -> SessionPaths:
    base = root or captures_root()
    session_name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    candidate = base / session_name
    suffix = 1
    while candidate.exists():
        candidate = base / f"{session_name}_{suffix:02d}"
        suffix += 1
    paths = SessionPaths.from_root(candidate)
    paths.ensure()
    metadata = {
        "schema_version": 1,
        "session_id": candidate.name,
        "created_at_utc": utc_now_iso(),
        "camera_device": config.camera_device,
        "raw_image_size": [config.raw_width, config.raw_height],
        "eye_image_size": [config.eye_width, config.eye_height],
        "side_by_side_mapping": {"left": [0, config.eye_width], "right": [config.eye_width, config.raw_width]},
        "pattern_cols": config.pattern_cols,
        "pattern_rows": config.pattern_rows,
        "square_size_mm": config.square_size_mm,
        "pairs": [],
        "deleted_pairs": [],
    }
    atomic_write_json(paths.metadata, metadata)
    return paths


def resolve_session(value: str, root: Optional[Path] = None) -> SessionPaths:
    base = root or captures_root()
    if value != "latest":
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        if not path.is_dir():
            raise FileNotFoundError(f"Capture session does not exist: {path}")
        return SessionPaths.from_root(path.resolve())
    sessions = sorted(p for p in base.glob("*") if p.is_dir() and (p / "metadata.json").is_file())
    if not sessions:
        raise FileNotFoundError(f"No capture sessions found under {base}")
    return SessionPaths.from_root(sessions[-1].resolve())


def atomic_write_json(path: Path, value: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_metadata(paths: SessionPaths) -> Dict:
    try:
        return json.loads(paths.metadata.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing metadata: {paths.metadata}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt metadata JSON: {paths.metadata}: {exc}") from exc


def active_pair_indices(paths: SessionPaths) -> List[int]:
    metadata = load_metadata(paths)
    return sorted(int(item["index"]) for item in metadata.get("pairs", []))


def next_pair_index(paths: SessionPaths) -> int:
    existing = active_pair_indices(paths)
    return (existing[-1] + 1) if existing else 1


def _write_png_atomic(path: Path, image: np.ndarray) -> None:
    temporary = path.with_name(path.stem + ".tmp.png")
    ok = cv2.imwrite(str(temporary), image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise IOError(f"cv2.imwrite failed: {temporary}")
    os.replace(temporary, path)


def save_pair(
    paths: SessionPaths,
    index: int,
    full: np.ndarray,
    left: np.ndarray,
    right: np.ndarray,
    config: CalibrationConfig,
    *,
    monotonic_ns: int,
    wall_time_utc: Optional[str] = None,
    left_descriptor: Optional[np.ndarray] = None,
    right_descriptor: Optional[np.ndarray] = None,
) -> PairPaths:
    paths.ensure()
    if full.shape[:2] != (config.raw_height, config.raw_width):
        raise ValueError(f"Full frame has wrong size: {full.shape}")
    for label, image in (("left", left), ("right", right)):
        if image.shape[:2] != (config.eye_height, config.eye_width):
            raise ValueError(f"{label} image has wrong size: {image.shape}")

    left_path = paths.left / f"pair_{index:04d}_left.png"
    right_path = paths.right / f"pair_{index:04d}_right.png"
    full_path = paths.full / f"pair_{index:04d}_full.png"
    for path in (left_path, right_path, full_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing capture: {path}")

    created: List[Path] = []
    try:
        for path, image in ((left_path, left), (right_path, right), (full_path, full)):
            _write_png_atomic(path, image)
            created.append(path)
        metadata = load_metadata(paths)
        record = {
            "index": index,
            "timestamp_utc": wall_time_utc or utc_now_iso(),
            "monotonic_ns": int(monotonic_ns),
            "raw_image_size": [config.raw_width, config.raw_height],
            "eye_image_size": [config.eye_width, config.eye_height],
            "pattern": [config.pattern_cols, config.pattern_rows],
            "square_size_mm": config.square_size_mm,
            "files": {
                "left": str(left_path.relative_to(paths.root)),
                "right": str(right_path.relative_to(paths.root)),
                "full": str(full_path.relative_to(paths.root)),
            },
            "corner_detector": "findChessboardCornersSB",
            "ordering_validation": "passed",
        }
        if left_descriptor is not None:
            record["left_pose_descriptor"] = np.asarray(left_descriptor).tolist()
        if right_descriptor is not None:
            record["right_pose_descriptor"] = np.asarray(right_descriptor).tolist()
        metadata.setdefault("pairs", []).append(record)
        metadata["pairs"] = sorted(metadata["pairs"], key=lambda item: int(item["index"]))
        atomic_write_json(paths.metadata, metadata)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return PairPaths(index, left_path, right_path, full_path)


def delete_last_pair(paths: SessionPaths) -> Optional[int]:
    metadata = load_metadata(paths)
    pairs = metadata.get("pairs", [])
    if not pairs:
        return None
    record = max(pairs, key=lambda item: int(item["index"]))
    index = int(record["index"])
    destination = paths.deleted / f"pair_{index:04d}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    destination.mkdir(parents=True, exist_ok=False)
    for relative in record.get("files", {}).values():
        source = paths.root / relative
        if source.exists():
            shutil.move(str(source), str(destination / source.name))
    metadata["pairs"] = [item for item in pairs if int(item["index"]) != index]
    deleted_record = dict(record)
    deleted_record["deleted_at_utc"] = utc_now_iso()
    deleted_record["recoverable_location"] = str(destination.relative_to(paths.root))
    metadata.setdefault("deleted_pairs", []).append(deleted_record)
    atomic_write_json(paths.metadata, metadata)
    return index


def discover_pairs(paths: SessionPaths) -> Tuple[List[PairPaths], List[str]]:
    errors: List[str] = []
    by_side: Dict[str, Dict[int, Path]] = {"left": {}, "right": {}, "full": {}}
    for side, directory in (("left", paths.left), ("right", paths.right), ("full", paths.full)):
        if not directory.is_dir():
            errors.append(f"missing directory: {directory}")
            continue
        for path in directory.iterdir():
            if not path.is_file():
                continue
            match = PAIR_RE.match(path.name)
            if not match or match.group(2) != side:
                errors.append(f"unexpected filename in {side}: {path.name}")
                continue
            index = int(match.group(1))
            if index in by_side[side]:
                errors.append(f"duplicate pair index {index:04d} in {side}")
            by_side[side][index] = path

    left_ids, right_ids = set(by_side["left"]), set(by_side["right"])
    for index in sorted(left_ids - right_ids):
        errors.append(f"pair {index:04d}: RIGHT file missing")
    for index in sorted(right_ids - left_ids):
        errors.append(f"pair {index:04d}: LEFT file missing")
    pairs = [
        PairPaths(index, by_side["left"][index], by_side["right"][index], by_side["full"].get(index))
        for index in sorted(left_ids & right_ids)
    ]
    return pairs, errors


def read_pair(pair: PairPaths) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    return cv2.imread(str(pair.left), cv2.IMREAD_COLOR), cv2.imread(str(pair.right), cv2.IMREAD_COLOR)
