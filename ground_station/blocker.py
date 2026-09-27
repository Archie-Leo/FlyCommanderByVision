"""Read-only LAST BLOCKER display based on existing gate outputs."""


def last_blocker(state, takeoff_eval):
    command = state.get("command_details") or {}
    intent = state.get("control_intent")
    limiter = command.get("limiter") or []
    if command.get("authority") == "BLOCKED" and intent not in (None, "--", "HOVER"):
        return "MOVEMENT · " + str(command.get("authority_reason") or "FLIGHT_AUTHORITY_BLOCKED")
    if limiter and limiter[0] in ("L", "P"):
        return "MOVEMENT · " + ("EPISODE_DISTANCE_LIMIT" if limiter[0] == "L"
                                 else "LOCAL_POSITION_INVALID")
    reason = state.get("stage6_reason")
    px4 = state.get("px4_details") or {}
    if reason not in (None, "--", "AUTHORIZED_GESTURE_FRESH", "FRESH_GESTURE_READY") and \
            (intent not in (None, "--", "HOVER") or px4.get("armed") == "True"):
        return "GESTURE · " + str(reason)
    if takeoff_eval["result"] == "BLOCKED" and px4.get("armed") != "True":
        return "TAKEOFF · " + takeoff_eval["primary_reason"]
    return "NONE"
