from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from .appearance import OperatorReIDGallery, cosine, normalize_embedding
from .tpose import TPoseRecognizer, TPoseTemporal
from .types import AuthorizedGestureV1, TrackedPersonV1, finite_bbox


class OwnershipState(str, Enum):
    WAIT_OPERATOR = "WAIT_OPERATOR"
    ACQUIRING = "ACQUIRING"
    LOCKED_HIGH = "LOCKED_HIGH"
    LOST = "LOST"
    REACQUIRING = "REACQUIRING"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class OperatorSession:
    operator_session_id: str
    current_track_id: int
    acquired_at_ms: int
    last_seen_ms: int
    last_bbox: tuple[float, float, float, float]
    ownership_score: float


def _field(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


class OperatorOwnershipManager:
    """One session, evidence-based binding. A MOT ID alone never grants authority."""

    def __init__(self, *, frame_timeout_ms: int = 350, reacquire_ms: int = 500,
                 reacquire_frames: int = 5, ambiguity_margin: float = 0.08):
        self.frame_timeout_ms = frame_timeout_ms
        self.reacquire_ms = reacquire_ms
        self.reacquire_frames = reacquire_frames
        self.ambiguity_margin = ambiguity_margin
        self.state = OwnershipState.WAIT_OPERATOR
        self.session: Optional[OperatorSession] = None
        self.gallery = OperatorReIDGallery()
        self.tpose = TPoseRecognizer()
        self.tpose_temporal = TPoseTemporal()
        self.selected_track: Optional[TrackedPersonV1] = None
        self.candidate_scores: list[dict] = []
        self.reject_reason = "NO_OPERATOR"
        self._last_frame_ms: Optional[int] = None
        self._pending_reacquire: Optional[tuple[int, int, int]] = None

    def reset(self) -> None:
        self.state = OwnershipState.WAIT_OPERATOR
        self.session = None
        self.gallery.clear()
        self.tpose_temporal.clear()
        self.selected_track = None
        self.candidate_scores = []
        self.reject_reason = "NO_OPERATOR"
        self._pending_reacquire = None
        self._last_frame_ms = None

    @staticmethod
    def _eligible(track: TrackedPersonV1) -> bool:
        return (
            bool(_field(track.pose_quality, "valid", False))
            and finite_bbox(track.bbox_xyxy)
            and math.isfinite(track.tracker_confidence)
            and track.tracker_confidence >= 0.45
            and track.crop_quality >= 0.75
            and normalize_embedding(track.embedding) is not None
            and track.skeleton is not None
        )

    def _score(self, track: TrackedPersonV1, now_ms: int) -> dict:
        assert self.session is not None
        appearance = self.gallery.score(track.embedding)
        old = self.session.last_bbox
        old_x, old_y = _center(old)
        x, y = _center(track.bbox_xyxy)
        diagonal = max(20.0, math.hypot(old[2] - old[0], old[3] - old[1]))
        normalized_jump = math.hypot(x - old_x, y - old_y) / diagonal
        geometry = max(0.0, 1.0 - normalized_jump / 1.5)
        dt = max(0, now_ms - self.session.last_seen_ms)
        continuity = max(0.0, 1.0 - dt / 1500.0)
        score = (0.65 * appearance + 0.30 * geometry + 0.05 * continuity) if appearance is not None else 0.0
        hard_pass = self._eligible(track) and appearance is not None and appearance >= 0.80 and geometry >= 0.35 and score >= 0.75
        return {
            "track_id": track.track_id, "appearance": appearance, "geometry": geometry,
            "time_continuity": continuity, "score": score, "hard_pass": bool(hard_pass),
            "same_track_id": track.track_id == self.session.current_track_id,
        }

    def update(self, tracks: list[TrackedPersonV1], timestamp_ms: int, frame_id: int) -> OwnershipState:
        self.selected_track = None
        self.candidate_scores = []
        if self._last_frame_ms is not None and timestamp_ms <= self._last_frame_ms:
            self.state = OwnershipState.LOST if self.session else OwnershipState.WAIT_OPERATOR
            self.reject_reason = "NON_MONOTONIC_TIMESTAMP"
            return self.state
        self._last_frame_ms = timestamp_ms
        by_id = {track.track_id: track for track in tracks if track.timestamp_ms == timestamp_ms and track.frame_id == frame_id}
        if len(by_id) != len(tracks):
            self.state = OwnershipState.LOST if self.session else OwnershipState.WAIT_OPERATOR
            self.reject_reason = "STALE_OR_DUPLICATE_TRACK"
            return self.state

        if self.session is None:
            return self._acquire(list(by_id.values()), timestamp_ms)
        return self._maintain(list(by_id.values()), timestamp_ms)

    def _acquire(self, tracks: list[TrackedPersonV1], now_ms: int) -> OwnershipState:
        self.tpose_temporal.retain_only({track.track_id for track in tracks})
        pending, ready = [], []
        for track in tracks:
            candidate = self.tpose.recognize(track.skeleton, track.track_id)
            matched = candidate.matched and self._eligible(track)
            confirmed = self.tpose_temporal.update(track.track_id, now_ms, matched)
            self.candidate_scores.append({"track_id": track.track_id, "tpose_matched": matched,
                                          "tpose_confirmed": confirmed, "tpose_score": candidate.score})
            if matched:
                pending.append(track)
            if confirmed:
                ready.append(track)
        if len(pending) > 1:
            self.state = OwnershipState.AMBIGUOUS
            self.reject_reason = "AMBIGUOUS_TPOSE"
            return self.state
        if len(ready) == 1 and len(pending) == 1:
            track = ready[0]
            self.session = OperatorSession(str(uuid.uuid4()), track.track_id, now_ms, now_ms,
                                           track.bbox_xyxy, 1.0)
            self.state = OwnershipState.LOCKED_HIGH
            self.selected_track = track
            self.gallery.update(track.embedding, locked_high=True, crop_quality=track.crop_quality,
                                unique_candidate=True)
            self.tpose_temporal.clear()
            self.reject_reason = ""
        else:
            self.state = OwnershipState.ACQUIRING if pending else OwnershipState.WAIT_OPERATOR
            self.reject_reason = "TPOSE_CONFIRMING" if pending else "NO_OPERATOR"
        return self.state

    def _maintain(self, tracks: list[TrackedPersonV1], now_ms: int) -> OwnershipState:
        assert self.session is not None
        if not tracks:
            self.state = OwnershipState.LOST
            self.reject_reason = "OPERATOR_LOST"
            self._pending_reacquire = None
            return self.state
        candidates = [self._score(track, now_ms) for track in tracks]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        self.candidate_scores = candidates
        eligible = [item for item in candidates if item["hard_pass"]]
        if not eligible:
            self.state = OwnershipState.LOST
            self.reject_reason = "LOW_IDENTITY_CONFIDENCE"
            self._pending_reacquire = None
            return self.state
        best = eligible[0]
        second = eligible[1] if len(eligible) > 1 else None
        if second and best["score"] - second["score"] < self.ambiguity_margin:
            self.state = OwnershipState.AMBIGUOUS
            self.reject_reason = "AMBIGUOUS_IDENTITY"
            self._pending_reacquire = None
            return self.state
        track = next(track for track in tracks if track.track_id == best["track_id"])
        same_continuous_track = (
            self.state == OwnershipState.LOCKED_HIGH
            and track.track_id == self.session.current_track_id
            and now_ms - self.session.last_seen_ms <= self.frame_timeout_ms
        )
        if not same_continuous_track:
            if best["appearance"] is None or best["appearance"] < 0.90 or best["score"] < 0.84:
                self.state = OwnershipState.LOST
                self.reject_reason = "REACQUIRE_EVIDENCE_INSUFFICIENT"
                self._pending_reacquire = None
                return self.state
            pending_id, start_ms, count = self._pending_reacquire or (track.track_id, now_ms, 0)
            if pending_id != track.track_id:
                pending_id, start_ms, count = track.track_id, now_ms, 0
            count += 1
            self._pending_reacquire = (pending_id, start_ms, count)
            if now_ms - start_ms < self.reacquire_ms or count < self.reacquire_frames:
                self.state = OwnershipState.REACQUIRING
                self.reject_reason = "REACQUIRING"
                return self.state
        self._pending_reacquire = None
        self.session.current_track_id = track.track_id
        self.session.last_seen_ms = now_ms
        self.session.last_bbox = track.bbox_xyxy
        self.session.ownership_score = best["score"]
        self.selected_track = track
        self.state = OwnershipState.LOCKED_HIGH
        self.reject_reason = ""
        self.gallery.update(track.embedding, locked_high=True, crop_quality=track.crop_quality,
                            unique_candidate=second is None or best["score"] - second["score"] >= self.ambiguity_margin)
        return self.state

    def authorize(self, track_id: Optional[int], gesture: Any, timestamp_ms: int, frame_id: int) -> AuthorizedGestureV1:
        session = self.session
        reasons = []
        if session is None:
            reasons.append("NO_OPERATOR")
        elif self.state != OwnershipState.LOCKED_HIGH or self.selected_track is None:
            reasons.append(self.reject_reason or "OPERATOR_LOST")
        elif track_id != session.current_track_id:
            reasons.append("BYSTANDER")
        elif self.selected_track.timestamp_ms != timestamp_ms or self.selected_track.frame_id != frame_id:
            reasons.append("TRACK_TIMEOUT")
        elif not _field(self.selected_track.pose_quality, "valid", False):
            reasons.append("POSE_INVALID")
        label = _field(gesture, "label", "UNKNOWN")
        label = _field(label, "value", label)
        stable = bool(_field(gesture, "stable", False))
        score = _field(gesture, "score", 0.0)
        if label == "INVALID":
            reasons.append("GESTURE_INVALID")
        elif label == "UNKNOWN":
            reasons.append("GESTURE_UNKNOWN")
        elif not stable:
            reasons.append("GESTURE_UNCONFIRMED")
        if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            reasons.append("NONFINITE_GESTURE_SCORE")
            score = 0.0
        valid = not reasons and label in {"LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER"}
        output = AuthorizedGestureV1(
            timestamp_ms=timestamp_ms, frame_id=frame_id,
            operator_session_id=session.operator_session_id if session else None,
            current_track_id=session.current_track_id if session else None,
            authorization_state=self.state.value,
            gesture=label if valid else "UNKNOWN", gesture_confidence=float(score) if valid else 0.0,
            valid=valid, reject_reasons=reasons,
            ownership_score=session.ownership_score if session and self.state == OwnershipState.LOCKED_HIGH else 0.0,
        )
        output.to_dict()  # Assert serialization invariants on every frame.
        return output
