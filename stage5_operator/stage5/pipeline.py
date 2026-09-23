from __future__ import annotations

from gesture import GeometryGestureRecognizer, TemporalStabilizer

from .ownership import OperatorOwnershipManager, OwnershipState
from .tracking import KalmanIoUTwoPassTracker


class Stage5Pipeline:
    """Tracks everyone, but evaluates Stage 4 Gesture only for the selected operator."""

    def __init__(self):
        self.tracker = KalmanIoUTwoPassTracker()
        self.ownership = OperatorOwnershipManager()
        self.gesture = GeometryGestureRecognizer()
        self.temporal = TemporalStabilizer()
        self._last_selected = None

    def process(self, detections, timestamp_ms: int, frame_id: int):
        tracks = self.tracker.update(detections, timestamp_ms)
        state = self.ownership.update(tracks, timestamp_ms, frame_id)
        selected = self.ownership.selected_track
        current_key = (self.ownership.session.operator_session_id, selected.track_id) if selected and self.ownership.session else None
        if current_key != self._last_selected:
            self.temporal.reset()
            self._last_selected = current_key
        raw = stable = None
        if state == OwnershipState.LOCKED_HIGH and selected is not None:
            raw = self.gesture.recognize(selected.skeleton)
            stable = self.temporal.update(raw)
        else:
            self.temporal.reset()
            self._last_selected = None
        authorized = self.ownership.authorize(selected.track_id if selected else None, stable, timestamp_ms, frame_id)
        log = {
            "timestamp_ms": timestamp_ms, "frame_id": frame_id,
            "tracked_people": [track.log_dict() for track in tracks],
            "ownership_state": state.value,
            "operator_session_id": self.ownership.session.operator_session_id if self.ownership.session else None,
            "current_track_id": self.ownership.session.current_track_id if self.ownership.session else None,
            "candidate_scores": self.ownership.candidate_scores,
            "gallery_size": len(self.ownership.gallery.entries),
            "gallery_model_version": self.ownership.gallery.model_version,
            "gesture_raw": raw.to_dict() if raw else None,
            "gesture_stable": stable.to_dict() if stable else None,
            "authorized_gesture": authorized.to_dict(),
        }
        return tracks, authorized, log
