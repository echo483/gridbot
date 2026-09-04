# 2026-09-04 — H-MAPENTRY1-EXEC1: first calibration and held-out renders (REFERENCE)

Instrument: `uv run python -m gridbot.analysis.exec_replay_cli --calibrate | --heldout`.
Registry: H-MAPENTRY1-EXEC1 (thresholds PENDING USER CONFIRMATION), -AMEND1 (level-
consumption fill channel), -AMEND1-NOTE1 (counterfactual reference). No verdict is
rendered; `--terminal` is refused. Everything below is a REFERENCE read.

## 1. Calibration on the 48 venue-verified armed-night orders (08-26/27)

Fixture reconciles exactly with the venue: 48 placed, 28 rested, 13 filled, 20 rejected
(the NOTE7 short mispricing plus five longs). Observations: the post-quote samples at
+30/+60/+120s (three per order, no sizes; four rested orders have none in-window).

| bracket | lower tp/fp/fn | upper tp/fp/fn | upper recall | recall on detectable | fill-time MAE |
|---|---|---|---|---|---|
| v1 touch only | 2/0/11 | 3/0/10 | 0.23 | — | 11s |
| v2 + consumption (AMEND1) | 3/0/10 | 6/0/7 | 0.46 | 6/7 = 0.86 | 16s |

Reading: the ask-touch proxy is blind to the dominant fill channel (a taker hitting our
resting bid; review finding #5). With the consumption channel the upper edge sees six of
the seven fills it could see (four fills fell in unobserved windows, three behind a
higher bid). Precision is 1.0 on both edges. The lower edge cannot confirm persistence
on three samples; the 2s tape can, so the fixture's lower-edge recall is regime-limited
(AMEND1 coverage rule). Detected fills on the fixture settle at −0.19 to −0.25 per
contract, consistent with the 13 real fills (3W-10L).

## 2. Held-out replay (hypothetical orders; series first-mapped at/after 2026-08-31T19:53:46Z)

130 deduplicated qualifying entries, 45 series, all in the unsized regime (post-quote
samples; tape sizes exist only from 2026-09-04 18:36Z).

| edge | fills | fill rate | mean PnL/contract | series-bootstrap CI95 | series |
|---|---|---|---|---|---|
| lower (touch, strict + persist) | 48 (45 resolved) | 37% | −0.167 | [−0.282, −0.070] | 31 |
| upper (touch + market consumption) | 78 (74 resolved) | 60% | −0.142 (unweighted) / −0.151 (qty-weighted) | [−0.203, −0.088] | 36 |

The two fill rates bracket the real 45% fill-at-rested rate. Under BOTH edges the
fill-conditional settled PnL is negative with the whole interval below zero. A first
render at 19:47Z that credited 89% fills is void (AMEND1-NOTE1: the cost-referenced
consumption test fires by construction on a hypothetical order that improves the bid).

## 3. What this says about "run small on live"

- The execution-realistic number NOTE8 asked for exists now, and it is −14 to −17
  points per filled contract on held-out data. That is the same sign and size as the
  three armed nights (−61pt per trade on 13 fills) and NOTE6's fill-conditional read.
- H-LIVESMALL1's purpose is to calibrate the fill bracket, and the bracket's upper edge
  already reproduces real fills at 0.86 recall with zero false fills. A live-small run
  of THIS entry rule would spend money to re-measure a fill model on a strategy the
  model says loses at fills.
- Recommendation: do not arm H-MAPENTRY1. Keep EXEC1 as the qualifying instrument for
  whatever entry rule comes next; the tape (sized, 2s) makes its lower edge computable
  from tonight. The qualifying question moves from "is the model right" (it is, on the
  holdout) to "why does the entry rule select losers" — the 09-01 finding (gap ≈ 0.9 ×
  discount) already names the mechanism: the book disagreeing with p_map is evidence.

Reproduce: `uv run python -m gridbot.analysis.exec_replay_cli --calibrate` and
`--heldout`; JSON under `data/analysis/exec_replay-*-20260904.json`.
