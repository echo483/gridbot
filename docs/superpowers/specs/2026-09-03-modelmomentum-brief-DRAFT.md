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
