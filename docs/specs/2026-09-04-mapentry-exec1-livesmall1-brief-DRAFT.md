# H-MAPENTRY1-EXEC1 + H-LIVESMALL1 — pre-registration brief (DRAFT)

Provenance: drafted 2026-09-04 03:20Z by the gpt-5.6-luna text lane from pasted evidence
(NOTE6/7/8/9, HOLDOUT1, the rested re-cut, the 6h BBO tape facts); adversarial method review by
deepseek-v4-pro appended below when it lands; Claude ops-session notes at the end. STATUS: DRAFT —
the registry rows are written from this after the user confirms the thresholds; the instrument may
be built and CALIBRATED (08-27 fixture) before that, but no held-out verdict population is read
until the rows are registered.

---
# Pre-registration brief: H-MAPENTRY1-EXEC1 and H-LIVESMALL1

Status: decision artifact for human sign-off. This document does not arm live trading and does not change `dry_run=True`.

## Part 1 — H-MAPENTRY1-EXEC1

### Frozen population and unit

Use only the held-out population: series whose first map mapping is at or after `2026-08-31T19:53:46Z`, appended in first-mapping order. The unit is one deduplicated `(map book, side)` qualifying entry. A book/side may produce at most one replay order. Do not use in-sample rows or the 08-27 rows in the verdict sample; 08-27 is a calibration fixture only.

The replay uses the corrected venue convention: venue price is always Yes price; a BUY_LONG costs `P`; a BUY_SHORT costs `1-P`. The posted price is the frozen H-MAPENTRY1 price in the mapentry row. Clip is irrelevant to per-contract edge, but the replay caps executable contracts at the recorded top size and the strategy clip ceiling.

### Fill bracket

Replay each order against the postquote rows and, after 2026-09-03, the change-only/30-second-heartbeat BBO tape. The TTL is 150 seconds from detection. An order is eligible only after a successful REST acknowledgement; rejected, ambiguous, and unacknowledged creates are not fills.

* **LOWER bound — strict adverse-touch, queue-behind-displayed-size.** Count a fill only when the opposing quote moves strictly through the posted price during the TTL (`ask < P` for a buy at `P`), with a subsequent observation confirming that the quote remains at or through `P`. Require available top-of-book size at the confirming observation. Cap fills at that displayed size and assume our order is behind all displayed size plus hidden queue; no fill is credited unless the observed move supplies at least one contract beyond that queue. With BBO-only data, the displayed size is the entire measurable queue and the unmeasured queue is treated as ahead of us. This is intentionally conservative.

* **UPPER bound — any-touch, front-of-displayed-queue.** Count a fill when the opposing quote is at or through the posted price at any recorded postquote/tape observation during the TTL (`ask <= P` for a buy at `P`), without requiring persistence. Assume our order is first in the displayed queue. Cap filled contracts at the top size observed at the touch and at the strategy clip/remaining order. Never infer more than one fill event per order, and never fill an order that was rejected or ambiguous.

For both bounds, a quote observation is valid only if it is inside the order's 150-second lifetime. Quote-lifetime is measured from the last observation at which the order could have been executable to the next observation: a lower-bound fill needs the confirming observation; an upper-bound fill is charged to the first touch. No trade print is imputed.

Calibrate the two bounds on the 48 venue-verified orders, using the order's actual REST/rejection status and lifetime. The 08-27 fixture must reproduce: rejection reason class for every order, rested-order count within 2 orders, and conditional fill rate within 15 percentage points of the observed 45%. Median filled time must be within 20 seconds when at least five fills are present. Failure of any calibration check blocks the verdict and requires a frozen amendment before accrual.

### Accounting and verdict

For every filled contract, record intended posted price, actual venue-side cost, fill timestamp, and settlement. At resolution, LONG PnL is `settlement - cost`; SHORT PnL is `(1 - settlement) - cost`, where `cost = 1 - posted_price` for SHORT. Include maker fees, rebates, and explicitly recorded rejection/cancel fees if any; absent a documented fee, use zero and record that assumption. Unfilled contracts contribute zero, not mark-to-market value. Report contracts, filled entries, gross PnL, fees, net PnL, and net PnL per filled contract in probability points.

