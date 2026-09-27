"""Pure, fail-closed ground flight command transaction logic.

No ROS imports or I/O. The adapter must publish only commands returned by tick().
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

TARGET_HEIGHT = 1.20
STATUS_MAX_AGE = 1.5
POSITION_MAX_AGE = 1.5
ACK_TIMEOUT = 3.0
ARMED_TIMEOUT = 4.0
TAKEOFF_TIMEOUT = 25.0
LAND_TIMEOUT = 90.0
STABLE_SEC = 0.75


@dataclass
class Telemetry:
    status_at: float = 0.0
    position_at: float = 0.0
    land_at: float = 0.0
    armed: bool = False
    nav_state: int = -1
    failsafe: bool = True
    preflight: bool = False
    z_valid: bool = False
    vz_valid: bool = False
    z: float = math.nan
    vz: float = math.nan
    z_reset: int = 0
    vz_reset: int = 0
    landed: bool | None = None


def precheck(command: str, t: Telemetry, now: float, *, takeoff_height_verified: bool) -> str | None:
    if now - t.status_at > STATUS_MAX_AGE or t.status_at > now:
        return "PX4 DISCONNECTED"
    if t.failsafe:
        return "FAILSAFE ACTIVE"
    if command == "TAKEOFF":
        if not t.preflight:
            return "PRECHECK FAILED"
        if t.armed:
            return "ALREADY ARMED"
        if t.landed is not True or now - t.land_at > STATUS_MAX_AGE:
            return "LAND STATE INVALID"
        if not takeoff_height_verified:
            return "TAKEOFF HEIGHT UNVERIFIED"
        if now - t.position_at > POSITION_MAX_AGE or not t.z_valid or not t.vz_valid:
            return "LOCAL POSITION INVALID"
        if not math.isfinite(t.z) or not math.isfinite(t.vz):
            return "LOCAL POSITION INVALID"
    elif command == "LAND":
        if not t.armed:
            return "DISARMED"
        if t.landed is not False or now - t.land_at > STATUS_MAX_AGE:
            return "AIRBORNE STATE INVALID"
    else:
        return "UNSUPPORTED COMMAND"
    return None


class Transaction:
    TERMINAL = {"COMPLETE", "FAILED", "ABORTED"}

    def __init__(self, request_id: str, command: str, now: float, z0: float | None,
                 z_reset: int = 0, vz_reset: int = 0):
        self.request_id = request_id
        self.command = command
        self.state = "PRECHECK"
        self.reason = ""
        self.started = self.changed = now
        self.z0 = z0
        self.z_reset = z_reset
        self.vz_reset = vz_reset
        self.ack = None
        self.ack_at = None
        self.stable_since = None
        self.sent = []

    @property
    def active(self):
        return self.state not in self.TERMINAL

    def move(self, state: str, now: float, reason: str = ""):
        self.state, self.changed, self.reason = state, now, reason

    def on_ack(self, command: int, result: int, now: float):
        expected = {"WAIT_ARM_ACK": 400, "WAIT_TAKEOFF_ACK": 22,
                    "WAIT_LAND_ACK": 21}.get(self.state)
        if expected != command or now < self.changed:
            return
        self.ack, self.ack_at = result, now
        if result == 5:  # PX4 has not accepted the final result yet.
            return
        if result == 0:
            next_state = {400: "WAIT_ARMED", 22: "TAKING_OFF", 21: "LANDING"}[command]
            self.move(next_state, now)
        else:
            self.move("FAILED", now, f"{self.state} ACK {result}")

    def tick(self, t: Telemetry, now: float, *, height_verified: bool):
        """Return at most one VehicleCommand name; never retry a sent command."""
        if not self.active:
            return None
        if now - t.status_at > STATUS_MAX_AGE or t.status_at > now:
            self.move("ABORTED", now, "PX4 DISCONNECTED")
            return None
        if t.failsafe:
            self.move("ABORTED", now, "FAILSAFE ACTIVE")
            return None
        if self.state == "PRECHECK":
            reason = precheck(self.command, t, now, takeoff_height_verified=height_verified)
            if reason:
                self.move("FAILED", now, reason)
                return None
            if self.command == "TAKEOFF":
                self.move("WAIT_ARM_ACK", now)
                self.sent.append("ARM")
                return "ARM"
            self.move("WAIT_LAND_ACK", now)
            self.sent.append("LAND")
            return "LAND"
        if self.state in ("WAIT_ARM_ACK", "WAIT_TAKEOFF_ACK", "WAIT_LAND_ACK"):
            if now - self.changed > ACK_TIMEOUT:
                self.move("FAILED", now, "COMMAND TIMEOUT")
            return None
        if self.state == "WAIT_ARMED":
            if t.armed:
                if (not height_verified or not t.preflight or t.landed is not True or
                        now - t.land_at > STATUS_MAX_AGE or
                        now - t.position_at > POSITION_MAX_AGE or
                        not t.z_valid or not t.vz_valid or
                        t.z_reset != self.z_reset or t.vz_reset != self.vz_reset):
                    self.move("ABORTED", now, "TAKEOFF PRECHECK LOST")
                    return None
                self.move("WAIT_TAKEOFF_ACK", now)
                self.sent.append("TAKEOFF")
                return "TAKEOFF"
            if now - self.changed > ARMED_TIMEOUT:
                self.move("FAILED", now, "ARMED CONFIRMATION TIMEOUT")
            return None
        if self.state in ("TAKING_OFF", "STABILIZING"):
            if not t.armed:
                self.move("ABORTED", now, "UNEXPECTED DISARM")
            elif self.ack_at is not None and now - self.ack_at > 2 and t.nav_state not in (17, 4):
                self.move("ABORTED", now, "TAKEOFF MODE LOST")
            elif now - t.position_at > POSITION_MAX_AGE or not t.z_valid or not t.vz_valid:
                self.move("ABORTED", now, "LOCAL POSITION INVALID")
            elif t.z_reset != self.z_reset or t.vz_reset != self.vz_reset:
                self.move("ABORTED", now, "LOCAL POSITION RESET")
            elif now - self.started > TAKEOFF_TIMEOUT:
                self.move("FAILED", now, "HEIGHT TIMEOUT")
            else:
                height = -(t.z - self.z0)
                if 1.05 <= height <= 1.35 and abs(t.vz) < 0.15:
                    if self.stable_since is None:
                        self.stable_since = now
                        self.move("STABILIZING", now)
                    elif now - self.stable_since >= STABLE_SEC:
                        self.move("COMPLETE", now, "HOVER 1.2 m; VISION CONTROL NOT ACTIVE")
                else:
                    self.stable_since = None
                    self.move("TAKING_OFF", now)
            return None
        if self.state in ("LANDING", "WAIT_DISARM"):
            if now - self.started > LAND_TIMEOUT:
                self.move("FAILED", now, "LAND TIMEOUT")
            elif t.landed is True and now - t.land_at <= STATUS_MAX_AGE:
                if not t.armed:
                    self.move("COMPLETE", now, "LAND COMPLETE")
                else:
                    self.move("WAIT_DISARM", now, "LANDED; WAITING DISARM")
            elif self.ack_at is not None and now - self.ack_at > 3 and t.nav_state != 18:
                self.move("ABORTED", now, "LAND MODE LOST")
            return None
        return None

    def feedback(self, t: Telemetry, now: float):
        height = None
        if self.z0 is not None and math.isfinite(t.z) and now - t.position_at <= POSITION_MAX_AGE:
            height = round(-(t.z - self.z0), 3)
        return {"request_id": self.request_id, "command": self.command,
                "transaction_state": self.state, "reason": self.reason,
                "px4_ack": self.ack, "armed": t.armed, "nav_state": t.nav_state,
                "failsafe": t.failsafe, "current_height": height,
                "target_height": TARGET_HEIGHT if self.command == "TAKEOFF" else None,
                "timestamp": time.time()}
