"""Automatic Windows evidence session joined to the RK3576 recorder."""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re
import socket
import threading
import time

from .command_protocol import decode, encode


SESSION_RE = re.compile(r"^\d{8}_\d{6}_F\d{3,4}$")


class GroundSessionRecorder:
    def __init__(self, root, host, key, *, port=5605):
        self.root, self.host, self.port, self.key = Path(root), host, port, key
        self.root.mkdir(parents=True, exist_ok=True)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0", 0))
        self.socket.settimeout(.2)
        self.pending = deque(maxlen=2048)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.session_id = None
        self.boot_id = None
        self.last_ack = 0.0
        self.last_hello = 0.0
        self.last_event_signature = {}
        self.path = self.root / f"pending_{int(time.time())}_{os.getpid()}.jsonl"
        self.file = self.path.open("a", encoding="utf-8", buffering=8192)
        self.thread = threading.Thread(target=self._loop, name="ground-session-recorder", daemon=True)
        self.thread.start()
        self.event("GROUND_STATION_STARTED", details={"automatic": True})

    def _send(self, body):
        now = time.time()
        body.update(issued_at=now, expires_at=now + 2)
        self.socket.sendto(encode(body, self.key), (self.host, self.port))

    def event(self, name, *, reason=None, details=None, dedup=True):
        details = details or {}
        if not isinstance(details, dict):
            return
        signature = json.dumps((reason, details), sort_keys=True, default=str)
        with self.lock:
            if dedup and self.last_event_signature.get(name) == signature:
                return
            self.last_event_signature[name] = signature
            self.pending.append({"timestamp_wall": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "timestamp_monotonic": time.monotonic(), "event": name,
                                 "reason": reason, "details": details})

    def snapshot(self):
        with self.lock:
            return {"session_id": self.session_id,
                    "connected": self.session_id is not None and time.monotonic() - self.last_ack <= 2.5,
                    "boot_id": self.boot_id}

    def _summary(self, reason):
        if not self.session_id:
            return
        target = self.root / self.session_id / "session_summary.json"
        target.write_text(json.dumps({"session_id": self.session_id,
                                      "end_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                      "end_monotonic": time.monotonic(), "end_reason": reason,
                                      "board_summary": "See /var/lib/fcv/logs/sessions/<SESSION_ID>"},
                                     indent=2), encoding="utf-8")

    def _join(self, body):
        name = body.get("session_id")
        if not isinstance(name, str) or not SESSION_RE.fullmatch(name):
            return
        with self.lock:
            self.last_ack = time.monotonic()
            if self.session_id == name:
                return
            old = self.session_id
            self.file.flush()
            self.file.close()
            if old:
                self._summary("BOARD_SESSION_CHANGED")
            target = self.root / name
            target.mkdir(parents=True, exist_ok=True)
            destination = target / "ground_station_events.jsonl"
            if self.path.name.startswith("pending_") and self.path.exists():
                with self.path.open("r", encoding="utf-8") as src, destination.open("a", encoding="utf-8") as dst:
                    for line in src:
                        dst.write(line)
                self.path.unlink()
            self.path = destination
            self.file = destination.open("a", encoding="utf-8", buffering=8192)
            self.session_id, self.boot_id = name, body.get("boot_id")
            (target / "ground_station_manifest.json").write_text(json.dumps({
                "session_id": name, "board_boot_id": self.boot_id,
                "joined_wall": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "joined_monotonic": time.monotonic(),
                "board_wall_minus_windows_wall_est_sec": round(body.get("board_wall", time.time()) - time.time(), 3),
                "video_recording": "optional_not_enabled",
            }, indent=2), encoding="utf-8")
        self.event("SESSION_JOINED", details={"session_id": name, "previous_session_id": old}, dedup=False)

    def _drain(self):
        with self.lock:
            items = list(self.pending)
            self.pending.clear()
            session_id = self.session_id
        for item in items:
            item["session_id"] = session_id
            self.file.write(json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            if session_id:
                try:
                    self._send({"kind": "session_event", "session_id": session_id,
                                "event": item["event"], "reason": item["reason"],
                                "details": item["details"]})
                except (OSError, ValueError):
                    pass

    def _loop(self):
        was_connected = False
        while not self.stop.is_set():
            now = time.monotonic()
            if now - self.last_hello >= 1:
                try:
                    self._send({"kind": "session_hello", "windows_wall": time.time(),
                                "windows_monotonic": now})
                except OSError:
                    pass
                self.last_hello = now
            try:
                data, address = self.socket.recvfrom(4096)
                if address[0] == self.host and address[1] == self.port:
                    body = decode(data, self.key)
                    if body.get("kind") == "session_ack":
                        self._join(body)
            except socket.timeout:
                pass
            except (OSError, ValueError, KeyError, TypeError):
                pass
            connected = self.snapshot()["connected"]
            if connected != was_connected:
                self.event("RECORDER_CONNECTED" if connected else "RECORDER_DISCONNECTED")
                was_connected = connected
            try:
                self._drain()
            except (OSError, ValueError) as exc:
                print(f"RECORDER_DEGRADED: Windows log write failed: {exc}", flush=True)

    def end_session(self):
        state = self.snapshot()
        if state["session_id"]:
            try:
                self._send({"kind": "session_end", "session_id": state["session_id"]})
            except OSError:
                pass

    def start_session(self):
        self.event("MANUAL_START_SESSION", dedup=False)
        try:
            self._send({"kind": "session_start", "session_id": self.snapshot()["session_id"]})
        except OSError:
            pass

    def close(self):
        self.event("GROUND_STATION_CLOSED", dedup=False)
        self.stop.set()
        self.thread.join(timeout=1)
        try:
            self._drain()
            self.file.flush()
            self._summary("GROUND_STATION_CLOSED")
            self.end_session()
        finally:
            self.file.close()
            self.socket.close()