Use a series-clustered bootstrap with 10,000 resamples of series, not rows. The primary statistic is mean net PnL per filled contract with a two-sided 95% bootstrap interval. Minimum sample for any verdict is 60 deduplicated filled entries under the relevant bound.

* **PASS:** under the LOWER bound, at least 60 filled entries and the 95% interval's lower endpoint is strictly greater than `+0.02` net probability points per contract.
* **FAIL:** under the UPPER bound, at least 60 filled entries and the 95% interval's upper endpoint is strictly less than `0.00`.
* **Between:** continue accruing in first-mapping order. Stop accrual at the earlier of 120 additional held-out series after the first qualifying series or 90 calendar days after first accrual. If neither PASS nor FAIL is met then, verdict is INCONCLUSIVE; do not arm and do not relabel it PASS.

### Reviewer artifacts and checks

A reviewer must be able to run:

```text
uv run pytest
uv run python -m gridbot.analysis.mapentry_exec_replay --population heldout --lower --upper --bootstrap 10000
uv run python -m gridbot.analysis.mapentry_exec_replay --fixture real_orders_2026-08-27 --calibrate
uv run ruff check .
```

Expected reconciliations are: held-out row count equals the source mapentry extract after the timestamp filter; deduped units equal distinct `(map_book, side)` keys; every replay fill has a qualifying source row, a valid 150-second window, and a settlement; lower fills are never greater than upper fills per order; and the real-order checks above pass. The report must include hashes or immutable identifiers for input extracts, the exact parameter file, and the verdict population cutoff.

### Ranked threats to validity

1. **Queue position is unobserved.** Measure with authenticated trade prints or venue queue/individual-order execution events; replace the bracket only by amendment.
2. **BBO sampling misses touches and quote changes.** Measure with a lossless venue event stream and compare missed-touch frequency against the 30-second heartbeat.
3. **Top size is not executable size.** Measure displayed-size-to-fill conversion by price level using trade prints and order acknowledgements.
4. **Selection/adverse drift makes fills nonstationary.** Measure lower/upper PnL separately by side, spread decile, shock status, and time-to-touch with series-clustered intervals.
5. **Model leakage or held-out contamination.** Rebuild the first-mapping partition from immutable raw timestamps and audit every model artifact's training cutoff.
6. **Venue rejects and outages censor opportunities.** Reconcile every intent to create acknowledgement, rejection, cancel, and settlement records; report rejected/ambiguous rates separately.

## Part 2 — H-LIVESMALL1

### Purpose and fixed design

H-LIVESMALL1 is a measurement run, not an edge verdict and not a license to change H-MAPENTRY1. The human must arm it; the engine remains maker-only, uses the corrected SHORT price convention, and keeps `max 3` open orders.

Run exactly **6 sessions**, one session per trading day, each lasting **3 hours**. Use `$1` clips for sessions 1–2 and `$5` clips for sessions 3–6, never exceeding `$50` realized daily loss. Keep the frozen 150-second TTL, one order per `(map book, side)`, and no manual repricing. Do not add capital to compensate for low fills.

### Pre-declared stop rules

Stop the session immediately on any daily loss of `$50`, any duplicate live order on a book/side, any order created without a recorded REST acknowledgement, any ambiguous acknowledgement not resolved within 10 minutes, or any missing ledger/venue reconciliation at session end. Stop the entire six-session run if two sessions have an unreconciled order, a rejected SHORT whose cost does not equal `1-P`, or an observed loss above `$50` after venue reconciliation. A stop is a protocol failure, not an edge result.

### Required measurements and comparison

Record every qualifying intent and every REST attempt: rejection class, RESTed indicator, displayed top size, posted price, first touch time, fill time, filled quantity, cancel time, and settlement. After six sessions compare these exact quantities with EXEC1's replay, separately for lower and upper classifications:

