# Gesture V1 Protocol

Status: **FROZEN FOR STAGE 4 BASELINE**  
Date: 2026-09-16

## Scope and safety boundary

Stage 4 consumes only `NormalizedSkeletonV1`. It does not read MediaPipe objects, identify an
operator, track a person, publish ROS 2/PX4 messages, or command a vehicle. Its output is a
`GestureCandidateV1` observation for later safety and intent layers.

Image/body coordinates remain those frozen in Stage 3: x points image-right and y points down.
`left_*` and `right_*` always mean the operator's anatomical left/right. For a front-facing
operator, anatomical left normally appears on the image-right.

No `ACTIVATE` gesture exists in V1. T-Pose is explicitly reserved for possible Stage 5 operator
authorization and must produce `UNKNOWN` in Stage 4.

## V1 labels

The five legal labels are `LEFT`, `RIGHT`, `ASCEND`, `DESCEND`, and `HOVER`. `UNKNOWN` and
`INVALID` are reject states, not commands.

### LEFT

- Anatomical left arm is straight and extended horizontally outward.
- Left wrist is at approximately shoulder height and clearly image-right of the left shoulder.
- Anatomical right arm is straight and naturally down.
- A T-Pose is not LEFT because the opposite arm is not down.

### RIGHT

- Mirror of LEFT using the operator's anatomical right arm.
- Right arm is straight and extended horizontally outward; left arm is naturally down.
- A T-Pose is not RIGHT.

### ASCEND

- Both arms are substantially above their corresponding shoulders.
- Both elbows are approximately straight.
- A Y-shaped or nearly vertical two-arm raise is accepted.

### DESCEND

- Both arms are straight and point diagonally down and outward, forming an inverted Y.
- Each wrist must be both below and laterally outside its shoulder.
- Natural arms-down is deliberately excluded because it lacks sufficient outward displacement.

### HOVER

- Both upper arms extend horizontally outward.
- Both elbows are bent approximately 90 degrees.
- Both forearms point upward, producing a deliberate double “goalpost” pose.
- This avoids overloading T-Pose and avoids treating natural standing as HOVER.

## Frozen engineering tolerances

All distances below are in Stage 3 body-scale units. They are project heuristics, not official
MediaPipe thresholds.

| Parameter | V1 value |
|---|---:|
| Minimum required joint confidence | 0.65 |
| Straight elbow minimum | 145 deg |
| Bent elbow interval | 65–125 deg |
| Horizontal vertical deviation | <= 0.35 |
| Single-arm horizontal reach | >= 0.65 |
| Opposite arm downward displacement | >= 0.65 |
| Opposite arm maximum lateral drift | <= 0.45 |
| ASCEND wrist rise above shoulder | >= 0.65 |
| DESCEND wrist drop below shoulder | >= 0.65 |
| DESCEND outward displacement | >= 0.40 |
| HOVER upper-arm outward displacement | >= 0.40 |
| HOVER forearm upward displacement | >= 0.35 |

Rules require valid left/right shoulder, elbow, and wrist joints. Invalid Stage 3 quality, missing
required joints, non-finite geometry, or insufficient confidence yields `INVALID`. No matching rule
yields `UNKNOWN`. More than one matching rule yields `UNKNOWN` with `AMBIGUOUS_MATCH`; V1 never
uses rule ordering to hide overlap.

The DESCEND outward threshold was revised from 0.35 to 0.40 on 2026-09-16 after the development
Pilot set exposed a natural-arms-down false trigger at 0.351–0.362. This removed the trigger while
retaining confirmation on all 3/3 Pilot DESCEND sequences. The Pilot set is therefore development
data; remaining collection must be treated as held-out evidence for this revision.

## Temporal confirmation

- A legal raw label requires at least 4 consecutive frames and 300 ms before `stable=true`.
- A stable label gets a short two-frame/120 ms release grace against one-frame jitter.
- `INVALID` clears temporal state immediately.
- Switching directly between legal gestures clears the old stable label and reconfirms the new one.
- State is per supplied skeleton stream only; it is not an operator session or identity tracker.

## GestureCandidateV1

Frozen fields:

```text
timestamp_ms
frame_id
local_detection_id
label                  # LEFT/RIGHT/ASCEND/DESCEND/HOVER/UNKNOWN/INVALID
score                  # engineering geometry score, not calibrated probability
stable
stable_for_ms
matched_labels
reasons
rule_scores
source_quality_score
source_schema_version  # must be NormalizedSkeletonV1
schema_version         # GestureCandidateV1
```

Downstream code must require both a legal label and `stable=true`. `UNKNOWN` is not automatically
rewritten to HOVER inside Stage 4; later safety arbitration decides vehicle behavior.
