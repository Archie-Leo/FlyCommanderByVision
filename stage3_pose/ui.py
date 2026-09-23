from __future__ import annotations

import cv2


CONNECTIONS = (
    ("left_shoulder", "right_shoulder"), ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"), ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"), ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"), ("left_hip", "right_hip"),
    ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
)


def draw_overlay(frame, pose, quality, fps, pose_ms, total_ms, verbose=False, countdown_seconds=None):
    canvas = frame.copy()
    if pose:
        for start_name, end_name in CONNECTIONS:
            start, end = pose.joints.get(start_name), pose.joints.get(end_name)
            if start and end and start.valid and end.valid:
                cv2.line(canvas, (round(start.x_px), round(start.y_px)),
                         (round(end.x_px), round(end.y_px)), (0, 220, 255), 2, cv2.LINE_AA)
        for name, joint in pose.joints.items():
            if joint.valid:
                color = (255, 80, 80) if name.startswith("left_") else (80, 80, 255)
                cv2.circle(canvas, (round(joint.x_px), round(joint.y_px)), 4, color, -1, cv2.LINE_AA)
                if verbose:
                    cv2.putText(canvas, name, (round(joint.x_px) + 4, round(joint.y_px) - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.36, color, 1, cv2.LINE_AA)
        x1, y1, x2, y2 = pose.bbox_xyxy
        cv2.rectangle(canvas, (round(x1), round(y1)), (round(x2), round(y2)), (255, 200, 0), 2)
    quality_color = (0, 220, 0) if quality.valid else (0, 0, 255)
    rows = [
        ("POSE: FOUND" if pose else "POSE: NOT FOUND", (0, 220, 0) if pose else (0, 0, 255)),
        (f"QUALITY: {'VALID' if quality.valid else 'INVALID'}  score={quality.score:.3f}", quality_color),
        ("Reason: " + (", ".join(quality.reasons) if quality.reasons else "NONE"), quality_color),
        (f"FPS: {fps:.1f}  Pose: {pose_ms:.1f} ms  Total: {total_ms:.1f} ms", (255, 255, 255)),
        ("[SPACE] 3s photo  [S] now  [L] verbose  [Q/ESC] quit", (255, 255, 255)),
    ]
    y = 28
    for text, color in rows:
        cv2.putText(canvas, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 1, cv2.LINE_AA)
        y += 27
    if countdown_seconds is not None:
        label = str(countdown_seconds)
        font = cv2.FONT_HERSHEY_DUPLEX
        scale = 5.0
        thickness = 10
        (width, height), _ = cv2.getTextSize(label, font, scale, thickness)
        origin = ((canvas.shape[1] - width) // 2, (canvas.shape[0] + height) // 2)
        cv2.putText(canvas, label, origin, font, scale, (0, 0, 0), thickness + 8, cv2.LINE_AA)
        cv2.putText(canvas, label, origin, font, scale, (0, 255, 255), thickness, cv2.LINE_AA)
    return canvas
