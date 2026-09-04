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
contract, the same sign as the 13 real fills (3W-10L). This recall is a property of the
real-resting-order regime only.

## 2. Held-out replay (hypothetical orders; series first-mapped at/after 2026-08-31T19:53:46Z)

PROVISIONAL: 130 deduplicated qualifying entries, 45 series, all in the unsized
regime (three post-quote samples per order at +30/+60/+120s; tape sizes exist only
from 2026-09-04 18:36Z). Every fill credits one contract on both edges (registry
EXEC1: "exactly 1 contract when unsized"), so the numbers below are per-contract,
unweighted. Re-render on the sized 2s tape before treating any magnitude as settled.

| edge | what it is | fills | fill rate | mean PnL/contract | series-bootstrap CI95 | leave-one-series-out | series |
|---|---|---|---|---|---|---|---|
| lower (touch-only, strict + persist) | the PnL read | 48 (45 resolved) | 37% | −0.167 | [−0.282, −0.070] | [−0.190, −0.150], all negative; top-3 series carry 34% of the loss; 23/31 series net negative | 31 |
| upper (touch + market-referenced consumption) | a FILL-RATE bound only | 78 (74 resolved) | 60% | −0.142 | [−0.203, −0.088] | [−0.154, −0.130] | 36 |

How to read it (after the deepseek-pro review appended below):

- The lower edge is the only PnL read. Its fills are ask-touches — a seller posted
  at or through our price — which is exact under both regimes.
- The upper edge's market-referenced consumption channel cannot tell a sale through
  the level we would front from a cancel of that level (AMEND1-NOTE1), so it bounds
  the fill RATE and is not a PnL number. That its sign agrees is corroboration, not
  evidence.
- The calibration recall (section 1) validates the channel on REAL resting orders
  only; the hypothetical-regime detector's accuracy is unknown until the live-small
  run or a trades tape. The fixture and the held-out population are different fill
  regimes by construction, which is what H-LIVESMALL1 is for.
- The 37% and 60% fill rates are bounds by construction; that the real 45% sits
  between them is consistency, not validation.
- Undetectable orders (someone stepped in front of our price: 25 of 130) are dropped
  from the fill denominator. Those are the front-run cases, which are the more
  adverse fills; the visible PnL may therefore understate the selection cost.
- The fill mark is up to 30s stale on three samples; the sized tape (2s) fixes both
  the persistence check and the mark timing.

## 3. What this says about "run small on live"

- On the touch-only edge the entry rule's fills settle at −0.167 per contract on
  held-out data, whole interval below zero, sign stable under leave-one-series-out.
  That agrees in SIGN with the 13 real fills (3W-10L) and NOTE6's fill-conditional
  read; no size comparison is claimed across those units.
