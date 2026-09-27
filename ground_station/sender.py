"""Nonblocking, 20 Hz UDP sender of the latest read-only snapshot."""
from __future__ import annotations

import copy
import json
import socket
import threading
import time

from .protocol import empty_snapshot, encode_packet, finite
from .px4_telemetry import read_px4_snapshot


def encode_with_optional_limiter(packet):
    """Preserve metadata if optional limiter evidence would exceed the MTU."""
    try:
        return encode_packet(packet)
    except ValueError:
        command = packet.get("command") or {}
        if not isinstance(command, dict) or "limiter" not in command:
            raise
        command.pop("limiter")
        return encode_packet(packet)


class MetadataSender:
    def __init__(self, host, port=5603, hz=20, *, dry_run=None, px4_file=None,
                 command_file=None, authority_snapshot=None, command_mode="SHADOW",
                 recorder_port=None):
        if not 1 <= hz <= 30 or not 1 <= port <= 65535:
            raise ValueError("invalid metadata rate or port")
        if command_mode not in {"SHADOW", "LIVE"}:
            raise ValueError("invalid command mode")
        self.address = (host, port)
        self.recorder_address = (("127.0.0.1", recorder_port) if recorder_port else None)
        self.dry_run = dry_run
        self.px4_file = px4_file
        self.command_file = command_file
        self.command_mode = command_mode
        self.authority_snapshot = authority_snapshot
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.period = 1 / hz
        self.lock = threading.Lock()
        self.latest = empty_snapshot()
        self.evidence = None
        self.capture_ns = None
        self.rates = {"ai_fps": None, "video_fps": None}
        self.sequence = 0
        self.sent = self.errors = self.oversize = 0
        self.sizes = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="metadata-udp", daemon=True)
        self.thread.start()

    def publish(self, snapshot, capture_ns, evidence=None):
        with self.lock:
            self.latest = snapshot
            self.capture_ns = capture_ns
            self.evidence = evidence

    def set_rates(self, ai_fps, video_fps):
        with self.lock:
            self.rates = {"ai_fps": finite(ai_fps, 2),
                          "video_fps": finite(video_fps, 2)}

    def _run(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            with self.lock:
                packet = copy.deepcopy(self.latest)
                packet["system"] = dict(self.rates)
                captured = self.capture_ns
                evidence = self.evidence
                self.sequence += 1
                packet["sequence"] = self.sequence
            packet["ai_age_ms"] = (round((time.monotonic_ns()-captured)/1e6, 1)
                                   if captured is not None else None)
            if self.dry_run is not None:
                safety = self.dry_run.snapshot()
                decision = safety.get("stage6_decision") or {}
                packet["stage6"] = {"intent": decision.get("intent", "HOVER"),
                    "valid": bool(decision.get("valid", False)),
                    "reason": decision.get("reason"), "lease": safety.get("lease_state")}
            if self.px4_file is not None:
                packet["px4"] = read_px4_snapshot(self.px4_file)
            if self.command_file is not None:
                try:
                    command = json.loads(self.command_file.read_text(encoding="utf-8"))
                    age_ns = time.monotonic_ns() - command["monotonic_ns"]
                    if not 0 <= age_ns <= 500_000_000 or command.get("mode") != self.command_mode:
                        raise ValueError("stale gateway snapshot")
                    packet["command"] = {
                        "mode": command["mode"], "fresh": True,
                        "transmitted": command.get("mode") == "LIVE" and command.get("ros_published") is True,
                        "intent": command.get("intent"),
                        "frame": command.get("frame"),
                        "velocity": command.get("velocity"),
                        "yawspeed": command.get("yawspeed"),
                        }
                    if command["mode"] == "SHADOW":
                        packet["command"].update(
                            projected_intent=command.get("projected_intent"),
                            projected_velocity=command.get("projected_velocity"),
                            projected_yawspeed=command.get("projected_yawspeed"))
                    state_code = {"IDLE": "I", "ACTIVE": "A",
                                  "LIMIT_REACHED": "L",
                                  "BLOCKED_INVALID_POSITION": "P"}.get(
                                      command.get("safety_limiter_state"))
                    if state_code is not None:
                        # Full fields stay in Gateway trace. Five wire values
                        # fit with skeleton and PX4 data under the 1400 B cap.
                        packet["command"]["limiter"] = [
                            state_code, command.get("episode_id"),
                            command.get("episode_distance_m"),
                            command.get("episode_limit_m"),
                            command.get("velocity_cap_mps")]
                except (OSError, ValueError, TypeError, KeyError):
                    packet["command"] = {"mode": self.command_mode,
                                         "transmitted": False,
                                         "fresh": False}
                if self.authority_snapshot is not None:
                    authority = self.authority_snapshot()
                    packet["command"]["authority"] = (
                        "GRANTED" if authority.get("flight_authority_enabled") else "BLOCKED")
                    packet["command"]["authority_reason"] = authority.get(
                        "authority_transition_reason")
            try:
                data = encode_with_optional_limiter(packet)
                self.socket.sendto(data, self.address)
                if self.recorder_address:
                    try:
                        self.socket.sendto(data, self.recorder_address)
                        if evidence is not None:
                            local = json.dumps({"kind": "vision_evidence", **evidence},
                                               allow_nan=False, separators=(",", ":")).encode()
                            self.socket.sendto(local, self.recorder_address)
                    except (OSError, BlockingIOError, ValueError, TypeError):
                        pass  # Recorder cannot affect the production metadata path.
                self.sent += 1
                self.sizes.append(len(data))
                if len(self.sizes) > 4096:
                    self.sizes = self.sizes[-2048:]
            except ValueError:
                self.oversize += 1
            except (OSError, BlockingIOError):
                self.errors += 1
            deadline += self.period
            self.stop_event.wait(max(0, deadline-time.monotonic()))
            if deadline < time.monotonic() - self.period:
                deadline = time.monotonic()

    def status(self):
        with self.lock:
            sizes = sorted(self.sizes)
            return {"sent": self.sent, "errors": self.errors, "oversize": self.oversize,
                    "sequence": self.sequence,
                    "mean_bytes": round(sum(sizes)/len(sizes), 1) if sizes else None,
                    "p95_bytes": sizes[int((len(sizes)-1)*.95)] if sizes else None,
                    "max_bytes": max(sizes) if sizes else None,
                    "thread_tid": self.thread.native_id}

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.socket.close()
