"""PX4 flight authority and post-Offboard fresh-motion gate (Stage 6 only).

Identity and Gallery belong to Stage 5. This object only revokes/renews the
Stage 6 motion lease. A fresh motion requires a *trusted neutral release*
observed after entering Offboard, followed by a new authorized gesture.
"""
from __future__ import annotations


class FlightAuthorityGate:
    def __init__(self, *, offboard_nav_state: int, status_timeout_ms: int = 1500,
                 neutral_release_ms: int = 150, neutral_release_frames: int = 2):
        if status_timeout_ms <= 0 or neutral_release_ms < 0 or neutral_release_frames < 2:
            raise ValueError("invalid flight-authority timing")
        self.offboard_nav_state = offboard_nav_state
        self.status_timeout_ns = status_timeout_ms * 1_000_000
        self.neutral_release_ms = neutral_release_ms
        self.neutral_release_frames = neutral_release_frames
        self.px4_armed = None
        self.px4_nav_state = None
        self.px4_failsafe = None
        self.last_status_ns = None
        self.enabled = False
        self.require_fresh_gesture = True
        self.transition_reason = "PX4_STATUS_UNAVAILABLE"
        self._neutral_since_ms = None
        self._neutral_frames = 0
        self._neutral_session = None
        self._ready_session = None

    def _reset_fresh(self):
        self.require_fresh_gesture = True
        self._neutral_since_ms = None
        self._neutral_frames = 0
        self._neutral_session = None
        self._ready_session = None

    def update_status(self, *, armed: bool, nav_state: int, failsafe: bool,
                      now_ns: int) -> bool:
        """Return True on an authority edge; caller must revoke lease at once."""
        was_enabled = self.enabled
        self.px4_armed = bool(armed)
        self.px4_nav_state = int(nav_state)
        self.px4_failsafe = bool(failsafe)
        self.last_status_ns = now_ns
        self.enabled = (self.px4_armed and
                        self.px4_nav_state == self.offboard_nav_state and
                        not self.px4_failsafe)
        if self.enabled != was_enabled:
            self._reset_fresh()
            if self.enabled:
                self.transition_reason = "OFFBOARD_ENTER_WAIT_FRESH_GESTURE"
            elif self.px4_failsafe:
                self.transition_reason = "PX4_FAILSAFE"
            elif not self.px4_armed:
                self.transition_reason = "PX4_DISARMED"
            else:
                self.transition_reason = "OFFBOARD_EXIT"
        elif not self.enabled:
            if self.px4_failsafe:
                self.transition_reason = "PX4_FAILSAFE"
            elif not self.px4_armed:
                self.transition_reason = "PX4_DISARMED"
            else:
                self.transition_reason = "FLIGHT_MODE_NOT_OFFBOARD"
        return self.enabled != was_enabled

    def refresh(self, now_ns: int) -> bool:
        """Return True if the PX4 status lease just expired."""
        if self.last_status_ns is None:
            return False
        if now_ns - self.last_status_ns < self.status_timeout_ns:
            return False
        was_enabled = self.enabled
        self.enabled = False
        self._reset_fresh()
        self.transition_reason = "PX4_STATUS_TIMEOUT"
        return was_enabled

    def allow(self, authorized, vision_log: dict, now_ns: int) -> tuple[bool, str]:
        self.refresh(now_ns)
        if not self.enabled:
            return False, self.transition_reason
        session = getattr(authorized, "operator_session_id", None)
        if not self.require_fresh_gesture and session != self._ready_session:
            self._reset_fresh()
            return False, "SESSION_CHANGED_WAIT_FRESH_GESTURE"
        if not self.require_fresh_gesture:
            return True, "FLIGHT_AUTHORITY_ENABLED"

        # A rejected frame, temporary pose loss or an unconfirmed legal pose
        # cannot count as releasing the old command. Stage 4 must report both
        # raw and stable UNKNOWN while Stage 5 still trusts the same operator.
        raw = vision_log.get("gesture_raw") or {}
        stable = vision_log.get("gesture_stable") or {}
        neutral = (getattr(authorized, "authorization_state", None) == "LOCKED_HIGH"
                   and isinstance(session, str) and bool(session)
                   and getattr(authorized, "current_track_id", None) is not None
                   and raw.get("label") == "UNKNOWN"
                   and stable.get("label") == "UNKNOWN"
                   and not bool(stable.get("stable")))
        now_ms = now_ns // 1_000_000
        if not neutral:
            self._neutral_since_ms = None
            self._neutral_frames = 0
            self._neutral_session = None
            return False, "WAIT_FRESH_GESTURE_RELEASE"
        if self._neutral_session != session:
            self._neutral_session = session
            self._neutral_since_ms = now_ms
            self._neutral_frames = 1
        else:
            self._neutral_frames += 1
        if (self._neutral_frames >= self.neutral_release_frames and
                now_ms - self._neutral_since_ms >= self.neutral_release_ms):
            self.require_fresh_gesture = False
            self._ready_session = session
            self.transition_reason = "FRESH_GESTURE_READY"
            return False, "FRESH_GESTURE_READY"
        return False, "WAIT_FRESH_GESTURE_RELEASE"

    def snapshot(self) -> dict:
        return {"px4_armed": self.px4_armed,
                "px4_nav_state": self.px4_nav_state,
                "px4_failsafe": self.px4_failsafe,
                "flight_authority_enabled": self.enabled,
                "require_fresh_gesture": self.require_fresh_gesture,
                "authority_transition_reason": self.transition_reason,
                "px4_status_receive_monotonic_ns": self.last_status_ns}
