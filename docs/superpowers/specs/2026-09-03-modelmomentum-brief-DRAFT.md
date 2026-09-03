# H-MODELMOMENTUM1 — design brief DRAFT (NOT REGISTERED)

**Status: DRAFT for the user's decision. Nothing here is in the registry; no
instrument runs under it; dry_run posture unchanged.** Drafted 2026-09-03
eve by the gpt-5.6-luna reasoning lane (dispatch `reasoning2`, ~2 min) from
the evidence pasted in its prompt (timing-chain addenda 1–2; the reconciled
post-shock funnel; NOTE6 fill selection; the venue price convention). An
adversarial method review of the underlying funnel by the deepseek-v4-pro
lane is in flight; its findings get appended below when they land.

Why this exists: two independent instruments landed on the same mechanism
today — the map book does not price rounds, it lags the model/state. The
post-shock instrument's own fade control failed (both arms positive:
DOWN→short +0.19 [+0.08, +0.30], UP→long +0.15 [−0.03, +0.33], in-sample,
series-clustered), which by pre-registration discipline closes "fade" and
requires a NEW mechanism claim before any instrument runs under it. This
is that claim, drafted for review.

Claude's notes on the draft (read before adopting):
- §1 item 7's control (shocks where the book had already moved ≥10pt) is the
  right shape; it needs a precise definition of "before the shock row" (the
  previous live quote's gap vs p_map) before it can be frozen.
- §2 items 21 and 23 already exist in the reconciled funnel (terminal
  exclusion; short cost = 1 − P). Keep them as restatements, not additions.
- §3 depth-limited taker fill is the change that turns the number into a
  trading number; the `book` side-channel on mapentry rows carries top-of-
  book size, so it is computable on the existing corpus.
- §4 thresholds (+3pt mean, CI lower bound > 0, floor 40/15) are proposals;
  the user sets them. FAIL as written (point ≤ 0 OR upper bound ≤ 0) is
  stricter than the current STARVED/INCONCLUSIVE convention — decide.
- §5 checks 51–52 (dead-book selection) and 53–54 (overconfident p_map
  where it disagrees with the book) are the two most likely artifacts given
  NOTE5/NOTE6; run them on the design pool BEFORE registering.

---

# H-MODELMOMENTUM1 Design Brief

## 1. Frozen statement and falsifiable expectation

1. **Hypothesis:** After a shock of at least 25 percentage points in `p_map`, the live book converges toward `p_map`; entering on the model’s side at the executable price captures part of the gap.
2. The strategy goes LONG the named team after an UP shock and SHORT the named team after a DOWN shock.
3. The shock is measured in `p_map`; the trade price is the executable venue price, not the midpoint.
4. **Primary falsifiable expectation:** Across held-out shocks meeting the frozen population funnel, the combined arms produce mean settled edge of at least **+3 percentage points** at executable prices.
5. The result must have a **series-clustered 95% confidence interval whose lower bound is above 0**.
6. The expected effect is momentum, so the UP control should not be materially negative while the primary arm is positive.
7. **Control arm:** Use otherwise eligible shocks where the book had already moved at least 10 points toward the eventual `p_map` direction before the shock row, leaving less than 10 points of remaining model-book gap at the shock row.
8. The control uses the same entry, settlement exclusion, sizing, and accounting rules.
9. Momentum predicts materially less edge in this already-moved control than in the primary arm; a similar result in both arms indicates a generic selection or settlement artifact.

## 2. Population and funnel

10. **Base funnel, frozen from H-POSTSHOCKFADE1:** a "shock" is a `>= 25pt` move in `p_map` within a window; population gates: shock priced by a live book, `prev_p_map >= 0.40`, not ended by a restart, market slug present, book alive on the shock row itself, staleness gate = book still `>= 10pt` away from `p_map` on the shock row, persistence = the NEXT live quote within 45s still `>= 5pt` away, one entry per (series, map, slug), earliest shock wins.
11. **Keep the shock threshold:** `>= 25pt` is the frozen mechanism boundary and prevents threshold drift.
12. **Keep shock priced by a live book:** Without a quote on the shock row there is no executable trade.
13. **Keep `prev_p_map >= 0.40`:** Preserve the original population definition and avoid changing the model’s low-probability regime without a new registration.
14. **Keep the restart exclusion:** Restarts can invalidate event ordering and contaminate the shock interpretation.
15. **Keep market slug present:** A missing slug cannot be joined to a tradable instrument.
16. **Keep book alive on the shock row:** The strategy must observe an executable venue state at entry.
17. **Keep the `>= 10pt` staleness gate:** The hypothesis requires a meaningful model-book gap, but test dead-book selection separately.
18. **Keep the next-quote persistence gate:** It prevents a one-tick stale or transient quote from defining the opportunity.
19. **Keep one entry per (series, map, slug):** Prevent repeated observations from inflating the effective sample size.
20. **Keep earliest shock wins:** This prevents outcome-dependent selection among multiple shocks.
21. **Add a terminal-observation exclusion:** Exclude entries within 60 seconds of the terminal observation because settlement information may already be present.
22. **Add executable-depth eligibility for the taker arm:** Require positive displayed depth at the relevant ask; otherwise the opportunity is not tradable at the recorded price.
23. **Add direction-specific accounting:** For DOWN shocks, account for SHORT exposure at cost `1 - P`; for UP shocks, account for LONG exposure at cost `P`.
24. **Do not add a post-entry outcome filter:** Eligibility must be determined only from information available at entry and the frozen persistence quote.

## 3. Execution model

25. **Primary verdict arm: taker execution.**
26. Enter at the shock-row executable ask: the Yes ask for LONG and the corresponding executable price for SHORT.
27. For a SHORT at Yes price `P`, use venue cost `1 - P`.
28. Requested size must be no greater than displayed top-of-book depth recorded in the `book` field on the shock row.
29. If displayed depth is below the configured unit size, record the maximum fillable size or reject the opportunity according to the frozen sizing rule; never assume hidden liquidity.
30. The backtest must apply venue fees, documented slippage, and the exact settlement payoff.
31. The taker fill assumption is **full fill up to displayed depth at the recorded ask, zero fill beyond displayed depth**.
32. No midpoint fills, last-trade fills, synthetic liquidity, or favorable price improvement are allowed.
33. **Secondary diagnostic only: maker-with-TTL.**
34. Simulate posting one tick inside with a 150-second TTL only as a separately labeled execution arm.
35. Maker fills must be based on observed or explicitly modeled queue-compatible fills; unfilled orders earn zero and incur no hypothetical edge.
36. Maker results cannot be pooled with taker results for the preregistered verdict.
37. The holdout verdict is based on the primary taker arm unless the registry is amended before holdout analysis to nominate maker execution instead.
38. Every trade must be settlement-informed only after the 60-second terminal exclusion and must use the price and depth available at entry.

## 4. Holdout verdict rule

39. **Minimum floor:** At least **40 resolved entries across at least 15 series**.
40. The current holdout has 14 resolved entries across 12 series and therefore cannot produce a verdict.
41. The statistic is **mean settled edge at executable price**, calculated per entry and reported with a series-clustered bootstrap confidence interval.
42. Report combined UP and DOWN arms first, then report each arm separately.
43. **PASS:** The floor is met, mean settled edge is at least **+3 percentage points**, and the lower bound of the series-clustered 95% confidence interval is above **0**.
44. **FAIL:** The floor is met and either the point estimate is **at or below 0** or the upper bound of the series-clustered 95% confidence interval is at or below **0**.
45. **INCONCLUSIVE:** The floor is not met, or the point estimate is positive but fails the PASS requirements without meeting the FAIL requirements.
46. An inconclusive result does not authorize live trading and does not change the frozen hypothesis.
47. A failed result closes the lane unless a new mechanism claim is preregistered before collecting a new instrument.
48. A PASS does not itself flip live execution; `dry_run=True` remains on until the user explicitly changes it.

## 5. Artifact risks and exposing checks

49. **Settlement leakage:** The apparent edge may come from information already embedded near map end.
50. **Check:** Recompute the result after enforcing the 60-second terminal-observation exclusion using only timestamps available before entry; compare excluded and retained rows.
51. **Staleness-gate selection of dead books:** The `>= 10pt` gap may select stale or non-executable quotes rather than real under-reaction.
52. **Check:** Require positive displayed depth and compare edge and fill rate for alive-depth rows against rows passing only the original book-alive gate.
53. **Overconfident `p_map` after shocks:** The model may be systematically wrong when it disagrees with the book by at least 4 points.
54. **Check:** Stratify holdout results by model-book disagreement, report calibration error and settled edge by bin, and reject any claim that depends only on the known overconfidence region.
55. **Restart contamination:** Restart boundaries may create false shocks, broken event order, or stale joins.
56. **Check:** Audit every candidate’s preceding and following restart markers and rerun the funnel with a conservative restart buffer; any material edge loss exposes contamination.
57. **Survivorship in the map-winner oracle:** Resolved markets or cleanly joined map winners may be easier to retain than failed or missing observations.
58. **Check:** Reconcile all eligible shock rows to the full live-book and series population, report unresolved, missing-slug, and missing-outcome counts, and rerun results with unresolved rows retained as censoring rather than silently dropped.
59. **Executable repricing artifact:** A midpoint gap may disappear at the ask.
60. **Check:** Compare midpoint, shock-row ask, depth-limited taker price, and settled edge; the preregistered result must use the latter.
61. **Repeated-opportunity dependence:** Multiple shocks from one map may inflate confidence.
62. **Check:** Enforce earliest shock wins and use series-clustered resampling; also report results clustered at the map level.

## 6. Instrument rails and required logging

63. The instrument must not call an LLM in the trade path.
64. The instrument must not alter `p_map`, invent missing quotes, infer hidden depth, or trade on a midpoint.
65. The instrument must not place live orders while `dry_run=True` has not been explicitly changed by the user after a preregistered PASS.
66. The instrument must not add instruments, change thresholds, change TTL, change sizing, or change gates without a registry amendment before the affected instrument runs.
67. The instrument must not trade within 60 seconds of the terminal observation.
68. The instrument must not retry a rejected post-only order by crossing the spread.
69. The instrument must use one account only and must never log, transmit, or request secrets or key material.
70. Log the frozen hypothesis ID, registry version, run ID, and code/config version.
71. Log series ID, map ID, market slug, named team, shock direction, shock timestamp, prior `p_map`, shock-row `p_map`, and shock magnitude.
72. Log shock-window definition, restart status, terminal-observation timestamp, and seconds from terminal observation to entry.
73. Log book timestamp, Yes bid and ask, relevant executable ask, midpoint, spread, displayed depth, book age, and the full `book` side-channel record required for reconstruction.
74. Log staleness-gap size, persistence-quote timestamp, persistence-gap size, and whether the control-arm condition was met.
75. Log requested size, fill assumption, simulated or actual fill size, taker/maker arm, TTL, rejection reason, and any partial fill.
76. Log fees, settlement result, gross payoff, net settled edge, and whether the row was excluded and why.
77. Log every eligible opportunity, including skipped opportunities, with a machine-readable skip reason.
78. Produce separate reports for primary taker, maker diagnostic, UP, DOWN, and control arms.
79. Preserve the raw evidence needed to reproduce the holdout funnel without accessing secrets or modifying the frozen hypothesis.

---

## Adversarial method review of the reconciled funnel (deepseek-v4-pro-0813, dispatch `reasoning3`, 2026-09-03 eve)

Claude's triage, before the verbatim findings:

| # | finding | status | action |
|---|---|---|---|
| 1 | the "shock" is post-round information; the entry is a pre-round quote | this IS the mechanism under test (latency lead), but the backtest must show the quote survives our pipe | diagnostic: time-to-convergence vs 15s pipe (dispatched) |
| 2 | exact reproduction of the reference is by construction | accepted; in-sample only, holdout starved | none — already stated everywhere |
| 3 | staleness + persistence select dead books | plausible, unmeasured | diagnostic: never-converged share, edge split (dispatched) |
| 4 | per-arm dedup lets one market enter both arms | real possibility; JSON has no per-entry rows to check | diagnostic: cross-arm overlap + global-dedup variant (dispatched) |
| 5 | taker price with no size check | real; `book.bid_size/ask_size` exist on the shock row | diagnostic: top size distribution, share < $5 (dispatched) |
| 6 | NEXT-quote persistence is fragile | measured "immaterial" by the lane, not shown | diagnostic: flip count any-vs-next (dispatched) |
| 7 | oracle survivorship | 1 unresolved entry per arm on this pool; negligible here | print counts (already) |
| 8 | "both arms positive" over-read: the UP CI includes zero | **accepted** — STATE.md/memory wording corrected: DOWN +0.19 [+0.08, +0.30] is the significant number; UP +0.15 [−0.03, +0.33] is not; the fade's asymmetry is neither confirmed nor refuted | wording fixed 09-03 |
| 9 | priced row vs leak-guard timestamp may diverge | **refuted by construction**: `find_shock_row` matches `ts_ns == rec["shock_ts_ns"]` exactly | none; a nonzero-count print is included anyway |

FINDING 1 — Look-ahead: the "shock" is post-round information the book hasn't priced yet, so both arms are front-running, not fading.
Claim: p_map on the shock row is updated from the live GSR score the instant the round resolves (a team wins the round → its win prob jumps ≥25pt). The entry fills at the shock-row BOOK quote, which still reflects the pre-round price because makers re-quote later. That makes DOWN (short the dump) and UP (long the surge) both positive for the same reason — the model leads the book in both directions — which is exactly the anomaly the OLD spec said a fade could not produce. This is measured model correctness, not a fade.
Why it matters: if the entry price is a book that couldn't yet have the round result but p_map could, the "edge" is unearned information transfer and vanishes in live trading where you can never access p_map before the market does.
Check: for each entered shock, pull the GSR round_number / round-end timestamp and the shock row's book-quote timestamp; report the fraction where a round boundary falls inside the 45s shock window but the entry bid/ask is a snapshot from before that boundary. Non-negligible → leakage confirmed.

FINDING 2 — In-sample circularity: exact reproduction of the reference (72/58, 38/35) is the same funnel re-derived on the same corpus, not independent confirmation.
Claim: the amendment "voids the old 70/32 anchors and pins the spec-literal funnel as reference," and the rewrite matches it exactly. Matching is expected when two implementations encode the same gates over the same in-sample design rows; it says nothing about whether those gates are correct. The holdout floor is NOT met (14 resolved / 12 series vs floor 40/15 → INCONCLUSIVE_STARVED), so the positive win rates/edges rest entirely on in-sample data with zero out-of-sample falsification.
Why it matters: this is the per-registration discipline's first rail — a funnel can reproduce its own reference by construction and still be tuned/leaky; "matches the reference" is the weakest possible validation of the ONLY numbers with a real sign.
Check: diff the reference anchor-trace §3 gate implementation against this one and confirm whether the ENTRY SET is identical at the (series,map,slug) level or merely the counts. If identical, also report which series are shared with the reference's design set — if the reference and this run share design rows, there is no held-out evidence at all.

FINDING 3 — The staleness gate (≥0.10) plus persistence (≥0.05 at next quote) jointly SELECT dead/illiquid books, so the "edge" is an uncapturable artifact.
Claim: a book ≥10pt stale that is STILL ≥5pt stale 45s later is by construction a book nobody is converging — the MM has withdrawn or never quoted. Pricing a systematically-stale dead book against a no-friction map-winner oracle yields an artificial high hit-rate: the oracle always resolves correctly, the dead book never reprices, so you "win" on paper. You cannot actually take that taker price because there is no live counterparty. maker fills 11/71 (15%) and 14/37 (38%) corroborate limited fillability.
Why it matters: even with zero leakage, a 62% "win rate" on books selected for their failure to converge is not a tradable edge — it's the corollary of "thin/squared books are wrong and stay wrong."
Check: for each entered book, measure spread-vs-p_map convergence between entry and resolution; report the fraction of entries whose book had converged to within ~5pt at any later point. If the entered books are concentrated in the never-converge tail, the edge is a dead-book selection artifact, not a captured fill.

FINDING 4 — Dedup is per-arm, so one (series,map,slug) can enter BOTH arms, double-counting outcomes and cross-contaminating the two "independent" positives.
Claim: the `seen` dict is reset inside the per-arm loop, so the dedup key (series,map,slug) does NOT include the arm. A market that produced both a down shock and an up shock yields one DOWN entry AND one UP entry whose resolution is the SAME map outcome. The spec clause says "ONE entry per (series, map, slug)" — no arm qualifier.
Why it matters: the arms are no longer independent samples; a handful of high-variance markets that shocked both ways drive "both arms positive" on the same underlying resolution, and the entry/win counts are inflated.
Check: compute the set intersection of (series_id,map_number,market_slug) across the DOWN and UP entry lists. If nonzero, re-run with GLOBAL dedup and report the resulting entry counts and win rates — if they move materially, the two-arm POSITIVE is partly one market counted twice.

FINDING 5 — Taker entry price = shock-row ask/bid with no size or depth check on a book already selected for illiquidity.
Claim: short/long fills at the shock-row top with no min-size test. On the dead books FINDING 3 selects, the displayed top is frequently a remnant stub (a few shares, or a withdrawn order's ghost) at trivial dollar size, not a fillable offer at meaningful size.
Why it matters: a "taker fill" at $0.14 of displayed size is not a position; the reported median entry px (0.410 / 0.465) is not a price you could actually buy/sell the intended size at.
Check: report the best-ask size (UP) / best-bid size (DOWN) at each shock-row entry; count entries whose available size at the entry price is below a $ threshold (e.g. <$5). If most entries are sub-threshold, the edge is not sizeable.

FINDING 6 — Persistence is NEXT-quote-only: a single transient quote flips the entry decision, and the gate is admitted to be definition-fragile.
Claim: `_persistence_ok` returns on the FIRST live quote's gap (≥0.05) and never looks at the rest of the 45s window. One fast quote (a maker snapping the book for an instant) kills an otherwise-persistent entry, or one stub-wide quote passes a book that is actually converged. The docstring concedes "any-vs-next was measured immaterial at this cadence" — but if the distinction is immaterial, the gate is be defined by noise and shouldn't pin a funnel whose numbers are cited.
Why it matters: a definitional ambiguity that is "immaterial" under one test can become material at the tail (dead books → sparse quotes → the first quote IS often the only quote), destabilizing the entry set and therefore the win/edge stats.
Check: recompute each entry's persistence under BOTH interpretations (any live quote in 45s ≥5pt, vs next-only) and count the entries whose verdict flips. A material flip count means the numbers are unstable to a pinned-but-unmotivated definition.

FINDING 7 — Oracle survivorship: only maps with a recorded winner resolve; post-entry restarts/forfeits/voids vanish from the denominator.
Claim: `resolve()` runs against a winner table keyed by (series,map) with first_seen_ts_ns; a map that never recorded a terminal winner simply drops out of won/lost (unresolved). `ended_by_restart` catches PRE-shock restarts only; a restart/forfeit that occurs AFTER the entry but before a winner is recorded removes that trade from the sample non-randomly.
Why it matters: conditioning on "the map resolved" removes exactly the adverse tail (the shock that was wrong and then the map voided/restarted), biasing win rate upward; it is a selection-on-outcome problem independent of the settlement-leak guard.
Check: enumerate all series/maps that produced an entry but have NO winner record, plus maps whose restart/abandonment timestamp is after the shock_ts_ns; best-effort classify each into won/lost and report whether they skew losing. A losing skew = survivorship bias in the oracle.

FINDING 8 — "UP control is positive" is over-read: the UP CI includes zero, so it is NOT a violation of the fade hypothesis.
Claim: UP edge +0.147 with 95% CI [-0.026, +0.327] crosses zero; only DOWN (+0.188, CI [+0.075,+0.299]) is significant. With n=37 the UP arm is consistent with the fade's expectation (UP ≈ 0), so the stated alarm that "both arms positive is what a fade forbids" is not actually supported by the numbers.
Why it matters: the leakage/bug narrative (FINDING 1) is being justified partly by a "violation" that the confidence intervals do not establish — conflating a point estimate with significance inflates the case for a bug and can misdirect debugging effort.
Check: compute the two-sided series-clustered p-value for the UP arm's mean edge; report it alongside the CI. Non-significant → treat UP as null and let DOWN's significance carry the evidence, rather than citing the UP point estimate as anomalous.

FINDING 9 — The entry price, persistence window, and settlement-exclusion anchor come from possibly DIFFERENT timestamps (sr vs rec["shock_ts_ns"]).
Claim: the entry bid/ask and p_map are taken from `sr` (find_shock_row's returned row), but the persistence window and the terminal-exclusion test use `ts0 = rec["shock_ts_ns"]` (the detect record's timestamp). If find_shock_row returns a row whose ts_ns ≠ rec["shock_ts_ns"], the book you price at and the instant you anchor the leak guard to diverge by one row.
Why it matters: a one-row skew between the priced book and the terminal-exclusion timestamp can admit an entry priced against a pre-terminal book while `ts0` sits past terminal (or vice-versa), silently defeating the settlement-leak exclusion that clause 5 depends on.
Check: assert, for every produced entry, that `sr["ts_ns"] == rec["shock_ts_ns"]` (or log the delta); count mismatches and re-check the terminal/after_terminal classification on those rows specifically.

---

## Premise update (17:05 PDT, Claude) — read before anything above

The timing chain's model-momentum read (Addendum 3 of the first-read doc)
is a NULL: in the ≥ 4pt entry band the book's move toward p_map over
30–120s and over the next round is +0.003 / −0.005 / −0.003 with every CI
straddling zero (n ≈ 235, 11 series). The book does not reprice toward the
model within a round. §1's statement "the live book converges toward p_map;
entering on the model's side captures part of the gap" is therefore NOT
supported as a reprice claim on this tape. If this hypothesis is registered
at all, it must be a SETTLEMENT-horizon claim (the model is right more
often than the stale book on the resolved outcome after a large fresh
shock) with an execution-realism gate (depth-limited taker fill at the
stale quote before it disappears), which is the H-MAPENTRY1 thesis
restricted to shocks — and H-MAPENTRY1 is where fill selection ate the
paper edge. Decide after the post-shock diagnostics land.
