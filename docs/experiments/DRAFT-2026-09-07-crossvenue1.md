# DRAFT registry row — H-CROSSVENUE1

**Not appended to `registry.jsonl`.** Rail 2: the registry is your own act.
Review, edit the bracketed thresholds, then say the word.

## How these bars were set

From first principles and from execution cost that was already measured on other
rows — **not** by looking at cross-venue basis data. No basis, lead or edge has
been computed. The holdout boundary was stamped at `2026-09-07T06:39:27Z`, before
the join verifier was even written.

- Clause (i) mirrors the BOOKPIN1 detector shape (Brier + series-clustered
  bootstrap) so it is comparable to the record we already have.
- Clause (ii)'s cost floor comes from NOTE9's measured PMUS match spread: p50 0.02,
  78% within 2 ticks. Crossing costs ~1 tick each way, so a basis under ~1 tick is
  untradeable at any accuracy. NOTE8's rule: no number without the spread it must
  cross.
- Clause (iii) cannot render until a trades tape exists — that is NOTE10, and it
  is written into the row rather than left implicit, so this lane cannot repeat
  H-MAPENTRY1's failure of discovering mid-flight that its instrument was
  unqualified.
- The calibration gate is stated as a **precondition**, with the explicit rule that
  a failed gate means NO verdict. That is the single lesson of `EXEC1-NOTE3`.

