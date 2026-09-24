from __future__ import annotations

import time

from gesture import GeometryGestureRecognizer, TemporalStabilizer

from stage5.appearance import torso_histogram
from .ownership import OwnershipManagerV2, PersonObservation, Settings, State


class Stage5PipelineV2:
    def __init__(self, tracker, embedder, depth, *, ownership_settings=None):
        self.tracker = tracker
        self.embedder = embedder
        self.depth = depth
        self.ownership = OwnershipManagerV2(ownership_settings or Settings())
        self.gesture = GeometryGestureRecognizer()
        self.temporal = TemporalStabilizer()
        self._last_selected = None

    def process(self, left_raw, right_raw, pose_frame, quality_evaluator, normalizer,
                *, rectified_left=None):
        start = time.perf_counter()
        poses = pose_frame.poses
        # Depth from the original simultaneous SBS pair; pose on rectified LEFT.
        rectified, depths = self.depth.process(left_raw, right_raw,
                                                [p.bbox_xyxy for p in poses],
                                                rectified_left=rectified_left)
        embeddings = []; qualities = []; keep = []
        osnet_total_ms = 0.0
        for i, pose in enumerate(poses):
            vector, quality = self.embedder.extract(rectified, pose.bbox_xyxy)
            osnet_total_ms += self.embedder.last_latency_ms
            if vector is not None:
                keep.append(i)
                embeddings.append(vector)
                qualities.append(quality)
        rows = self.tracker.update(rectified, [poses[i].bbox_xyxy for i in keep],
                                   [float(poses[i].pose_score or 0.) for i in keep], embeddings)
        people = []
        for row in rows:
            index = row["detection_index"]
            if not 0 <= index < len(keep):
                continue  # Predicted-only MOT tracks cannot authorize a gesture.
            source = keep[index]
            pose = poses[source]
            quality = quality_evaluator.evaluate(pose)
            skeleton = normalizer.normalize(pose, quality)
            hsv, _ = torso_histogram(rectified, pose.bbox_xyxy)
            people.append(PersonObservation(
                row["track_id"], pose_frame.timestamp_ms, pose_frame.frame_id,
                row["bbox_xyxy"], row["confidence"], quality, skeleton,
                embeddings[index], qualities[index], depths[source], hsv))
        fusion_start = time.perf_counter()
        self.ownership.update(people, pose_frame.timestamp_ms, pose_frame.frame_id)
        fusion_latency_ms = (time.perf_counter()-fusion_start)*1000.0
        selected = self.ownership.selected
        key = (self.ownership.memory.operator_session_id, selected.track_id) if selected and self.ownership.memory else None
        if key != self._last_selected:
            self.temporal.reset()
            self._last_selected = key
        gesture_raw = gesture_stable = None
        if (self.ownership.state == State.LOCKED_HIGH and selected is not None and
                self.ownership.state_transition_reason != "AUTO_REAUTH_SUCCESS"):
            gesture_raw = self.gesture.recognize(selected.skeleton)
            gesture_stable = self.temporal.update(gesture_raw)
        else:
            self.temporal.reset()
            self._last_selected = None
        authorized = self.ownership.authorize(gesture_stable, pose_frame.timestamp_ms, pose_frame.frame_id)
        candidates = [c.to_dict() for c in self.ownership.candidates]
        memory = self.ownership.memory
        log = {
            "schema_version": "Stage5V2Frame", "timestamp_ms": pose_frame.timestamp_ms,
            "frame_id": pose_frame.frame_id, "tracker": "boxmot_native_botsort",
            "reid_model": self.ownership.gallery.model_version,
            "operator_session_id": memory.operator_session_id if memory else None,
            "current_track_id": memory.current_track_id if memory else None,
            "ownership_state": self.ownership.state.value,
            "ui_state": self.ownership.ui_state(), "reject_reason": self.ownership.reject_reason,
            **self.ownership.reacquire_diagnostics(pose_frame.timestamp_ms),
            **self.ownership.auto_reauthorize_diagnostics(pose_frame.timestamp_ms),
            "candidates": candidates, "best_candidate": candidates[0] if candidates else None,
            "second_candidate": candidates[1] if len(candidates)>1 else None,
            "ambiguity_margin": self.ownership.margin,
            "suspected_track_id": self.ownership.suspected.track_id if self.ownership.suspected else None,
            "gallery_size": len(self.ownership.gallery.entries),
            "gallery_updated": self.ownership.gallery_updated,
            "acquisition_checks": self.ownership.acquisition_checks,
            "people": [{"track_id": p.track_id, "bbox_xyxy": list(p.bbox_xyxy),
                        "depth_m": p.depth.depth_m, "depth_quality": p.depth.depth_quality,
                        "crop_quality": p.crop_quality, "pose_valid": bool(getattr(p.pose_quality, "valid", False))}
                       for p in people],
            "gesture_raw": gesture_raw.to_dict() if gesture_raw else None,
            "gesture_stable": gesture_stable.to_dict() if gesture_stable else None,
            "authorized_gesture": authorized.to_dict(),
            "latency_ms": {"mot": self.tracker.last_latency_ms,
                           "osnet_total": osnet_total_ms,
                           "stereo_depth": self.depth.last_latency_ms,
                           "ownership_fusion": fusion_latency_ms,
                           "stage5_total": (time.perf_counter()-start)*1000.0}}
        return rectified, people, authorized, log
