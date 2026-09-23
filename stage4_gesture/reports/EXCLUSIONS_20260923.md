# Stage 4 sequence exclusions — 2026-09-23

Source session: `/home/sentinel/drone_stage4_gesture/datasets/20260923_051541_UTC`

At the operator's explicit request, the following two sequences were removed from the active
evaluation set. They were **moved, not deleted**: both remain in the same session's `excluded/`
directory. The active evaluator receives only top-level `sequence_*.jsonl` files, so this
directory is outside normal evaluation globs. No labels, skeleton frames, or source bytes were
changed.

| Sequence | Reason for exclusion | SHA-256 |
|---|---|---|
| `sequence_0039_ASCEND.jsonl` | **Operator-confirmed recording error / invalid ground truth:** target ASCEND was not correctly performed. The skeleton audit found downward-sloping arms and 5 stable DESCEND frames. | `03a983be6356ecf6dd05ecadc26eb69c95b04beff99923bdec78f5e19a8d89f6` |
| `sequence_0040_DESCEND.jsonl` | **Operator-confirmed recording error / invalid ground truth:** target DESCEND was not correctly performed. The earlier skeleton audit found right-wrist outward median 0.398 against the 0.40 frozen rule. | `232dd7ced6f98687306b53355d3da1a879c96ce1952039f7d753a490057fbe7b` |

The hashes were verified before and after moving. Original labels have **not** been corrected or
reinterpreted. The pre-exclusion diagnostic report remains
`new_20260923_0027_0048_recomputed.json`; do not confuse it with current active-set metrics.

Subsequent operator confirmation (2026-09-23) established that **both target gestures were not
correctly performed during recording**. Their labeled ground truth is therefore invalid for
the final held-out set. This confirmation, not the recognizer's failure, is the exclusion reason.
Source video was not saved, so independent visual reconfirmation is impossible; the decision is
explicitly attributed to the operator. The 48-sequence pre-exclusion result remains mandatory
historical/raw diagnostic evidence; the 46-sequence curated set is now the final valid held-out
set. Both excluded files remain recoverable in `excluded/` and their hashes are unchanged.

After exclusion, supplementary sequences `0027`–`0048` have 20 active recordings (four per
legal class) and all 20 confirmed their intended stable class, with no wrong stable label.
The current session has 46 active recordings: 36 legal and 10 UNKNOWN. Combining it with the
22-sequence development Pilot gives 68 active recordings (51 legal, 17 UNKNOWN). All 51 legal
sequences confirmed at least once, and no stable legal false trigger occurred in the 17 UNKNOWN
sequences / 6,582 negative frames. These are dataset-specific replay results, not a Stage 4 Gate
PASS: the Pilot informed the DESCEND threshold, distances were not saved as metadata, and
pose/video quality plus the position-by-distance matrix still require review.

Current reports:

- `new_20260923_0027_0048_excluding_0039_0040.json`
- `session_20260923_active_excluding_0039_0040.json`
- `stage4_active_pilot_plus_20260923_excluding_0039_0040.json`
