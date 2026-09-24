"""Read-only ROS/PX4 observer; it never publishes a control message."""
from __future__ import annotations

import threading
import time

from drone_control_gateway.msg import Intent
from px4_msgs.msg import (OffboardControlMode, TrajectorySetpoint,
                          VehicleLocalPosition, VehicleStatus)
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from .run_context import RunContext, write_jsonl


TRAJECTORY_TOPIC = "/fmu/in/trajectory_setpoint"
OFFBOARD_TOPIC = "/fmu/in/offboard_control_mode"
LOCAL_POSITION_CANDIDATES = (
    "/fmu/out/vehicle_local_position", "/fmu/out/vehicle_local_position_v1")
STATUS_CANDIDATES = (
    "/fmu/out/vehicle_status_v1", "/fmu/out/vehicle_status")


def local_position_fields(message):
    return {"px4_source_timestamp_us": int(message.timestamp),
            "px4_sample_timestamp_us": int(message.timestamp_sample),
            "x": float(message.x), "y": float(message.y), "z": float(message.z),
            "vx": float(message.vx), "vy": float(message.vy), "vz": float(message.vz),
            "xy_valid": bool(message.xy_valid), "z_valid": bool(message.z_valid),
            "v_xy_valid": bool(message.v_xy_valid),
            "v_z_valid": bool(message.v_z_valid)}


def vehicle_status_fields(message):
    return {"px4_source_timestamp_us": int(message.timestamp),
            "arming_state": int(message.arming_state),
            "armed": int(message.arming_state) == int(message.ARMING_STATE_ARMED),
            "nav_state": int(message.nav_state),
            "failsafe": bool(message.failsafe)}


def trajectory_fields(message):
    velocity = [float(value) for value in message.velocity]
    position = [float(value) for value in message.position]
    return {"px4_source_timestamp_us": int(message.timestamp),
            "trajectory_position": position,
            "trajectory_velocity_x": velocity[0],
            "trajectory_velocity_y": velocity[1],
            "trajectory_velocity_z": velocity[2],
            "yawspeed": float(message.yawspeed)}


class Gate6CObserver(Node):
    """Discover live topic types, then log received messages in host time."""
    def __init__(self, context: RunContext, gateway_log, px4_log,
                 *, intent_topic: str):
        super().__init__("stage6_gate6c_evidence_observer")
        self.run_context = context
        self.gateway_log = gateway_log
        self.px4_log = px4_log
        self.intent_topic = intent_topic
        self._snapshot_lock = threading.Lock()
        self._latest_position = None
        self._latest_status = None
        self._latest_position_ns = None
        self.observed_topics = {}
        self.publisher_sources = {}
        self.topic_errors = {}
        self.event_counts = {"received_intent": 0, "trajectory_setpoint": 0,
                             "offboard_control_mode": 0,
                             "local_position": 0, "vehicle_status": 0}
        self.evidence_subscriptions = {}
        qos = QoSProfile(depth=20, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.evidence_subscriptions[intent_topic] = self.create_subscription(
            Intent, intent_topic, self._on_intent, qos)
        self.discovery_timer = self.create_timer(.5, self.discover_topics)
        self.discover_topics()

    def _stamp(self):
        now_ns = time.monotonic_ns()
        return {**self.run_context.stamp(now_ns),
                "host_receive_monotonic_ns": now_ns,
                "ros_receive_time_ns": self.get_clock().now().nanoseconds}

    def _write_gateway(self, record):
        write_jsonl(self.gateway_log, record)
        self.event_counts[record["event_type"]] += 1

    def _write_px4(self, record):
        write_jsonl(self.px4_log, record)
        self.event_counts[record["event_type"]] += 1

    def _on_intent(self, message):
        self._write_gateway({**self._stamp(), "event_type": "received_intent",
            "topic": self.intent_topic,
            "ros_source_stamp_ns": int(message.stamp.sec) * 1_000_000_000
                                   + int(message.stamp.nanosec),
            "received_intent": message.intent,
            "intent_valid": bool(message.valid), "intent_seq": int(message.seq),
            "intent_reason": message.reason,
            "operator_id": int(message.operator_id),
            "requested_speed_m_s": float(message.requested_speed_m_s),
            "requested_yaw_rate_rad_s": float(message.requested_yaw_rate_rad_s)})

    def _on_trajectory(self, message):
        self._write_gateway({**self._stamp(), "event_type": "trajectory_setpoint",
                             "topic": TRAJECTORY_TOPIC,
                             **trajectory_fields(message)})

    def _on_offboard(self, message):
        self._write_gateway({**self._stamp(), "event_type": "offboard_control_mode",
            "topic": OFFBOARD_TOPIC,
            "px4_source_timestamp_us": int(message.timestamp),
            "position": bool(message.position), "velocity": bool(message.velocity),
            "acceleration": bool(message.acceleration)})

    def _on_position(self, message, topic):
        stamp = self._stamp()
        payload = local_position_fields(message)
        with self._snapshot_lock:
            self._latest_position = payload
            self._latest_position_ns = stamp["host_monotonic_ns"]
        self._write_px4({**stamp, "event_type": "local_position",
                         "topic": topic, **payload})

    def _on_status(self, message, topic):
        stamp = self._stamp()
        payload = vehicle_status_fields(message)
        with self._snapshot_lock:
            self._latest_status = payload
        self._write_px4({**stamp, "event_type": "vehicle_status",
                         "topic": topic, **payload})

    def discover_topics(self):
        graph = {name: types for name, types in self.get_topic_names_and_types()}
        for name in (TRAJECTORY_TOPIC, OFFBOARD_TOPIC):
            if name in graph:
                self.publisher_sources[name] = sorted({
                    f"{info.node_namespace.rstrip('/')}/{info.node_name}"
                    for info in self.get_publishers_info_by_topic(name)})
        specs = [(TRAJECTORY_TOPIC, "px4_msgs/msg/TrajectorySetpoint",
                  TrajectorySetpoint, self._on_trajectory),
                 (OFFBOARD_TOPIC, "px4_msgs/msg/OffboardControlMode",
                  OffboardControlMode, self._on_offboard)]
        position_topic = next((name for name in LOCAL_POSITION_CANDIDATES
                               if name in graph), None)
        status_topic = next((name for name in STATUS_CANDIDATES
                             if name in graph), None)
        if position_topic:
            specs.append((position_topic, "px4_msgs/msg/VehicleLocalPosition",
                          VehicleLocalPosition,
                          lambda msg, topic=position_topic: self._on_position(msg, topic)))
        if status_topic:
            specs.append((status_topic, "px4_msgs/msg/VehicleStatus",
                          VehicleStatus,
                          lambda msg, topic=status_topic: self._on_status(msg, topic)))
        qos = QoSProfile(depth=20, reliability=ReliabilityPolicy.BEST_EFFORT)
        for name, expected, cls, callback in specs:
            if name not in graph or name in self.evidence_subscriptions:
                continue
            if expected not in graph[name]:
                self.topic_errors[name] = {"expected": expected,
                                           "actual": graph[name]}
                continue
            self.evidence_subscriptions[name] = self.create_subscription(
                cls, name, callback, qos)
            self.observed_topics[name] = expected

    def px4_snapshot(self):
        with self._snapshot_lock:
            fresh = (self._latest_position_ns is not None and
                     time.monotonic_ns() - self._latest_position_ns < 1_000_000_000)
            return (dict(self._latest_position) if fresh else None,
                    dict(self._latest_status) if fresh and self._latest_status else None)
