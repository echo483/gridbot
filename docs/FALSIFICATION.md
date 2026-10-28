# Falsification ledger

What this project tried, the test that decided it, and what survived. Each
row links to the evidence document. The instrument that produced it runs from
tag **`evidence-2026-10`** (`git checkout evidence-2026-10 -- scripts/<name>.py`)
if it is no longer on `main`. Hypothesis rows and their frozen bars are in
`docs/experiments/registry.jsonl`.

The pattern worth keeping: **most of these died when the measurement got more
honest, not when the data changed.** Clustering by series, pricing at the
executable side, and requiring depth are the three corrections that killed the
most ideas.

| # | claim | the test that decided it | outcome | evidence |
|---|---|---|---|---|
| 1 | The map-win forecast `p_map` has **+0.198 skill** in the armed band | Same data, resampled by **series** (80 series) instead of treating 2,668 correlated qualifying rows as independent | **Retracted.** Skill −0.0007, CI (−0.164, +0.139). In-band `p_map` over-forecasts by 21 pts, CI excludes zero | `analysis/2026-08-30-pmap-slate-variance.md` |
| 2 | The 21-pt over-forecast is a property of `p_map` | Same oracle/dedup on ALL evaluations vs the qualifying subset | **Refined.** All evaluations: +0.049 gap (n=7,677). Qualifying only: +0.196 (n=399, 87 series). The bias is created by *selection into the trade*, not by the forecast | `analysis/2026-08-31-pcal-population-finding.md` |
| 3 | A large discount to fair value is edge | Fill outcomes by discount bucket + oriented post-quote drift (n≈7,900) | **Dead (H-PRICEDIN1).** Fills win 0.20–0.67 against corpus anchors of 0.83–0.85; drift is +0.5–1 pt per 2 min, an order of magnitude short of a 20-pt discount | `analysis/2026-08-28-priced-in-test.md` |
| 4 | The entry signal's paper edge (+2.8 pt/contract) is realisable | Split opportunities by whether they would have filled | **Fill selection, not signal.** Fills: win 0.553 / EV −3.5 pt. Non-fills: 0.672 / +7.5 pt, at the same price. Direction only; every CI spans zero | `analysis/2026-08-28-pmap-calibration.md`, registry NOTE5/NOTE6 |
| 5 | Band-skipped books that cross into the band are an entry (Queue 16 → H-CROSSIN1) | Reprice from the +120 s **mid** to the **executable ask at first cross**, plus persistence | **Dead.** Mid-priced headline: edge +0.159. Executable: −0.047, CI (−0.146, +0.048) on the candidate arm; 157 entries (both arms) survive from 3,439 skip events | `analysis/2026-08-31-queue16-bandskip.md`, `…-queue16-phase2.md`, `…-crossin-design.md` |
| 6 | Post-shock books lag the model, so fade (or follow) them | Funnel reconciled to the spec, then split by whether the book ever repriced and had depth | **Dead-book artifact.** The whole in-sample edge sat in books that never repriced and carried no depth (86–92% of rows); live books ran −2.6 pt (DOWN) / −33 pt (UP). Fade and momentum both closed | `analysis/2026-09-01-postshock-anchor-trace.md` |
| 7 | Model-momentum within a round (H-MODELMOMENTUM1, never registered) | Timing chain: does the book move toward `p_map` inside a round? | **No.** Moves oriented on the model: +0.003 at +60 s, −0.003 by the next round, every CI straddles zero, share positive 0.45–0.54. Separately, the book drifts the same ~1.6 ticks after either team wins a round: it tracks map state, not rounds | `analysis/2026-09-03-timing-chain-first-read.md` |
| 8 | A better map aggregation beats the frozen one (H-MAPENTRY2) | Pre-registered scoring of candidates A/B0/B10/B20 at n ≥ 60 resolved maps, quintile ECE | **No challenger wins** (n=61) — the frozen aggregation stays | registry `H-MAPENTRY2` verdict row |
| 9 | Book-flow momentum predicts the short-horizon move | Correlation / AUC against the 120 s oriented move | **Weak but real:** corr ≈ +0.06, AUC 0.58. Licensed as a possible model input, never as an entry gate | `analysis/2026-08-28-flow-features-study.md` |
| 10 | There is a taker latency payload on Polymarket US | Venue pricing cut across map and match books | **No.** Map books are dead (8-tick spreads); match books are 1-tick but move ~1/3 tick per round | `analysis/2026-09-04-venue-pricing-and-holdout.md`, registry NOTE9 |
| 11 | Shorts were filling at the expected price | Venue order records vs the ledger | **Wire bug, not market behaviour.** The venue `price` is always the Yes price; every short was sent at our cost. 20 of 48 venue-verified orders never rested. Fixed; the timing profile is re-cut on rested orders (13/28 = 0.46 fill rate) | `analysis/2026-09-03-integrity-pass.md`, registry NOTE7 |
| 12 | "ECE (quintile bins)" is one number | Recompute every implementation on identical fixtures | **Three incompatible conventions** (equal-count + mean, equal-width + midpoint, others). The midpoint version understates error by more than the 0.08 bar. Pinned in `tests/test_scorecard_consolidation.py` | `docs/WORKBLOCKS.md` § Kernel findings |

## What survived

- **The information lead.** GRID reports round outcomes before Polymarket US
  books reprice: p50 +13.1 s (lower bound; upper bound +27.5 s) on 293 book
  moves over 682 round ends. **It is a timing fact, not yet an edge.** The
  first move agrees with the round winner only 55% of the time, and a round
  end is worth about half a tick. The book prices map state over minutes,
  not rounds. Turning the lead into money is the open problem.
- **The round model.** Calibrated live: Brier 0.1105 (skill +0.063), ECE 0.031
  over 1,984 resolved shadow forecasts.
- **H-MAPENTRY1 on its holdout.** Not falsified: the model beats the book mid
  as a forecaster and is calibrated. Clause (iii) is under-powered (39/60) and
  its paper edge is negative, so it is not a PASS.
- **The wire path.** Place 394 ms, cancel 137 ms on a real order.

## What the dead lanes left behind

Reusable machinery, now part of the system rather than any single lane: the
series-clustered bootstrap and the single ECE convention (`gridbot.scorecard`),
the depth-limited fill gate as the rule for any backtest number, the
first-observation map-winner oracle (`scripts/grid_winners.py`), the rested-only
fill statistics (`scripts/timing_profile.py`), and the timing chain itself.