1. **REST acceptance:** live RESTed / create attempts versus the replay's accepted / attempted rate; flag a difference above 15 percentage points.
2. **Conditional fill rate:** filled RESTed orders / RESTed orders versus the 45% calibration target and each replay-bound rate; flag outside ±15 percentage points.
3. **Time to fill:** live median and p90 seconds from REST to fill versus replay medians/p90s; flag either statistic differing by more than 20 seconds.
4. **Touch conversion:** fills / orders with a lower-bound strict touch and fills / orders with an upper-bound any-touch; compare directly to the replay's two rates, with a 15-point tolerance.
5. **Depth utilization:** filled contracts / top size at first touch; report median and p90 and compare with the upper model's first-touch cap.
6. **Execution price:** actual cost minus posted cost in probability points, by side; any nonzero median is slippage and is not silently folded into edge.
7. **Lifetime:** unfilled order lifetime and cancel-at-TTL fraction versus the replay; flag a TTL-cancel fraction difference above 15 points.
8. **Rejections:** counts and percentages by reason, with exact match required for the known SHORT-price rejection class.

The run is informative only if it completes all 6 sessions, has at least 30 create attempts, at least 12 RESTed orders, and at least 5 fills, with zero unresolved ledger rows. Otherwise label it **UNINFORMATIVE**, do not update either fill bound, and do not use its PnL as a verdict. Even when informative, report PnL descriptively only; the n is too small for PASS/FAIL.

Files changed: `/home/ops/H-MAPENTRY1-EXEC1-H-LIVESMALL1.md`

Commands run with real output: no repository commands were run; this artifact was written from the supplied prompt under the no-repository-access constraint.

Anything not verified: repository module paths, CLI availability, input extract schemas, exact 08-27 order counts, and all numerical reconciliations require implementation/reviewer execution in the gridbot repository.

Anything that deviates from the brief: none.

Sign-off required: human approval of thresholds, registry insertion, and any later arming action.

---

# Adversarial review (deepseek-v4-pro text lane, 2026-09-04 ~04:00Z) — verdict: cannot be frozen as written; fold in #1–#4 first

# Adversarial method review — H-MAPENTRY1-EXEC1 / H-LIVESMALL1 brief

Ranked findings. Each: (a) flaw, (b) concrete failure case, (c) fix/measurement, (d) severity.

---

## 1. Lower bound is uncomputable on the no-size BBO tape — the bracket collapses to [~0, upper]

(a) The LOWER bound's fill conditions are *all* size-dependent: "require available top-of-book size at the confirming observation," "cap fills at displayed size," "no fill unless the observed move supplies ≥1 contract beyond the queue." The BBO tape (the only data source for the post-09-03 portion of the holdout) is change-only + 30s heartbeat with **no sizes and no trades**. 86–92% of shock rows carry no size even in the sized sources. So on the tape the lower bound can never confirm size nor "one contract beyond queue" → it can credit essentially zero fills for the entire tape regime.

(b) Buy posted P=0.52 on 09-05. Tape: ask 0.51 @ +40s (strict through), 0.50 @ +70s (persists at/through). Every price condition for a lower-bound fill is met, but there is no size field, so "available top size" and "≥1 contract beyond queue" are unverifiable → no fill credited. Upper bound credits the fill. Over a full holdout the lower count stays ≈0–5, the "lower ≤ upper" reconciliation holds trivially, and the PASS gate (≥60 lower-bound fills) is unreachable.

(c) Freeze an explicit no-size fallback for the lower bound (e.g. BBO-only lower fill = touch + persistence, size unmeasured → assume 1 contract), OR require a size-bearing tape before the lower bound may gate a verdict. Do not ship a lower bound that is defined in terms of data the holdout source cannot supply.

(d) BLOCKER.

## 2. Calibration regime ≠ holdout regime — one freeze, two different instruments

(a) Calibration runs on the 08-26/27 fixture, whose evidence is postquote rows that *occasionally carry size*. The holdout after 09-03 is governed by a no-size change-only tape. The lower bound's size conditions are evaluable in the fixture and not evaluable in the holdout. The "frozen" bracket is therefore a different function in the two regimes, so a calibration pass on 08-27 transfers no validity to 09-03+.

