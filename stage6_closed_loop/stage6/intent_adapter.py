"""Fail-closed AuthorizedGestureV1 to frozen Gateway Intent contract mapping."""
from __future__ import annotations

import math
from dataclasses import dataclass

from stage5.types import AuthorizedGestureV1


GESTURE_TO_INTENT = {
    "LEFT": "MOVE_LEFT", "RIGHT": "MOVE_RIGHT", "ASCEND": "ASCEND",
    "DESCEND": "DESCEND", "HOVER": "HOVER",
}


@dataclass(frozen=True)
class IntentDecision:
    timestamp_ms: int
    seq: int
    intent: str
    valid: bool
    reason: str
    operator_session_id: str | None
    ownership_state: str
    gesture: str
    confidence: float
    source_timestamp_ms: int | None
    intent_age_ms: int | None
    control_active: bool

    def to_dict(self):
        return vars(self).copy()


class AuthorizedGestureIntentAdapter:
    """Only fresh, GREEN AuthorizedGestureV1 can renew a movement lease.

    The ROS publisher calls ``tick`` on its own timer. It never trusts a
    cached command after ``vision_command_timeout_ms``. Input timestamps are
    NUC monotonic milliseconds, matching the Stage 5 camera clock.
    """

    def __init__(self, vision_command_timeout_ms=300):
        if (isinstance(vision_command_timeout_ms, bool) or
                not isinstance(vision_command_timeout_ms, (int, float)) or
                not math.isfinite(vision_command_timeout_ms) or
                not 1 <= vision_command_timeout_ms <= 500):
            raise ValueError("vision timeout must be finite, >0 and <= Gateway 500 ms")
        self.timeout_ms = int(vision_command_timeout_ms)
        self._latest: AuthorizedGestureV1 | None = None
        self._last_seen_timestamp_ms: int | None = None
        self._session_id: str | None = None
        self._revoked_sessions: set[str] = set()
        self._reason = "NO_AUTHORIZED_GESTURE"
        self._seq = 0

    def clear(self, reason="VISION_STOPPED"):
        self._latest = None
        self._reason = reason

    def ingest(self, value: AuthorizedGestureV1, now_ms: int):
        """Accept a new observation; invalid input immediately cancels lease."""
        if not isinstance(value, AuthorizedGestureV1):
            self.clear("INVALID_INPUT_TYPE")
            return False
        if any(isinstance(v, bool) or not isinstance(v, int) for v in
               (value.timestamp_ms, value.frame_id, now_ms)):
            self.clear("NONFINITE_OR_INVALID_TIMESTAMP")
            return False
        if (self._last_seen_timestamp_ms is not None and
                value.timestamp_ms <= self._last_seen_timestamp_ms):
            self.clear("STALE_OR_OUT_OF_ORDER_TIMESTAMP")
            return False
        self._last_seen_timestamp_ms = value.timestamp_ms
        if value.operator_session_id is not None and not isinstance(value.operator_session_id, str):
            self.clear("MALFORMED_SESSION")
            return False
        if value.operator_session_id != self._session_id:
            if self._session_id:
                self._revoked_sessions.add(self._session_id)
            self._session_id = value.operator_session_id
            self._latest = None
        if (value.operator_session_id in self._revoked_sessions or
                value.timestamp_ms > now_ms or now_ms-value.timestamp_ms > self.timeout_ms):
            self.clear("STALE_OR_REVOKED_SESSION")
            return False
        if (not value.valid or value.authorization_state != "LOCKED_HIGH" or
                not isinstance(value.operator_session_id, str) or
                not value.operator_session_id or
                isinstance(value.current_track_id, bool) or
                not isinstance(value.current_track_id, int) or
                not isinstance(value.gesture, str) or
                value.gesture not in GESTURE_TO_INTENT or
                not isinstance(value.gesture_confidence, (int, float)) or
                not math.isfinite(value.gesture_confidence) or
                not 0 <= value.gesture_confidence <= 1):
            self.clear("NOT_AUTHORIZED_OR_UNKNOWN")
            return False
        self._latest = value
        self._reason = "AUTHORIZED_GESTURE_FRESH"
        return True

    def tick(self, now_ms: int):
        self._seq += 1
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            self.clear("NONFINITE_OR_INVALID_TIMESTAMP")
            return self._hover(0, self._reason)
        value = self._latest
        if value is None:
            return self._hover(now_ms, self._reason)
        age = now_ms-value.timestamp_ms
        if age < 0 or age >= self.timeout_ms:
            self.clear("VISION_COMMAND_TIMEOUT")
            return self._hover(now_ms, self._reason)
        return IntentDecision(now_ms,self._seq,GESTURE_TO_INTENT[value.gesture],True,
                              "AUTHORIZED_GESTURE_FRESH",value.operator_session_id,
                              value.authorization_state,value.gesture,
                              value.gesture_confidence,value.timestamp_ms,age,True)

    def _hover(self, now_ms, reason):
        return IntentDecision(now_ms,self._seq,"HOVER",False,reason,self._session_id,
                              "NOT_GREEN","UNKNOWN",0.0,None,None,False)
