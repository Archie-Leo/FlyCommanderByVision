#!/usr/bin/env python3
"""Isolated ROS 2 Intent transport smoke; never publishes Gateway topic."""
from __future__ import annotations

import json
import time

import rclpy
from drone_control_gateway.msg import Intent
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from stage5.types import AuthorizedGestureV1
from stage6.ros_intent_node import AuthorizedIntentPublisher, DRY_RUN_TOPIC


def main():
    rclpy.init()
    publisher = AuthorizedIntentPublisher(topic=DRY_RUN_TOPIC)
    observer = Node("stage6_dry_run_observer")
    received = []
    observer.create_subscription(Intent,DRY_RUN_TOPIC,
        lambda msg: received.append((msg.intent,msg.valid,msg.seq,msg.reason)),
        QoSProfile(depth=10,reliability=ReliabilityPolicy.RELIABLE))
    try:
        deadline = time.monotonic()+3.
        while publisher.publisher.get_subscription_count() == 0 and time.monotonic()<deadline:
            rclpy.spin_once(observer,timeout_sec=.05)
        if publisher.publisher.get_subscription_count() == 0:
            raise RuntimeError("dry-run subscriber discovery timeout")
        now = time.monotonic_ns()//1_000_000
        gesture = AuthorizedGestureV1(now,1,"dry-run-session",2,"LOCKED_HIGH",
                                      "LEFT",.9,True,[],.95)
        publisher.submit(gesture)
        publisher.publish_once()
        deadline = time.monotonic()+2.
        while len(received)<1 and time.monotonic()<deadline:
            rclpy.spin_once(observer,timeout_sec=.05)
        if not received or received[-1][0:2] != ("MOVE_LEFT",True):
            raise AssertionError(f"expected valid MOVE_LEFT, got {received}")
        publisher.vision_failed("PROBE_VISION_STOPPED")
        publisher.publish_once()
        deadline = time.monotonic()+2.
        while len(received)<2 and time.monotonic()<deadline:
            rclpy.spin_once(observer,timeout_sec=.05)
        if len(received)<2 or received[-1][0:2] != ("HOVER",False):
            raise AssertionError(f"expected invalid HOVER, got {received}")
        print(json.dumps({"status":"PASS","topic":DRY_RUN_TOPIC,
                          "messages":received,"gateway_topic_used":False}))
    finally:
        observer.destroy_node()
        publisher.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