(b) 08-27 order has a postquote row with top_size=20 → lower bound can apply the "1 contract beyond queue" test → a fill may credit. A structurally identical 09-04 entry shows the same price path on the tape but has no size → lower bound credits nothing. Same instrument, opposite outputs, driven purely by which data source was live.

(c) Either calibrate on a data source with the same fields as the holdout, or pre-register that calibration is regime-partial and the verdict's lower bound uses a size-independent definition (see #1). Report fill rates separately per regime and require a regime-consistency check before any verdict.

(d) BLOCKER.

## 3. "Reproduce rejection reason class for every order" is unsatisfiable/circular

(a) The fill bracket is a pure function of price/size data. It has no venue-lifecycle logic, so it can neither observe nor reproduce the 11 "wrong venue level" SHORT rejections. The calibration gate therefore either (i) always fails — the bracket never emits "rejected" — blocking the verdict forever, or (ii) is satisfied only by hardcoding the known rejections, which is circular and validates nothing.

(b) 08-26 order #7, SHORT posted at the wrong level, venue-rejected. The replay sees a valid mapentry row and price path; it has no "rejected" state, so it counts the order as rested (and possibly filled), inflating rested/fill counts; or the calibration check "reproduce rejection class for every order" fails outright because the bracket can't say "rejected," and the gate blocks accrual indefinitely.

(c) Treat rejections as exogenous venue facts: load them from the fixture and subtract them *before* the fill bracket runs. The bracket calibrates only the rest/fill/no-fill decision on orders that were actually accepted. Rejection-class reproduction is a reconciliation-of-intent check (Part 2 measurement 8), not a bracket-calibration target.

(d) BLOCKER.

## 4. Fill-rate calibration is aggregate matching with knobs — it is tuning, not validation

(a) The calibration only checks aggregate fill rate (within 15pp of 45.5%) and median fill time, plus rested count. The two bounds have tunable knobs (strict vs any touch, size requirement, persistence requirement) that can be adjusted until the aggregate lands in the tolerance band. Aggregate-rate matching never tests whether the bracket correctly classifies the 48 orders' *ground-truth* fill outcomes at the per-order level — which is the one thing that would actually validate the touch proxy.

(b) 08-27 target = 10/22 = 45.5%. Wilson 95% CI ≈ [24.7%, 66.3%] (±~21pp). The ±15pp tolerance is narrower than the fixture's own sampling noise. A bracket tuned (strict→any, size→ignore) to report 45% passes while mis-classifying which specific orders filled; the actual fill timestamps (40–124s) and settled fill/rest outcomes are never compared order-by-order.

(c) Require a per-order confusion matrix against the 48 orders: precision/recall of the fill proxy on the 13 actual fills vs 28 rests, and a mean-absolute-error bound on predicted-vs-actual fill time. Aggregate-rate agreement is necessary but never sufficient. Pre-register the bound knobs as frozen constants chosen *before* looking at the fixture, not fitted to it.

(d) BLOCKER.

## 5. Touch proxy models only "ask travels to us"; it misses taker-sell-into-bid fills on one-sided/dead books

(a) A maker BUY at P < ask fills the moment a taker sell hits our resting bid at P. On a one-sided/dead book (22% of rows; books "do NOT reprice on rounds"), we are often the only/best bid, so a taker market-sell fills us *without* moving the ask (there may be no ask at all). The bracket only detects "opposing quote moves through P" (ask ≤ P), so it is structurally blind to the dominant fill channel on exactly the dead books this strategy trades.

(b) One-sided book, we post BUY P=0.60 as the only bid. A taker market-sells 2 contracts → we fill at 0.60; ask unchanged (none existed). Bracket sees ask never ≤0.60 → no fill credited under either bound. Ground-truth fill missed. On 22% one-sided rows this is the norm, not the exception.

(c) Validate the touch proxy against the 48 ground-truth fills *conditioned on book one-sidedness*: report detection rate (recall) of actual fills separately for one-sided vs two-sided books. If recall is low on one-sided books, add a bid-hit proxy (our level consumed) or admit the bracket is a lower bound on fills even in its "upper" form.

