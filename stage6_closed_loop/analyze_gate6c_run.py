#!/usr/bin/env python3
"""Conservative offline Gate 6C evidence alignment; never passes a dry-run."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

import cv2
import yaml


EXPECTED = {
    "MOVE_RIGHT": ("RIGHT", "y", "trajectory_velocity_y", 1),
    "MOVE_LEFT": ("LEFT", "y", "trajectory_velocity_y", -1),
    "ASCEND": ("ASCEND", "z", "trajectory_velocity_z", -1),
    "DESCEND": ("DESCEND", "z", "trajectory_velocity_z", 1),
    "HOVER": ("HOVER", None, None, 0),
}
GESTURES = ("RIGHT", "LEFT", "ASCEND", "DESCEND", "HOVER")


def read_jsonl(path: Path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_video_alignment(run_dir: Path, vision: list[dict], recordings):
    if not recordings:
        return False, "ANNOTATED_VIDEO_MISSING", 0
    by_id = {row.get("frame_id"): row for row in vision}
    total = 0
    for clip in recordings:
        clip_dir = Path(clip.get("clip_dir", ""))
        if not clip_dir.is_absolute():
            clip_dir = run_dir / clip_dir
        if not clip_dir.resolve().is_relative_to(run_dir.resolve()):
            return False, "VIDEO_CLIP_OUTSIDE_RUN", total
        video = clip_dir / "annotated.avi"
        frame_log = clip_dir / "frames.jsonl"
        if clip.get("status") != "COMPLETE" or not video.is_file() or video.stat().st_size == 0:
            return False, "ANNOTATED_VIDEO_INCOMPLETE", total
        frames = read_jsonl(frame_log)
        if len(frames) != clip.get("frames"):
            return False, "VIDEO_FRAME_LOG_COUNT_MISMATCH", total
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            return False, "ANNOTATED_VIDEO_UNREADABLE", total
        encoded_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        capture.release()
        if encoded_frames != len(frames):
            return False, "VIDEO_ENCODED_FRAME_COUNT_MISMATCH", total
        for index, frame in enumerate(frames):
            diagnostic = frame.get("diagnostics") or {}
            source = by_id.get(diagnostic.get("frame_id"))
            if (frame.get("recording_frame_index") != index or source is None or
                    source.get("capture_monotonic_ns") != frame.get("capture_monotonic_ns") or
                    source.get("video_frame_index") != index):
                return False, "VIDEO_VISION_ALIGNMENT_MISMATCH", total
        total += len(frames)
    return True, None, total


def bag_topic_counts(run_dir: Path):
    metadata = run_dir / "rosbag" / "metadata.yaml"
    if not metadata.is_file():
        return {}
    try:
        bag = yaml.safe_load(metadata.read_text(encoding="utf-8"))
        topics = bag["rosbag2_bagfile_information"]["topics_with_message_count"]
        return {entry["topic_metadata"]["name"]: int(entry["message_count"])
                for entry in topics}
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        return {}


def episodes(events, *, max_gap_ms=200):
    """Consecutive valid Intent publications, separated by label or time gap."""
    result = []
    current = None
    for event in events:
        label = event.get("intent")
        t = event.get("run_elapsed_ms")
        if not event.get("valid", event.get("ros_output_valid", False)) or label not in EXPECTED or not finite(t):
            if current:
                result.append(current)
                current = None
            continue
        if current and (label != current["intent"] or
                        t - current["end_ms"] > max_gap_ms or
                        event.get("operator_session_id") != current["operator_session_id"]):
            result.append(current)
            current = None
        if current is None:
            current = {"intent": label, "start_ms": t, "end_ms": t,
                       "operator_session_id": event.get("operator_session_id"),
                       "intent_events": 0}
        current["end_ms"] = t
        current["intent_events"] += 1
    if current:
        result.append(current)
    return result


def in_window(rows, start, end, event_type=None):
    return [row for row in rows
            if finite(row.get("run_elapsed_ms"))
            and start <= row["run_elapsed_ms"] <= end
            and (event_type is None or row.get("event_type") == event_type)]


def evaluate_episode(episode, vision, gateway, px4, *, live_mode,
                     response_delay_ms=200, window_ms=1300,
                     min_displacement_m=.10, min_velocity_m_s=.08,
                     hover_speed_m_s=.20, setpoint_epsilon=.05):
    intent = episode["intent"]
    gesture, axis, setpoint_axis, sign = EXPECTED[intent]
    start, end = episode["start_ms"], episode["end_ms"]
    result = {**episode, "gesture": gesture,
              "result": "INSUFFICIENT_EVIDENCE", "reasons": [],
              "gesture_seen": False, "authorized": False,
              "gateway_intent_seen": False, "setpoint_seen": False,
              "px4_state_seen": False}
    if not live_mode:
        result["reasons"].append("DRY_RUN_CANNOT_PROVE_AIRCRAFT_MOVEMENT")
        return result
    matching = in_window(vision, start-250, end+100)
    matching = [row for row in matching
                if row.get("operator_session_id") == episode["operator_session_id"]]
    result["gesture_seen"] = any((row.get("gesture_stable") or {}).get("label") == gesture
                                 for row in matching)
    result["authorized"] = any((row.get("authorized_gesture") or {}).get("gesture") == gesture
                               and (row.get("authorized_gesture") or {}).get("valid")
                               for row in matching)
    received = in_window(gateway, start-150, end+150, "received_intent")
    result["gateway_intent_seen"] = any(row.get("topic") == "/interaction/intent"
                                         and row.get("received_intent") == intent
                                         and row.get("intent_valid") for row in received)
    setpoints = in_window(gateway, start, end+100, "trajectory_setpoint")
    positions = in_window(px4, start+response_delay_ms,
                          start+response_delay_ms+window_ms, "local_position")
    statuses = in_window(px4, start+response_delay_ms,
                         start+response_delay_ms+window_ms, "vehicle_status")
    if not result["gesture_seen"]: result["reasons"].append("STABLE_GESTURE_MISSING")
    if not result["authorized"]: result["reasons"].append("AUTHORIZED_GESTURE_MISSING")
    if not result["gateway_intent_seen"]: result["reasons"].append("GATEWAY_INTENT_UNOBSERVED")
    if len(setpoints) < 3: result["reasons"].append("SETPOINT_SAMPLES_INSUFFICIENT")
    if len(positions) < 4: result["reasons"].append("PX4_POSITION_SAMPLES_INSUFFICIENT")
    if not statuses: result["reasons"].append("PX4_STATUS_MISSING")
    elif not all(row.get("armed") is True and row.get("nav_state") == 14
                 and row.get("failsafe") is False for row in statuses):
        result["reasons"].append("PX4_NOT_ARMED_OFFBOARD_OR_FAILSAFE")
    if result["reasons"]:
        return result
    if intent == "HOVER":
        vectors = [(row.get("trajectory_velocity_x"),
                    row.get("trajectory_velocity_y"),
                    row.get("trajectory_velocity_z")) for row in setpoints]
        if not all(all(finite(value) for value in vector) for vector in vectors):
            result["reasons"].append("NONFINITE_SETPOINT")
            return result
        result["setpoint_seen"] = all(max(abs(v) for v in vector) <= setpoint_epsilon
                                       for vector in vectors)
        speeds = []
        for row in positions:
            vector = (row.get("vx"), row.get("vy"), row.get("vz"))
            if not row.get("v_xy_valid") or not row.get("v_z_valid") or not all(map(finite, vector)):
                result["reasons"].append("PX4_VELOCITY_INVALID")
                return result
            speeds.append(math.sqrt(sum(v*v for v in vector)))
        result["px4_state_seen"] = True
        result["final_speed_m_s"] = statistics.median(speeds[len(speeds)//2:])
        result["result"] = ("PASS" if result["setpoint_seen"] and
                            result["final_speed_m_s"] <= hover_speed_m_s else "FAIL")
        return result
    values = [row.get(setpoint_axis) for row in setpoints]
    if not all(map(finite, values)):
        result["reasons"].append("NONFINITE_SETPOINT")
        return result
    result["setpoint_seen"] = sum(value*sign > setpoint_epsilon for value in values) >= math.ceil(.8*len(values))
    if not all(row.get("xy_valid") if axis == "y" else row.get("z_valid") for row in positions):
        result["reasons"].append("PX4_POSITION_INVALID")
        return result
    if not all(row.get("v_xy_valid") if axis == "y" else row.get("v_z_valid") for row in positions):
        result["reasons"].append("PX4_VELOCITY_INVALID")
        return result
    position_values = [row.get(axis) for row in positions]
    velocity_values = [row.get("v"+axis) for row in positions]
    if not all(map(finite, position_values+velocity_values)):
        result["reasons"].append("NONFINITE_PX4_STATE")
        return result
    result["px4_state_seen"] = True
    result["delta_"+axis+"_m"] = position_values[-1]-position_values[0]
    result["median_v"+axis+"_m_s"] = statistics.median(velocity_values)
    result["result"] = ("PASS" if result["setpoint_seen"] and
                        result["delta_"+axis+"_m"]*sign >= min_displacement_m and
                        result["median_v"+axis+"_m_s"]*sign >= min_velocity_m_s
                        else "FAIL")
    return result


def check_movement_release(movement_episodes, intent, gateway,
                           *, max_gap_ms=500, setpoint_epsilon=.05):
    """Each movement must be followed by invalid HOVER and two zero setpoints."""
    if not movement_episodes:
        return False, ["NO_MOVEMENT_EPISODE_FOR_RELEASE_CHECK"]
    problems = []
    for episode in movement_episodes:
        end = episode["end_ms"]
        hover = next((row for row in intent
                      if row.get("intent") == "HOVER"
                      and not row.get("valid",row.get("ros_output_valid",False))
                      and finite(row.get("run_elapsed_ms"))
                      and end <= row["run_elapsed_ms"] <= end+max_gap_ms), None)
        if hover is None:
            problems.append(f"RELEASE_HOVER_MISSING:{episode['intent']}@{end}")
            continue
        zero = in_window(gateway,hover["run_elapsed_ms"],
                         hover["run_elapsed_ms"]+max_gap_ms,"trajectory_setpoint")
        if len(zero) < 2:
            problems.append(f"RELEASE_SETPOINT_INSUFFICIENT:{episode['intent']}@{end}")
            continue
        vectors = [(row.get("trajectory_velocity_x"),
                    row.get("trajectory_velocity_y"),
                    row.get("trajectory_velocity_z")) for row in zero]
        if not all(all(finite(v) and abs(v) <= setpoint_epsilon for v in vector)
                   for vector in vectors):
            problems.append(f"RELEASE_SETPOINT_NOT_ZERO:{episode['intent']}@{end}")
    return not problems, problems


def analyze(run_dir: Path, **settings):
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir/"run_manifest.json").read_text(encoding="utf-8"))
    vision = read_jsonl(run_dir/"vision_frames.jsonl")
    intent = read_jsonl(run_dir/"intent_events.jsonl")
    gateway = read_jsonl(run_dir/"gateway_events.jsonl")
    px4 = read_jsonl(run_dir/"px4_state.jsonl")
    video_ok, video_reason, video_frames = check_video_alignment(
        run_dir, vision, manifest.get("recordings", []))
    live_mode = (manifest.get("intent_topic") == "/interaction/intent"
                 and manifest.get("gateway_topic_explicit") is True)
    bag_counts = bag_topic_counts(run_dir)
    bag_ready = bool(bag_counts)
    required_bag_topics = {"/interaction/intent", "/fmu/in/trajectory_setpoint",
                           "/fmu/in/offboard_control_mode"}
    position_topics = {row.get("topic") for row in px4
                       if row.get("event_type") == "local_position"}
    status_topics = {row.get("topic") for row in px4
                     if row.get("event_type") == "vehicle_status"}
    required_bag_topics.update(position_topics | status_topics)
    bag_complete = (required_bag_topics.issubset(bag_counts) and
                    all(bag_counts[topic] > 0 for topic in required_bag_topics) and
                    bool(position_topics) and bool(status_topics))
    results = [evaluate_episode(item, vision, gateway, px4,
                                live_mode=live_mode, **settings)
               for item in episodes(intent)]
    per_gesture = {}
    for gesture in GESTURES:
        items = [item for item in results if item["gesture"] == gesture]
        if not items or any(item["result"] == "INSUFFICIENT_EVIDENCE" for item in items):
            status = "INSUFFICIENT_EVIDENCE"
        elif any(item["result"] == "FAIL" for item in items):
            status = "FAIL"
        else:
            status = "PASS"
        per_gesture[gesture] = {"result": status, "episodes": len(items)}
    release, release_problems = check_movement_release(
        [item for item in episodes(intent) if item["intent"] != "HOVER"],
        intent, gateway)
    reasons = []
    if not live_mode: reasons.append("DRY_RUN_NO_AIRCRAFT_CLAIM")
    if any(row.get("test_injection") for row in vision):
        reasons.append("SYNTHETIC_AUTHORIZATION_NOT_REAL_GATE_EVIDENCE")
    if not bag_ready: reasons.append("ROSBAG_METADATA_MISSING")
    elif live_mode and not bag_complete:
        reasons.append("ROSBAG_REQUIRED_TOPICS_MISSING_OR_EMPTY")
    if not video_ok: reasons.append(video_reason)
    elif video_frames != len(vision) or not manifest.get("record_requested"):
        reasons.append("VIDEO_DOES_NOT_COVER_FULL_RUN")
    publishers = manifest.get("publisher_sources", {}).get(
        "/fmu/in/trajectory_setpoint", [])
    if live_mode and publishers != ["/control_gateway"]:
        reasons.append("TRAJECTORY_PUBLISHER_NOT_UNIQUELY_GATEWAY")
    if live_mode and not release: reasons.extend(release_problems)
    statuses = [row["result"] for row in per_gesture.values()]
    overall = ("PASS" if not reasons and all(x == "PASS" for x in statuses)
               else "FAIL" if any(x == "FAIL" for x in statuses)
               or any(reason.startswith("RELEASE_SETPOINT_NOT_ZERO") for reason in reasons)
               else "INSUFFICIENT_EVIDENCE")
    return {"schema_version": "Gate6CAnalysisV1", "gate6c_result": overall,
            "reasons": reasons, "per_gesture": per_gesture,
            "episodes": results, "release_hover_seen": release,
            "inputs": {"vision_frames": len(vision), "intent_events": len(intent),
                       "gateway_events": len(gateway), "px4_events": len(px4),
                       "video_frames_aligned": video_frames,
                       "rosbag_topic_counts": bag_counts},
            "settings": settings,
            "timing_note": "host run_elapsed_ms only; PX4 source timestamps retained without assuming their clock domain"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--response-delay-ms", type=int, default=200)
    parser.add_argument("--window-ms", type=int, default=1300)
    parser.add_argument("--min-displacement-m", type=float, default=.10)
    parser.add_argument("--min-velocity-m-s", type=float, default=.08)
    parser.add_argument("--hover-speed-m-s", type=float, default=.20)
    args = parser.parse_args()
    settings = {key: value for key, value in vars(args).items() if key != "run_dir"}
    result = analyze(args.run_dir, **settings)
    (args.run_dir/"gate6c_analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+"\n",
        encoding="utf-8")
    summary_path = args.run_dir/"summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary["gate6c_analysis"] = {"result":result["gate6c_result"],
                                  "per_gesture":result["per_gesture"],
                                  "reasons":result["reasons"]}
    summary_path.write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",
                            encoding="utf-8")
    print(json.dumps(summary["gate6c_analysis"],ensure_ascii=False))


if __name__ == "__main__":
    main()