- H-LIVESMALL1's purpose is to calibrate the fill bracket in the hypothetical
  regime. That is a real gap (the review's first finding), but it does not change
  the direction: every independent line — real fills, armed nights, the touch-only
  held-out edge — points the same way.
- Recommendation: do not arm H-MAPENTRY1's entry rule. Keep EXEC1 as the
  qualifying instrument; re-render on the sized tape from tonight; the qualifying
  question is now "why does the entry rule select losers" (the 09-01 finding, gap ≈
  0.9 × discount, names the mechanism).

Reproduce: `uv run python -m gridbot.analysis.exec_replay_cli --calibrate` and
`--heldout`; JSON under `data/analysis/exec_replay-*-20260904.json`.

---

## Adversarial review (deepseek-v4-pro text lane, 2026-09-04 20:35Z) — folded in above

Review complete. I read the report and NOTE1 as given, plus the maker-entry playbook and edge-validation skill for the adverse-selection/regime conventions. No repo/code/tape access assumed (per the no-repo rail) — I reviewed the report text on its own terms. Findings ranked by severity.

==================================================
ADVERSARIAL METHOD REVIEW — H-MAPENTRY1-EXEC1 REFERENCE read
==================================================

FINDING 1 — BLOCKER — The held-out fill detector is unvalidated; the calibration's accuracy does not transfer.
Flaw: the 0.86 recall / 1.0 precision (and 16s MAE) are measured on real resting orders, where "own_bid < c → taken" is exact because our order WAS the best bid. The held-out population switches to reference = min(c, market bid) — a structurally different detector with a cancel false-positive mode the calibration never saw. None of the calibration validation transfers; the held-out detection accuracy is unknown, not 0.86.
Failure case: a hypothetical bid at 0.62 (one tick under a 0.63 ask) with the market bid at 0.60. On the fixture the analogous trigger was "best bid dropped below our cost c" = provably taken. On held-out the trigger is "reference level gone," which fires on a maker pull with no trade. The report's own table presents this as a measured fill model; it is not measured.
Fix: stop citing calibration recall/precision as evidence about held-out detection. State the held-out detection accuracy as UNKNOWN pending H-LIVESMALL1 or the 2s tape. This is the gap the registry itself names as the purpose of H-LIVESMALL1.

FINDING 2 — BLOCKER — Market-referenced consumption conflates fills with cancels; the upper-edge PnL is not a clean PnL read.
Flaw: NOTE1 pins that a BBO tape cannot tell "sale through the reference level" from "cancel." A cancel is adversely selected on the bid side (makers pull bids into a falling tape), so cancel-induced phantom "fills" bias the upper-edge PnL downward for the same reason real fills do — but by a different, unmodeled mechanism. The upper-edge −0.142/−0.151 therefore mixes fill-selection with cancel-selection, and its magnitude is not trustworthy even though its sign happens to agree with the lower edge.
Failure case: 09-02, hypothetical bid 0.62 / market bid 0.60. At +30s the market maker pulls the 0.60 bid (no trade) and the ask ticks to 0.63→0.61. consumption_ref fires ("own_bid < reference") → recorded as a fill at 0.60. At +120s mark 0.59 → −0.01 PnL. Ten such cancels across a down-drifting series drag the upper-edge mean below zero with zero real fills behind them.
Fix: present the upper edge as a fill-rate UPPER BOUND only, never as a PnL read. Carry the PnL conclusion on the touch-only lower edge. Settle cancel-vs-trade with the 2s tape (trade prints are distinguishable) once sized data covers the population.

FINDING 3 — MAJOR — Three samples at +30/+60/+120s are too coarse for both fill detection and PnL timing.
Flaw: with only three post-quote samples the consumption channel misses fills that fill-and-repost inside a 30s gap (false negatives), and the lower edge's "strict + persist" cannot be confirmed on three points (the report concedes this is regime-limited). The PnL mark is pinned to the first sample where the reference disappeared — up to 30s stale, in a 2-minute esports market.
Failure case: hypothetical bid 0.55 filled by a taker at +31s, re-posted by a new maker at +45s; the +60s sample sees 0.55 still bid → no consumption, no persistence → fill MISSED. A fill at +35s is only marked at +60s (25s stale); across a map-2 break the price moved ~8pt, so the "fill PnL" is a lagged mark, not a fill-time PnL.
Fix: re-render the held-out once the 2s sized tape covers the population (from 2026-09-04 18:36Z). Label the current 3-sample read provisional and put this caveat in the headline, not a footnote.

FINDING 4 — MAJOR — Quantity weighting is arbitrary and asymmetric: touch fills credit the full 25-contract clip, consumption fills credit one.
Flaw: in the unsized regime there is no size information, so the "qty-weighted −0.151" is a 25:1 blend dominated by touch fills. It is not an expected-PnL; it is a guess at a weighting that happens to be 25x asymmetric between two channels whose per-contract PnL may differ.
Failure case: touch-detected fills settle −0.10/contract, consumption-detected −0.30/contract. Unweighted = −0.20. The "qty-weighted" mash (25·−0.10 + 1·−0.30)/26 ≈ −0.11 — the reported −0.151 hides the channel that actually fills worse.
Fix: report only per-contract unweighted PnL as the REFERENCE read. Drop or clearly de-scope the qty-weighted number until the sized tape supplies real fill quantities; do not let it sit beside the unweighted figure as an equally valid "PnL."

FINDING 5 — MAJOR — Series-bootstrap CI rests on 31–36 clusters; "whole interval below zero" is thin and possibly single-series-driven.
Flaw: 130 entries collapse to 31 (lower) / 36 (upper) series for the bootstrap. A few high-loss series can dominate the interval, and the report gives no leave-one-out or per-series loss distribution to show the sign is not carried by 2–3 clusters.
Failure case: 3 series (a 0-13 blowout, a map-3 collapse, a map-5 choke) contribute 14 of the 48 lower-edge fills and the bulk of −0.167. Dropping the worst series shifts the CI upper edge from −0.070 toward −0.04, approaching zero. The "whole interval below zero" claim then rests on ~3 clusters.
Fix: report a leave-one-series-out (jackknife) and the per-series fill/loss histogram; confirm the sign survives removing the top-loss series before asserting the interval is below zero.

FINDING 6 — MAJOR — The "undetectable bucket" (someone-above-us) biases fill-conditional PnL by survivorship.
Flaw: detected fills are exactly the fills where we were NOT front-run. Fills we'd have missed (a better bid stepped in front and got hit) are systematically the more-adverse events. On the fixture 3 of 7 upper-edge fills were "behind a higher bid" and invisible. So fill-conditional PnL is conditioned on being at the front of the book — a selected state — and the negative number may understate true adverse selection because the worst fills are the ones you can't see.
Failure case: competitor posts 0.63 against our 0.62 and a taker hits it; our hypothetical fill is undetectable. That front-run event is precisely the informed-adversary signal the maker-entry playbook flags as the adverse-selection signature, and it is dropped from the denominator.
Fix: bound the effect; the 2s tape can count trades that printed through a better bid while our level sat behind. Report that count as a lower-bound on unseen (more-adverse) fills.

FINDING 7 — MINOR — "Same sign and size" as the armed nights is not supported across the units actually stated.
Flaw: the report claims held-out (−14 to −17 points per filled contract) is "the same sign and size" as the armed nights (−61pt per trade on 13 fills). Sign is consistent; size is asserted across an unstated per-contract normalization. Under the obvious readings the magnitudes differ by ~4x (1 contract/trade) or ~6x (25-contract clip/trade), in opposite directions.
Fix: state the armed-night per-contract PnL (or the average contracts per fill) to substantiate "size." Otherwise claim sign-consistency only.

FINDING 8 — MINOR — "The two fill rates bracket the real 45%" is partly by construction, not independent validation.
Flaw: 37% is a lower bound by construction (touch-only + persistence unverifiable on 3 samples → undercount), 60% an upper bound (cancel false-positives → overcount). A lower and an upper bound bracketing the true value is what bounds do; it carries no independent validation weight for the detector.
Fix: present the bounds as bounds, not as a bracketing "check" that implies calibration quality.

==================================================
VERDICT (five lines)
==================================================
"do not arm" is supported by the numbers on sign, but it is the REAL-fill evidence that carries it — the 13 armed-night fills (3W-10L, −0.19/−0.25 per contract) and three nights at −61pt/trade — not the held-out REFERENCE read, which is regime-confounded and only directionally corroborating.
The held-out upper edge (−0.142/−0.151) is not a trustworthy PnL number: the market-referenced consumption channel is cancel-contaminated by the project's own NOTE1, and its detection accuracy is unvalidated against the hypothetical regime.
The cleanest held-out read is the touch-only lower edge (−0.167, CI [−0.282, −0.070] on 31 series), which is negative and consistent with the known maker-entry adverse-selection signature (−0.13 to −0.20 per fill), but thin and unverifiable for persistence on three samples.
The recommendation does not over-reach on the DIRECTION — every independent line (real fills, armed nights, lower edge) points the same way, and "why does the entry rule select losers" is the right next question.
It does over-claim on PRECISION: the −14 to −17pt magnitude, the "same size" comparison, and the qty-weighted figure are not settled at the stated resolution — hold those pending the 2s-tape re-render from tonight, and do not let the upper edge into the headline PnL.

==================================================
FILES CHANGED: none (analysis-only review; no repo/code edits).
COMMANDS RUN: none (self-contained report review; no executable verification path in scope).
COULD NOT VERIFY: the exec_fills.FillRule / exec_replay.replay_orders implementation, the raw JSON under data/analysis/exec_replay-*; the 2s sized tape (referenced but not in the report); the actual per-series fill/loss distribution and bootstrap rep count behind the CIs. All findings are derived from the report text and NOTE1 alone.