(d) MAJOR.

## 6. PASS floor (60 lower-bound fills) is arithmetically unreachable within the accrual cap

(a) Observed density: 40 series → 39 deduped qualifying (book, side) entries that settled (~1/series). At ~1 qualifying entry per series and a lower-bound fill rate of ~15–25% (strict-through + size + persistence), 120 additional series yield ~18–30 lower-bound fills — below the 60-fill PASS floor. The upper bound at 45% yields ~54. The instrument is pre-committed to INCONCLUSIVE regardless of whether a real edge exists.

(b) Holdout accrues 120 series, ~120 qualifying entries, upper fill 45% → 54 fills, lower ~15% → 18 fills. Both < 60. Stop rule triggers at 120 series / 90 days with verdict INCONCLUSIVE — every time, by construction.

(c) Reconcile the PASS floor against measured entry density and the fixture's lower/upper fill rates *before freezing*. Either raise the accrual cap (or drop the series cap in favor of a fill-count stop), or lower the floor with a pre-registered justification. The floor and the stop must be arithmetically compatible.

(d) MAJOR.

## 7. Sequential peeking: PASS/FAIL CI recomputed continuously with no alpha-spending

(a) The brief says "continue accruing in first-mapping order" and stop at PASS/FAIL — i.e., the 95% CI is recomputed as each series lands and the run stops on first crossing. That is repeated-look hypothesis testing with no spending function; the effective false-PASS rate inflates well above 5%.

(b) At series 45 the lower-bound CI endpoint happens to exceed +0.02 by sampling noise → PASS fires and accrual stops. Under a fixed 95% CI rechecked 45+ times, the probability of at least one spurious crossing is >>5%; the verdict is a function of the stop timing, not the edge.

