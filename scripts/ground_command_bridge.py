#!/usr/bin/env python3
"""Independent Ground Station command receiver; disabled until explicitly enabled.

The service may boot in READY/IDLE. It never publishes during startup and
never replays a transaction following restart.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ground_station.command_protocol import decode, encode
from scripts.ground_command_core import Telemetry, Transaction


class Bridge:
    def __init__(self, *, port, allowed_ip, key, enabled, height_verified, ledger, state_path):
        import rclpy
        from px4_msgs.msg import (VehicleCommand, VehicleCommandAck,
                                  VehicleLandDetected, VehicleLocalPosition, VehicleStatus)
        from rclpy.executors import ExternalShutdownException
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        self.rclpy = rclpy
        self.ExternalShutdownException = ExternalShutdownException
        self.VehicleCommand = VehicleCommand
        rclpy.init()
        self.node = Node("fcv_ground_command_bridge")
        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.node.create_subscription(VehicleStatus, "/fmu/out/vehicle_status_v4", self.status, qos)
        self.node.create_subscription(VehicleLocalPosition,
            "/fmu/out/vehicle_local_position_v1", self.position, qos)
        self.node.create_subscription(VehicleLandDetected,
            "/fmu/out/vehicle_land_detected", self.land, qos)
        self.node.create_subscription(VehicleCommandAck,
            "/fmu/out/vehicle_command_ack_v1", self.ack, qos)
        self.output_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                                     durability=DurabilityPolicy.VOLATILE)
        # Discover PX4's subscriber at boot; creating a publisher sends nothing.
        self.publisher = self.node.create_publisher(VehicleCommand,
                                                    "/fmu/in/vehicle_command", self.output_qos)
        self.t = Telemetry()
        self.transaction = None
        self.terminal_saved = None
        self.last_status_timestamp_us = 0
        self.expected_ack_after_us = 0
        self.command_count = 0
        self.allowed_ip, self.key = allowed_ip, key
        self.enabled, self.height_verified = enabled, height_verified
        self.state_path = state_path
        self.db = sqlite3.connect(ledger)
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, command TEXT, state TEXT)")
        self.db.execute("UPDATE requests SET state='ABORTED_RESTART' WHERE state='ACTIVE'")
        self.db.commit()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        self.sock.setblocking(False)
        self._write_state()

    def status(self, msg):
        self.t.status_at = time.monotonic()
        self.last_status_timestamp_us = int(msg.timestamp)
        self.t.armed = msg.arming_state == msg.ARMING_STATE_ARMED
        self.t.nav_state = int(msg.nav_state)
        self.t.failsafe = bool(msg.failsafe)
        self.t.preflight = bool(msg.pre_flight_checks_pass)

    def position(self, msg):
        self.t.position_at = time.monotonic()
        self.t.z_valid = bool(msg.z_valid)
        self.t.vz_valid = bool(msg.v_z_valid)
        self.t.z = float(msg.z)
        self.t.vz = float(msg.vz)
        self.t.z_reset = int(msg.z_reset_counter)
        self.t.vz_reset = int(msg.vz_reset_counter)

    def land(self, msg):
        self.t.land_at = time.monotonic()
        self.t.landed = bool(msg.landed)

    def ack(self, msg):
        # Commander routes ACK to the request's source component. Its ACK
        # from_external flag is not set in PX4 1.18, so do not filter on it.
        if (self.transaction and msg.target_system == 1 and msg.target_component == 191 and
                int(msg.timestamp) > self.expected_ack_after_us):
            self.transaction.on_ack(int(msg.command), int(msg.result), time.monotonic())

    def _write_state(self):
        state = {"service": "READY", "transaction": self.transaction.state if self.transaction else "IDLE",
                 "command_count": self.command_count, "enabled": self.enabled,
                 "takeoff_height_verified": self.height_verified,
                 "updated_at": time.time()}
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self.state_path)
        return state

    def _send_feedback(self, address, *, request_id=None, reason="",
                       transaction_state=None, command=None):
        now = time.monotonic()
        current = self.transaction.feedback(self.t, now) if self.transaction else {}
        state = self._write_state()
        body = {"kind": "feedback", "request_id": request_id or current.get("request_id"),
                "command": command or current.get("command"),
                "transaction_state": transaction_state or current.get("transaction_state", "IDLE"),
                "reason": reason or current.get("reason", ""), "px4_ack": current.get("px4_ack"),
                "armed": self.t.armed, "nav_state": self.t.nav_state, "failsafe": self.t.failsafe,
                "current_height": current.get("current_height"), "target_height": current.get("target_height"),
                "enabled": self.enabled, "takeoff_height_verified": self.height_verified,
                "bridge_state": state["service"], "timestamp": time.time(),
                "issued_at": time.time(), "expires_at": time.time() + 2}
        self.sock.sendto(encode(body, self.key), address)

    def _receive(self):
        while True:
            try:
                packet, address = self.sock.recvfrom(4096)
            except BlockingIOError:
                return
            if address[0] != self.allowed_ip:
                continue
            try:
                body = decode(packet, self.key)
                kind = body["kind"]
                if kind == "ping":
                    self._send_feedback(address)
                    continue
                if kind != "request":
                    continue
                request_id = body["request_id"]
                command = body["command"]
                if not isinstance(request_id, str) or not 8 <= len(request_id) <= 80:
                    continue
                if command not in ("TAKEOFF", "LAND"):
                    self._send_feedback(address, request_id=request_id,
                                        reason="UNSUPPORTED COMMAND", transaction_state="FAILED")
                    continue
                existing = self.db.execute("SELECT command,state FROM requests WHERE id=?", (request_id,)).fetchone()
                if existing:
                    self._send_feedback(address, request_id=request_id,
                                        reason="DUPLICATE " + existing[1])
                    continue
                # Persist before any command can be published. Reboot cannot replay.
                self.db.execute("INSERT INTO requests VALUES (?,?,?)", (request_id, command, "ACTIVE"))
                self.db.commit()
                reason = ("BRIDGE DISABLED" if not self.enabled else
                          "COMMAND BUSY" if self.transaction and self.transaction.active else "")
                if reason:
                    self.db.execute("UPDATE requests SET state='REJECTED' WHERE id=?", (request_id,))
                    self.db.commit()
                    self._send_feedback(address, request_id=request_id, reason=reason,
                                        transaction_state="FAILED", command=command)
                    continue
                from scripts.ground_command_core import precheck
                now = time.monotonic()
                reason = precheck(command, self.t, now, takeoff_height_verified=self.height_verified)
                if reason:
                    self.db.execute("UPDATE requests SET state='REJECTED' WHERE id=?", (request_id,))
                    self.db.commit()
                    self._send_feedback(address, request_id=request_id, reason=reason,
                                        transaction_state="FAILED", command=command)
                    continue
                self.transaction = Transaction(request_id, command, now,
                                               self.t.z if command == "TAKEOFF" else None,
                                               self.t.z_reset, self.t.vz_reset)
                self.client = address
                self._send_feedback(address)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue

    def _publish(self, name):
        # Only these three commands exist; no mode switch, OFFBOARD, or DISARM.
        msg = self.VehicleCommand()
        msg.timestamp = int(time.time_ns() // 1000)
        msg.command = {"ARM": msg.VEHICLE_CMD_COMPONENT_ARM_DISARM,
                       "TAKEOFF": msg.VEHICLE_CMD_NAV_TAKEOFF,
                       "LAND": msg.VEHICLE_CMD_NAV_LAND}[name]
        # PX4 command parameters use NaN for unspecified fields. Zero is a
        # real latitude/longitude/altitude, not an "unset" sentinel.
        msg.param1 = msg.param2 = msg.param3 = msg.param4 = float("nan")
        msg.param5 = msg.param6 = float("nan")
        msg.param7 = float("nan")
        if name == "ARM":
            msg.param1 = 1.0
            msg.param2 = 0.0  # Never use PX4's force-arm bypass sentinel.
        # PX4 local-only native TAKEOFF uses verified MIS_TAKEOFF_ALT.
        # param7 is AMSL; NaN selects PX4's configured takeoff altitude.
        msg.target_system = 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 191
        msg.from_external = True
        self.expected_ack_after_us = self.last_status_timestamp_us
        self.publisher.publish(msg)
        self.command_count += 1

    def run(self):
        try:
            while self.rclpy.ok():
                self.rclpy.spin_once(self.node, timeout_sec=0.05)
                self._receive()
                if self.transaction:
                    name = self.transaction.tick(self.t, time.monotonic(),
                                                 height_verified=self.height_verified)
                    if name:
                        self._publish(name)
                    if (self.transaction.state in self.transaction.TERMINAL and
                            self.terminal_saved != self.transaction.request_id):
                        self.db.execute("UPDATE requests SET state=? WHERE id=?",
                            (self.transaction.state, self.transaction.request_id))
                        self.db.commit()
                        self.terminal_saved = self.transaction.request_id
                    if hasattr(self, "client"):
                        self._send_feedback(self.client)
                else:
                    self._write_state()
        except self.ExternalShutdownException:
            pass
        finally:
            self.sock.close()
            if self.transaction and self.transaction.active:
                self.db.execute("UPDATE requests SET state='ABORTED_RESTART' WHERE id=?",
                                (self.transaction.request_id,))
                self.db.commit()
            self.db.close()
            self.node.destroy_node()
            if self.rclpy.ok():
                self.rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5604)
    parser.add_argument("--allowed-ip", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=Path("/var/lib/fcv/ground_commands.sqlite3"))
    parser.add_argument("--state-file", type=Path, default=Path("/tmp/fcv_ground_command.json"))
    parser.add_argument("--enable-live-commands", action="store_true")
    parser.add_argument("--verified-native-takeoff-height", action="store_true")
    args = parser.parse_args()
    key = args.key_file.read_bytes().strip()
    if len(key) < 32:
        raise SystemExit("Ground command key must contain at least 32 bytes")
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    Bridge(port=args.port, allowed_ip=args.allowed_ip, key=key,
           enabled=args.enable_live_commands, height_verified=args.verified_native_takeoff_height,
           ledger=args.ledger, state_path=args.state_file).run()


if __name__ == "__main__":
    main()
