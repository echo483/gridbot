# 2026-09-04 — model re-evaluation on the full corpus (through 09-04 09:30Z)

Outputs: `scripts/quant_eval.py` (32,337/32,388 resolved instances) and
`scripts/pmap_calibration.py` (38,843 rows, 725 resolved maps), both run
2026-09-04; H-MAPENTRY1 render from `verdict --holdout`. Row-pooled unless
stated (the 08-30 retraction stands: row-pooled skill overstates; only the
series-clustered verdict numbers are decision-grade). Nothing here changes a
gate or a registry bar.

## 1. Served round model (q_round), by cell

Pooled Brier 0.1236, skill vs base rate 0.0611, equal-count ECE 0.0212.

| cell | n | win | fair | Brier | ECE | entry | EV/ct | EV lo | Kelly | P(2x) |
|---|---|---|---|---|---|---|---|---|---|---|
| H-ECONBUY1:4-10k | 389 | 0.710 | 0.710 | 0.206 | 0.349 | 0.670 | +0.040 | −0.007 | 0.120 | 0.965 |
| H-ECONBUY1:>=10k | 2,889 | 0.842 | 0.849 | 0.134 | 0.249 | 0.780 | +0.061 | +0.048 | 0.279 | 1.000 |
| H-MANADV1 | 1,350 | 0.907 | 0.930 | 0.085 | 0.135 | 0.850 | +0.057 | +0.040 | 0.378 | 1.000 |
| H-MODELENTRY1:thr0.65 | 9,346 | 0.784 | 0.806 | 0.157 | 0.022 | 0.766 | +0.018 | +0.010 | 0.078 | 0.884 |
| H-MODELENTRY1:thr0.75 | 9,137 | 0.849 | 0.868 | 0.122 | 0.020 | 0.828 | +0.021 | +0.013 | 0.121 | 0.969 |
| H-MODELENTRY1:thr0.85 | 8,199 | 0.906 | 0.920 | 0.083 | 0.016 | 0.880 | +0.026 | +0.020 | 0.220 | 0.999 |
| H-PISTOLCARRY1 | 1,027 | 0.820 | 0.871 | 0.151 | 0.253 | 0.800 | +0.020 | −0.005 | 0.099 | 0.939 |

Reading: the round model's threshold cells are well calibrated (ECE ≤ 0.022)
and monotone; the event lanes carry large ECE because their fair values are
frozen anchors, not model outputs. Every EV column assumes an entry at
fair − 0.04 capped at the ceiling with a fill; `quoted = 0` on every cell —
no round book exists on the venue, so these are paper numbers.

## 2. p_map, the number the executor trades

| population | n | base rate | Brier | Brier base | skill | 10-bin ECE |
|---|---|---|---|---|---|---|
| all evaluations | 36,536 | 0.573 | 0.1926 | 0.2447 | 0.213 | 0.102 |
| qualifying rows | 6,185 | 0.654 | 0.2139 | 0.2262 | 0.054 | 0.165 |

Reliability on the qualifying subset (what the engine would trade):

| p_map bucket | n | predicted | realized | gap |
|---|---|---|---|---|
| 0.4–0.5 | 206 | 0.45 | 0.214 | −0.236 |
| 0.5–0.6 | 542 | 0.55 | 0.384 | −0.166 |
| 0.6–0.7 | 147 | 0.65 | 0.435 | −0.215 |
| 0.7–0.8 | 674 | 0.75 | 0.541 | −0.208 |
| 0.8–0.9 | 990 | 0.85 | 0.600 | −0.250 |
| 0.9–1.0 | 3,329 | 0.95 | 0.818 | −0.132 |

On all evaluations the model is UNDER-confident below 0.4 (+13 to +22pt) and
over-confident above 0.7 (−8 to −15pt); on the qualifying subset it is
over-confident in every bucket (−13 to −25pt). That is the 09-01 finding
again: the book disagreeing with p_map is itself evidence against p_map
(gap ≈ 0.9 × discount). Paper edge: +5.4pt per qualifying row, **+0.3pt per
deduped book** (431 entries, win 0.566 at mean posted 0.563) — the number
that matches the engine's dedup key is zero.

## 3. H-MAPENTRY1: in-sample vs the pre-registered holdout

| clause | in-sample (15,314) | holdout (40 series, 1,084) |
|---|---|---|
| (i) Brier model vs book mid | 0.1961 vs 0.1806 → BREACH | 0.1213 vs 0.1490 → ok |
| (ii) quintile ECE (bar 0.08) | 0.0916 → BREACH | 0.0535 → ok |
| (iii) settled edge (binds at 60) | +0.006, n=414 → BREACH | −0.059, n=39 → not binding |

The holdout series are calibrated where the in-sample corpus was not, and the
model beats the book there; the qualified entries still lose on paper. Two
readings that are both true: p_map is a usable forecaster on new series, and
the qualifying rule selects against it.

## 4. What a retrain would need

The served model (`data/models/roundwin-v1`) was trained 2026-08-25 on a 20%
earliest-series holdout (`train.py HOLDOUT_FRACTION`). `scripts/promote_model.py`
is fail-closed: candidate and served are scored on the served model's pinned
`holdout_series.json`; the swap and the registry amendment are human acts.
Expectation, not result: a candidate trained through 09-04 sees ~1.6× the
rows; the round model's ECE is already 0.02, so the gain, if any, is in the
map aggregation (the race model's p_fut prior), not the round model. A
retrain does not touch the qualifying-subset over-confidence, which is a
selection effect, not a fit effect.

## 5. Ranked recommendations for the next instrument build

1. Build EXEC1's fill bracket and calibration gate first: the deduped paper
   edge is +0.3pt, so only fill-conditional numbers can move the decision.
2. Score p_map against the book on the holdout by discount tercile (the
   09-01 cut, series-clustered): if gap ≈ 0.9 × discount holds out of sample,
   H-PCAL1 registers on that population only.
3. Leave the round model alone; retrain only after EXEC1 has a fill-model
   number to compare against, or the comparison has no baseline.

Not verified here: fill probability at any assumed entry price (quoted = 0 on
round cells; map books dead per NOTE9), and the series-clustered CIs for the
row-pooled tables above.