(c) Pre-register exactly one terminal evaluation (at the accrual cap) or a fixed set of interim looks with a pre-specified alpha-spending function (e.g. O'Brien-Fleming), and freeze the seed. No look without a budget.

(d) MAJOR.

## 8. Upper-bound contract cap is undefined on the no-size tape

(a) The upper bound caps filled contracts at "top size observed at the touch." The tape has no sizes. So the filled quantity — and therefore "net PnL per filled contract," the primary statistic — is undefined in the tape regime and will silently fall back to some assumed size.

(b) 09-10 touch at ask ≤ P; no size on tape. The code must choose a fill quantity: 1 contract, the clip ceiling, or something else. The choice changes net PnL per filled contract materially, and it is not frozen anywhere in the brief.

(c) Freeze an explicit no-size fallback (e.g. assume 1 contract, or min(clip, 1)) and state it in the parameter file; report per-contract PnL is then conditional on that assumption, not on observed executable depth.

(d) MAJOR.

## 9. SHORT-side touch condition is not pinned in No-price terms — same class of bug as the 11 rejections

(a) Venue price is always Yes. A SHORT at Yes P is a buy of the No side at cost 1−P. Its "opposing quote that must travel" is the No-side ask = 1 − yes_bid. The brief writes only "ask < P for a buy at P" without the short mapping; implementing it in Yes terms (yes_ask ≤ P) is the wrong side and would reproduce the sign/side error that caused the 11 wrong-level rejections.

(b) SHORT posted at Yes P=0.62 → No cost 0.38. Correct fill touch = No_ask ≤ 0.38 ⟺ yes_bid ≥ 0.62. If the bracket instead tests yes_ask ≤ 0.62, it credits fills when the wrong side moved, corrupting the SHORT fill count and the (1−settlement)−(1−P) PnL attribution.

(c) Pin the SHORT touch/persistence condition explicitly in No-price terms in the frozen parameter file, and add a sign-check reconciliation (SHORT fills must correspond to yes_bid ≥ P at the confirming observation).

(d) MINOR.

## 10. Unresolved-map fills have no settlement → reconciliation drops them (survivorship bias)

(a) The reconciliation requires "every replay fill has a settlement," and PnL needs the oracle. But maps never played/never resolved (e.g. a map-3 book in a 2-0 series, forfeits, cancellations) produce no settlement. Those fills are real capital events yet are silently dropped from the PnL denominator.

(b) Map-3 book fills at P=0.50, series ends 2-0, map 3 never resolves. No oracle settlement → the fill is excluded from filled-entry PnL, biasing net PnL toward whichever fills happen to resolve (favorites vs longshots resolve at different rates).

(c) Define the disposition of unsettled fills up front (exclude-and-count, or settle-at-0.5 sensitivity), report unsettled-fill count separately, and run a sensitivity that includes them. Never let the reconciliation silently drop them.

(d) MINOR.

---

## Verdict

Cannot be frozen as written. Blockers #1–#4 make the produced number uncomputable (lower bound on a no-size tape), cross-regime-invalid (calibrate-on-sized vs apply-on-no-size), circular (rejection gate), and tuned-not-validated (aggregate-rate match only). Fold in first: a per-order confusion-matrix calibration against the 48 ground-truth outcomes, an explicit no-size fallback for both bounds' contract quantity, rejection-as-exogenous-subtraction, and a PASS floor arithmetically reachable under measured entry density and fill rate. Then #5–#8 (touch-proxy recall on one-sided books, peeking control, upper-bound quantity, short-side pinning) and #10 (unresolved-map disposition) as amendments before accrual begins. Until then, any PASS/FAIL the instrument emits is a property of its data gaps and tuning, not of maker edge.

---

# v2 — frozen candidate (Claude ops session, 2026-09-04 19:00Z; folds review findings #1–#10)

Status: candidate for registration. The registry rows below are written as
REGISTERED_NO_RUN with the thresholds marked PENDING USER CONFIRMATION; the
instrument may be BUILT and CALIBRATED on the 08-26/27 fixture, and interim
held-out renders are REFERENCE only, until the user confirms the bars.

## H-MAPENTRY1-EXEC1 v2

**Unit and population.** One deduplicated qualifying entry per (series, map,
us_side): the earliest DETECTOR-provenance mapentry row with `qualifies=true`
and a two-sided book at detection, from series whose FIRST GRID↔PMUS mapping
row is at/after 2026-08-31T19:53:46Z (first-mapping order; no cap at 40 — the
window is closed by the stop rule below). Rows before that start never enter.

**Order.** Posted our-side cost `c = posted_price(row)` = min(side_ask − 0.01,
p_map − 0.04, 0.90) in our-side terms (for a SHORT this is the No cost; the
venue Yes price is 1 − c, per NOTE7). Lifetime (t0, t0 + 150s], t0 = the
row's `ts_ns`. Quantity: sized regime → min(clip contracts, displayed size per
`exec_fills`); unsized regime → exactly 1 contract, both bounds (#8).

**Observations.** (a) postquote rows joined on `src_ts_ns == mapentry.ts_ns`
(+30/+60/+120s; no sizes → unsized); (b) tape rows for the slug inside the
lifetime (sizes since 2026-09-04 18:36Z → sized; before → unsized). Our-side
opposing quote: LONG `opp_ask = ask`, `opp_size = ask_size`; SHORT
`opp_ask = 1 − bid`, `opp_size = bid_size` (#9: a SHORT touch is
`yes_bid ≥ 1 − c`, never `yes_ask ≤ ·`). Dead/one-sided → opp_ask None.

**Fill bracket** (`gridbot.analysis.exec_fills`, knobs frozen here, before
the fixture is examined by this instrument): LOWER = strict touch
(`opp_ask < c`), persistence (next in-window observation still touching),
queue behind displayed size when sized, `LOWER_UNSIZED` (1 contract) when
not (#1, #2). UPPER = any touch (`opp_ask ≤ c`), front of queue, sized cap or
1 contract. No trade print is imputed. Fill-detection recall is reported by
book one-sidedness at detection (#5); if recall on one-sided books in the
fixture is below 0.5, every render carries the label FILL-DETECTION-PARTIAL
and a FAIL cannot be declared (a PASS can, because missed fills are the
adverse-touch kind that lowers PnL).

**Rejections are exogenous** (#3): on the fixture days, orders whose venue
state is REJECTED (`timing_profile.venue_states`) are subtracted before the
bracket; the replay never emits "rejected". Held-out replay assumes the
corrected convention (NOTE7) and treats every simulated order as accepted;
the live-small run measures the actual acceptance rate.

**Accounting.** `y_side` = 1 if the row's `team` wins the map, on BOTH sides:
a mapentry SHORT row's `team` is the team we back (the No leg), and its
`p_map`/posted cost are already in that leg's terms (correction 2026-09-04
19:20Z — the earlier wording "SHORT: team loses" would double-orient). PnL per contract = `y_side − c`. Fees 0 (the
venue's position records carry `fees: null` on all 13 real fills — recorded
assumption). Unresolved maps: exclude-and-count, plus a settle-at-0.5
sensitivity line (#10). Series-clustered bootstrap, 10,000 resamples, seed
17, contracts weighted by filled quantity.

**Calibration gate** (must pass before the held-out population is read; a
failure → amendment, never a tuned knob): on the 48 venue-verified orders of
08-26/27 with their postquote observations, per-order confusion matrix vs the
actual fill/rest outcome (#4): rested count reconciles exactly with the venue
states; UPPER recall on the 13 actual fills ≥ 0.6; LOWER precision on
predicted fills ≥ 0.6; fill-time MAE on detected fills ≤ 45s; recall by
one-sidedness printed.

**Verdict (single terminal look, #7).** Interim renders are REFERENCE and
never stop accrual. The terminal evaluation happens once, at the earlier of:
(a) ≥ 60 LOWER-bound filled entries AND ≥ 60 UPPER-bound filled entries, or
(b) 45 calendar days after the first held-out row read under this row.
PASS: under LOWER, ≥ 60 fills and the 95% CI's lower endpoint > +0.02 per
contract. FAIL: under UPPER, ≥ 60 fills and the CI's upper endpoint < 0.
Otherwise INCONCLUSIVE — no arming, no relabel. Arithmetic check (#6): ~1
entry per held-out series, ~77 newly mapped series/day, upper fill ≈ 45%,
lower fill ≈ 15–25% → 60 lower fills ≈ 240–400 entries ≈ 4–6 days of
mapping; both floors are reachable inside 45 days.

**Artifacts.** `uv run python -m gridbot.analysis.exec_replay --calibrate`
(fixture gate, confusion matrix, recall by one-sidedness) and
`--heldout [--terminal]` (REFERENCE unless --terminal; --terminal refuses to
run before the stop rule is met and writes the verdict JSON once). Every
report lists input file names + row counts, the frozen knobs, the regime mix,
and reconciliations: held-out row count == filtered extract; deduped units ==
distinct (series, map, us_side); every fill has a source row, a window, a
settlement or an unresolved flag; lower fills ⊆ upper fills per order.

## H-LIVESMALL1 v2

Purpose: calibrate the fill bracket against real venue behaviour; NOT an edge
verdict. Prerequisites, all before session 1: EXEC1 calibration gate PASSED;
`GRIDBOT_LIVE=1 uv run python scripts/live_fire_test.py --side short` prints
CONVENTION VERIFIED; the user arms per docs/LIVE_FIRE.md (rail 3). Design:
6 sessions × 3h, one per trading day; $1 clips sessions 1–2, $5 sessions 3–6;
max 3 open; $50 daily loss; frozen 150s TTL; one order per (map book, side);
no manual repricing; no added capital. Stop rules and the informative floor
(≥30 create attempts, ≥12 rested, ≥5 fills, zero unreconciled ledger rows) as
in Part 2 above, plus: a second REJECTED short at the wrong venue level ends
the run. Measurements 1–8 as in Part 2, compared to EXEC1's two bounds, plus
fill-detection recall of the bracket on the live fills (the touch proxy vs
the venue's actual fills, by one-sidedness). PnL descriptive only.