```json
{"run_ts": "2026-09-07T07:30:00Z", "hypothesis_id": "H-CROSSVENUE1", "hypothesis_text": "CROSS-VENUE MISPRICING, pre-registered BEFORE any basis, lead or edge is computed. HOLDOUT BOUNDARY 2026-09-07T06:39:27Z, stamped before the join verifier was written; membership = GRID series whose FIRST GRID<->PMUS mapping row is at/after that instant. Everything before it is training/calibration and may be explored freely. MECHANISM CLAIM: the intl Polymarket CLOB and Polymarket US are structurally segmented markets settling the same CS2 events -- the participant sets are disjoint by regulation (intl is closed to this account, per the H-MAPENTRY1 venue-bridge row), so no arbitrageur closes the gap, and the thin venue (PMUS) goes stale relative to the deep one (intl) after information arrives. The ESTIMATOR is swapped, not improved: we stop pricing our model against a market (p_map, retired 2026-09-07 per H-MAPENTRY1-CLOSE1 -- gap ~= 0.9 x discount, the book was ~90% right when we disagreed) and instead price a small market against a large one. q_round is NOT the fair value here; it is the informed-vs-noise filter (is this repricing information?), and it remains a calibrated quant model -- rail 1 holds, no LLM anywhere in this path. UNIT: one observation per (GRID series, market kind, GRID information event), deduped to the first qualifying observation per market per event. ORIENTATION IS A PRECONDITION, NOT A STEP: both venues quote a Yes price; intl Yes is outcomes[0] of a two-outcome token pair, PMUS Yes is the 'long' team of the book's sides map, and the venue price is ALWAYS the Yes price (NOTE7). Every pair must resolve BOTH venues' Yes to a NAMED TEAM and agree; an unresolvable pair is REFUSED and counted, never guessed (gridbot.analysis.crossvenue_join; a sign-inverted basis does not look broken, it looks like a stable tradeable signal, and NOTE7 records 15 of 24 armed-night shorts lost to exactly this class of error while the ledger read 'placed'). Refusal rate is reported with every render. CLAUSE (i) DETECTOR: on held-out series, intl_mid restated in the PMUS Yes convention beats PMUS_mid at predicting the settled outcome, by Brier difference with a series-clustered bootstrap (10,000 resamples, seed 17) whose CI95 EXCLUDES ZERO. If intl is not the better estimator, the whole mechanism is false and the lane closes. CLAUSE (ii) COST: the basis at signal must exceed the spread it must cross. PMUS match spread is p50 0.02 with 78% of rows within 2 ticks (NOTE9), so the pre-registered floor is [BASIS_FLOOR = 0.02] with the realised spread distribution reported alongside every basis number (NOTE8: no number without the spread it must cross). CLAUSE (iii) EXECUTION: fill-modelled PnL per contract, depth-limited against recorded size, quote-lifetime aware, series-clustered. THIS CLAUSE MAY NOT RENDER ON A BBO TAPE (H-MAPENTRY1-NOTE10, binding): it requires a PMUS TRADES tape (prints), because a BBO tape cannot separate a sale through our resting level from a cancel of that level and cannot observe queue position at all. Until the trades tape exists, clause (iii) renders NOT-EVALUABLE, never a number. CALIBRATION GATE (precondition, before any held-out read): the fill model must reach [RECALL >= 0.60] against real observed fills with [PRECISION >= 0.60] and fill-time MAE [<= 45s]. A FAILED GATE MEANS NO VERDICT OF ANY KIND -- not a PASS, not a FAIL, not an INCONCLUSIVE with a number attached. This is the explicit lesson of H-MAPENTRY1-EXEC1-NOTE3, where the held-out FAIL criterion was separately satisfiable and was correctly NOT claimed because the instrument was uncalibrated. STARVATION: a render requires [>= 200] deduped qualifying observations across [>= 30] distinct series; below that the verdict is INCONCLUSIVE, so a quiet venue can never manufacture a PASS. Slate context (venue-liveness per scripts/evidence_qa.py) is reported with every render so 'the venue went quiet' is never confusable with 'our capture broke'. VERDICT: single terminal look at the earlier of the starvation bar being met on all three clauses, or [45] calendar days after the first held-out read. PASS requires clause (i) CI95 excluding zero in intl's favour AND clause (ii) satisfied AND clause (iii) CI95 lower endpoint > [+0.02]/contract. FAIL on clause (i) CI95 excluding zero against intl, or clause (iii) CI95 upper endpoint < 0. Otherwise INCONCLUSIVE. Interim renders are REFERENCE and never stop accrual. THRESHOLDS IN [BRACKETS] ARE PENDING USER CONFIRMATION: until confirmed, the instrument may be built, calibrated and may print REFERENCE renders, but no verdict is rendered. A PASS here is still a paper PASS on a fill MODEL; arming remains the user's own act (rail 3), and dry_run stays True until then. PREREQUISITES, all stated so none is discovered mid-flight: (1) intl CS2 winner-market capture -- LANDED 2026-09-07, previously zero such markets had ever been captured; (2) the orientation-checked join -- LANDED 2026-09-07, 28 joined match markets on the first partial day, all aligned, 0 inverted seen in the wild so the inversion detector is currently insurance rather than a demonstrated catch; (3) a PMUS trades tape -- NOT YET RUNNING, gates clause (iii); (4) pre-match book coverage -- NOT YET CAPTURED, PMUS lists a match a median 4.46h before scheduled start and the recorder starts only at GRID 'started', so the window where a thin book is most mispriced is currently invisible."}
```

## What I deliberately did NOT put in the row

- **No expected effect size.** I have not measured one, and inventing a number to
  aim at is how a bar gets fitted to data.
- **No taker rule.** NOTE9 measured taking this book at −0.004 to −0.014 with CIs
  at or below zero. The maker side is the only thing the data leaves open.
- **No arming path.** Rail 3 is unchanged and is not this row's business.

## Open question for you

Clause (iii) is gated on the trades tape, so the lane can only render clauses (i)
and (ii) until that runs. Two options:
- **(a)** register now, let (i)/(ii) accrue on the book tape, add (iii) when the
  trades tape has data. Gets the holdout clock started on the parts that work.
- **(b)** hold registration until the trades tape runs, so all three clauses accrue
  over the same window.

I lean **(a)**: clause (i) is the mechanism test — if intl is not the better
estimator, nothing downstream matters, and that is answerable on book data alone.
Finding that out sooner is strictly better than finding it out later.
