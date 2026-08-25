# Round-win model (roundwin-v1, trained 2026-08-24)

`gridbot.model` turns `data/analysis/training_rounds.jsonl` into the served
round-win quant model q: a LightGBM binary classifier over the 20-feature
contract (`gridbot.corpus.rows.FEATURE_NAMES`, CT-framed, label 1.0 = CT-side
team won the round) plus Platt calibration. Train with
`uv run python -m gridbot.model.train`; artifacts land in
`data/models/roundwin-v1/` (`model.txt`, `calibration.json`, `metrics.json`,
`holdout_series.json`, `feature_names.json`). q is never an LLM.

## Leak filter (BINDING — CORPUS-F1-LEAKFILTER)

The historical corpus contains post-round transition snapshots whose features
(`score_lead`/`rounds_played`/`man_advantage`) encode the labeled round's own
outcome. `gridbot.model.dataset.load_dataset` therefore keeps a row ONLY if

```
features.rounds_played + 1 == features.round_number == top-level round_number
```

Verified accounting on the frozen 22,273-row corpus: **2,898 leak rows
dropped → 19,375**, minus **49 more** rows missing at least one of the 20
feature keys (67 such pre-schema-extension rows exist; 18 also fail the leak
filter) → **19,326 trained rows**. Nothing else is filtered — the ~43
economy-free historical rows stay. Any pipeline that scores or trains on
`training_rounds` without this filter is invalid by registry decision.

## Split design

Splits are by SERIES, never by row — rows of one series never straddle a
split (shared teams/momentum would leak across otherwise).

1. Sort unique `series_id` by earliest `captured_at`.
2. Last 20% of series = **HOLDOUT** (temporal: the model is judged only on
   series newer than everything it trained on). v1: 32 of 158 series.
3. Random seeded 15% of the remaining series = **validation**, used for early
   stopping (binary logloss, patience 50, ≤2000 trees, num_leaves 31,
   lr 0.05) and for fitting Platt scaling (`p = sigmoid(a·raw + b)` on the
   LightGBM raw score). v1: 19 series; train: 107 series.

Holdout rows influence nothing: not the trees, not the stopping point, not
the calibration. All reported metrics are holdout-only.

## Benchmark contract (pinned holdout, fail-closed promotion)

`holdout_series.json` is **PINNED**. Every future retrain MUST evaluate on
exactly these held-out series — `train.py` reuses the file automatically when
it exists; never edit or delete it. A retrained model may be promoted to
served-q ONLY if its **calibrated Brier on the pinned holdout improves**;
otherwise the old model stays (fail-closed keep-old — a silent metric
regression must never reach the trade path). Re-pinning the holdout (e.g.
once enough post-2026-07 capture accrues) is a new model version
(`roundwin-v2/`) with its own directory and a fresh baseline, never an edit
in place.

## v1 holdout metrics (n=4,276 rows; best_iteration=96)

| metric     | raw    | calibrated |
|------------|--------|------------|
| Brier      | 0.1499 | 0.1501     |
| ECE (10b)  | 0.0307 | 0.0296     |
| AUC        | 0.8640 | 0.8640     |
| log loss   | 0.4488 | 0.4508     |

Platt is near-identity here (a=0.968, b=-0.128): it trades a hair of Brier /
log loss for better ECE. Which score to serve is an instrument-level choice;
the promotion gate is calibrated Brier.

## Honest comparison caveat

The inherited cs2agent benchmark (**Brier 0.1514 / ECE 0.0104**) was computed
on data containing the leak and is **NOT comparable** to any number above —
leaked rows let that model partially read the answer off the features, and
its ECE was measured against a differently-composed evaluation set. Do not
cite the old numbers as a baseline this model "beats" or "misses"; the only
legitimate comparisons are between models evaluated under this leak filter on
the pinned holdout series.
