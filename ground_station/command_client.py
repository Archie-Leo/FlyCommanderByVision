"""Windows command bridge client. Requests are sent only by explicit UI clicks."""
from __future__ import annotations

import socket
import threading
import time
import uuid

from .command_protocol import decode, encode


class CommandClient:
    def __init__(self, host: str, port: int, key: bytes):
        self.host, self.port, self.key = host, port, key
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.25)
        self.lock = threading.Lock()
        self.feedback = None
        self.received_at = 0.0
        self.pending_id = None
        self.pending_command = None
        self.pending_since = 0.0
        self.last_request_at = 0.0
        self.last_error = None
        self.last_error_at = 0.0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _send(self, body):
        now = time.time()
        body.update(issued_at=now, expires_at=now + 2)
        self.sock.sendto(encode(body, self.key), (self.host, self.port))

    def _loop(self):
        last_ping = 0.0
        while not self.stop.is_set():
            if time.monotonic() - last_ping >= .5:
                self._send({"kind": "ping"})
                last_ping = time.monotonic()
            with self.lock:
                request_id, command = self.pending_id, self.pending_command
                started, last_request = self.pending_since, self.last_request_at
            if request_id and time.monotonic() - started > 3:
                with self.lock:
                    if self.pending_id == request_id:
                        self.pending_id = self.pending_command = None
            elif request_id and time.monotonic() - last_request > .5:
                try:
                    self._send({"kind": "request", "request_id": request_id,
                                "command": command})
                    with self.lock:
                        self.last_request_at = time.monotonic()
                except OSError:
                    pass
            try:
                packet, address = self.sock.recvfrom(4096)
                if address[0] != self.host or address[1] != self.port:
                    continue
                feedback = decode(packet, self.key)
                if feedback.get("kind") != "feedback":
                    continue
                with self.lock:
                    self.feedback, self.received_at = feedback, time.monotonic()
                    if (feedback.get("request_id") == self.pending_id and
                            feedback.get("transaction_state") in ("FAILED", "ABORTED")):
                        self.last_error = feedback.get("reason") or "COMMAND FAILED"
                        self.last_error_at = time.monotonic()
                    if feedback.get("request_id") == self.pending_id and feedback.get("transaction_state") in (
                            "COMPLETE", "FAILED", "ABORTED"):
                        self.pending_id = self.pending_command = None
            except socket.timeout:
                continue
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def snapshot(self):
        with self.lock:
            fresh = time.monotonic() - self.received_at < 1.5
            if not fresh or not self.feedback:
                return None
            result = dict(self.feedback)
            if self.last_error and time.monotonic() - self.last_error_at < 5:
                result["last_error"] = self.last_error
            return result

    def request(self, command: str):
        if command not in ("TAKEOFF", "LAND"):
            raise ValueError("unsupported command")
        with self.lock:
            feedback = self.feedback
            if time.monotonic() - self.received_at > 1.5 or not feedback or not feedback.get("enabled"):
                raise RuntimeError("BRIDGE DISCONNECTED OR DISABLED")
            if self.pending_id or feedback.get("transaction_state") not in (
                    "IDLE", "COMPLETE", "FAILED", "ABORTED"):
                raise RuntimeError("COMMAND BUSY")
            request_id = uuid.uuid4().hex
            self.last_error = None
            self.pending_id = request_id
            self.pending_command = command
            self.pending_since = self.last_request_at = time.monotonic()
        try:
            self._send({"kind": "request", "request_id": request_id, "command": command})
        except OSError:
            with self.lock:
                self.pending_id = None
                self.pending_command = None
            raise
        return request_id

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        self.sock.close()
