from __future__ import annotations

import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from stage5.appearance import normalize_embedding
from stage5.tpose import TPoseRecognizer, TPoseTemporal
from stage5.types import AuthorizedGestureV1, finite_bbox
from .depth import PersonDepthV1
from .evidence import WEIGHTS, CandidateEvidence, Evidence, similarity
from .gallery import OperatorReIDGalleryV2


class State(str, Enum):
    WAIT_OPERATOR = "WAIT_OPERATOR"
    ACQUIRING = "ACQUIRING"
    LOCKED_HIGH = "LOCKED_HIGH"
    LOCKED_LOW = "LOCKED_LOW"
    SHADOW_TRACKING = "SHADOW_TRACKING"
    REACQUIRING = "REACQUIRING"
    REACQUIRE_CONFIRMING = "REACQUIRE_CONFIRMING"
    OPERATOR_LOST = "OPERATOR_LOST"
    REAUTHORIZING = "REAUTHORIZING"
    AUTO_REAUTHORIZE_CONFIRMING = "AUTO_REAUTHORIZE_CONFIRMING"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class PersonObservation:
    track_id: int
    timestamp_ms: int
    frame_id: int
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    pose_quality: object
    skeleton: object
    embedding: tuple[float, ...] | None
    crop_quality: float
    depth: PersonDepthV1
    hsv: tuple[float, ...] | None = None
    occlusion: float = 0.0
    face_conflict: bool = False


@dataclass
class OperatorTargetMemory:
    operator_session_id: str
    current_track_id: int
    acquired_at_ms: int
    last_confirmed_timestamp: int
    last_confirmed_bbox: tuple[float, float, float, float]
    last_depth_m: float | None
    last_xyz: tuple[float, float, float] | None
    identity_confidence: float = 1.0
    ambiguity_margin: float = 1.0
    ownership_state: State = State.LOCKED_HIGH
    bbox_history: deque = field(default_factory=lambda: deque(maxlen=20))
    depth_history: deque = field(default_factory=lambda: deque(maxlen=20))
    xyz_history: deque = field(default_factory=lambda: deque(maxlen=20))
    last_2d_velocity: tuple[float, float] = (0.0, 0.0)
    estimated_3d_velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pose_geometry_summary: float | None = None
    gallery_quality: float = 0.0
    hsv_reference: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Settings:
    green_score: float = .82
    red_score: float = .63
    ambiguity_margin: float = .10
    reacquire_ms: int = 400
    reacquire_frames: int = 5
    red_grace_ms: int = 1100
    reacquire_confirmation_window_ms: int = 900
    overall_reacquire_timeout_ms: int = 2000
    min_confirm_crop_quality: float = .50
    shadow_ms: int = 300
    max_2d_speed_boxes_s: float = 5.0
    max_depth_speed_m_s: float = 2.5
    max_depth_jump_m: float = .35
    min_observation_quality: float = .20
    min_reid_similarity: float = .66
    reauth_reid_similarity: float = .90
    auto_reauthorize_enabled: bool = False
    auto_reauthorize_reid_threshold: float = .94
    auto_reauthorize_score_threshold: float = .88
    auto_reauthorize_margin_threshold: float = .15
    auto_reauthorize_min_effective_weight: float = .35
    auto_reauthorize_min_crop_quality: float = .75
    auto_reauthorize_topk_threshold: float = .92
    auto_reauthorize_sample_threshold: float = .90
    auto_reauthorize_min_match_count: int = 2
    auto_reauthorize_min_frames: int = 8
    auto_reauthorize_min_ms: int = 700
    auto_reauthorize_attempt_timeout_ms: int = 1500
    auto_reauthorize_gallery_hold_ms: int = 1000
    weights: dict[str, float] = field(default_factory=lambda: dict(WEIGHTS))


def _center(box):
    return ((box[0]+box[2])/2, (box[1]+box[3])/2)


def _iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    area = max(0, x2-x1)*max(0, y2-y1)
    aa = (a[2]-a[0])*(a[3]-a[1]); bb = (b[2]-b[0])*(b[3]-b[1])
    return area/(aa+bb-area) if aa+bb-area > 0 else 0.0


def _pose_ratio(skeleton):
    bones = getattr(skeleton, "bones", {}) or {}
    shoulder = getattr(bones.get("shoulder_line"), "length", None)
    torso = [getattr(bones.get(key), "length", None) for key in ("left_torso", "right_torso")]
    torso = [float(value) for value in torso if value is not None and math.isfinite(float(value)) and value > 0]
    if shoulder is None or not math.isfinite(float(shoulder)) or not torso:
        return None
    value = float(shoulder)/(sum(torso)/len(torso))
    return value if .2 <= value <= 5.0 else None


