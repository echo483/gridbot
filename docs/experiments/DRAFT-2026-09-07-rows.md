# DRAFT registry rows — 2026-09-07

Drafted for the user's approval. **Not appended to `registry.jsonl`.** Rail 2:
registry rows are the user's own act. Review, edit, then say the word and they
land verbatim.

Three rows: two close H-MAPENTRY1 honestly, one pins a generalizable measurement
rule that cost us this lane.

---

## ROW 1 — `H-MAPENTRY1-EXEC1-NOTE3` (disclosure: the gate fails)

**Why this row exists:** on 2026-09-07 the EXEC1 calibration gate was re-run on
the sized 2s tape and **does not pass**. Disclosing rather than silently repairing
follows the NOTE2/NOTE3 precedent. Without this row the record would imply EXEC1
was a usable instrument.

```json
{"run_ts": "2026-09-07T06:45:00Z", "hypothesis_id": "H-MAPENTRY1-EXEC1-NOTE3", "hypothesis_text": "CALIBRATION-GATE FAILURE, disclosed rather than silently repaired (precedent NOTE2/NOTE3). Re-run 2026-09-07 on the sized 2s tape, the frozen EXEC1 calibration gate does NOT pass. Gate (frozen 2026-09-04T19:05Z): rested reconciles exactly; UPPER recall on the 13 actual fills >=0.6; LOWER precision >=0.6; fill-time MAE <=45s. MEASURED on the 48 venue-verified 08-26/27 orders: placed 48, rested 28, filled 13, rejected 20, unknown 0 (reconciles exactly, PASS); LOWER precision 1.0000 (PASS); fill-time MAE upper 15.6s / lower 43.5s (PASS); UPPER RECALL 0.4615 = 6/13 (FAIL, bar 0.6). LOWER recall 0.2308 = 3/13. The 0.86 figure cited in the 2026-09-04 first-renders doc is recall on the DETECTABLE subset (6/7), which excludes 14 undetectable and 4 unobserved orders; the frozen gate is written against all 13 actual fills and on that basis fails. CONSEQUENCE: EXEC1 is NOT QUALIFIED to render a verdict on H-MAPENTRY1. The single terminal look is not taken. Note the held-out FAIL criterion was separately satisfiable at the time of this row (UPPER 95 fills, series-bootstrap CI95 [-0.1904, -0.0583], upper endpoint < 0), and it is deliberately NOT claimed: a verdict from an instrument that failed its own pre-registered calibration gate is not evidence. Held-out REFERENCE render on the sized tape for the record (202 orders / 73 series, sized=72 unsized=130): LOWER 48 fills, mean -0.1669/contract, CI95 [-0.2820, -0.0704], 31 series, jackknife all-negative; UPPER 95 fills, mean -0.1234, CI95 [-0.1904, -0.0583], 47 series. The LOWER edge did not move at all versus the 2026-09-04 render (still exactly 48 fills, identical CI) despite 72 additional sized orders: reasons no_touch=132 dominates, consistent with the measured PMUS map-book change-event collapse, not with a stale run. Reproduce: uv run python -m gridbot.analysis.exec_replay_cli --calibrate and --heldout."}
```

## ROW 2 — `H-MAPENTRY1-NOTE10` (the generalizable rule)

**Why this row exists:** the gate failure is not an EXEC1 bug. It is a property of
the *tape*, and it will recur in any future maker lane unless pinned as a rule —
exactly as NOTE8 pinned "no backtest number without a depth/fill model."

```json
{"run_ts": "2026-09-07T06:46:00Z", "hypothesis_id": "H-MAPENTRY1-NOTE10", "hypothesis_text": "MEASUREMENT RULE (NOTE8 corollary, binding on every future maker lane): A MAKER STRATEGY CANNOT BE VALIDATED ON A BBO TAPE. Evidence: the EXEC1 fill bracket, built and amended specifically for this purpose, reaches only 0.2308 (touch-only) / 0.4615 (touch + level-consumption) recall against 13 known real fills, against a frozen 0.6 bar (NOTE3). Root cause is structural, not a tuning failure: the dominant maker fill channel is a taker hitting our resting level, and a best-bid/best-ask tape cannot distinguish (a) a sale through our level from (b) a cancel of that level (pinned in AMEND1-NOTE1), nor can it observe queue position at all. AMEND1 added the consumption channel and moved recall 0.23 -> 0.46, i.e. the amendment worked and was still not enough. CONSEQUENCE: any lane whose entry rests in a book requires a TRADES TAPE (prints) before any fill-conditional or PnL number is published. On PMUS that means the authenticated WebSocket subscribe_trades feed; the public BBO recorder has none. Corollary for lane design: a strategy whose validation requires data we do not capture is not a strategy yet, it is a capture requirement. This rule is the reason H-MAPENTRY1 closes without a formal verdict (NOTE11) rather than with one."}
```

