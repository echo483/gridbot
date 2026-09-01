# H-PCAL1 population finding: the overconfidence lives in the QUALIFYING subset

Run 2026-09-01 ~06:4xZ, ops session, during the first real-data `pcal_fit.py
fit`. Reporting only — no artifact was written (the STOP-grade anchor refused,
by design), no registry edit, nothing armed.

## What happened

`scripts/pcal_fit.py fit` (H-PCAL1 instrument, built tonight) computes the
armed-band raw gap (mean p_map − realized win rate, p_map ∈ [0.50, 0.80]) on
its fit population — first evaluation per (series, map, round), resolved via
the frozen grid_winners oracle, pre-holdout series only — and refuses to fit
unless that gap reproduces the measured +0.15..+0.30 signal.

It refused: **gap +0.049 on n=7,677 observations.**

The 08-30 measurement (docs/analysis/2026-08-30-pmap-slate-variance.md,
−0.2129 CI (−0.309, −0.114)) was taken on **qualifying rows** (2,668 rows /
80 series) — the traded population. Recomputed tonight with the same
oracle/dedup/holdout machinery, restricted to `qualifies == true` rows:

| population (first-eval dedup, pre-holdout, resolved) | armed-band n | series | gap |
|---|---:|---:|---:|
| ALL evaluations | (of 7,677 obs) | — | **+0.049** |
| QUALIFYING only | 399 | 87 | **+0.196** |

## Read

1. The 21pt armed-band overconfidence is NOT a property of p_map per se; the
   unconditional per-round forecast is ~5pt overconfident in-band. It is a
   property of the QUALIFYING subset: requiring `side_ask ≤ p_map − 0.04`
   conditions on the book pricing 4+pts under the model, and that
   disagreement is informative — the market is usually the one that's right.
   Conditioning on it takes the gap from +0.049 to +0.196.
2. This is the fill-selection/adverse-selection mechanism (H-MAPENTRY1-NOTE6,
   H-PRICEDIN1) appearing in calibration space, measured cleanly for the
   first time: qualification itself is a ~15pt negative signal about p_map.
3. Amendment implication: a g(p_map) recalibration fit on ALL evaluations
   cannot fix the traded quantity — it would under-correct ~15pt exactly
   inside the trade gate. H-PCAL1's registry draft must pin the fit
   population as the QUALIFYING stream (thin: 399 in-band first-evals, wide
   clustered CIs) or model the correction conditional on the discount, e.g.
   g(p_map, side_ask) — a design decision for the user before registration.
4. The anchor guard (ANCHOR MISMATCH → exit 1, no artifact) did exactly what
   it was frozen to do. Keep it.

Repro: `uv run python scripts/pcal_fit.py fit` (refusal); qualifying-subset
one-liner in the ops session log 2026-09-01 (loads via pcal_fit's own
loaders, filters `qualifies is True`, same dedup + holdout split).
