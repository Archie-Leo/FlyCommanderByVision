# Stage 4 Gesture V1 Dataset Plan

Status: **HISTORICAL COLLECTION PLAN / FINAL AUDIT COMPLETED 2026-09-23**. The current Gate
decision is in `STAGE4_FINAL_VALIDATION_REPORT.md`; this plan does not request new collection.

The Stage 4 Gate requires real metrics. Synthetic tests and the recycled Stage 3 hard negatives
are software evidence, not a substitute for a balanced five-gesture dataset.

## Collection matrix

The minimum Stage 4 Gate set communicated to the operator is 60 sequences:

- for every legal gesture, approximately 0.8 m, 1.5 m and 2.0 m;
- 3 independent repetitions at each distance;
- use the three repetitions to cover center, image-left and image-right where space permits.

This produces 9 short sequences per gesture and 45 legal-action sequences total. Each sequence
should last about 2–3 seconds. Start recording while neutral, deliberately enter the selected pose,
hold it, then stop. This preserves the transition and temporal confirmation evidence.

Collect at least 15 negative/hard-negative sequences distributed across:

- natural arms down;
- T-Pose (reserved, must remain UNKNOWN);
- half-raised or incomplete LEFT/RIGHT;
- scratching the head/face;
- adjusting clothes;
- crossing arms casually;
- turning partially sideways;
- walking through the frame;
- arm occlusion and body truncation;
- empty frame;
- chair occlusion similar to Stage 3 frame 430.

The extended follow-up target, if the minimum set exposes unstable cells, is 15 sequences per legal
gesture and 30 negatives. These counts are project engineering targets, not official statistical
thresholds.

The 2026-09-16 Pilot contains one center repetition at each of the three distances for every legal
gesture (15 legal sequences) plus 7 negatives. It is development data because it was used to revise
the DESCEND outward threshold. To finish the minimum Gate set, add two repetitions per distance—one
toward image-left and one toward image-right—for every legal gesture (30 more), plus 8 new diverse
negative sequences. Those new 38 sequences are the held-out evaluation subset.

## Live collection controls

Run `live_gesture.py`, select the ground-truth class before recording, then use `R`:

```text
0 = UNKNOWN
1 = LEFT
2 = RIGHT
3 = ASCEND
4 = DESCEND
5 = HOVER
R = start/stop labeled sequence recording
SPACE = three-second single snapshot
S = immediate single snapshot
Q/ESC = quit
```

Do not change the selected label while a sequence is recording. The application enforces this.
Single snapshots are useful for visual diagnostics; recorded JSONL sequences are the source for
temporal confirmation metrics.

## Formal evaluation

Run raw snapshot evaluation with `evaluate_dataset.py` and temporal sequence evaluation with
`evaluate_sequences.py`. Report at least:

- confusion matrix;
- per-gesture precision and recall;
- legal-action rejection rate;
- false-trigger count and source sequence;
- confirmation latency for every legal sequence;
- failures grouped by position, distance, transition, occlusion, and pose amplitude.

Do not tune and report final metrics on exactly the same samples without recording that limitation.
If the dataset becomes large enough, split by complete sequence (never individual frames) into
development and held-out evaluation sets.
