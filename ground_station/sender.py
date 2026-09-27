"""Nonblocking, 20 Hz UDP sender of the latest read-only snapshot."""
from __future__ import annotations

import copy
import socket
import threading
import time

from .protocol import empty_snapshot, encode_packet, finite


class MetadataSender:
    def __init__(self, host, port=5603, hz=20, *, dry_run=None):
        if not 1 <= hz <= 30 or not 1 <= port <= 65535:
            raise ValueError("invalid metadata rate or port")
        self.address = (host, port)
        self.dry_run = dry_run
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.period = 1 / hz
        self.lock = threading.Lock()
        self.latest = empty_snapshot()
        self.capture_ns = None
        self.rates = {"ai_fps": None, "video_fps": None}
        self.sequence = 0
        self.sent = self.errors = self.oversize = 0
        self.sizes = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="metadata-udp", daemon=True)
        self.thread.start()

    def publish(self, snapshot, capture_ns):
        with self.lock:
            self.latest = snapshot
            self.capture_ns = capture_ns

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
            try:
                data = encode_packet(packet)
                self.socket.sendto(data, self.address)
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
