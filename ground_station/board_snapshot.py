"""Read existing Pose/Stage5 data; never create or change decisions."""
from __future__ import annotations

from .protocol import JOINTS, empty_snapshot, finite


def _gesture(value):
    if isinstance(value, dict):
        return value.get("label")
    return value if isinstance(value, str) else None


def build_visual_snapshot(pose_frame, people, record, source_frame_id, adapter):
    snapshot = empty_snapshot()
    snapshot["source_frame_id"] = source_frame_id
    snapshot["processed_frame_id"] = pose_frame.frame_id
    snapshot["people_count"] = len(pose_frame.poses)
    state = record.get("ownership_state") or "WAIT_OPERATOR"
    operator = snapshot["operator"]
    operator["state"] = state
    operator["session_id"] = record.get("operator_session_id")
    selected_id = (record.get("current_track_id") if state == "LOCKED_HIGH"
                   else (record.get("auto_reauthorize_candidate_id") or
                         (record.get("best_candidate") or {}).get("track_id")))
    person = next((p for p in people if p.track_id == selected_id), None)
    if person is not None:
        pose = next((p for p in pose_frame.poses if
                     p.local_detection_id == person.skeleton.local_detection_id), None)
        if pose is not None:
            operator["kind"] = "operator" if state == "LOCKED_HIGH" else "candidate"
            operator["track_id"] = selected_id
            box = adapter.bbox(pose.bbox_xyxy)
            operator["bbox"] = [finite(v, 1) for v in box] if box else None
            operator["pose_score"] = finite(pose.pose_score, 3)
            joints = []
            for name in JOINTS:
                joint = pose.joints.get(name)
                point = (adapter.point(joint.x_px, joint.y_px) if joint is not None
                         and joint.valid and joint.x_px is not None and joint.y_px is not None
                         else None)
                joints.append([finite(point[0], 1), finite(point[1], 1),
                               finite(joint.confidence, 2)] if point else None)
            operator["keypoints"] = joints
            depth = person.depth
            observation = next((d for d in record.get("depth_observations", [])
                                if d.get("track_id") == selected_id), {})
            snapshot["depth"] = {"m": finite(depth.depth_m, 3),
                "valid": bool(depth.available),
                "age_ms": finite(observation.get("age_ms"), 1),
                "quality": finite(depth.depth_quality, 3)}
    snapshot["gesture"] = {"raw": _gesture(record.get("gesture_raw")),
                           "stable": _gesture(record.get("gesture_stable"))}
    return snapshot
