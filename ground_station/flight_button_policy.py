"""Pure evaluation of the existing Ground Station button enable rules."""
from __future__ import annotations


TERMINAL = {"IDLE", "COMPLETE", "FAILED", "ABORTED"}


def evaluate_buttons(feedback, px4):
    feedback = feedback or {}
    busy = bool(feedback and feedback.get("transaction_state") not in TERMINAL)
    bridge_connected = bool(feedback and feedback.get("bridge_state") == "READY")
    bridge_enabled = feedback.get("enabled") is True
    common = {"bridge_connected": bridge_connected, "bridge_enabled": bridge_enabled,
              "transaction_idle": not busy, "px4_fresh": bool(px4["connected"]),
              "armed": px4["armed"], "failsafe": px4["failsafe"],
              "preflight": px4.get("preflight"), "landed": px4.get("landed"),
              "z_valid": px4.get("z_valid"), "vz_valid": px4.get("vz_valid"),
              "local_position_valid": px4["local_position_valid"],
              "takeoff_height_verified": feedback.get("takeoff_height_verified") is True}
    bridge_ready = bridge_connected and bridge_enabled
    takeoff_ready = (bridge_ready and not busy and px4["connected"] and
                     px4["armed"] == "False" and px4["failsafe"] == "False" and
                     px4.get("preflight") is True and px4.get("landed") is True and
                     px4["local_position_valid"] == "VALID" and
                     feedback.get("takeoff_height_verified") is True)
    land_ready = (bridge_ready and not busy and px4["connected"] and
                  px4["armed"] == "True" and px4["failsafe"] == "False" and
                  px4.get("landed") is False)
    if not feedback or not bridge_connected:
        blocker = "BRIDGE_DISCONNECTED"
    elif not bridge_enabled:
        blocker = "BRIDGE_DISABLED"
    elif busy:
        blocker = "COMMAND_BUSY"
    elif not px4["connected"]:
        blocker = "PX4_DISCONNECTED"
    elif px4["failsafe"] == "True":
        blocker = "FAILSAFE_ACTIVE"
    elif px4.get("preflight") is not True:
        blocker = "PREFLIGHT_FAILED"
    elif px4["local_position_valid"] != "VALID":
        blocker = "LOCAL_POSITION_INVALID"
    elif not feedback.get("takeoff_height_verified"):
        blocker = "TAKEOFF_HEIGHT_UNVERIFIED"
    elif px4["armed"] == "True":
        blocker = "ALREADY_ARMED"
    elif px4.get("landed") is not True:
        blocker = "LAND_STATE_INVALID"
    else:
        blocker = "NONE"
    takeoff = {**common, "button": "TAKEOFF", "result": "ENABLED" if takeoff_ready else "BLOCKED",
               "primary_reason": "NONE" if takeoff_ready else blocker}
    land_reason = ("NONE" if land_ready else
                   "BRIDGE_DISCONNECTED" if not bridge_connected else
                   "BRIDGE_DISABLED" if not bridge_enabled else
                   "COMMAND_BUSY" if busy else
                   "PX4_DISCONNECTED" if not px4["connected"] else
                   "DISARMED" if px4["armed"] != "True" else
                   "FAILSAFE_ACTIVE" if px4["failsafe"] != "False" else
                   "NOT_AIRBORNE")
    land = {**common, "button": "LAND", "result": "ENABLED" if land_ready else "BLOCKED",
            "primary_reason": land_reason}
    return takeoff, land
