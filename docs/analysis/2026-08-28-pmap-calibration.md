# Is the number we trade calibrated? p_map vs GRID's own map winners

**2026-08-28.** `scripts/pmap_calibration.py`, output
`data/analysis/pmap_calibration-20260828.json`. Ground truth is
`scripts/grid_winners.py` (first-observation oracle, MR12 terminal-score
validated, ambiguous finals excluded): **277 resolved maps**, 11,692
mapentry evaluations, **0 rows excluded for an unjoinable team name**.

## Why this was missing

`quant_eval` measures **q_round** — ECE 0.014, Brier skill +0.069 on ~5k
instances. The executor does not trade q_round. It trades **p_map**, the
frozen race aggregation, and prices every order at `p_map − 4pt`. A
well-calibrated round model can aggregate into a badly-calibrated map
number and nothing we ran would have seen it.

## Result: the model has real skill, and is systematically overconfident

| population | n | Brier | base-rate Brier | skill | ECE |
|---|---|---|---|---|---|
| all evaluations | 11,016 | 0.1920 | 0.2394 | **+0.198** | 0.106 |
| qualifying (would-trade) | 1,489 | 0.1969 | 0.2165 | +0.090 | 0.140 |

Reliability on the qualifying rows — every bucket we actually trade sits
**below** its prediction:

| p_map bucket | n | predicted | realized | gap |
|---|---|---|---|---|
| 0.6–0.7 | 26 | 0.650 | 0.346 | **−0.304** |
| 0.7–0.8 | 171 | 0.750 | 0.602 | −0.148 |
| 0.8–0.9 | 239 | 0.850 | 0.669 | −0.181 |
| 0.9–1.0 | 813 | 0.950 | 0.828 | −0.122 |

**The 4pt entry margin does not cover a 12–18pt overconfidence.** That is
the headline. `entry_price_max = p_map − 0.04` was sized as a safety
margin against a number that is 12–18pt optimistic exactly where 70% of
our qualifying volume sits.

## But the posted price saves it — on paper

The margin is not what protects us; `side_ask − 1 tick` is. The posted
price binds far below the cap, so paper EV stays positive:

| population | n | win rate | mean posted price | EV / contract |
|---|---|---|---|---|
| qualifying rows | 1,489 | 0.683 | 0.570 | +$0.113 |
| **deduped per map book** | **117** | 0.598 | 0.570 | **+$0.028** |

The deduped row is the honest one — the engine enters each
(series, map, side) once, so the 1,489-row figure counts the same book
once per re-qualifying round and overstates both n and the edge by ~13×.

**Real paper edge: +2.8pt per contract on 117 opportunities over 3 days.**

## What this says about the −$7.99

It says the loss is not evidence of anything yet.

- Paper edge deduped: **+2.8pt/contract**. Realized: **−61pt/trade** over
  13 settled trades.
- Both independent quant lanes (deepseek-v4-flash and the new
  finance-tuned ling-3.0-flash-fin, run blind on the same prompt) put the
  sample size needed to separate a +2pt edge from zero at 95% at
  **~1,500 settled trades**, from σ = √(p(1−p)) ≈ 0.39–0.41 per contract.
  13 trades is **0.9%** of that. −$7.99 is noise at this n.
- The gap between +2.8pt paper and −61pt realized is **fill selection**,
  not signal: a resting maker order fills only when flow crosses into it,
  which is disproportionately when the market is moving against the side
  we took. This is the same mechanism the 20.5% exit adverse-selection
  number measures from the other end.

## Two candidate amendments — NEITHER applied, both need pre-registration

1. **Calibration shrink.** Trade `p_cal = calibrated(p_map)` fitted on
   this data (isotonic or a single Platt logit), not raw `p_map`. Keeps
   the margin at 4pt but makes 4pt mean what it was supposed to mean.
   Needs a held-out split — fitting and verdicting on these 277 maps is
   the classic way to manufacture a fake edge.
2. **Widen ENTRY_MARGIN** from 4pt to cover the measured bias in the
   bucket being traded. Cruder, no fitting, no overfit risk, and it
   directly shrinks the qualifying population — which is also the
   starvation problem, so it trades one known failure for another.

Both change what H-MAPENTRY1 tests. Neither ships without a frozen
hypothesis and a held-out window.

## What to measure next (cheap, no amendment needed)

Paper edge assumes a fill at the posted price. The one number that would
actually explain the PnL is **edge conditional on filling**: replay the
captured books, mark each resting order filled only where later flow
crossed its price, and recompute EV on that subset. If it goes negative
while unconditional stays positive, fill selection is proven and the fix
is an execution change, not a model change.
