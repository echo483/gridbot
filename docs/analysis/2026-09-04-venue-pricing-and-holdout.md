# 2026-09-04 — "H-MAPENTRY1 is falsified, move to the best strat": what the data says

Ops session, on the user's question ("hmapentry is falsified right? so move to
the best strat and try it"). Two measurements, one tape defect, one render fix.
Registry: H-MAPENTRY1-HOLDOUT1, H-MAPENTRY1-NOTE9. Nothing armed; nothing new
registered as a strategy.

## 1. H-MAPENTRY1 on its pre-registered holdout: NOT falsified (clause iii under-powered)

`uv run python -m gridbot.instruments.verdict --holdout` now renders the
BOOKPIN1 verdict on the exact held-out population (the next 40 series whose
first GRID↔PMUS mapping row is at/after 2026-08-31T19:53:46Z; membership by
first-mapping timestamp, not row time). The previous render path only counted
holdout instances while printing in-sample clauses.

| clause | in-sample (15,314 inst.) | holdout (40 series, 1,084 inst.) |
|---|---|---|
| (i) Brier model vs book mid | 0.1961 vs 0.1806, diff +0.0155 CI (−0.008, +0.039) → BREACH | 0.1213 vs 0.1490, diff −0.0277 CI (−0.067, +0.009) → ok |
| (ii) quintile ECE (bar 0.08) | 0.0916 CI (0.062, 0.124) → BREACH | 0.0535 CI (0.036, 0.097) → ok |
| (iii) settled edge, deduped (binds at ≥60) | +0.006 CI (−0.019, +0.032), n=414 → BREACH | −0.059 CI (−0.132, +0.003), n=39 → does not bind |
| starvation (bar <0.05) | 0.166 ok | 0.138 ok |

Reading: on new series the model is calibrated and beats the book mid as a
forecaster, but the entries it qualifies still show a negative paper edge at
n=39. Clause (iii) cannot power up inside the frozen holdout (all 274 held-out
qualifying rows are resolved; one map pending), so a clause-(iii) verdict needs
a pre-registered amendment extending the holdout for that clause only. That is
the user's call (DECISIONS D2). The lane is not closed by its own bar, and the
in-sample FALSIFIED reading does not transfer.

Caveat that stays: NOTE6 (fill selection) and NOTE7 (short mispricing on the
armed nights) mean even a positive clause (iii) is a paper number until an
execution-realistic instrument exists (NOTE8 rule).

## 2. Venue pricing cut: no taker latency payload on Polymarket US

`scripts/venue_pricing_cut.py 20260903 --book map|match --spreads`
(389 book-joined round ends, 11 series, 6h tape; series-clustered CIs).

Spreads over the whole tape:

| book | rows | dead/one-sided | spread p50 | p75 | p90 | rows ≤2 ticks |
|---|---|---|---|---|---|---|
| map | 2,140 | 22% | 0.08 | 0.17 | 0.64 | 9% |
| match | 2,677 | 5% | 0.01 | 0.02 | 0.04 | 78% |

Taker at the stale quote (buy the round winner's side at the last quote at/before
our GRID observation, mark to that side's mid at +120s), by round leverage:

| swing of P(map) | n | map book | match book |
|---|---|---|---|
| <0.05 | 63 | −0.151 [−0.302, −0.047] | −0.013 [−0.025, −0.005] |
| 0.05–0.10 | 42 | −0.139 [−0.285, −0.011] | −0.004 [−0.013, +0.009] |
| 0.10–0.20 | 208 | −0.071 [−0.136, −0.027] | −0.007 [−0.017, +0.003] |
| ≥0.20 | 76 | −0.100 [−0.198, −0.037] | −0.014 [−0.033, −0.000] |

Oriented mid moves after a round are +0.001…+0.015 (map) and −0.004…+0.004
(match) at +30s, CIs straddling zero. Neither book prices the round; the match
book moves about a third of a tick per round and the map book's moves are
8–14-tick-wide flickers at ~52% direction agreement. The map end is not a shock
either: the match book pre-prices a 12–5 map at ~0.70 before the last round.

Conclusion: the 13–27s lead has no taker payload on this venue. Crossing the
spread to act on a round costs more than the round is worth. The only use of
the lead left open by the data is the maker side (skew or pull resting
match-book quotes before the public reprices), which needs a trades tape to
evaluate (the SDK's `subscribe_trades` is an authenticated WebSocket; the BBO
recorder has none). That is gridbot-lab research-layer work.

## 3. Tape defect fixed: the recorder went blind at every map end

The BBO recorder dropped a series the moment its map finished (`current_game`
is None between maps), so no map-deciding round and no map end was ever covered
(e.g. `aec-cs2-mgc-big-2026-09-03`: last row 29s before the map end, next row
at map-2 start, +975s). Since 2026-09-04T02:13Z it keeps the last finished
map's book and the match book on tape between maps and for 600s after the
series ends (`FINISHED_GRACE_S`). Covered map ends accrue from tonight; the
first read of them is the timing chain's next amendment.

## What this means for "the best strat"

- Not a taker strategy on PMUS: measured negative at every horizon and leverage.
- Not H-MAPENTRY1 as armed before: paper edge negative on held-out entries and
  fills are adversely selected (NOTE6).
- Open: (a) extend the holdout for clause (iii) and let the execution-realistic
  instrument (D2b) decide MAPENTRY1; (b) maker-side latency on the match book,
  which needs a trades tape first. Both are registry/design decisions, not
  arming decisions.
