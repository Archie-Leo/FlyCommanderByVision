"""Pure display mapping for the competition evidence chain.

The packet is read-only. No gesture, ownership, or Stage6 decision is made here.
"""
from __future__ import annotations

from .display_policy import display_state


def _label(value, fallback="--"):
    return str(value) if value is not None and value != "" else fallback


def _px4_state(packet, receive_age_ms):
    px4 = (packet or {}).get("px4") or {}
    age = px4.get("status_age_ms")
    current = (px4.get("connected") is True and isinstance(age, (float, int))
               and receive_age_ms is not None and age + receive_age_ms <= 1500)
    if not current:
        return {"node": ("DISCONNECTED", "PX4 telemetry stale", "muted"),
                "connected": False, "mode": "--", "armed": "--", "failsafe": "--",
                "local_position_valid": "--", "position": None, "velocity": None,
                "status_age_ms": None, "nav_state": None, "ack": None}
    valid = px4.get("local_position_valid")
    return {"node": ("CONNECTED", "PX4 telemetry current", "authorized"),
            "connected": True, "mode": _label(px4.get("mode")),
            "armed": _label(px4.get("armed")), "failsafe": _label(px4.get("failsafe")),
            "local_position_valid": ("VALID" if valid is True else
                                     "INVALID" if valid is False else "--"),
            "position": px4.get("position"), "velocity": px4.get("velocity"),
            "status_age_ms": age + receive_age_ms, "nav_state": px4.get("nav_state"),
            "ack": px4.get("last_vehicle_command_ack")}


def competition_state(packet, receive_age_ms, *, video_ready=True,
                      metadata_stale_ms=500, overlay_timeout_ms=1000):
    policy = display_state(packet, receive_age_ms,
        metadata_stale_ms=metadata_stale_ms,
        overlay_timeout_ms=overlay_timeout_ms)
    if not policy["metadata_ready"]:
        return {
            "metadata_ready": False, "ai_ready": False,
            "perception": ("METADATA LOST", "No current observation", "warning"),
            "authorization": ("--", "Evidence unavailable", "muted"),
            "gesture": ("--", "Evidence unavailable", "muted"),
            "safety": ("--", "Decision unavailable", "muted"),
            "px4": ("DISCONNECTED", "PX4 telemetry unavailable", "muted"),
            "px4_details": _px4_state(None, None),
            "command_details": None,
            "operator_state": "METADATA LOST", "track": "--", "depth": "--",
            "control_gesture": "--", "control_intent": "--", "lease": "--",
            "control_safety": "NO CURRENT DATA", "stage6_reason": "--",
            "system": "METADATA LOST" if video_ready else "VIDEO + METADATA LOST",
            "person_visible": False,
        }

    op = (packet.get("operator") or {})
    gesture = (packet.get("gesture") or {})
    stage6 = (packet.get("stage6") or {})
    depth = (packet.get("depth") or {})
    raw_state = _label(op.get("state"), "WAIT_OPERATOR")
    track_id = op.get("track_id")
    track = f"#{track_id}" if track_id is not None else "--"
    people = packet.get("people_count") or 0
    ai_live = policy["ai_ready"]

    if not ai_live:
        perception = ("AI STALE", "No current observation", "warning")
        authorization = ("STALE", raw_state, "warning")
        gesture_node = ("--", "No current gesture", "muted")
        person_visible = False
    else:
        person_visible = bool(policy["draw_person"] and op.get("bbox"))
        if raw_state == "OPERATOR_LOST":
            perception = ("NO OPERATOR", f"{people} person(s) in frame", "warning")
        elif op.get("kind") == "candidate" and track_id is not None:
            perception = (f"CANDIDATE {track}", f"{people} person(s) in frame", "candidate")
        elif op.get("kind") == "operator" and track_id is not None:
            perception = (f"PERSON {track}", f"{people} person(s) in frame", "normal")
        elif people:
            perception = (f"{people} PERSON" if people == 1 else f"{people} PEOPLE",
                          "No selected operator", "normal")
        else:
            perception = ("NO PERSON", "Camera observing", "muted")
        if raw_state == "LOCKED_HIGH":
            authorization = ("AUTHORIZED", raw_state, "authorized")
        elif raw_state == "WAIT_OPERATOR":
            authorization = ("WAITING", raw_state, "muted")
        elif raw_state == "OPERATOR_LOST":
            authorization = ("OPERATOR LOST", raw_state, "warning")
        else:
            authorization = (raw_state.replace("_", " "), raw_state, "candidate")
        stable = _label(gesture.get("stable"))
        if raw_state != "LOCKED_HIGH":
            gesture_node = ("--", "Awaiting authorization", "muted")
        else:
            gesture_node = (stable, "Stable gesture", "normal" if stable not in ("--", "UNKNOWN") else "muted")

    intent = _label(stage6.get("intent"))
    valid = stage6.get("valid") is True
    reason = _label(stage6.get("reason"))
    if intent == "--":
        safety = ("--", "No Stage6 decision", "muted")
        control_safety = "NO DECISION"
    elif valid:
        safety = (intent, "VALID · Stage6 decision", "authorized")
        control_safety = "READY"
    elif intent == "HOVER":
        safety = ("SAFE HOVER", reason.replace("_", " "), "warning")
        control_safety = "SAFE HOVER"
    else:
        safety = ("INHIBITED", f"{intent} · {reason.replace('_', ' ')}", "warning")
        control_safety = "INHIBITED"

    command = (packet.get("command") or {})
    command_current = command.get("mode") == "SHADOW" and command.get("fresh") is True
    if command_current and command.get("authority") == "BLOCKED" and valid and intent != "HOVER":
        authority_reason = _label(command.get("authority_reason"), "FLIGHT_AUTHORITY_BLOCKED")
        safety = ("COMMAND BLOCKED", authority_reason.replace("_", " "), "warning")
        control_safety = "COMMAND BLOCKED"

    distance = (f"{depth['m']:.2f} m" if ai_live and person_visible and
                depth.get("valid") and isinstance(depth.get("m"), (float, int)) else "--")
    system = ("VIDEO LOST" if not video_ready else
              "AI STALE" if not ai_live else "READY")
    return {
        "metadata_ready": True, "ai_ready": ai_live,
        "perception": perception, "authorization": authorization,
        "gesture": gesture_node, "safety": safety,
        "px4": _px4_state(packet, receive_age_ms)["node"],
        "px4_details": _px4_state(packet, receive_age_ms),
        "command_details": command if command_current else None,
        "operator_state": raw_state if ai_live else "AI STALE",
        "track": track if ai_live and person_visible else "--", "depth": distance,
        "control_gesture": gesture_node[0], "control_intent": intent,
        "lease": _label(stage6.get("lease")),
        "control_safety": control_safety, "stage6_reason": reason,
        "system": system, "person_visible": person_visible,
    }
