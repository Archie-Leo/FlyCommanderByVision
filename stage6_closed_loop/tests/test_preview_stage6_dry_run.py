"""The browser dry-run uses the existing Stage6 lease adapter in-process."""

import time

from scripts.preview_stage5_web import Stage6DryRun
from stage5.types import AuthorizedGestureV1


def observation(timestamp_ms, gesture="RIGHT", valid=True, state="LOCKED_HIGH"):
    return AuthorizedGestureV1(
        timestamp_ms=timestamp_ms, frame_id=1, operator_session_id="operator-a",
        current_track_id=7, authorization_state=state, gesture=gesture,
        gesture_confidence=0.9, valid=valid)


def record(timestamp_ms):
    return {
        "timestamp_ms": timestamp_ms, "frame_id": 1,
        "ownership_state": "LOCKED_HIGH", "operator_session_id": "operator-a",
        "current_track_id": 7, "gesture_raw": None, "gesture_stable": None,
        "authorized_gesture": observation(timestamp_ms).to_dict(),
    }


def test_preview_movement_invalid_and_timeout_are_fail_closed():
    dry_run = Stage6DryRun()
    try:
        now_ms = time.monotonic_ns() // 1_000_000
        dry_run.submit(observation(now_ms), record(now_ms))
        assert dry_run.snapshot()["stage6_intent"] == "MOVE_RIGHT"
        assert dry_run.snapshot()["lease_state"] == "ACTIVE"

        time.sleep(.003)
        invalid_ms = time.monotonic_ns() // 1_000_000
        dry_run.submit(observation(invalid_ms, gesture="UNKNOWN", valid=False),
                       record(invalid_ms))
        assert dry_run.snapshot()["stage6_intent"] == "HOVER"
        assert dry_run.snapshot()["lease_state"] == "INACTIVE"

        time.sleep(.003)
        now_ms = time.monotonic_ns() // 1_000_000
        dry_run.submit(observation(now_ms), record(now_ms))
        with dry_run.lock:
            dry_run.decision = dry_run.adapter.tick(now_ms + 300)
        assert dry_run.snapshot()["stage6_intent"] == "HOVER"
        assert dry_run.snapshot()["lease_state"] == "EXPIRED"
    finally:
        dry_run.close()
