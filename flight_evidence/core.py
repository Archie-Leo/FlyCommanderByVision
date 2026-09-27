"""Bounded, fail-open session storage. No ROS or flight-control imports."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import time


TELEMETRY_FIELDS = (
    "time_wall", "time_monotonic", "session_id", "px4_connected", "armed",
    "nav_state", "failsafe", "preflight", "landed", "x", "y", "z", "vx",
    "vy", "vz", "heading", "heading_valid", "operator_state", "gesture",
    "intent", "lease_active", "authority_state", "authority_reason",
    "safety_state", "safety_reason", "gateway_vx", "gateway_vy", "gateway_vz",
    "offboard_position_flag", "offboard_velocity_flag", "ros_tx_vx", "ros_tx_vy",
    "ros_tx_vz", "ros_tx_age_ms", "command_transaction", "command_state",
)


def wall_time():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unavailable"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def write_json(path, value):
    target = Path(path)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2),
                    encoding="utf-8")
    os.replace(temp, target)


class SessionStore:
    def __init__(self, root, manifest, *, max_sessions=30):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_defaults = dict(manifest)
        self.max_sessions = max_sessions
        self.active = None
        self.event_cache = {}
        self.summary = {}
        self.events_file = self.system_file = self.telemetry_file = None
        self.telemetry_writer = None
        self.last_telemetry_at = 0.0
        self.last_flush_at = time.monotonic()
        self.degraded = False
        self.recover_incomplete()
        self.start("BOOT_START")

    def recover_incomplete(self):
        for path in self.root.iterdir():
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            if (path / "session_summary.json").exists():
                continue
            manifest = read_json(path / "manifest.json") or {}
            write_json(path / "session_summary.json", {
                "session_id": path.name, "result": "INCOMPLETE",
                "end_reason": "RECORDER_RESTART_OR_REBOOT", "start_time": manifest.get("start_time"),
                "end_time": wall_time(), "primary_failure_layer": "UNKNOWN",
                "primary_blocker": "EVIDENCE_INTERRUPTED",
                "px4_rx_direct_evidence": "unavailable_online",
                "px4_ulog": "requires manual correlation by timestamp",
            })

    def _next_id(self):
        base = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        for index in range(1, 10000):
            name = f"{base}_F{index:03d}"
            if not (self.root / name).exists():
                return name
        raise RuntimeError("session id space exhausted")

    def start(self, reason, initial_px4=None):
        if self.active is not None:
            return self.active
        name = self._next_id()
        path = self.root / name
        path.mkdir(mode=0o750)
        self.active = name
        self.event_cache = {}
        self.summary = {"session_id": name, "result": "INCOMPLETE",
                        "takeoff_attempted": False, "land_attempted": False,
                        "operator_locked": False, "offboard_entered": False,
                        "gesture_commands_seen": [], "last_valid_gesture": None,
                        "last_intent": None, "last_authority": None,
                        "last_blocker": None, "last_gateway_output": None,
                        "ros_tx": None, "px4_last": None,
                        "actual_movement_observed": None,
                        "px4_rx_direct_evidence": "unavailable_online",
                        "px4_ulog": "requires manual correlation by timestamp",
                        "primary_failure_layer": "UNKNOWN", "primary_blocker": None}
        manifest = {**self.manifest_defaults, "session_id": name,
                    "start_time": wall_time(), "start_monotonic": time.monotonic(),
                    "initial_px4": initial_px4, "px4_ulog": self.summary["px4_ulog"]}
        write_json(path / "manifest.json", manifest)
        self.events_file = (path / "events.jsonl").open("a", encoding="utf-8", buffering=8192)
        self.system_file = (path / "system_events.jsonl").open("a", encoding="utf-8", buffering=8192)
        self.telemetry_file = (path / "telemetry.csv").open("a", encoding="utf-8", newline="", buffering=8192)
        self.telemetry_writer = csv.DictWriter(self.telemetry_file, fieldnames=TELEMETRY_FIELDS)
        self.telemetry_writer.writeheader()
        self.event("recorder", "SESSION_STARTED", new_state=name, reason=reason)
        self.rotate()
        return name

    def event(self, source, event, *, old_state=None, new_state=None, reason=None,
              details=None, system=False, dedup=True, signature=None):
        if self.active is None:
            self.start("EVENT_AUTO_START")
        details = details or {}
        key = (source, event)
        signature = json.dumps((new_state, reason, details) if signature is None else signature,
                               sort_keys=True, default=str)
        if dedup and self.event_cache.get(key) == signature:
            return False
        self.event_cache[key] = signature
        record = {"timestamp_wall": wall_time(), "timestamp_monotonic": time.monotonic(),
                  "session_id": self.active, "boot_id": self.manifest_defaults["boot_id"],
                  "source": source, "event": event, "old_state": old_state,
                  "new_state": new_state, "reason": reason, "details": details}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        try:
            (self.system_file if system else self.events_file).write(line)
        except OSError:
            self.degraded = True
            print("RECORDER_DEGRADED: event write failed", flush=True)
            return False
        self._summarize(record)
        if event in ("ARM_REQUEST_SENT", "TAKEOFF_REQUEST_SENT", "LAND_REQUEST_SENT",
                     "ABORTED", "FAILED", "TAKEOFF_COMPLETE", "LAND_COMPLETE"):
            self.flush_if_due(force=True)
        return True

    def flush_if_due(self, *, force=False):
        now = time.monotonic()
        if not force and now - self.last_flush_at < 1:
            return
        try:
            for handle in (self.events_file, self.system_file, self.telemetry_file):
                if handle is not None:
                    handle.flush()
            self.last_flush_at = now
        except OSError:
            self.degraded = True
            print("RECORDER_DEGRADED: buffered flush failed", flush=True)

    def _summarize(self, record):
        event, details = record["event"], record["details"]
        if event in ("OPERATOR_LOCKED_HIGH", "OPERATOR_REAUTHORIZED"):
            self.summary["operator_locked"] = True
        if event == "GESTURE_CONFIRMED":
            self.summary["last_valid_gesture"] = details.get("gesture")
            gesture = details.get("gesture")
            if gesture and gesture not in self.summary["gesture_commands_seen"]:
                self.summary["gesture_commands_seen"].append(gesture)
        if event == "INTENT_CHANGED":
            self.summary["last_intent"] = record["new_state"]
        if event == "FLIGHT_AUTHORITY_CHANGED":
            self.summary["last_authority"] = record["new_state"]
        if event == "GATEWAY_OUTPUT_CHANGED":
            self.summary["last_gateway_output"] = details
        if event in ("TAKEOFF_REQUEST_SENT", "REQUEST_RECEIVED") and details.get("command") == "TAKEOFF":
            self.summary["takeoff_attempted"] = True
        if event in ("LAND_REQUEST_SENT", "REQUEST_RECEIVED") and details.get("command") == "LAND":
            self.summary["land_attempted"] = True
        if event in ("TAKEOFF_COMPLETE", "LAND_COMPLETE", "ABORTED", "FAILED"):
            self.summary[event.lower()] = {"reason": record["reason"], **details}
        if event in ("FLIGHT_AUTHORITY_CHANGED", "SAFETY_BLOCK", "REQUEST_REJECTED",
                     "PRECHECK_BLOCKED", "TAKEOFF_BUTTON_EVAL", "LAND_BUTTON_EVAL") and record["reason"] and \
                record["new_state"] != "GRANTED":
            self.summary["last_blocker"] = record["reason"]
        if event == "PX4_STATE_CHANGED":
            self.summary["px4_last"] = details
            if details.get("nav_state") in (14, "OFFBOARD"):
                self.summary["offboard_entered"] = True
        if event == "ROS_TX_STATE_CHANGED":
            self.summary["ros_tx"] = details
        if event == "COMMAND_EFFECT_OBSERVED":
            self.summary["actual_movement_observed"] = True
        if event == "COMMAND_EFFECT_NOT_OBSERVED":
            self.summary["actual_movement_observed"] = False

    def telemetry(self, sample, *, now=None):
        now = time.monotonic() if now is None else now
        if now - self.last_telemetry_at < 0.095:
            return False
        self.last_telemetry_at = now
        row = {name: sample.get(name) for name in TELEMETRY_FIELDS}
        row.update(time_wall=wall_time(), time_monotonic=now, session_id=self.active)
        try:
            self.telemetry_writer.writerow(row)
        except OSError:
            self.degraded = True
            print("RECORDER_DEGRADED: telemetry write failed", flush=True)
            return False
        return True

    def close(self, reason="NORMAL_END"):
        if self.active is None:
            return
        self.event("recorder", "SESSION_ENDED", reason=reason, dedup=False)
        name = self.active
        for handle in (self.events_file, self.system_file, self.telemetry_file):
            try:
                handle.flush()
                handle.close()
            except OSError:
                self.degraded = True
        summary = {**self.summary, "end_time": wall_time(), "end_monotonic": time.monotonic(),
                   "end_reason": reason, "recorder_degraded": self.degraded}
        summary["takeoff_result"] = ("COMPLETE" if summary.get("takeoff_complete") else
                                     "FAILED" if summary.get("failed") and summary["takeoff_attempted"] else
                                     "ABORTED" if summary.get("aborted") and summary["takeoff_attempted"] else
                                     "INCOMPLETE" if summary["takeoff_attempted"] else "NOT_ATTEMPTED")
        summary["land_result"] = ("COMPLETE" if summary.get("land_complete") else
                                  "FAILED" if summary.get("failed") and summary["land_attempted"] else
                                  "ABORTED" if summary.get("aborted") and summary["land_attempted"] else
                                  "INCOMPLETE" if summary["land_attempted"] else "NOT_ATTEMPTED")
        if summary.get("takeoff_complete") or summary.get("land_complete"):
            summary["result"] = "PASS"
        elif summary.get("aborted") or summary.get("failed"):
            summary["result"] = "ABORTED" if summary.get("aborted") else "FAIL"
        if summary["result"] != "PASS" and summary["last_blocker"] and (
                summary["takeoff_attempted"] or summary["land_attempted"] or
                summary["gesture_commands_seen"]):
            summary["primary_blocker"] = summary["last_blocker"]
        blocker = summary.get("primary_blocker") or ""
        if summary.get("actual_movement_observed") is False and summary.get("ros_tx"):
            summary["primary_failure_layer"] = "UNKNOWN"
            summary["primary_blocker"] = "DIRECT_PX4_UORB_RX_EVIDENCE_UNAVAILABLE"
            summary["next_diagnostic_point"] = "PX4 trajectory_setpoint listener / ULog"
            summary["evidence_reaches"] = "ROS_TX"
        elif blocker in ("BRIDGE_DISABLED", "BRIDGE DISABLED", "BRIDGE_DISCONNECTED"):
            summary["primary_failure_layer"] = "COMMAND_BRIDGE"
        elif blocker in ("PREFLIGHT_FAILED", "PRECHECK FAILED", "LAND_STATE_INVALID"):
            summary["primary_failure_layer"] = "GROUND_UI"
        elif blocker in ("WAIT_FRESH_GESTURE_RELEASE", "PX4_STATUS_TIMEOUT", "PX4_DISARMED",
                         "FLIGHT_MODE_NOT_OFFBOARD", "PX4_FAILSAFE"):
            summary["primary_failure_layer"] = "FLIGHT_AUTHORITY"
        elif blocker in ("EPISODE_DISTANCE_LIMIT", "INVALID_OR_STALE_LOCAL_POSITION"):
            summary["primary_failure_layer"] = "SAFETY_LIMITER"
        try:
            write_json(self.root / name / "session_summary.json", summary)
        except OSError:
            self.degraded = True
            print("RECORDER_DEGRADED: summary write failed", flush=True)
        self.active = None
        self.rotate()

    def rotate(self):
        sessions = sorted((p for p in self.root.iterdir() if p.is_dir() and
                           (p / "session_summary.json").exists()), key=lambda p: p.name)
        for path in sessions[:max(0, len(sessions) - self.max_sessions)]:
            if path.name != self.active:
                try:
                    shutil.rmtree(path)
                except OSError:
                    self.degraded = True
                    print("RECORDER_DEGRADED: retention failed", flush=True)
