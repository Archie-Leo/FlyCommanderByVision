"""Authenticated, bounded UDP messages for the separate flight command bridge."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

MAX_PACKET = 4096


def encode(payload: dict, key: bytes) -> bytes:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    signature = hmac.new(key, body, hashlib.sha256).hexdigest()
    return json.dumps({"body": payload, "mac": signature},
                      separators=(",", ":")).encode()


def decode(packet: bytes, key: bytes, *, now=None) -> dict:
    if len(packet) > MAX_PACKET:
        raise ValueError("packet too large")
    outer = json.loads(packet)
    body = outer["body"]
    expected = hmac.new(key, json.dumps(body, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(outer["mac"], expected):
        raise ValueError("invalid MAC")
    now = time.time() if now is None else now
    issued, expires = float(body["issued_at"]), float(body["expires_at"])
    if not (now <= expires <= now + 5 and now - 5 <= issued <= now + 1
            and issued <= expires):
        raise ValueError("expired or invalid TTL")
    return body
