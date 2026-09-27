#!/usr/bin/env python3
"""Read PX4 DDS state and atomically expose a local, read-only snapshot.

This process creates subscriptions and a timer only. It never creates a PX4
input publisher. The fan-out process may read the file without importing ROS.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from px4_msgs.msg import FailsafeFlags, VehicleCommandAck, VehicleLocalPosition, VehicleStatus
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy


def finite(value):
    import math
    number = float(value)
    return round(number, 3) if math.isfinite(number) else None


def nav_name(value):
    for name in dir(VehicleStatus):
        if name.startswith("NAVIGATION_STATE_") and getattr(VehicleStatus, name) == value:
            return name.removeprefix("NAVIGATION_STATE_")
    return f"UNKNOWN_{value}"


class Px4TelemetryReader(Node):
    def __init__(self, path: Path):
        super().__init__("fcv_px4_telemetry_readonly")
        self.path = path
        self.status = self.position = self.failsafe_flags = self.ack = None
        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(VehicleStatus, "/fmu/out/vehicle_status_v4", self.on_status, qos)
        self.create_subscription(VehicleLocalPosition, "/fmu/out/vehicle_local_position_v1",
                                 self.on_position, qos)
        self.create_subscription(FailsafeFlags, "/fmu/out/failsafe_flags", self.on_failsafe, qos)
        self.create_subscription(VehicleCommandAck, "/fmu/out/vehicle_command_ack_v1",
                                 self.on_ack, qos)
        self.create_timer(0.1, self.write_snapshot)

    def on_status(self, msg):
        self.status = {"received_monotonic_ns": time.monotonic_ns(),
                       "nav_state": int(msg.nav_state), "mode": nav_name(int(msg.nav_state)),
                       "armed": int(msg.arming_state) == int(msg.ARMING_STATE_ARMED),
                       "arming_state": int(msg.arming_state), "failsafe": bool(msg.failsafe),
                       "accepts_offboard_setpoints": bool(msg.accepts_offboard_setpoints),
                       "px4_timestamp_us": int(msg.timestamp)}

    def on_position(self, msg):
        self.position = {"received_monotonic_ns": time.monotonic_ns(),
                         "xy_valid": bool(msg.xy_valid), "z_valid": bool(msg.z_valid),
                         "v_xy_valid": bool(msg.v_xy_valid), "v_z_valid": bool(msg.v_z_valid),
                         "x": finite(msg.x), "y": finite(msg.y), "z": finite(msg.z),
                         "vx": finite(msg.vx), "vy": finite(msg.vy), "vz": finite(msg.vz),
                         "heading": finite(msg.heading),
                         "px4_timestamp_us": int(msg.timestamp)}

    def on_failsafe(self, msg):
        self.failsafe_flags = {"received_monotonic_ns": time.monotonic_ns(),
                               "px4_timestamp_us": int(msg.timestamp)}

    def on_ack(self, msg):
        self.ack = {"received_monotonic_ns": time.monotonic_ns(),
                    "command": int(msg.command), "result": int(msg.result),
                    "px4_timestamp_us": int(msg.timestamp)}

    def write_snapshot(self):
        payload = {"status": self.status, "position": self.position,
                   "failsafe_flags": self.failsafe_flags, "last_vehicle_command_ack": self.ack}
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False),
                             encoding="utf-8")
        os.replace(temporary, self.path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/fcv_px4_telemetry.json"))
    args = parser.parse_args()
    rclpy.init()
    node = Px4TelemetryReader(args.output)
    try:
        rclpy.spin(node)
    except ExternalShutdownException:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
