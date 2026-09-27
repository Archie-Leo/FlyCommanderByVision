"""UDP latest-packet receiver; local monotonic time controls UI freshness."""
from __future__ import annotations

import socket
import threading
import time

from .protocol import decode_packet


class MetadataReceiver:
    def __init__(self, host="0.0.0.0", port=5603):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((host, port))
        self.socket.settimeout(.2)
        self.lock = threading.Lock()
        self.latest = None
        self.received_at = None
        self.gaps = self.received = self.invalid = 0
        self.last_sequence = None
        self.last_sender = None
        self.started_at = time.monotonic()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="metadata-receiver", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                data, sender = self.socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                packet = decode_packet(data)
            except (ValueError, UnicodeDecodeError):
                with self.lock:
                    self.invalid += 1
                continue
            now = time.monotonic()
            with self.lock:
                sequence = packet["sequence"]
                if sender == self.last_sender and self.last_sequence is not None and sequence > self.last_sequence:
                    self.gaps += sequence - self.last_sequence - 1
                self.last_sequence, self.last_sender = sequence, sender
                self.latest, self.received_at = packet, now
                self.received += 1

    def snapshot(self):
        with self.lock:
            age_ms = ((time.monotonic()-self.received_at)*1000
                      if self.received_at is not None else None)
            return self.latest, age_ms, {"received": self.received, "gaps": self.gaps,
                "invalid": self.invalid,
                "hz": self.received/max(.001, time.monotonic()-self.started_at)}

    def close(self):
        self.stop_event.set()
        self.socket.close()
        self.thread.join(timeout=1)