class OwnershipManagerV2:
    def __init__(self, settings=Settings()):
        self.settings = settings
        self.state = State.WAIT_OPERATOR
        self.memory: OperatorTargetMemory | None = None
        self.gallery = OperatorReIDGalleryV2()
        self.tpose = TPoseRecognizer()
        self.tpose_temporal = TPoseTemporal()
        self._seed: dict[int, list[tuple[float, ...]]] = {}
        self._last_frame_ms: int | None = None
        self._red_since: int | None = None
        self._pending: tuple[int, int, int] | None = None
        self._confirm_deadline_ms: int | None = None
        self._confirmation_attempted = False
        self.state_transition_reason = ""
        self.selected: PersonObservation | None = None
        self.suspected: PersonObservation | None = None
        self.candidates: list[CandidateEvidence] = []
        self.reject_reason = "NO_OPERATOR"
        self.margin = 0.0
        self.gallery_updated = False
        self.acquisition_checks: list[dict] = []
        self._auto_pending: tuple[int, int, int] | None = None
        self._auto_last: PersonObservation | None = None
        self._auto_gallery_hold_until_ms = 0
        self.auto_reauthorize_state = "DISABLED" if not settings.auto_reauthorize_enabled else "SEARCHING"
        self.auto_reauthorize_reject_reason = "AUTO_REAUTH_DISABLED" if not settings.auto_reauthorize_enabled else "AUTO_REAUTH_NO_CANDIDATE"
        self.auto_reauthorize_candidate_id: int | None = None
        self.auto_reauthorize_stats: dict | None = None
        self.auto_reauthorize_identity_score: float | None = None
        self.auto_reauthorize_margin: float | None = None
        self.authorization_source: str | None = None
        self.old_operator_session_id: str | None = None
        self.new_operator_session_id: str | None = None
        self._auto_success_until_ms = 0

    def reset(self):
        self.__init__(self.settings)

    def _acquire(self, people, now):
        self.tpose_temporal.retain_only({p.track_id for p in people})
        self._seed = {key: value for key, value in self._seed.items()
                      if key in {p.track_id for p in people}}
        pending = []; ready = []
        for p in people:
            pose_ok = bool(getattr(p.pose_quality, "valid", False))
            tpose = self.tpose.recognize(p.skeleton, p.track_id)
            matched = bool(tpose.matched)
            self.acquisition_checks.append({
                "track_id": p.track_id, "mode": "FIRST_AUTH", "tpose_matched": matched,
                "tpose_reasons": list(tpose.reasons), "pose_valid": pose_ok,
                "person_confidence": p.confidence, "crop_quality": p.crop_quality,
                "embedding_available": normalize_embedding(p.embedding) is not None,
                "bbox_valid": finite_bbox(p.bbox_xyxy)})
            eligible = (matched and pose_ok and p.confidence >= .45 and
                        p.crop_quality >= .75 and normalize_embedding(p.embedding) is not None and
                        finite_bbox(p.bbox_xyxy))
            confirmed = self.tpose_temporal.update(p.track_id, now, eligible)
            if eligible:
                pending.append(p)
                self._seed.setdefault(p.track_id, []).append(p.embedding)
            if confirmed:
                ready.append(p)
        if len(pending) != 1:
            self.state = State.AMBIGUOUS if len(pending) > 1 else State.WAIT_OPERATOR
            self.reject_reason = "AMBIGUOUS_TPOSE" if pending else "NO_OPERATOR"
            return
        p = pending[0]
        if p not in ready:
            self.state = State.ACQUIRING
            self.reject_reason = "TPOSE_CONFIRMING"
            return
        if not self.gallery.seed(self._seed.get(p.track_id, [])):
            self.state = State.ACQUIRING
            self.reject_reason = "INCONSISTENT_REID_SEED"
            return
        self.memory = OperatorTargetMemory(str(uuid.uuid4()), p.track_id, now, now,
                                           p.bbox_xyxy, p.depth.depth_m, p.depth.xyz_center_m)
        self.memory.hsv_reference = p.hsv
        self.memory.pose_geometry_summary = _pose_ratio(p.skeleton)
        self.memory.bbox_history.append((now, p.bbox_xyxy))
        if p.depth.available:
            self.memory.depth_history.append((now, p.depth.depth_m))
        self.state = State.LOCKED_HIGH
        self.authorization_source = "FIRST_TPOSE"
        self.selected = p
        self.reject_reason = ""
        self.tpose_temporal.clear()
        self._seed.clear()

    def _reauthorize(self, people, now):
        """A lost session cannot recover from a stale motion prediction.

        Require a fresh T-Pose and old-gallery identity evidence. The old
        gallery is read-only while lost; the new authorization gets a new UUID.
        """
        assert self.memory is not None
        self.tpose_temporal.retain_only({p.track_id for p in people})
        eligible = []
        for p in people:
            tpose = self.tpose.recognize(p.skeleton, p.track_id)
            reid = self.gallery.score(p.embedding)
            checks = {
                "track_id": p.track_id, "mode": "REAUTH", "tpose_matched": bool(tpose.matched),
                "tpose_reasons": list(tpose.reasons),
                "pose_valid": bool(getattr(p.pose_quality, "valid", False)),
                "person_confidence": p.confidence, "crop_quality": p.crop_quality,
                "embedding_available": normalize_embedding(p.embedding) is not None,
                "bbox_valid": finite_bbox(p.bbox_xyxy), "old_gallery_similarity": reid}
            self.acquisition_checks.append(checks)
            passes = (checks["tpose_matched"] and checks["pose_valid"] and
                      p.confidence >= .45 and p.crop_quality >= .75 and
                      checks["embedding_available"] and checks["bbox_valid"] and
                      reid is not None and reid >= self.settings.reauth_reid_similarity)
            confirmed = self.tpose_temporal.update(p.track_id, now, bool(passes))
            if passes:
                eligible.append((p, reid, confirmed))
        if len(eligible) != 1:
            if len(eligible) > 1:
                # Frames with competing T-Poses cannot count toward a later
                # unique-person authorization if one candidate walks away.
                self.tpose_temporal.clear()
            self.state = State.OPERATOR_LOST
            self.reject_reason = "REAUTH_AMBIGUOUS" if len(eligible) > 1 else "REAUTH_TPOSE_AND_IDENTITY_REQUIRED"
            return
        p, reid, confirmed = eligible[0]
        others = [self.gallery.score(other.embedding) for other in people if other.track_id != p.track_id]
        others = [score for score in others if score is not None]
        margin = reid - max(others, default=0.0)
        if margin < self.settings.ambiguity_margin:
            self.state = State.OPERATOR_LOST
            self.reject_reason = "REAUTH_IDENTITY_AMBIGUOUS"
            self.tpose_temporal.clear()
            return
        if not confirmed:
            self.state = State.REAUTHORIZING
            self.reject_reason = "REAUTH_TPOSE_CONFIRMING"
            return
        self.memory = OperatorTargetMemory(str(uuid.uuid4()), p.track_id, now, now,
                                           p.bbox_xyxy, p.depth.depth_m, p.depth.xyz_center_m)
        self.memory.hsv_reference = p.hsv
        self.memory.pose_geometry_summary = _pose_ratio(p.skeleton)
        self.memory.bbox_history.append((now, p.bbox_xyxy))
        if p.depth.available:
            self.memory.depth_history.append((now, p.depth.depth_m))
        self.state = State.LOCKED_HIGH
        self.authorization_source = "LOST_TPOSE"
        self.selected = p
        self.reject_reason = ""
        self._red_since = None
        self._pending = None
        self._confirm_deadline_ms = None
        self._confirmation_attempted = False
        self.tpose_temporal.clear()
        self._auto_pending = None
        self._auto_last = None
        self.auto_reauthorize_state = "IDLE" if self.settings.auto_reauthorize_enabled else "DISABLED"

    def _auto_abort(self, reason):
        self._auto_pending = None
        self._auto_last = None
        self.auto_reauthorize_state = "SEARCHING"
        self.auto_reauthorize_reject_reason = reason
        self.state = State.OPERATOR_LOST
        self.selected = self.suspected = None
        self.reject_reason = reason

    def _auto_evidence(self, p, now):
        """Fresh long-term evidence: stale old-session motion/depth are not identity."""
        try:
            c = CandidateEvidence(p.track_id)
            stats = self.gallery.statistics(
                p.embedding, match_threshold=self.settings.auto_reauthorize_sample_threshold)
            if stats is not None:
                c.reid = Evidence(stats["max"], min(1., p.crop_quality*(1.-p.occlusion)), True)
            pose_ok = bool(getattr(p.pose_quality, "valid", False))
            ratio = _pose_ratio(p.skeleton) if pose_ok else None
            old_ratio = self.memory.pose_geometry_summary if self.memory else None
            if ratio is not None and old_ratio is not None:
                c.pose = Evidence(max(0., 1.-abs(ratio-old_ratio)/max(.3,old_ratio)), .35, True)
            elif pose_ok:
                c.pose = Evidence(1., .35, True)
            hsv_score = similarity(self.memory.hsv_reference, p.hsv) if self.memory else None
            if hsv_score is not None:
                c.hsv = Evidence(hsv_score, .3, True)
            continuous = True
            if self._auto_last is not None:
                previous = self._auto_last
                dt = (now-previous.timestamp_ms)/1000.
                diagonal = max(20., math.hypot(previous.bbox_xyxy[2]-previous.bbox_xyxy[0],
                                               previous.bbox_xyxy[3]-previous.bbox_xyxy[1]))
                jump = math.dist(_center(previous.bbox_xyxy), _center(p.bbox_xyxy))/diagonal
                continuous = (0 < dt <= 1.5 and
                              jump/max(dt,.001) <= self.settings.max_2d_speed_boxes_s)
                if previous.depth.available and p.depth.available:
                    continuous = continuous and abs(p.depth.depth_m-previous.depth.depth_m) <= (
                        self.settings.max_depth_jump_m+self.settings.max_depth_speed_m_s*dt)
            c.hard_gates = {
                "person_valid": finite_bbox(p.bbox_xyxy) and math.isfinite(p.confidence) and p.confidence >= .45,
                "timestamp_valid": p.timestamp_ms == now and
                                   (self._last_frame_ms is None or now == self._last_frame_ms),
                "observation_quality": math.isfinite(p.crop_quality) and
                                       p.crop_quality >= self.settings.auto_reauthorize_min_crop_quality,
                "pose_valid": pose_ok,
                "continuity": continuous,
                "face_no_conflict": not p.face_conflict,
                "reid_no_strong_conflict": stats is not None and
                                           stats["max"] >= self.settings.min_reid_similarity}
            c.fuse(self.settings.weights)
            return c, stats
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            return None, None

    def _auto_reauthorize(self, people, now):
        assert self.memory is not None
        if not self.settings.auto_reauthorize_enabled:
            self.auto_reauthorize_state = "DISABLED"
            self.auto_reauthorize_reject_reason = "AUTO_REAUTH_DISABLED"
            return
        if not self.gallery.entries:
            self._auto_abort("AUTO_REAUTH_NO_GALLERY")
            return
        if self._auto_pending is not None and now-self._auto_pending[1] > self.settings.auto_reauthorize_attempt_timeout_ms:
            self._auto_abort("AUTO_REAUTH_TIMEOUT")
            return
        if not people:
            self._auto_abort("AUTO_REAUTH_CANDIDATE_LOST" if self._auto_pending else "AUTO_REAUTH_NO_CANDIDATE")
            return
        evidence = []
        for p in people:
            c, stats = self._auto_evidence(p, now)
            if c is None:
                self._auto_abort("AUTO_REAUTH_HARD_GATE_FAIL")
                return
            evidence.append((p,c,stats))
        evidence.sort(key=lambda row: row[1].fused_normalized, reverse=True)
        self.candidates = [row[1] for row in evidence]
        p, best, stats = evidence[0]
        second = evidence[1] if len(evidence)>1 else None
        margin = best.fused_normalized-(second[1].fused_normalized if second else 0.)
        self.margin = margin
        self.auto_reauthorize_candidate_id = p.track_id
        self.auto_reauthorize_stats = stats
        self.auto_reauthorize_identity_score = best.fused_normalized
        self.auto_reauthorize_margin = margin
        strong_second = second is not None and second[2] is not None and second[2]["max"] >= self.settings.auto_reauthorize_reid_threshold
        if strong_second:
            reason = "AUTO_REAUTH_AMBIGUOUS"
        elif margin < self.settings.auto_reauthorize_margin_threshold:
            reason = "AUTO_REAUTH_MARGIN_LOW"
        elif not best.accepted:
            reason = "AUTO_REAUTH_LOW_CROP_QUALITY" if not best.hard_gates.get("observation_quality") else "AUTO_REAUTH_HARD_GATE_FAIL"
        elif stats is None or stats["max"] < self.settings.auto_reauthorize_reid_threshold:
            reason = "AUTO_REAUTH_IDENTITY_LOW"
        elif stats["topk_mean"] < self.settings.auto_reauthorize_topk_threshold or stats["match_count"] < self.settings.auto_reauthorize_min_match_count:
            reason = "AUTO_REAUTH_IDENTITY_LOW"
        elif best.fused_normalized < self.settings.auto_reauthorize_score_threshold or best.effective_weight < self.settings.auto_reauthorize_min_effective_weight:
            reason = "AUTO_REAUTH_SCORE_LOW"
        else:
            reason = None
        if reason:
            self._auto_abort(reason)
            return
        if self._auto_pending is not None and self._auto_pending[0] != p.track_id:
            self._auto_abort("AUTO_REAUTH_CANDIDATE_LOST")
            return  # New MOT ID may start a fresh, independently confirmed attempt next frame.
        if self._auto_pending is None:
            self._auto_pending = (p.track_id, now, 0)
        ident, start, count = self._auto_pending
        count += 1
        self._auto_pending = (ident,start,count)
        self._auto_last = p
        self.state = State.AUTO_REAUTHORIZE_CONFIRMING
        self.auto_reauthorize_state = "CONFIRMING"
        self.auto_reauthorize_reject_reason = "AUTO_REAUTH_CONFIRMING"
        self.reject_reason = "AUTO_REAUTH_CONFIRMING"
        self.suspected = p
        if count < self.settings.auto_reauthorize_min_frames or now-start < self.settings.auto_reauthorize_min_ms:
            return
        old_id = self.memory.operator_session_id
        self.memory = OperatorTargetMemory(str(uuid.uuid4()),p.track_id,now,now,
                                           p.bbox_xyxy,p.depth.depth_m,p.depth.xyz_center_m)
        self.memory.hsv_reference = p.hsv
        self.memory.pose_geometry_summary = _pose_ratio(p.skeleton)
        self.memory.bbox_history.append((now,p.bbox_xyxy))
        if p.depth.available:
            self.memory.depth_history.append((now,p.depth.depth_m))
        self.old_operator_session_id = old_id
        self.new_operator_session_id = self.memory.operator_session_id
        self.authorization_source = "AUTO_REAUTHORIZE"
        self._auto_gallery_hold_until_ms = now+self.settings.auto_reauthorize_gallery_hold_ms
        self._auto_success_until_ms = now+2000
        self._auto_pending = None
        self._auto_last = None
        self.auto_reauthorize_state = "SUCCESS"
        self.auto_reauthorize_reject_reason = "AUTO_REAUTH_SUCCESS"
        self.state = State.LOCKED_HIGH
        self.selected = p
        self.suspected = None
        self.reject_reason = ""
        self.state_transition_reason = "AUTO_REAUTH_SUCCESS"
        self._red_since = None
        self._pending = None
        self._confirm_deadline_ms = None
        self._confirmation_attempted = False
        self.tpose_temporal.clear()

    def auto_reauthorize_diagnostics(self, now):
        pending = self._auto_pending
        stats = self.auto_reauthorize_stats or {}
        return {
            "auto_reauthorize_enabled": self.settings.auto_reauthorize_enabled,
            "auto_reauthorize_state": self.auto_reauthorize_state,
            "auto_reauthorize_candidate_id": self.auto_reauthorize_candidate_id,
            "auto_reauthorize_reid_similarity": stats.get("max"),
            "auto_reauthorize_gallery_max": stats.get("max"),
            "auto_reauthorize_gallery_topk_mean": stats.get("topk_mean"),
            "auto_reauthorize_gallery_match_count": stats.get("match_count"),
            "auto_reauthorize_identity_score": self.auto_reauthorize_identity_score,
            "auto_reauthorize_margin": self.auto_reauthorize_margin,
            "auto_reauthorize_frames": pending[2] if pending else 0,
            "auto_reauthorize_elapsed_ms": max(0,now-pending[1]) if pending else 0,
            "auto_reauthorize_deadline_ms": pending[1]+self.settings.auto_reauthorize_attempt_timeout_ms if pending else None,
            "auto_reauthorize_reject_reason": self.auto_reauthorize_reject_reason,
            "authorization_source": self.authorization_source,
            "old_operator_session_id": self.old_operator_session_id,
            "new_operator_session_id": self.new_operator_session_id,
            "auto_reauthorize_success_notice": now < self._auto_success_until_ms,
        }

    def _evidence(self, p, now):
        m = self.memory; assert m is not None
        c = CandidateEvidence(p.track_id)
        dt = (now-m.last_confirmed_timestamp)/1000.0
        good_time = 0 < dt <= 3.0
        good_box = finite_bbox(p.bbox_xyxy)
        old = m.last_confirmed_bbox
        diagonal = max(20., math.hypot(old[2]-old[0], old[3]-old[1]))
        jump = math.dist(_center(old), _center(p.bbox_xyxy))/diagonal if good_box else math.inf
        speed = jump/max(dt, .001)
        motion_pass = good_time and speed <= self.settings.max_2d_speed_boxes_s
        depth_pass = True
        if m.last_depth_m is not None and p.depth.available:
            delta = abs(p.depth.depth_m-m.last_depth_m)
            depth_pass = delta <= self.settings.max_depth_jump_m+self.settings.max_depth_speed_m_s*dt
            xyz_jump = None
            if m.last_xyz is not None and p.depth.xyz_center_m is not None:
                xyz_jump = math.dist(m.last_xyz, p.depth.xyz_center_m)
                depth_pass = depth_pass and xyz_jump <= self.settings.max_depth_jump_m+self.settings.max_depth_speed_m_s*dt
            innovation = max(delta, xyz_jump if xyz_jump is not None else delta)
            c.depth = Evidence(max(0., 1.-innovation/max(.35, 1.5*dt)), p.depth.depth_quality, True)
        reid = self.gallery.score(p.embedding)
        if reid is not None:
            c.reid = Evidence(reid, min(1., p.crop_quality*(1.-p.occlusion)), True)
        c.motion = Evidence(max(0., 1.-speed/self.settings.max_2d_speed_boxes_s),
                            max(0., 1.-min(1., dt/3.)), good_time and good_box)
        c.mot = Evidence(1.0 if p.track_id == m.current_track_id else .4,
                         min(1., p.confidence), True)
        pose_ok = bool(getattr(p.pose_quality, "valid", False))
        ratio = _pose_ratio(p.skeleton) if pose_ok else None
        if ratio is not None and m.pose_geometry_summary is not None:
            difference = abs(ratio-m.pose_geometry_summary)
            c.pose = Evidence(max(0., 1.-difference/max(.3,m.pose_geometry_summary)), .35, True)
        elif p.skeleton is not None:
            c.pose = Evidence(1.0 if pose_ok else .2, .12, True)
        hsv_score = similarity(m.hsv_reference, p.hsv)
        if hsv_score is not None:
            c.hsv = Evidence(hsv_score, .3, True)  # deliberately weak clothing evidence
        c.hard_gates = {"person_valid": good_box and p.confidence >= .3,
                        "timestamp_valid": p.timestamp_ms == now and good_time,
                        "observation_quality": p.crop_quality >= self.settings.min_observation_quality,
                        "motion_plausible": motion_pass,
                        "xyz_depth_plausible": depth_pass,
                        "face_no_conflict": not p.face_conflict,
                        "reid_no_strong_conflict": reid is None or reid >= self.settings.min_reid_similarity}
        c.fuse(self.settings.weights)
        return c

    def _red(self, now, reason, suspected=None, state=State.LOCKED_LOW):
        was_confirming = self.state == State.REACQUIRE_CONFIRMING
        if was_confirming:
            self._pending = None
            self._confirm_deadline_ms = None
            state = State.AMBIGUOUS if state == State.AMBIGUOUS else State.REACQUIRING
            self.state_transition_reason = (
                "REACQUIRE_AMBIGUOUS" if state == State.AMBIGUOUS else
                "REACQUIRE_CONFIRMATION_LOST")
        self.state = state
        self.selected = None
        self.suspected = suspected
        self.reject_reason = reason
        if self._red_since is None:
            self._red_since = now
        limit = (self.settings.overall_reacquire_timeout_ms if self._confirmation_attempted
                 else self.settings.red_grace_ms)
        if now-self._red_since >= limit:
            if self.state != State.OPERATOR_LOST:
                self.tpose_temporal.clear()
                self._pending = None
                self._confirm_deadline_ms = None
            self.state = State.OPERATOR_LOST
            self.suspected = None
            self.reject_reason = ("REACQUIRE_OVERALL_TIMEOUT" if self._confirmation_attempted
                                  else "GRACE_TIMEOUT")
            self.state_transition_reason = self.reject_reason

    def reacquire_diagnostics(self, now):
        active = self.state == State.REACQUIRE_CONFIRMING and self._pending is not None
        return {
            "reacquire_confirmation_active": active,
            "reacquire_confirmation_candidate_id": self._pending[0] if active else None,
            "reacquire_confirmation_frames": self._pending[2] if active else 0,
            "reacquire_confirmation_elapsed_ms": max(0, now-self._pending[1]) if active else 0,
            "reacquire_confirmation_deadline_ms": self._confirm_deadline_ms if active else None,
            "overall_reacquire_elapsed_ms": max(0, now-self._red_since) if self._red_since is not None else 0,
            "overall_reacquire_deadline_ms": (self._red_since+self.settings.overall_reacquire_timeout_ms
                                                if self._red_since is not None else None),
            "state_transition_reason": self.state_transition_reason,
        }

    def _confirm(self, p, score, margin, now, was_reacquiring=False):
        m = self.memory; assert m is not None
        previous_center = _center(m.last_confirmed_bbox)
        dt = max(.001, (now-m.last_confirmed_timestamp)/1000.)
        m.last_2d_velocity = tuple((b-a)/dt for a,b in zip(previous_center, _center(p.bbox_xyxy)))
        if m.last_xyz is not None and p.depth.xyz_center_m is not None:
            m.estimated_3d_velocity = tuple((b-a)/dt for a,b in zip(m.last_xyz, p.depth.xyz_center_m))
        m.current_track_id = p.track_id
        m.last_confirmed_timestamp = now
        m.last_confirmed_bbox = p.bbox_xyxy
        m.last_depth_m = p.depth.depth_m if p.depth.available else m.last_depth_m
        m.last_xyz = p.depth.xyz_center_m if p.depth.available else m.last_xyz
        m.identity_confidence = score
        m.ambiguity_margin = margin
        m.ownership_state = State.LOCKED_HIGH
        m.gallery_quality = p.crop_quality
        ratio = _pose_ratio(p.skeleton)
        if ratio is not None and score >= .85:
            m.pose_geometry_summary = ratio if m.pose_geometry_summary is None else .9*m.pose_geometry_summary+.1*ratio
        if p.hsv is not None and score >= .85 and p.crop_quality >= .75 and margin >= .10:
            m.hsv_reference = p.hsv
        m.bbox_history.append((now, p.bbox_xyxy))
        if p.depth.available:
            m.depth_history.append((now, p.depth.depth_m))
            m.xyz_history.append((now, p.depth.xyz_center_m))
        self.selected = p
        self.suspected = None
        self.state = State.LOCKED_HIGH
        self._red_since = None
        self._pending = None
        self._confirm_deadline_ms = None
        self._confirmation_attempted = False
        self.state_transition_reason = "REACQUIRE_CONFIRMED" if was_reacquiring else ""
        self.reject_reason = ""
        self.gallery_updated = (now >= self._auto_gallery_hold_until_ms and self.gallery.update(
            p.embedding, green=True, confidence=score, crop_quality=p.crop_quality,
            occlusion=p.occlusion, margin=margin, conflict=False,
            motion_pass=True, depth_pass=True))

    def update(self, people, now, frame_id):
        self.state_transition_reason = ""
        self.selected = self.suspected = None
        self.candidates = []
        self.gallery_updated = False
        self.acquisition_checks = []
        self.auto_reauthorize_candidate_id = None
        self.auto_reauthorize_stats = None
        self.auto_reauthorize_identity_score = None
        self.auto_reauthorize_margin = None
        if self.memory is not None and self.state == State.LOCKED_HIGH and now >= self._auto_success_until_ms:
            self.auto_reauthorize_state = "IDLE" if self.settings.auto_reauthorize_enabled else "DISABLED"
        if self._last_frame_ms is not None and now <= self._last_frame_ms:
            if self.state in (State.OPERATOR_LOST, State.REAUTHORIZING,
                              State.AUTO_REAUTHORIZE_CONFIRMING):
                self._auto_abort("AUTO_REAUTH_HARD_GATE_FAIL")
            else:
                self._red(now, "NON_MONOTONIC_TIMESTAMP", state=State.REACQUIRING)
            return self.state
        self._last_frame_ms = now
        if len({p.track_id for p in people}) != len(people) or any(
            p.timestamp_ms != now or p.frame_id != frame_id for p in people):
            if self.state in (State.OPERATOR_LOST, State.REAUTHORIZING,
                              State.AUTO_REAUTHORIZE_CONFIRMING):
                self._auto_abort("AUTO_REAUTH_HARD_GATE_FAIL")
            else:
                self._red(now, "STALE_OR_DUPLICATE_TRACK", state=State.REACQUIRING)
            return self.state
        if self.memory is None:
            self._acquire(people, now)
            return self.state
        if self.state in (State.OPERATOR_LOST, State.REAUTHORIZING,
                          State.AUTO_REAUTHORIZE_CONFIRMING):
            self._reauthorize(people, now)
            if self.state == State.REAUTHORIZING:
                self._auto_pending = None
                self._auto_last = None
                return self.state
            if self.state == State.OPERATOR_LOST:
                self._auto_reauthorize(people, now)
            return self.state
        if self._red_since is not None:
            limit = (self.settings.overall_reacquire_timeout_ms if self._confirmation_attempted
                     else self.settings.red_grace_ms)
            if now-self._red_since >= limit:
                self._red(now, "REACQUIRE_OVERALL_TIMEOUT", state=State.REACQUIRING)
                return self.state
        if not people:
            self._red(now, "DETECTION_MISS", state=State.SHADOW_TRACKING)
            return self.state
        self.candidates = sorted((self._evidence(p, now) for p in people),
                                 key=lambda c: c.fused_normalized, reverse=True)
        best = self.candidates[0]
        second = self.candidates[1] if len(self.candidates) > 1 else None
        self.margin = best.fused_normalized-(second.fused_normalized if second else 0.)
        p = next(p for p in people if p.track_id == best.track_id)
        if not best.accepted:
            self._red(now, "HARD_GATE:"+",".join(k for k,v in best.hard_gates.items() if not v),
                      state=State.REACQUIRING)
            return self.state
        if self.margin < self.settings.ambiguity_margin:
            self._red(now, "AMBIGUOUS_CANDIDATES", p, State.AMBIGUOUS)
            return self.state
        if not best.reid.available or best.reid.value < .76:
            self._red(now, "REID_INSUFFICIENT", p)
            return self.state
        if best.fused_normalized < self.settings.green_score or best.effective_weight < .35:
            self._red(now, "LOW_IDENTITY_CONFIDENCE", p)
            return self.state
        needs_confirmation = (self.state != State.LOCKED_HIGH or
                              p.track_id != self.memory.current_track_id)
        if needs_confirmation and p.crop_quality < self.settings.min_confirm_crop_quality:
            self._red(now, "REACQUIRE_CROP_QUALITY_LOW", p, State.REACQUIRING)
            return self.state
        # A changed MOT ID or a RED state needs stable multi-frame reacquisition.
        if needs_confirmation:
            if self.state == State.REACQUIRE_CONFIRMING and self._pending is not None:
                ident, start, count = self._pending
                if ident != p.track_id:
                    self._red(now, "REACQUIRE_CANDIDATE_CHANGED", p, State.REACQUIRING)
                    return self.state
                if self._confirm_deadline_ms is not None and now > self._confirm_deadline_ms:
                    self._red(now, "REACQUIRE_CONFIRMATION_TIMEOUT", p, State.REACQUIRING)
                    return self.state
            else:
                if self._red_since is None:
                    self._red_since = now
                self._confirmation_attempted = True
                ident, start, count = p.track_id, now, 0
                self._confirm_deadline_ms = min(
                    now+self.settings.reacquire_confirmation_window_ms,
                    self._red_since+self.settings.overall_reacquire_timeout_ms)
                self.state_transition_reason = "REACQUIRE_CANDIDATE_FOUND"
            count += 1
            self._pending = (ident, start, count)
            if now-start < self.settings.reacquire_ms or count < self.settings.reacquire_frames:
                self.state = State.REACQUIRE_CONFIRMING
                self.selected = None
                self.suspected = p
                self.reject_reason = "REACQUIRE_CONFIRMING"
                if count > 1:
                    self.state_transition_reason = ""
                return self.state
            was_reacquiring = True
        else:
            was_reacquiring = False
        self._confirm(p, best.fused_normalized, self.margin, now, was_reacquiring)
        return self.state

    def authorize(self, gesture, now, frame_id):
        m = self.memory
        reasons = []
        if self.state != State.LOCKED_HIGH or self.selected is None:
            reasons.append(self.reject_reason or "NOT_GREEN")
        elif not getattr(self.selected.pose_quality, "valid", False):
            reasons.append("POSE_INVALID")
        label = getattr(gesture, "label", "UNKNOWN")
        label = getattr(label, "value", label)
        score = getattr(gesture, "score", 0.)
        if label not in {"LEFT", "RIGHT", "ASCEND", "DESCEND", "HOVER"}:
            reasons.append("GESTURE_UNKNOWN")
        if not getattr(gesture, "stable", False):
            reasons.append("GESTURE_UNCONFIRMED")
        valid = not reasons
        result = AuthorizedGestureV1(now, frame_id,
            m.operator_session_id if m else None, m.current_track_id if m else None,
            self.state.value, label if valid else "UNKNOWN", float(score) if valid else 0.,
            valid, reasons, m.identity_confidence if valid else 0.)
        result.to_dict()
        return result

    def ui_state(self):
        if self.memory is None:
            return "YELLOW"
        return {State.LOCKED_HIGH: "GREEN", State.OPERATOR_LOST: "GRAY",
                State.WAIT_OPERATOR: "YELLOW", State.ACQUIRING: "YELLOW"}.get(self.state, "RED")
