"""Compact, complete UDP snapshots for the read-only ground station."""
from __future__ import annotations

import json
import math

SCHEMA_VERSION = 1
MAX_PACKET_BYTES = 1400
JOINTS = (
    "nose", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee",
    "right_knee", "left_ankle", "right_ankle",
)
# Keep the same topology as stage3_pose/ui.py, represented by JOINTS indices.
EDGES = ((1, 2), (1, 3), (3, 5), (2, 4), (4, 6), (1, 7), (2, 8),
         (7, 8), (7, 9), (9, 11), (8, 10), (10, 12))


def finite(value, digits=2):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def empty_snapshot():
    return {
        "schema_version": SCHEMA_VERSION, "sequence": 0,
        "source_frame_id": None, "processed_frame_id": None,
        "ai_age_ms": None, "people_count": 0,
        "operator": {"state": "WAIT_OPERATOR", "kind": None, "track_id": None,
                     "session_id": None, "bbox": None, "pose_score": None,
                     "keypoints": None},
        "gesture": {"raw": None, "stable": None},
        "stage6": {"intent": "HOVER", "valid": False,
                   "reason": "NO_AUTHORIZED_GESTURE", "lease": "INACTIVE"},
        "depth": {"m": None, "valid": False, "age_ms": None, "quality": None},
        "system": {"ai_fps": None, "video_fps": None},
    }


def encode_packet(snapshot):
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    if len(data) > MAX_PACKET_BYTES:
        raise ValueError(f"metadata packet is {len(data)} bytes, limit {MAX_PACKET_BYTES}")
    return data


def decode_packet(data):
    if len(data) > MAX_PACKET_BYTES:
        raise ValueError("oversized metadata packet")
    packet = json.loads(data)
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported metadata schema")
    if not isinstance(packet.get("sequence"), int) or packet["sequence"] < 0:
        raise ValueError("invalid metadata sequence")
    for name in ("operator", "gesture", "stage6", "depth", "system"):
        if not isinstance(packet.get(name), dict):
            raise ValueError(f"invalid metadata {name}")
    return packet
