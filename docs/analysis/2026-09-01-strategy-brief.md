# Strategy brief: which rail gets the next small-account nights

2026-09-01, ops session, pre-holdout data only (BOOKPIN1 discipline: 52
holdout series mapped, none played yet). Decision input for the user —
nothing here changes any gate or registry row by itself.

## The new measurement (this morning)

Qualifying rows (the traded population), first-eval dedup, resolved via the
frozen oracle, pre-holdout: p_map is overconfident in **every** decile
(+7..+23pt, n=2,013 / 115 series). And within the armed band, the gap
scales with the DISCOUNT (p_map − side_ask) almost 1:1:

| discount tercile | avg disc | n | model says | realized | gap |
|---|---|---|---|---|---|
| small | 0.078 | 147 | 0.631 | 0.551 | +0.080 |
| mid | 0.191 | 147 | 0.659 | 0.490 | +0.169 |
| large | 0.370 | 148 | 0.688 | 0.351 | **+0.337** |

gap ≈ 0.9 × discount in all three terciles. **The discount IS the
miscalibration**: when the book disagrees with p_map, the book is ~90%
right, in proportion to the disagreement. A discount-conditional
recalibration g(p_map, side_ask) would converge on ≈ the book price — the
model adds ~10% of the disagreement at best on this population.

## What this does to the three candidate rails

**Rail A — value vs frozen fair (H-MAPENTRY1's thesis): effectively dead.**
Consistent with everything else measured: fill-selection −6.5pt, priced-in
verdict, the crossin executable-price collapse (−3.1pt), and now this. At
most ~10% of the discount is real; after maker fill selection it prices
negative. The lane's holdout verdict (accruing tonight) renders formally.

**Rail B — latency/event: the survivor.** Our actual asset is the 13–27s
GRID lead minus the 8.5s pipe — being FIRST to the new fair, not smarter
than the book's settled price. Supporting threads: oriented post-quote
drift positive at +30/60s; the drift-gate's live strictness was the value
thesis failing, not the latency thesis; and the postshock WIP anomaly —
BOTH arms positive when following the model at stale prices right after
≥25pt shocks — is, if it survives reconciliation (fix-dispatch running),
direct rail-B evidence: model-momentum in the seconds after hard
information, both directions. That would be a new mechanism claim → new
pre-registered instrument (taker-priced, offset-capped), not a bolt-on.

**Rail C — calibration overlay: harm reduction only.** Given gap ≈
0.9×discount, p_cal ≈ trust-the-book; it can stop bad entries but cannot
create edge. Worth building as a guardrail after the population decision;
never the strategy.

## Recommendation for the next armed-small nights

1. Do NOT arm on rail A again; let H-MAPENTRY1's holdout verdict close it
   formally (starvation clauses run on their own).
2. Gate the next forward test on rail B: postshock reconciliation first;
   if the both-directions effect is real at taker prices inside +30s,
   pre-register the model-momentum instrument (mechanism claim: book lags
   hard information by more than our pipe latency) with UP/DOWN as twin
   candidate arms and dry-run it before any arming.
3. H-PCAL1: register only with the discount-conditional population (this
   brief is the evidence Row 2's broad-population draft was wrong).

Repro: the decile/tercile tables come from pcal_fit's own loaders filtered
to qualifies==true (ops session 2026-09-01; one-liner in session log).
