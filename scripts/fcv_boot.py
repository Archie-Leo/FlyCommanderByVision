#!/usr/bin/env python3
"""Read-only device, network, PX4 and running-stack checks for systemd."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def run(*args, timeout=5):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def props(device):
    result = run("udevadm", "info", "-q", "property", "-n", str(device))
    if result.returncode:
        raise RuntimeError(f"udevadm failed for {device}: {result.stderr.strip()}")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def camera_device():
    stable = Path(os.environ["CAMERA"])
    resolved = stable.resolve(strict=True)
    if not re.fullmatch(r"/dev/video\d+", str(resolved)):
        raise RuntimeError("camera symlink does not resolve to /dev/videoN")
    info = props(resolved)
    if (info.get("ID_VENDOR_ID"), info.get("ID_MODEL_ID")) != ("0bda", "5883"):
        raise RuntimeError("unexpected camera USB identity")
    result = run("v4l2-ctl", "-d", str(resolved), "--list-formats-ext")
    if result.returncode:
        raise RuntimeError(f"camera mode enumeration failed: {result.stderr.strip()}")
    mjpg = result.stdout.split("'MJPG'", 1)
    if len(mjpg) != 2 or not re.search(
        r"Size: Discrete 2560x960\s+Interval: Discrete 0\.017s \(60\.000 fps\)",
        mjpg[1].split("'YUYV'", 1)[0]):
        raise RuntimeError("camera index is not MJPG 2560x960 at 60 fps")
    return resolved


def serial_device():
    stable = Path(os.environ["PX4_SERIAL"])
    resolved = stable.resolve(strict=True)
    if not re.fullmatch(r"/dev/ttyUSB\d+", str(resolved)):
        raise RuntimeError("PX4 serial symlink does not resolve to /dev/ttyUSBN")
    info = props(resolved)
    if (info.get("ID_VENDOR_ID"), info.get("ID_MODEL_ID")) != ("1a86", "7523"):
        raise RuntimeError("unexpected PX4 serial adapter identity")
    if not os.access(resolved, os.R_OK | os.W_OK):
        raise RuntimeError("PX4 serial adapter is not readable and writable")
    return resolved


def route():
    host = os.environ["GROUND_HOST"]
    result = run("ip", "-j", "route", "get", host)
    if result.returncode:
        raise RuntimeError(f"no route to Ground Station {host}")
    rows = json.loads(result.stdout)
    if not rows or not rows[0].get("dev") or not rows[0].get("prefsrc", rows[0].get("src")):
        raise RuntimeError("Ground Station route has no device/source")
    return rows[0]


def ros_read(timeout):
    import rclpy
    from px4_msgs.msg import VehicleLocalPosition, VehicleStatus
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    rclpy.init()
    node = Node("fcv_stack_readonly_check")
    samples = {"status": None, "position": None}
    qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(VehicleStatus, "/fmu/out/vehicle_status_v4",
                             lambda msg: samples.__setitem__("status", (msg, time.monotonic_ns())), qos)
    node.create_subscription(VehicleLocalPosition, "/fmu/out/vehicle_local_position_v1",
                             lambda msg: samples.__setitem__("position", (msg, time.monotonic_ns())), qos)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        if all(samples.values()) and all(sample_fresh(v) for v in samples.values()):
            break
    return rclpy, node, samples


def sample_fresh(entry):
    if entry is None:
        return False
    msg, received_ns = entry
    source_us = int(getattr(msg, "timestamp_sample", 0) or msg.timestamp)
    age_us = time.time_ns() // 1000 - source_us
    return 0 <= time.monotonic_ns() - received_ns <= 1_500_000_000 and -250_000 <= age_us <= 1_500_000


def gateway_params(node):
    import rclpy
    from rcl_interfaces.srv import GetParameters

    client = node.create_client(GetParameters, "/control_gateway/get_parameters")
    if not client.wait_for_service(timeout_sec=1.0):
        return None
    names = ["shadow_mode", "demo_horizontal_cap_mps", "demo_vertical_cap_mps",
             "demo_episode_limit_m", "live_snapshot_path"]
    request = GetParameters.Request()
    request.names = names
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=2.0)
    if not future.done() or future.result() is None:
        return None
    values = future.result().values
    return {name: (value.bool_value if name == "shadow_mode" else
                   value.string_value if name == "live_snapshot_path" else
                   value.double_value) for name, value in zip(names, values)}


def wait_gateway(timeout):
    import rclpy
    from rclpy.node import Node

    rclpy.init()
    node = Node("fcv_gateway_readiness_check")
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            subscriptions = node.get_subscriptions_info_by_topic("/interaction/intent")
            publishers = node.get_publishers_info_by_topic("/fmu/in/trajectory_setpoint")
            if (len(subscriptions) == len(publishers) == 1 and
                    subscriptions[0].node_name == publishers[0].node_name == "control_gateway"):
                params = gateway_params(node)
                if params and params.get("shadow_mode") is False:
                    print("LIVE Gateway ready")
                    return
        raise RuntimeError("LIVE Gateway not ready")
    finally:
        node.destroy_node()
        rclpy.shutdown()


def processes():
    found = {"agent": [], "gateway": [], "vision": []}
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            argv = (folder / "cmdline").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if not argv or not argv[0]:
            continue
        text = " ".join(value.decode(errors="replace") for value in argv)
        if "MicroXRCEAgent serial" in text and "bash -c" not in text:
            found["agent"].append(int(folder.name))
        if "control_gateway_node" in text and "bash -c" not in text:
            found["gateway"].append(int(folder.name))
        if "run_rk3576_fanout.py" in text and "bash -c" not in text:
            found["vision"].append(int(folder.name))
    return found


def camera_owners(device):
    owners = []
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            if any(fd.resolve() == device for fd in (folder / "fd").iterdir()):
                owners.append(int(folder.name))
        except (OSError, PermissionError):
            continue
    return owners


def recent_window():
    result = run("journalctl", "--no-pager", "-u", "fcv-runtime.service",
                 "--since", "-20 seconds", "-o", "cat", timeout=5)
    for line in reversed(result.stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "WINDOW":
            return event
    return None


def health():
    checks = {}
    units = ["fcv-stack.target", "fcv-xrce.service", "fcv-telemetry.service",
             "fcv-runtime.service", "fcv-health.service"]
    unit_state = {name: run("systemctl", "is-active", name).stdout.strip() for name in units}
    checks["BOOT"] = all(value == "active" for value in unit_state.values())
    print("[BOOT]", unit_state)
    proc = processes()
    try:
        camera = camera_device()
        owners = camera_owners(camera)
        checks["CAMERA"] = len(proc["vision"]) == 1 and owners == proc["vision"]
        print("[CAMERA]", os.environ["CAMERA"], "->", camera, "owners", owners,
              "production vision", proc["vision"])
    except (OSError, RuntimeError, KeyError) as exc:
        checks["CAMERA"] = False
        print("[CAMERA]", exc)
    try:
        serial = serial_device()
        checks["SERIAL"] = len(proc["agent"]) == 1
        print("[SERIAL]", os.environ["PX4_SERIAL"], "->", serial, "agents", proc["agent"])
    except (OSError, RuntimeError, KeyError) as exc:
        checks["SERIAL"] = False
        print("[SERIAL]", exc)
    try:
        path = route()
        ports = (int(os.environ["VIDEO_PORT"]), int(os.environ["META_PORT"]))
        checks["NETWORK"] = ports == (5600, 5603) and int(os.environ["VIDEO_BITRATE_KBPS"]) == 4000
        print("[NETWORK]", os.environ["GROUND_HOST"], "via", path["dev"],
              "VIDEO", ports[0], "META", ports[1])
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        checks["NETWORK"] = False
        print("[NETWORK]", exc)

    rclpy = node = None
    status = position = None
    try:
        rclpy, node, samples = ros_read(2.5)
        checks["PX4 DDS"] = all(sample_fresh(v) for v in samples.values())
        status = samples["status"][0] if sample_fresh(samples["status"]) else None
        position = samples["position"][0] if sample_fresh(samples["position"]) else None
        print("[PX4 DDS]", "CONNECTED" if checks["PX4 DDS"] else "STALE/MISSING")
        if status is not None:
            print("[PX4 STATE] arming_state", status.arming_state,
                  "nav_state", status.nav_state, "failsafe", status.failsafe,
                  "pre_flight_checks_pass", status.pre_flight_checks_pass)
        else:
            print("[PX4 STATE] unavailable")
        if position is not None:
            print("[PX4 POSITION] xy_valid", position.xy_valid, "z_valid", position.z_valid)
        # DDS graph discovery may lag the first received PX4 sample on a busy
        # board. Bound this read-only retry so a transient empty graph is not
        # reported as a missing production publisher.
        graph_deadline = time.monotonic() + 2.0
        while True:
            intent_pubs = node.get_publishers_info_by_topic("/interaction/intent")
            intent_subs = node.get_subscriptions_info_by_topic("/interaction/intent")
            mode_pubs = node.get_publishers_info_by_topic("/fmu/in/offboard_control_mode")
            trajectory_pubs = node.get_publishers_info_by_topic("/fmu/in/trajectory_setpoint")
            if (intent_pubs and intent_subs and mode_pubs and trajectory_pubs) or time.monotonic() >= graph_deadline:
                break
            rclpy.spin_once(node, timeout_sec=0.1)
        vehicle_commands = node.get_publishers_info_by_topic("/fmu/in/vehicle_command")
        shadow_subs = node.get_subscriptions_info_by_topic("/interaction/intent_shadow")
        checks["ROS"] = (len(intent_pubs) == 1 and len(intent_subs) == 1 and
                         intent_subs[0].node_name == "control_gateway" and
                         not any(info.node_name == "control_gateway" for info in shadow_subs))
        checks["PX4 OUTPUT"] = (len(mode_pubs) == len(trajectory_pubs) == 1 and
                                mode_pubs[0].node_name == trajectory_pubs[0].node_name == "control_gateway" and
                                len(vehicle_commands) == 0 and len(proc["gateway"]) == 1)
        print("[ROS] intent publishers", len(intent_pubs), "gateway subscribers", len(intent_subs),
              "live topic", checks["ROS"])
        print("[PX4 OUTPUT] mode", len(mode_pubs), "trajectory", len(trajectory_pubs),
              "VehicleCommand", len(vehicle_commands))
        params = gateway_params(node)
        checks["SAFETY"] = bool(params and params.get("shadow_mode") is False and
                                 abs(params["demo_horizontal_cap_mps"] - .30) < 1e-6 and
                                 abs(params["demo_vertical_cap_mps"] - .20) < 1e-6 and
                                 abs(params["demo_episode_limit_m"] - .50) < 1e-6)
        print("[SAFETY]", params)
    except Exception as exc:
        for section in ("PX4 DDS", "ROS", "PX4 OUTPUT", "SAFETY"):
            checks[section] = False
        print("[ROS CHECK ERROR]", exc)
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy is not None:
            rclpy.shutdown()

    snapshot = None
    try:
        snapshot = json.loads(Path(os.environ["GATEWAY_LIVE_FILE"]).read_text())
        age = time.monotonic_ns() - int(snapshot["monotonic_ns"])
        checks["GATEWAY"] = (snapshot["mode"] == "LIVE" and snapshot.get("ros_published") is True
                             and 0 <= age <= 1_000_000_000 and len(proc["gateway"]) == 1)
        if status is not None and status.arming_state == status.ARMING_STATE_DISARMED:
            checks["SAFETY"] = checks.get("SAFETY", False) and (
                snapshot.get("authority") == "BLOCKED" and
                snapshot.get("velocity") == [0, 0, 0] and snapshot.get("intent") == "HOVER")
        print("[GATEWAY]", snapshot.get("mode"), snapshot.get("authority"),
              snapshot.get("intent"), snapshot.get("velocity"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checks["GATEWAY"] = False
        print("[GATEWAY]", exc)

    window = recent_window()
    checks["VIDEO"] = bool(window and window.get("video_h264_kbps", 0) > 0)
    metadata = (window or {}).get("metadata") or {}
    checks["METADATA"] = bool(metadata.get("sent", 0) > 0 and
                               metadata.get("errors", 0) == 0 and
                               metadata.get("oversize", 0) == 0)
    print("[GROUND STATION] video", "SENDING" if checks["VIDEO"] else "NOT VERIFIED",
          "metadata", "SENDING" if checks["METADATA"] else "NOT VERIFIED",
          "target", os.environ.get("GROUND_HOST"), os.environ.get("VIDEO_PORT"),
          os.environ.get("META_PORT"))
    if status is None:
        flight_authority = "BLOCKED_STALE_PX4"
    elif status.arming_state == status.ARMING_STATE_DISARMED:
        flight_authority = "BLOCKED_DISARMED"
    elif status.failsafe:
        flight_authority = "BLOCKED_FAILSAFE"
    elif status.nav_state != status.NAVIGATION_STATE_OFFBOARD:
        flight_authority = "BLOCKED_MODE"
    else:
        flight_authority = "ARMED_OFFBOARD_STILL_GATED"
    result = {
        "system": "READY" if all(checks.values()) else "PARTIAL",
        "camera": "READY" if checks.get("CAMERA") else "UNAVAILABLE",
        "vision": "READY" if len(proc["vision"]) == 1 else "UNAVAILABLE",
        "px4_dds": "CONNECTED" if checks.get("PX4 DDS") else "DISCONNECTED",
        "ground_station_target": os.environ.get("GROUND_HOST"),
        "video": "SENDING" if checks.get("VIDEO") else "NOT_VERIFIED",
        "metadata": "SENDING" if checks.get("METADATA") else "NOT_VERIFIED",
        "gateway": "LIVE" if checks.get("GATEWAY") else "UNAVAILABLE",
        "flight_authority": flight_authority,
        "checks": checks,
        "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    output = Path(os.environ.get("FCV_STATUS_FILE", "/tmp/fcv_system_status.json"))
    temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, output)
    print("[RESULT]", result["system"], "status", output)
    return 0 if result["system"] == "READY" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("camera", "serial", "route", "wait-px4",
                                            "wait-gateway", "check"))
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    try:
        if args.command == "camera":
            print(camera_device())
        elif args.command == "serial":
            print(serial_device())
        elif args.command == "route":
            print(route()["dev"])
        elif args.command == "wait-px4":
            rclpy, node, samples = ros_read(args.timeout)
            try:
                if not all(sample_fresh(value) for value in samples.values()):
                    raise RuntimeError("fresh PX4 VehicleStatus/LocalPosition not available")
                print("PX4 DDS fresh")
            finally:
                node.destroy_node()
                rclpy.shutdown()
        elif args.command == "wait-gateway":
            wait_gateway(args.timeout)
        else:
            return health()
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