## ROW 3 — `H-MAPENTRY1-CLOSE1` (the closure itself)

**Why this row exists:** the lane must close on stated grounds. It is **not** a
falsification — the holdout did not falsify it — and the record must not claim
one. It closes for lack of any viable path to a verdict, plus a structural finding
that it was fishing the wrong book.

```json
{"run_ts": "2026-09-07T06:47:00Z", "hypothesis_id": "H-MAPENTRY1-CLOSE1", "hypothesis_text": "LANE CLOSURE as a live candidate, user-directed 2026-09-07. H-MAPENTRY1's entry rule is CLOSED. This is explicitly NOT a falsification and must not be recorded as one: on the pre-registered BOOKPIN1 holdout the detector was NOT falsified (HOLDOUT1: model beats book mid, Brier 0.121 vs 0.149; calibrated, ECE 0.054 vs the 0.08 bar). It closes on three stated grounds, none of which is a verdict. (i) NO PATH TO POWER: HOLDOUT1 clause (iii) stands at 39 deduped qualifying entries against a >=60 bar and its frozen window is fully resolved, so it can never power up on its own; HOLDOUT2 pre-registered an extension but the map-book change-event collapse (78.0 -> 2.7 -> 23.6 non-heartbeat events per slug, 09-04/05/06) starves it, and 0 gate-passing crossings were recorded on both 09-05 and 09-06. (ii) NO QUALIFYING INSTRUMENT: EXEC1 fails its own calibration gate at 0.4615 vs 0.6 (NOTE3) and cannot be fixed on the available data (NOTE10). (iii) STRUCTURAL: the lane trades the MAP book, which the venue-pricing cut identified as the illiquid side (p50 spread 0.07-0.12, 22% dead/one-sided, 23 change-events/slug on 09-06) while the MATCH book on the same venue runs p50 spread 0.02, ~4-5% dead, 95 change-events/slug and 78% of rows within 2 ticks. Recount 2026-09-07 across 09-03/04/06 confirms match books carry ~4x the price activity per slug at ~4-6x tighter spreads. The paper edge that motivated the lane was measured at +2.8pt/contract deduped, against a realized -61pt/trade, with the gap attributed to fill selection (NOTE5/NOTE6, -6.5pt) — a mechanism that can only be resolved with a trades tape (NOTE10). WHAT IS RETAINED: the frozen race aggregation p_map is RETIRED as a fair-value estimator (2026-09-01: gap ~= 0.9 x discount in every tercile, n=2,013/115 series — the book is ~90% right when it disagrees). q_round is NOT retired and remains calibrated live (1,984 resolved instances, pooled Brier 0.1105, skill +0.063, ECE 0.031). The band gate, second-side gate, drift-confirmation gate, wire_price convention and the EXEC1 replay machinery are retained as reusable execution components. Recorders continue running for corpus continuity (rail 4); no orders, dry_run untouched."}
```

---

## What this closure does NOT do

- does not declare a falsification the evidence does not support
- does not claim a FAIL from an uncalibrated instrument
- does not retire `q_round`, the gates, or the execution components
- does not stop any recorder (rail 4: the corpus keeps accruing)

## Companion edits, pending the same approval

- `docs/STATE.md` — mark H-MAPENTRY1 closed; retire `p_map`-as-estimator from the
  PROVEN table; add the match-vs-map structural finding
- `docs/DECISIONS.md` — D2 resolved by this closure; D1 (MODELMOMENTUM) closes
  with it under the same NOTE8 reasoning
