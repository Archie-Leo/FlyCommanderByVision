#!/usr/bin/env python3
"""Independent, read-only RK3576 flight evidence recorder."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import time

from flight_evidence.core import SessionStore, boot_id, read_json, wall_time
from ground_station.command_protocol import decode, encode
from ground_station.protocol import decode_packet


SYSTEM_CHECK_EVENTS = {
    "PX4 DDS": ("PX4_TELEMETRY_FRESH", "PX4_TELEMETRY_STALE"),
    "VIDEO": ("VIDEO_STARTED", "VIDEO_LOST"),
    "METADATA": ("METADATA_STARTED", "METADATA_LOST"),
    "GATEWAY": ("GATEWAY_READY", "GATEWAY_LOST"),
    "COMMAND BRIDGE": ("COMMAND_BRIDGE_READY", "COMMAND_BRIDGE_LOST"),
}


def git_value(repo, *args):
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args],
                                       stderr=subprocess.DEVNULL, timeout=2).decode().strip()
    except (OSError, subprocess.SubprocessError):
        return None


def file_hash(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def manifest_from_env(repo, px4):
    env = os.environ
    return {
        "boot_id": boot_id(), "hostname": socket.gethostname(),
        "git_commit": git_value(repo, "rev-parse", "HEAD"),
        "branch": git_value(repo, "branch", "--show-current"),
        "px4_version": None, "ground_command_enabled": env.get("GROUND_COMMAND_ENABLED") == "1",
        "camera_device": env.get("CAMERA"), "serial_device": env.get("PX4_SERIAL"),
        "ground_station_ip": env.get("GROUND_HOST"),
        "h264_port": env.get("VIDEO_PORT"), "metadata_port": env.get("META_PORT"),
        "command_port": env.get("GROUND_COMMAND_PORT"),
        "calibration_file": env.get("FCV_CALIBRATION_PATH"),
        "calibration_sha256": file_hash(env.get("FCV_CALIBRATION_PATH")),
        "system_mode": "LIVE_GUARDED", "recorder_started_wall": wall_time(),
        "recorder_started_monotonic": time.monotonic(),
        "initial_px4": px4,
    }


def fresh_px4(payload, now_ns=None):
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    data = payload or {}
    status = data.get("status") or {}
    position = data.get("position") or {}
    land = data.get("land") or {}
    control = data.get("control_mode") or {}
    stamp = status.get("received_monotonic_ns")
    connected = isinstance(stamp, int) and 0 <= now_ns - stamp <= 1_500_000_000
    position_at = position.get("received_monotonic_ns")
    pos_fresh = isinstance(position_at, int) and 0 <= now_ns - position_at <= 1_500_000_000
    land_at = land.get("received_monotonic_ns")
    land_fresh = isinstance(land_at, int) and 0 <= now_ns - land_at <= 1_500_000_000
    control_at = control.get("received_monotonic_ns")
    control_fresh = isinstance(control_at, int) and 0 <= now_ns - control_at <= 1_500_000_000
    return {"connected": connected, "armed": status.get("armed") if connected else None,
            "arming_state": status.get("arming_state") if connected else None,
            "nav_state": status.get("nav_state") if connected else None,
            "mode": status.get("mode") if connected else None,
            "failsafe": status.get("failsafe") if connected else None,
            "preflight": status.get("pre_flight_checks_pass") if connected else None,
            "landed": land.get("landed") if land_fresh else None,
            "xy_valid": position.get("xy_valid") if pos_fresh else None,
            "z_valid": position.get("z_valid") if pos_fresh else None,
            "v_xy_valid": position.get("v_xy_valid") if pos_fresh else None,
            "v_z_valid": position.get("v_z_valid") if pos_fresh else None,
            "heading_valid": position.get("heading_good_for_control") if pos_fresh else None,
            "x": position.get("x") if pos_fresh else None,
            "y": position.get("y") if pos_fresh else None,
            "z": position.get("z") if pos_fresh else None,
            "vx": position.get("vx") if pos_fresh else None,
            "vy": position.get("vy") if pos_fresh else None,
            "vz": position.get("vz") if pos_fresh else None,
            "heading": position.get("heading") if pos_fresh else None,
            "px4_timestamp_us": status.get("px4_timestamp_us") if connected else None,
            "control_mode": {key: control.get(key) for key in
                             ("armed", "offboard_enabled", "position_enabled", "velocity_enabled")}
            if control_fresh else None}


class BoardRecorder:
    def __init__(self, root, repo, key, *, gs_ip, session_port=5605, local_port=5606,
                 px4_file="/tmp/fcv_px4_telemetry.json",
                 health_file="/tmp/fcv_system_status.json",
                 gateway_file="/tmp/fcv_gateway_live.json",
                 bridge_file="/tmp/fcv_ground_command.json"):
        self.repo, self.key, self.gs_ip = Path(repo), key, gs_ip
        self.px4_file, self.health_file = px4_file, health_file
        self.gateway_file, self.bridge_file = gateway_file, bridge_file
        self.px4 = fresh_px4(read_json(px4_file))
        self.store = SessionStore(root, manifest_from_env(repo, self.px4))
        self.store.event("system", "BOOT_START", new_state=self.store.manifest_defaults["boot_id"],
                         system=True)
        self.gs_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.gs_sock.bind(("0.0.0.0", session_port))
        self.gs_sock.setblocking(False)
        self.local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.local_sock.bind(("127.0.0.1", local_port))
        self.local_sock.setblocking(False)
        self.metadata = {}
        self.vision_evidence = {}
        self.metadata_received_at = 0.0
        self.gateway = {}
        self.bridge = {}
        self.health = {}
        self.flight_active_seen = False
        self.had_flight_control = False
        self.safe_since = None
        self.effect = None
        self.last_tx_count = None
        self.last_tx_at = None
        self.tx_rate_hz = None
        self.tx_samples = deque()
        self.system_states = {}
        self.safety_state = None
        self.wall_mono_offset = time.time() - time.monotonic()
        self.stopping = False

    def _gs_message(self, data, address):
        if address[0] != self.gs_ip:
            return
        try:
            msg = decode(data, self.key)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return
        kind = msg.get("kind")
        if kind == "session_hello":
            reply = {"kind": "session_ack", "session_id": self.store.active,
                     "board_wall": time.time(), "board_monotonic": time.monotonic(),
                     "boot_id": self.store.manifest_defaults["boot_id"],
                     "issued_at": time.time(), "expires_at": time.time() + 2}
            self.gs_sock.sendto(encode(reply, self.key), address)
            self.store.event("ground_station", "GROUND_STATION_CONNECTED", new_state=address[0],
                             details={"windows_wall": msg.get("windows_wall"),
                                      "windows_monotonic": msg.get("windows_monotonic")},
                             signature=address[0])
        elif kind == "session_event":
            if msg.get("session_id") != self.store.active:
                return
            event = msg.get("event")
            if isinstance(event, str) and len(event) <= 64:
                self.store.event("ground_station", event, reason=msg.get("reason"),
                                 details=msg.get("details") if isinstance(msg.get("details"), dict) else {},
                                 dedup=False)
        elif kind == "session_start":
            if msg.get("session_id") != self.store.active or self.px4.get("armed"):
                return
            self.store.close("MANUAL_START_NEW_SESSION")
            self.store.start("MANUAL_START_SESSION", self.px4)
        elif kind == "session_end" and msg.get("session_id") == self.store.active:
            self.store.event("ground_station", "GROUND_STATION_CLOSED")
            if not self.px4.get("armed"):
                self.store.close("GROUND_STATION_CLOSED")
                self.store.start("CONTINUOUS_RECORDING", self.px4)

    def _local_message(self, data):
        try:
            msg = json.loads(data)
            if isinstance(msg, dict) and msg.get("kind") == "bridge_event":
                event = msg.get("event")
                if isinstance(event, str) and len(event) <= 64:
                    self.store.event("command_bridge", event, old_state=msg.get("old_state"),
                                     new_state=msg.get("new_state"), reason=msg.get("reason"),
                                     details=msg.get("details") or {}, dedup=False)
                return
            if isinstance(msg, dict) and msg.get("kind") == "vision_evidence":
                self.vision_evidence = msg
                return
            self.metadata = decode_packet(data)
            self.metadata_received_at = time.monotonic()
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            pass

    def _state(self, source, event_on, event_off, state, *, details=None):
        key = (source, event_on, event_off)
        old = self.system_states.get(key)
        if old is state:
            return
        self.system_states[key] = state
        self.store.event(source, event_on if state else event_off,
                         old_state=("READY" if old else "LOST") if old is not None else None,
                         new_state="READY" if state else "LOST", details=details or {},
                         system=True, dedup=False)

    def _system(self):
        self.health = read_json(self.health_file) or {}
        checks = self.health.get("checks") or {}
        for name, (yes, no) in SYSTEM_CHECK_EVENTS.items():
            self._state("system", yes, no, bool(checks.get(name)))
        self._state("xrce", "XRCE_READY", "XRCE_LOST", bool(checks.get("SERIAL")),
                    details={"scope": "agent_process_only"})
        self._state("camera", "CAMERA_READY", "CAMERA_LOST",
                    bool(os.environ.get("CAMERA") and Path(os.environ["CAMERA"]).exists()),
                    details={"device": os.environ.get("CAMERA")})
        self._state("vision", "VISION_RUNTIME_READY", "VISION_RUNTIME_LOST",
                    bool(checks.get("CAMERA")))
        self._state("system", "SYSTEM_READY", "SYSTEM_DEGRADED",
                    self.health.get("system") == "READY",
                    details={"checks_failed": [key for key, val in checks.items() if not val]})

    def _px4(self):
        self.px4 = fresh_px4(read_json(self.px4_file))
        p = self.px4
        self.store.event("px4", "PX4_CONTROL_MODE_CHANGED",
                         new_state=p.get("control_mode"), signature=p.get("control_mode"))
        self.store.event("px4", "PX4_STATE_CHANGED", new_state=(p["mode"] if p["connected"] else "STALE"),
                         details={key: p[key] for key in ("connected", "armed", "nav_state", "failsafe",
                                                         "preflight", "landed", "xy_valid", "z_valid",
                                                         "control_mode")})
        if p["armed"] or p["mode"] == "OFFBOARD":
            self.flight_active_seen = True
            self.had_flight_control = True

    def _vision(self):
        op = self.metadata.get("operator") or {}
        gesture = self.metadata.get("gesture") or {}
        stage6 = self.metadata.get("stage6") or {}
        command = self.metadata.get("command") or {}
        state = op.get("state") or "UNAVAILABLE"
        old = getattr(self, "operator_state", None)
        if old != state:
            event = ("OPERATOR_REAUTHORIZED" if state == "LOCKED_HIGH" and old == "OPERATOR_LOST" else
                     "OPERATOR_LOCKED_HIGH" if state == "LOCKED_HIGH" else
                     "OPERATOR_REAUTHORIZED" if "REAUTH" in state else
                     "OPERATOR_LOST" if "LOST" in state else "OPERATOR_LOCK_STARTED")
            self.store.event("stage5", event, old_state=old, new_state=state,
                             details={"track_id": op.get("track_id"), "operator_session_id": op.get("session_id")},
                             dedup=False)
            self.operator_state = state
        raw, stable = gesture.get("raw"), gesture.get("stable")
        evidence = self.vision_evidence
        confidence = evidence.get("gesture_confidence")
        self.store.event("stage4", "GESTURE_RAW_CHANGED", new_state=raw,
                         details={"track_id": op.get("track_id"), "confidence": confidence},
                         signature=raw)
        if raw not in (None, "UNKNOWN") and raw != stable:
            self.store.event("stage4", "GESTURE_CANDIDATE", new_state=raw,
                             details={"gesture": raw, "confidence": confidence,
                                      "track_id": op.get("track_id")}, signature=(raw, stable))
        if evidence.get("authorized_valid") is False and raw not in (None, "UNKNOWN"):
            self.store.event("stage4", "GESTURE_INVALID", new_state=raw,
                             reason="AUTHORIZED_GESTURE_INVALID",
                             details={"gesture": raw, "track_id": op.get("track_id")})
        if stable != getattr(self, "stable_gesture", None):
            event = ("GESTURE_UNKNOWN" if stable in (None, "UNKNOWN") else
                     "GESTURE_RELEASED" if stable == "HOVER" else "GESTURE_CONFIRMED")
            self.store.event("stage4", event, old_state=getattr(self, "stable_gesture", None),
                             new_state=stable, details={"gesture": stable, "track_id": op.get("track_id"),
                                                        "confidence": confidence},
                             dedup=False)
            self.stable_gesture = stable
        intent = stage6.get("intent") or "HOVER"
        self.store.event("stage6", "INTENT_CHANGED", new_state=intent,
                         reason=stage6.get("reason"), details={"valid": stage6.get("valid"),
                                                               "lease": stage6.get("lease")})
        self.store.event("flight_authority", "FLIGHT_AUTHORITY_CHANGED",
                         new_state=command.get("authority") or "BLOCKED",
                         reason=command.get("authority_reason") or "NO_CURRENT_GATEWAY_METADATA")
        if state == "LOCKED_HIGH" or (stage6.get("valid") and intent != "HOVER"):
            self.flight_active_seen = True

    def _gateway(self):
        command = read_json(self.gateway_file) or {}
        age_ns = time.monotonic_ns() - command.get("monotonic_ns", 0)
        self.gateway = command if 0 <= age_ns <= 1_500_000_000 else {}
        g = self.gateway
        velocity = g.get("velocity") or [0, 0, 0]
        self.store.event("gateway", "GATEWAY_OUTPUT_CHANGED", new_state=g.get("intent") or "UNAVAILABLE",
                         reason=g.get("limiter_reason"), details={"intent": g.get("intent"),
                             "authority": g.get("authority"), "safety": g.get("safety_limiter_state"),
                             "ned_velocity": velocity, "yawspeed": g.get("yawspeed")})
        reason = g.get("limiter_reason") or "GATEWAY_UNAVAILABLE"
        blocked = g.get("safety_limiter_state") in ("LIMIT_REACHED", "BLOCKED_INVALID_POSITION") or not g
        safety = (blocked, g.get("safety_limiter_state"), reason)
        if safety != self.safety_state:
            self.store.event("safety_limiter", "SAFETY_BLOCK" if blocked else "SAFETY_PASS",
                             new_state=g.get("safety_limiter_state"), reason=reason,
                             dedup=False)
            self.safety_state = safety
        count = g.get("tx_count")
        if isinstance(count, int):
            now = time.monotonic()
            if self.last_tx_count is not None and count < self.last_tx_count:
                self.tx_samples.clear()
            self.tx_samples.append((now, count))
            while self.tx_samples and now - self.tx_samples[0][0] > 5:
                self.tx_samples.popleft()
            if len(self.tx_samples) > 1 and now - self.tx_samples[0][0] >= 1:
                old_at, old_count = self.tx_samples[0]
                self.tx_rate_hz = round((count - old_count) / (now - old_at), 2)
            self.last_tx_count, self.last_tx_at = count, now
        self.store.event("ros", "ROS_TX_STATE_CHANGED", new_state="PUBLISHING" if g.get("ros_published") else "UNAVAILABLE",
                         details={"publisher_active": bool(g.get("ros_published")),
                                  "rate_hz": self.tx_rate_hz, "flags": g.get("offboard_mode_flags"),
                                  "last_setpoint": {"position": g.get("setpoint_position"),
                                                    "velocity": velocity, "yaw": g.get("setpoint_yaw"),
                                                    "yawspeed": g.get("yawspeed")},
                                  "last_publish_monotonic_ns": g.get("last_publish_monotonic_ns"),
                                  "px4_rx_direct_evidence": "unavailable_online"},
                         signature=(bool(g.get("ros_published")),
                                    g.get("offboard_mode_flags"), velocity, g.get("yawspeed")))
        self.store.summary["ros_tx"] = {
            "publisher_active": bool(g.get("ros_published")),
            "offboard_control_mode_rate_hz": self.tx_rate_hz,
            "trajectory_setpoint_rate_hz": self.tx_rate_hz,
            "mode_flags": g.get("offboard_mode_flags"),
            "last_setpoint": velocity,
            "last_publish_monotonic_ns": g.get("last_publish_monotonic_ns")}
        self._effect(velocity)

    def _effect(self, velocity):
        if not self.px4.get("connected") or not self.px4.get("armed"):
            self.effect = None
            return
        try:
            vector = [float(value) for value in velocity]
            speed = sum(value * value for value in vector) ** .5
            position = [float(self.px4[key]) for key in ("x", "y", "z")]
            actual = [float(self.px4[key]) for key in ("vx", "vy", "vz")]
        except (TypeError, ValueError):
            self.effect = None
            return
        if speed < .08:
            self.effect = None
            return
        intent = self.gateway.get("intent")
        now = time.monotonic()
        if self.effect is None or self.effect["intent"] != intent:
            self.effect = {"intent": intent, "at": now, "position": position, "done": False}
            return
        if self.effect["done"] or now - self.effect["at"] < 1.5:
            return
        projection = sum(vector[i] * actual[i] for i in range(3)) / speed
        delta = sum(vector[i] * (position[i] - self.effect["position"][i]) for i in range(3)) / speed
        observed = projection > .035 or delta > .05
        self.store.event("px4", "COMMAND_EFFECT_OBSERVED" if observed else "COMMAND_EFFECT_NOT_OBSERVED",
                         new_state=intent, reason=None if observed else "NO_DIRECTIONAL_RESPONSE_IN_WINDOW",
                         details={"requested_velocity": vector, "actual_velocity": actual,
                                  "projected_velocity": round(projection, 3),
                                  "projected_displacement_m": round(delta, 3),
                                  "window_sec": round(now - self.effect["at"], 2)}, dedup=False)
        self.effect["done"] = True

    def _bridge(self):
        self.bridge = read_json(self.bridge_file) or {}
        state = self.bridge.get("transaction")
        self.store.event("command_bridge", "TRANSACTION_CHANGED", new_state=state,
                         details={"enabled": self.bridge.get("enabled"),
                                  "command_count": self.bridge.get("command_count")})
        if state not in (None, "IDLE", "COMPLETE", "FAILED", "ABORTED"):
            self.flight_active_seen = True
            self.had_flight_control = True

    def _telemetry(self):
        p, g, m, b = self.px4, self.gateway, self.metadata, self.bridge
        s, c = m.get("stage6") or {}, m.get("command") or {}
        op, gesture = m.get("operator") or {}, m.get("gesture") or {}
        active = (p["armed"] or p["mode"] == "OFFBOARD" or
                  b.get("transaction") not in (None, "IDLE", "COMPLETE", "FAILED", "ABORTED") or
                  op.get("state") == "LOCKED_HIGH" or bool(s.get("valid")))
        if not active:
            return
        velocity = g.get("velocity") or [None, None, None]
        row = {"px4_connected": p["connected"], "armed": p["armed"],
               "nav_state": p["nav_state"], "failsafe": p["failsafe"],
               "preflight": p["preflight"], "landed": p["landed"],
               **{key: p[key] for key in ("x", "y", "z", "vx", "vy", "vz", "heading")},
               "heading_valid": p["heading_valid"], "operator_state": op.get("state"),
               "gesture": gesture.get("stable"), "intent": s.get("intent"),
               "lease_active": s.get("lease"), "authority_state": c.get("authority"),
               "authority_reason": c.get("authority_reason"),
               "safety_state": g.get("safety_limiter_state"),
               "safety_reason": g.get("limiter_reason"),
               "gateway_vx": velocity[0], "gateway_vy": velocity[1], "gateway_vz": velocity[2],
               "offboard_position_flag": (g.get("offboard_mode_flags") or {}).get("position"),
               "offboard_velocity_flag": (g.get("offboard_mode_flags") or {}).get("velocity"),
               "ros_tx_vx": velocity[0] if g.get("ros_published") else None,
               "ros_tx_vy": velocity[1] if g.get("ros_published") else None,
               "ros_tx_vz": velocity[2] if g.get("ros_published") else None,
               "ros_tx_age_ms": (round((time.monotonic_ns() - g["last_publish_monotonic_ns"])/1e6, 1)
                                 if g.get("last_publish_monotonic_ns") else None),
               "command_transaction": b.get("transaction"), "command_state": b.get("service")}
        self.store.telemetry(row)

    def poll_once(self):
        wall_mono_offset = time.time() - time.monotonic()
        if abs(wall_mono_offset - self.wall_mono_offset) > 2:
            self.store.event("system", "CLOCK_ADJUSTED", system=True,
                             details={"previous_offset_sec": self.wall_mono_offset,
                                      "current_offset_sec": wall_mono_offset}, dedup=False)
        self.wall_mono_offset = wall_mono_offset
        for sock in (self.gs_sock, self.local_sock):
            for _ in range(64):
                try:
                    data, address = sock.recvfrom(8192)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if sock is self.gs_sock:
                    self._gs_message(data, address)
                else:
                    self._local_message(data)
        if time.monotonic() - self.metadata_received_at > .6:
            self.metadata = {}
            self.vision_evidence = {}
        self._px4()
        self._bridge()
        self._gateway()
        self._vision()
        self._system()
        self._telemetry()
        self.store.flush_if_due()
        operator_active = ((self.metadata.get("operator") or {}).get("state") == "LOCKED_HIGH" or
                           bool((self.metadata.get("stage6") or {}).get("valid")))
        should_close = self.had_flight_control or (self.flight_active_seen and not operator_active)
        if should_close and self.px4["landed"] is True and self.px4["armed"] is False:
            if self.safe_since is None:
                self.safe_since = time.monotonic()
            elif time.monotonic() - self.safe_since >= 8:
                self.store.close("LANDED_DISARMED_STABLE")
                self.store.start("CONTINUOUS_RECORDING", self.px4)
                self.flight_active_seen = False
                self.had_flight_control = False
                self.safe_since = None
        else:
            self.safe_since = None

    def run(self):
        try:
            while not self.stopping:
                started = time.monotonic()
                try:
                    self.poll_once()
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    self.store.degraded = True
                    print(f"RECORDER_DEGRADED: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(max(0, .1 - (time.monotonic() - started)))
        except KeyboardInterrupt:
            pass
        finally:
            self.store.event("system", "SYSTEM_STOP", system=True)
            self.store.close("SERVICE_STOP")
            self.gs_sock.close()
            self.local_sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/var/lib/fcv/logs/sessions"))
    parser.add_argument("--repo", type=Path, default=Path("/home/lckfb/FlyCommanderByVision"))
    parser.add_argument("--key-file", type=Path, default=Path("/etc/fcv/ground_command.key"))
    parser.add_argument("--ground-ip", default=os.getenv("GROUND_HOST", "192.168.1.7"))
    args = parser.parse_args()
    recorder = BoardRecorder(args.root, args.repo, args.key_file.read_bytes().strip(),
                             gs_ip=args.ground_ip)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: setattr(recorder, "stopping", True))
    recorder.run()


if __name__ == "__main__":
    main()
