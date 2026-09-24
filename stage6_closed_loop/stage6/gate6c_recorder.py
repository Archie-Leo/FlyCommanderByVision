"""Stage 6 wrapper around the frozen Stage 5 diagnostic AVI recorder."""
from __future__ import annotations

from pathlib import Path

import cv2
from px4_msgs.msg import VehicleStatus

from stage5_v2.recording import DiagnosticRecorder


SKELETON_EDGES = (("left_shoulder", "left_elbow"),
                  ("left_elbow", "left_wrist"),
                  ("right_shoulder", "right_elbow"),
                  ("right_elbow", "right_wrist"),
                  ("left_shoulder", "right_shoulder"),
                  ("left_shoulder", "left_hip"),
                  ("right_shoulder", "right_hip"))


def add_evidence_overlay(annotated, people, *, operator_track_id,
                         elapsed_ms, frame_id, session_id, intent,
                         intent_valid, control_reason, px4_position,
                         px4_status, recording, identity_authorized=False,
                         flight_authority_enabled=False,
                         require_fresh_gesture=False,
                         flight_authority_gate_active=False,
                         motion_lease_active=False, flight_nav_state=None,
                         authority_transition_reason=""):
    """Keep Stage 5 ownership UI; add only essential skeleton/Intent/PX4 cues."""
    image = annotated.copy()
    for person in people:
        skeleton = getattr(person, "skeleton", None)
        if not skeleton or not skeleton.valid or not skeleton.body_center_px or not skeleton.body_scale_px:
            continue
        cx, cy = skeleton.body_center_px
        scale = skeleton.body_scale_px
        color = ((0, 220, 0) if person.track_id == operator_track_id
                 else (0, 190, 255))
        points = {}
        for name, joint in skeleton.joints.items():
            if joint.valid and joint.x is not None and joint.y is not None:
                points[name] = (int(cx + joint.x * scale),
                                int(cy + joint.y * scale))
        for first, second in SKELETON_EDGES:
            if first in points and second in points:
                cv2.line(image, points[first], points[second], color, 2,
                         cv2.LINE_AA)
        for name in ("left_shoulder", "left_elbow", "left_wrist",
                     "right_shoulder", "right_elbow", "right_wrist"):
            if name in points:
                cv2.circle(image, points[name], 3, color, -1, cv2.LINE_AA)
    height, width = image.shape[:2]
    cv2.rectangle(image, (0, height - 140), (width, height), (0, 0, 0), -1)
    if px4_position is None:
        position = velocity = "PX4 POSITION/VELOCITY: unavailable"
    else:
        position = (f"PX4 NED X {px4_position['x']:.2f}  "
                    f"Y {px4_position['y']:.2f}  Z {px4_position['z']:.2f}")
        velocity = (f"PX4 VX {px4_position['vx']:.2f}  "
                    f"VY {px4_position['vy']:.2f}  VZ {px4_position['vz']:.2f}")
    status = ("ARMED " + str(px4_status["armed"]) + "  NAV "
              + str(px4_status["nav_state"]) + "  FAILSAFE "
              + str(px4_status["failsafe"])) if px4_status else "PX4 status: unavailable"
    nav_state = (flight_nav_state if flight_authority_gate_active else
                 px4_status.get("nav_state") if px4_status else None)
    flight_mode = ("UNAVAILABLE" if authority_transition_reason == "PX4_STATUS_TIMEOUT"
                   else "OFFBOARD" if nav_state ==
                   VehicleStatus.NAVIGATION_STATE_OFFBOARD else
                   "HOLD/POS/OTHER" if nav_state is not None else "UNAVAILABLE")
    motion = ("ACTIVE" if motion_lease_active else
              "WAIT_FRESH_GESTURE" if require_fresh_gesture and flight_authority_enabled
              else "READY" if flight_authority_enabled else "DISABLED")
    lines = [f"RUN {elapsed_ms/1000:.2f}s  Frame {frame_id}  Session {(session_id or '-')[:8]}  REC {'ON' if recording else 'OFF'}",
             f"IDENTITY {'TRUSTED' if identity_authorized else 'NOT AUTH'}  MODE {flight_mode}  FLIGHT {'ON' if flight_authority_enabled else 'OFF'}{' DRY-RUN' if not flight_authority_gate_active else ''}  MOTION {motion}",
             f"Intent {intent}  Valid {'YES' if intent_valid else 'NO'}  CONTROL {'ACTIVE' if motion_lease_active else 'SAFE'}  {control_reason}",
             position + "  " + status, velocity]
    for index, line in enumerate(lines):
        cv2.putText(image, line, (10, height - 116 + 27 * index),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1,
                    cv2.LINE_AA)
    return image


class Gate6CRecorder:
    def __init__(self, run_dir: Path, *, playback_fps: float = 10.0):
        self._recorder = DiagnosticRecorder(run_dir, raw_stereo=False,
                                            playback_fps=playback_fps)
        self.clip_paths: list[str] = []

    @property
    def active(self):
        return self._recorder.active

    @property
    def clips(self):
        return self._recorder.clips

    @property
    def frame_count(self):
        return self._recorder.frame_count

    def start(self, annotated, metadata: dict):
        path = self._recorder.start(annotated, {
            **metadata,
            "source_runner": "live_closed_loop.py",
            "alignment_key": "diagnostics.frame_id + capture_monotonic_ns",
        })
        self.clip_paths.append(str(path.relative_to(self._recorder.run_dir)))
        return path

    def write(self, annotated, raw_sbs, vision_record: dict,
              capture_monotonic_ns: int):
        if vision_record.get("frame_id") is None:
            raise ValueError("vision frame_id required for video alignment")
        if vision_record.get("capture_monotonic_ns") != capture_monotonic_ns:
            raise ValueError("video/vision capture timestamp mismatch")
        self._recorder.write(annotated, raw_sbs, vision_record,
                             capture_monotonic_ns)

    def stop(self, reason="USER_STOP"):
        return self._recorder.stop(reason=reason)
