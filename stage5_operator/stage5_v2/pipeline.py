from __future__ import annotations

import time

from gesture import GeometryGestureRecognizer, TemporalStabilizer

from stage5.appearance import torso_histogram
from .ownership import OwnershipManagerV2, PersonObservation, Settings, State


class Stage5PipelineV2:
    def __init__(self, tracker, embedder, depth, *, ownership_settings=None,
                 roi_depth=True):
        self.tracker = tracker
        self.embedder = embedder
        self.depth = depth
        self.roi_depth = roi_depth
        self.ownership = OwnershipManagerV2(ownership_settings or Settings())
        self.gesture = GeometryGestureRecognizer()
        self.temporal = TemporalStabilizer()
        self._last_selected = None

    def process(self, left_raw, right_raw, pose_frame, quality_evaluator, normalizer,
                *, rectified_left=None):
        start = time.perf_counter()
        poses = pose_frame.poses
        # ReID and the tracker need only the rectified left image. Disparity is
        # expensive and is useful only for a current tracked observation.
        rectified, _ = self.depth.process(left_raw, right_raw, [],
                                          rectified_left=rectified_left)
        rectified_at = time.perf_counter()
        embeddings = []; qualities = []; keep = []
        osnet_total_ms = 0.0
        for i, pose in enumerate(poses):
            vector, quality = self.embedder.extract(rectified, pose.bbox_xyxy)
            osnet_total_ms += self.embedder.last_latency_ms
            if vector is not None:
                keep.append(i)
                embeddings.append(vector)
                qualities.append(quality)
        reid_at = time.perf_counter()
        rows = self.tracker.update(rectified, [poses[i].bbox_xyxy for i in keep],
                                   [float(poses[i].pose_score or 0.) for i in keep], embeddings)
        tracking_at = time.perf_counter()
        tracked_sources = list(dict.fromkeys(
            keep[row["detection_index"]] for row in rows
            if 0 <= row["detection_index"] < len(keep)))
        # Preserve the exact Stage2 depth algorithm and original SBS inputs.
        # If no detection survived ReID/tracking, avoid full-frame SGBM.
        tracked_boxes = [poses[i].bbox_xyxy for i in tracked_sources]
        track_by_source = {keep[row["detection_index"]]: row["track_id"] for row in rows
                           if 0 <= row["detection_index"] < len(keep)}
        tracked_ids = [track_by_source[i] for i in tracked_sources]
        use_roi_depth = self.roi_depth and callable(getattr(self.depth, "process_tracked", None))
        if use_roi_depth:
            _, tracked_depths = self.depth.process_tracked(
                left_raw, right_raw, tracked_boxes, tracked_ids,
                frame_id=pose_frame.frame_id, timestamp_ms=pose_frame.timestamp_ms,
                rectified_left=rectified)
        else:
            _, tracked_depths = self.depth.process(
                left_raw, right_raw, tracked_boxes, rectified_left=rectified)
        depth_at = time.perf_counter()
        depths = dict(zip(tracked_sources, tracked_depths))
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
        observations_at = time.perf_counter()
        fusion_start = time.perf_counter()
        self.ownership.update(people, pose_frame.timestamp_ms, pose_frame.frame_id)
        fusion_latency_ms = (time.perf_counter()-fusion_start)*1000.0
        ownership_at = time.perf_counter()
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
        gesture_at = time.perf_counter()
        candidates = [c.to_dict() for c in self.ownership.candidates]
        memory = self.ownership.memory
        total_ms = (time.perf_counter()-start)*1000.0
        depth_ages = getattr(self.depth, "last_depth_ages_ms", []) if use_roi_depth else []
        depth_sources = getattr(self.depth, "last_depth_sources", []) if use_roi_depth else []
        depth_observations = [dict(track_id=track_id, valid=value.available,
                                   age_ms=depth_ages[i] if i < len(depth_ages) else None,
                                   source_frame_id=depth_sources[i] if i < len(depth_sources) else None,
                                   depth_m=value.depth_m)
                              for i, (track_id, value) in enumerate(zip(tracked_ids, tracked_depths))]
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
                           "stage5_total": total_ms,
                           "stage5_non_depth": max(0., total_ms-self.depth.last_latency_ms)},
            "runtime_profile_ms": {
                "rectified_input": (rectified_at-start)*1000.,
                "reid_section": (reid_at-rectified_at)*1000.,
                "tracking_section": (tracking_at-reid_at)*1000.,
                "depth_section": (depth_at-tracking_at)*1000.,
                "observations": (observations_at-depth_at)*1000.,
                "ownership": (ownership_at-fusion_start)*1000.,
                "stage4_authorize": (gesture_at-ownership_at)*1000.,
            },
            "depth_mode": "ROI" if use_roi_depth else "FULL_FRAME",
            "depth_profile_ms": getattr(self.depth, "last_profile_ms", {}) if use_roi_depth else {},
            "depth_age_ms": getattr(self.depth, "last_depth_ages_ms", []) if use_roi_depth else [],
            "depth_observations": depth_observations,
            "depth_source_frame_ids": getattr(self.depth, "last_depth_sources", []) if use_roi_depth else [],
            "depth_valid": getattr(self.depth, "last_depth_valid", []) if use_roi_depth else [],
            "depth_updated_count": getattr(self.depth, "last_updated_count", 0) if use_roi_depth else 0,
            "depth_roi_shapes": getattr(self.depth, "last_roi_shapes", []) if use_roi_depth else []}
        return rectified, people, authorized, log
