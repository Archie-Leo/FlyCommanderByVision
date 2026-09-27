"""Pure presentation rules; no path back into Stage6 or the board."""
from __future__ import annotations


def display_state(packet, receive_age_ms, *, metadata_stale_ms=500,
                  overlay_timeout_ms=1000):
    received = packet is not None and receive_age_ms is not None
    packet_stale = not received or receive_age_ms > metadata_stale_ms
    ai_age = packet.get("ai_age_ms") if packet else None
    ai_stale = ai_age is None or ai_age > metadata_stale_ms
    old_overlay = not received or receive_age_ms > overlay_timeout_ms or \
                  ai_age is None or ai_age > overlay_timeout_ms
    state = (packet.get("operator") or {}).get("state") if packet else None
    lost = state == "OPERATOR_LOST"
    return {
        "metadata_ready": not packet_stale,
        "ai_ready": not packet_stale and not ai_stale,
        "draw_person": not old_overlay and not lost,
        "gesture": ((packet.get("gesture") or {}).get("stable") or "UNKNOWN")
                   if not packet_stale and not ai_stale else "STALE",
        "intent": ((packet.get("stage6") or {}).get("intent") or "HOVER")
                  if not packet_stale else "HOVER / STALE VISUALIZATION",
        "safety_reason": ((packet.get("stage6") or {}).get("reason") or "UNKNOWN")
                         if not packet_stale else "METADATA STALE",
        "operator_state": state if not packet_stale and not ai_stale else "AI / METADATA STALE",
    }
