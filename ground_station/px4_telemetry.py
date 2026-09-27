"""Fail-closed view of the board's ROS2 read-only PX4 snapshot."""
from __future__ import annotations

import json
from pathlib import Path
import time

from .protocol import finite


PX4_STATUS_TIMEOUT_MS = 1500  # FlightAuthorityGate default.


def disconnected(age_ms=None):
    return {"connected": False, "status_age_ms": age_ms, "mode": None,
            "nav_state": None, "armed": None, "failsafe": None,
            "local_position_valid": None, "position": None, "velocity": None,
            "heading": None, "last_vehicle_command_ack": None}


def read_px4_snapshot(path: Path, *, now_ns=None):
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        status = data.get("status")
        received = status["received_monotonic_ns"]
        age_ms = max(0, (now_ns - received) / 1e6)
        if received > now_ns or age_ms > PX4_STATUS_TIMEOUT_MS:
            return disconnected(round(age_ms, 1) if received <= now_ns else None)
        position = data.get("position") or {}
        position_received = position.get("received_monotonic_ns")
        position_fresh = isinstance(position_received, int) and (
            0 <= now_ns - position_received <= PX4_STATUS_TIMEOUT_MS * 1_000_000)
        pos_valid = bool(position.get("xy_valid") and position.get("z_valid")) if position_fresh else None
        vel_valid = bool(position.get("v_xy_valid") and position.get("v_z_valid")) if position_fresh else False
        ack = data.get("last_vehicle_command_ack") or {}
        ack_received = ack.get("received_monotonic_ns")
        ack_fresh = isinstance(ack_received, int) and 0 <= now_ns - ack_received <= 10_000_000_000
        return {"connected": True, "status_age_ms": round(age_ms, 1),
                "mode": status.get("mode"), "nav_state": status.get("nav_state"),
                "armed": status.get("armed"), "failsafe": status.get("failsafe"),
                "local_position_valid": pos_valid,
                "position": ({axis: finite(position.get(axis), 2) for axis in ("x", "y", "z")}
                             if pos_valid else None),
                "velocity": ({axis: finite(position.get(axis), 2) for axis in ("vx", "vy", "vz")}
                             if vel_valid else None),
                "heading": finite(position.get("heading"), 3) if position_fresh else None,
                "last_vehicle_command_ack": ({key: ack.get(key) for key in ("command", "result")}
                                             if ack_fresh else None)}
    except (OSError, ValueError, TypeError, KeyError):
        return disconnected()
