"""Thin ROS 2 publisher for the existing drone_control_gateway/Intent.msg."""
from __future__ import annotations

import hashlib
import json
import threading
import time

import rclpy
from drone_control_gateway.msg import Intent
from px4_msgs.msg import VehicleStatus
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from .flight_authority import FlightAuthorityGate
from .intent_adapter import AuthorizedGestureIntentAdapter


DRY_RUN_TOPIC = "/interaction/intent_dry_run"
GATEWAY_TOPIC = "/interaction/intent"


def operator_id_from_session(session_id: str | None):
    """Stable diagnostic int64 only; this value is never an authorization key."""
    if not session_id:
        return 0
    digest = hashlib.blake2b(session_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & ((1 << 63)-1)


class AuthorizedIntentPublisher(Node):
    def __init__(self, *, topic=DRY_RUN_TOPIC, allow_live_output=False,
                 timeout_ms=300, event_log=None, run_context=None,
                 px4_status_timeout_ms=1500):
        if topic == GATEWAY_TOPIC and not allow_live_output:
            raise ValueError("Gateway topic requires explicit allow_live_output")
        if topic not in {DRY_RUN_TOPIC, GATEWAY_TOPIC}:
            raise ValueError("unsupported Intent topic")
        super().__init__("stage6_authorized_intent_adapter")
        self.adapter = AuthorizedGestureIntentAdapter(timeout_ms)
        # Dry-run keeps the frozen Gate 6A/6B transport semantics. Only the
        # live Gateway route can acquire flight authority from actual PX4.
        self.flight_gate = (FlightAuthorityGate(
            offboard_nav_state=VehicleStatus.NAVIGATION_STATE_OFFBOARD,
            status_timeout_ms=px4_status_timeout_ms)
            if topic == GATEWAY_TOPIC else None)
        self._lock = threading.Lock()
        self.publisher = self.create_publisher(
            Intent,topic,QoSProfile(depth=10,reliability=ReliabilityPolicy.RELIABLE))
        self.status_subscription = None
        if self.flight_gate is not None:
            self.status_subscription = self.create_subscription(
                VehicleStatus,"/fmu/out/vehicle_status_v1",self._on_px4_status,
                QoSProfile(depth=10,reliability=ReliabilityPolicy.BEST_EFFORT))
        self.timer = self.create_timer(.05,self.publish_once)
        self.topic = topic
        self.event_log = event_log
        self.run_context = run_context
        self._vision_log = {}
        self.last_decision = None
        self.last_submit_reason = "NO_AUTHORIZED_GESTURE"
        self._stopping = False

    def submit(self, authorized, vision_log=None):
        now_ns = time.monotonic_ns()
        now_ms = now_ns//1_000_000
        with self._lock:
            if self._stopping:
                return False
            if self.flight_gate is not None:
                allowed, reason = self.flight_gate.allow(authorized,vision_log or {},now_ns)
                if not allowed:
                    self.adapter.clear(reason)
                    self.last_submit_reason = reason
                    self._vision_log = dict(vision_log or {})
                    return False
            accepted = self.adapter.ingest(authorized,now_ms)
            self.last_submit_reason = self.adapter._reason
            self._vision_log = dict(vision_log or {})
        return accepted

    def _on_px4_status(self, message):
        now_ns = time.monotonic_ns()
        with self._lock:
            if self._stopping or self.flight_gate is None:
                return
            changed = self.flight_gate.update_status(
                armed=int(message.arming_state) == int(message.ARMING_STATE_ARMED),
                nav_state=int(message.nav_state),failsafe=bool(message.failsafe),
                now_ns=now_ns)
            if changed:
                self.adapter.clear(self.flight_gate.transition_reason)
                # Do not wait for the next 50-ms timer tick on an authority edge.
                self._publish_once_locked(now_ns//1_000_000)

    def authority_snapshot(self):
        with self._lock:
            if self.flight_gate is None:
                return {"flight_authority_gate_active":False,
                        "px4_armed":None,"px4_nav_state":None,
                        "px4_failsafe":None,
                        "flight_authority_enabled":False,
                        "require_fresh_gesture":False,
                        "authority_transition_reason":"DRY_RUN_NO_FLIGHT_AUTHORITY"}
            if self.flight_gate.refresh(time.monotonic_ns()):
                self.adapter.clear(self.flight_gate.transition_reason)
            return {"flight_authority_gate_active":True,
                    **self.flight_gate.snapshot()}

    def vision_failed(self, reason="VISION_PIPELINE_EXCEPTION"):
        with self._lock:
            self.adapter.clear(reason)
            self._vision_log = {"ownership_state":"VISION_FAILURE"}

    def stop_and_publish(self, reason="VISION_STOPPED"):
        """Revoke the lease and send the final safe Intent before ROS shutdown."""
        with self._lock:
            self._stopping = True
            self.timer.cancel()
            self.adapter.clear(reason)
            self._vision_log = {"ownership_state":"VISION_FAILURE"}
            self._publish_once_locked(time.monotonic_ns()//1_000_000)

    def publish_once(self):
        now_ms = time.monotonic_ns()//1_000_000
        with self._lock:
            if self._stopping:
                return
            if self.flight_gate is not None and self.flight_gate.refresh(time.monotonic_ns()):
                self.adapter.clear(self.flight_gate.transition_reason)
            self._publish_once_locked(now_ms)

    def _publish_once_locked(self, now_ms):
        decision = self.adapter.tick(now_ms)
        vision = self._vision_log.copy()
        authority = ({"flight_authority_gate_active":True,
                      **self.flight_gate.snapshot()}
                     if self.flight_gate is not None else
                     {"flight_authority_gate_active":False,
                      "px4_armed":None,"px4_nav_state":None,
                      "px4_failsafe":None,
                      "flight_authority_enabled":False,
                      "require_fresh_gesture":False,
                      "authority_transition_reason":"DRY_RUN_NO_FLIGHT_AUTHORITY"})
        self.last_decision = decision
        run_stamp = (self.run_context.stamp() if self.run_context is not None
                     else {"host_monotonic_ns": time.monotonic_ns(),
                           "run_elapsed_ms": None})
        msg = Intent()
        msg.stamp = self.get_clock().now().to_msg()
        msg.operator_id = operator_id_from_session(decision.operator_session_id)
        msg.intent = decision.intent
        msg.confidence = float(decision.confidence)
        msg.valid = decision.valid
        msg.seq = decision.seq
        msg.reason = decision.reason
        msg.requested_speed_m_s = 0.0  # Gateway owns nominal speed and clamp.
        msg.requested_yaw_rate_rad_s = 0.0
        self.publisher.publish(msg)
        if self.event_log is not None:
            record = {"timestamp_ms":now_ms,"topic":self.topic,
                      **run_stamp,
                      "ros_source_stamp_ns": int(msg.stamp.sec) * 1_000_000_000
                                             + int(msg.stamp.nanosec),
                      "operator_session_id":decision.operator_session_id,
                      "ownership_state":vision.get("ownership_state",decision.ownership_state),
                      "gesture_raw":vision.get("gesture_raw"),
                      "gesture_stable":vision.get("gesture_stable"),
                      "authorized_gesture_valid":vision.get("authorized_gesture",{}).get("valid",False),
                      "authorized_gesture":vision.get("authorized_gesture",{}).get("gesture","UNKNOWN"),
                      "intent":decision.intent,"intent_seq":decision.seq,
                      "valid": decision.valid, "reason": decision.reason,
                      "intent_age_ms":decision.intent_age_ms,
                      "input_age_ms":decision.intent_age_ms,
                      "requested_speed_m_s": float(msg.requested_speed_m_s),
                      "requested_yaw_rate_rad_s": float(msg.requested_yaw_rate_rad_s),
                      "control_active":decision.control_active,
                      "control_reason":decision.reason,
                      "ros_output_valid":decision.valid,
                      "source_timestamp_ms":decision.source_timestamp_ms}
            record.update(authority)
            record["identity_authorized"] = bool(
                vision.get("ownership_state") == "LOCKED_HIGH" and
                vision.get("operator_session_id"))
            record["motion_lease_active"] = bool(
                decision.valid and decision.intent != "HOVER")
            self.event_log.write(json.dumps(record,allow_nan=False)+"\n")
            self.event_log.flush()
