# H-POSTSHOCKFADE1 — post-shock fade instrument design brief

> **AMENDMENT 2026-09-03 (ops session, from docs/analysis/2026-09-01-postshock-anchor-trace.md).**
> The design-mode anchors quoted below as "108 → 70 deduped entries / 55 series"
> and "32 UP control" are VOID: the run that produced them skipped two of the
> population gates §1.1/§1.2 name (`prev_p_map ≥ 0.40`, `not ended_by_restart`)
> and never pinned its corpus state. The spec-literal funnel is the reference:
> on the pinned anchor corpus (seven day files 08-26→09-01, 09-01 truncated to
> rows with ts_ns ≤ 1788258303057068175; 28,471 rows) it yields **67 deduped
> candidate entries / 53 series** and **37 / 34 UP control**; on the full corpus
> as of 2026-09-01 (31,127 rows) **72 / 58** and **38 / 35**. Clause 1 is
> pinned to the shock row ITSELF, clause 3 to the NEXT live quote, and the UP
> population mirrors the `prev_p_map ≥ 0.40` gate (stated below). Nothing else
> in this brief changes; the instrument (scripts/postshock_fade.py, WIP) must be
> reconciled to these numbers before any of its output is read.


Author: gridbot-reasoner (hard-reasoning lane), 2026-09-01. Status: DESIGN ONLY.
No registry edit (the JSON in §3 is a draft pending user sign-off), no src/,
no code beyond read-only verification runs cited below, no secrets.
Buildable tomorrow by a glm/deepseek lane without further design.

## Why this instrument exists (and why it is suspect from line one)

Tonight's H-CROSSIN1 run killed the slow-decay read: the +16.2pt mid-priced
fallen-favorite edge prices NEGATIVE at executable ask with persistence at the
300s scale — above-band settled edge −0.047 CI [−0.146, +0.048], re-run with
the terminal cutoff −0.031 CI [−0.124, +0.061]
(docs/analysis/2026-08-31-crossin-design.md, §Design-mode arms + ops addendum;
F1 settlement-cutoff verified empty, so the collapse is clean, not a leak
artifact). That falsifies mechanisms A/B (slow behavioral decay, favorite–
longshot bias / retail momentum-chasing) from the amendment memo
(docs/specs/2026-08-31-amendment-drafts.md Part 2) at the 300s
scale, and points at mechanism C: MM withdrawal / stale tops with a
fast-converging gap — latency-race territory, which is our actual edge claim
(13–27s GRID lead, docs/STATE.md line 17; 8.5s median signal→ledger pipe,
docs/STATE.md lines 275–276).

The measured signature (STATE.md item 11d; scripts/postshock_lag.py):
DOWN shocks leave the book ABOVE the model (+15.1c median gap on the 08-27
corpus, n=230 shocks / 52 priced), converging in ~29s median at 44% rate;
UP shocks only +3.2c. A SHORT-side fade of DOWN shocks sells the stale high
book and rides the convergence. The amendment memo's own ranked list called
this "CHASE, small" (Part 2 item 2) and warned: window ≈ 29s, pipe eats a
third of it, n=52 is thin.

Two additional bad-news anchors this design must carry, not decorate:

1. The pcal population finding (docs/analysis/2026-08-31-pcal-population-finding.md):
   conditioning on book-vs-model disagreement of ≥4pts moves the armed-band
   calibration gap from +0.049 (all evals, n=7,677) to +0.196 (qualifying,
   n=399). A post-shock fade CONDITIONS ON EXACTLY SUCH A DISAGREEMENT — the
   shock is the model repricing hard, and the entry is the book disagreeing
   with the new model price by 10–50pts. That disagreement is ~15pts of
   evidence that the MODEL is wrong, not the market. This is the single most
   dangerous confound in the instrument and §3 makes it a bar clause.

2. Night-3 splits (STATE.md item 11e): both losing shorts were mid-band
   (~0.59 fair) model-flip fades entered mid-map; the XDM-NASE loss was
   entered within 10 min of a ≥25pt shock. The naive version of this trade
   has ALREADY LOST live money. entry_quality.py's n=45 follow-up then
   contradicted the fills read, so nothing was registered — but the
   live-loss precedent stands as the prior to beat.

---

## 1. INSTRUMENT SPEC — H-POSTSHOCKFADE1 (candidate: SHORT-side fade of DOWN shocks)

### 1.1 Shock definition (frozen constants)

Reuse scripts/postshock_lag.py's pinned definitions verbatim where sound;
deviations are named.

- Source rows: `data/shadow/mapentry-*.jsonl` UTC day files, all rows with
  `type=="mapentry"` (any `src_hypothesis_id`; slate rows INCLUDED here —
  postshock_lag.py already sequences on numeric `p_map` only and slate rows
  carry it; deviation from CROSSIN1's slate exclusion, named below).
- Sequence key: (input file, series_id, map_number, team), ordered by ts_ns
  (postshock_lag.py `_seq_key`/`_norm_key_part`; the per-file provenance tag
  is kept — never bridge capture holes).
- SHOCK fires at row t when |p_map(t) − p_map(prev)| ≥ 0.25, prev = previous
  valid-p_map row of the sequence. Direction from sign: "down" = p_map fell.
  Constants: --shock 0.25, --window-s 300, --converge 0.05 — all reused
  unchanged from postshock_lag.py DEFAULT_SHOCK/DEFAULT_WINDOW_S/
  DEFAULT_CONVERGE (lines 83–85).
- At most one active window per sequence; a new shock RESTARTS the window
  (ended_by_restart flagged). REUSED, but with a NEW frozen rule for the
  instrument: shocks flagged ended_by_restart are EXCLUDED from the entry
  population (a re-shock inside 300s means the state is still moving under
  us; entering the first shock's gap is trading into a second repricing).
  Reason: 54/409 full-corpus DOWN priced shocks ended by restart — material
  contamination, and the restart row itself re-defines the "fair" the gap is
  measured against.
- DEVIATION 1 (named): FALLEN-FAVORITE filter — only DOWN shocks with
  prev_p_map ≥ 0.40 enter. Reason: this instrument is the SHORT-side sibling
  of CROSSIN1's above-band fallen-favorite arm; a shock from 0.15 to 0.05 is
  a map already decided, not a favorite falling. Measured consequence on the
  design corpus: 415 → 394 priced DOWN shocks (verified run, 2026-09-01).
- DEVIATION 2 (named): the entry quote is the shock row's OWN book
  (bid/ask/p_map on row t), not a later same-slug quote. Reason: the
  mechanism (C) says the stale top is AT the shock; waiting for the next
  evaluation row (~23s median cadence) is waiting out the convergence itself.
  The shock row carries bid/ask when the book was live at detection
  (mapentry.py writes `bid`/`ask` from parse_bbo, src/gridbot/instruments/
  mapentry.py:196–212, 391–392), so no new capture is needed.

### 1.2 Entry rule (SHORT-side fade, EXECUTABLE prices)

What is posted/paid. The shocked team's map book is the one where the
favorite just fell: after a DOWN shock p_map is low, and the STALE book still
prices the favorite high (gap = bid − p_map > 0). Fading it = SHORT the
favorite = on the favorite-named book, buy the No leg. Executable price =
1 − bid (mapentry.py `side_ask`, lines 303–307: short pays 1 − bid; a short
on an opponent-named book buys the No leg — same convention CROSSIN1 froze,
docs/analysis/2026-08-31-crossin-design.md §Method).

- Candidate entry condition (all frozen):
  1. DOWN shock, prev_p_map ≥ 0.40, not ended_by_restart, market_slug present,
     shock-row book alive (bid and ask numeric, > 0, ask > bid on the shock
     row ITSELF — row t's own fields, never a later quote; zero prices are
     dead-book placeholders per mapentry.py parse_bbo). [pinned 2026-09-03]
  2. STALENESS GATE: shock-row gap bid − p_map ≥ 0.10 (10 book points).
     Rationale: 08-27 corpus DOWN median gap was +15.1c (STATE.md 11d);
     0.10 is below the median (admits the typical episode) but above the
     noise band around the 0.05 converge constant. This is a SIZE threshold
     on the stale top, pinned at 10pt.
  3. PERSISTENCE/VALIDITY: the NEXT same-sequence live quote (strictly after
     the shock row, within 45s of the shock ts_ns; live = bid and ask numeric,
     > 0, ask > bid) must show bid − p_map ≥ 0.05 (still stale, half the
     entry gate). Failed persistence ⇒ no entry. This kills single-evaluation
     phantom tops and is the crossin-style persistence filter scaled to the
     29s convergence window. Measured cost (spec-literal, pinned anchor
     corpus): 162 gate-passing → 83 persisted → 67 deduped / 53 series
     (the earlier "108 → 70" figure is VOID — see the 2026-09-03 amendment).
  4. TIMING OFFSETS (pinned to the ~29s convergence and the 8.5s pipe):
     - Decision anchor = shock row ts_ns (grid_ts_ns-carried detection).
     - Wire latency budget: place within +8.5s of detection (median
       signal→ledger latency, STATE.md lines 275–276; place 394ms + cancel
       137ms wire baseline, STATE.md line 28).
     - Maker leg: post at (1 − bid) − 0.01 (one tick inside the stale No-ask
       equivalent) with TTL 150s (the standing MAKER_TTL_S).
     - Taker leg (shadow accounting only): pay 1 − bid at detection.
     - The convergence window is ~29s median (08-27 DOWN ttc 28.6s,
       postshock-20260827.json by_direction.down) and the pipe eats 8.5s of
       it ⇒ usable decision budget ≈ 20s. Any fill or mark taken later than
       shock+30s is OUTSIDE the mechanism and is excluded from edge
       accounting (offset cap pinned at +30s).
  5. Settlement cutoff: exclude any mark/quote with ts_ns after the
     grid_winners.py oracle's map-terminal observation for that
     (series_id, map_number) — the CROSSIN1 F1 pin, applied identically
     (docs/specs/2026-08-31-amendment-drafts.md Part 3 F1).
- Maker vs taker economics, BOTH computed, cost-complete:
  - TAKER: entry at 1 − bid (cross our-side spread); settled edge = won −
    entry_price, won ∈ {0,1} from the oracle.
  - MAKER: post at (1 − bid) − 0.01; fill inferred per the NOTE6 fill proxy
    (any later snapshot within TTL 150s showing our side's effective ask ≤
    posted price; registry H-MAPENTRY1-NOTE6) — AND reported with the
    measured −6.5pt fill-selection cost flagged (NOTE6: filled EV −3.5pt vs
    unfilled +7.5pt at identical price; the paper edge lived entirely in
    orders that never fill). The maker leg is describe-only at verdict time;
    the taker leg carries the bar (conservative: the maker leg can only look
    BETTER than reality due to fill selection).
- Dedup: ONE entry per (series_id, map_number, market_slug) — earliest
  qualifying shock wins the slot; persistence-failed shocks do NOT consume
  the slot (CROSSIN1 F5 reading, frozen here verbatim).

### 1.3 Negative-control arm

UP shocks, same plumbing mirrored: LONG the riser, executable price = ask
(the favorite's ask after p_map rose), gate ask-gap p_map − ask ≥ 0.10,
persistence p_map − ask ≥ 0.05 within 45s, same dedup/settlement/offsets.
UP POPULATION (pinned 2026-09-03): the §1.2 clause-1 gates mirror verbatim —
prev_p_map ≥ 0.40 (the riser started as a plausible favorite), not
ended_by_restart, market_slug present, shock-row book alive on the row itself.
Expected ≈ no edge (08-27 UP gap was only +3.2c, STATE.md 11d — the book
nearly caught up). Spec-literal design-corpus supply: 37 deduped UP control
entries / 34 series (anchor corpus; 38 / 35 on the 09-01 full corpus) vs
67 candidate — adequate, not generous (the earlier "32 / 48" is VOID). The two arm CIs are emitted and NOTHING ELSE (CROSSIN1 F7: no
day/distance splits as verdict inputs).

### 1.4 Resolution and statistics

- Resolution: scripts/grid_winners.py first-observation map-winner oracle
  over data/grid/series_state-*.jsonl (MR12-validated, ambiguous finals
  excluded; subprocess, pure consumer — phase-2's exact pattern). WON iff the
  shocked team LOST the map (we shorted them); unresolved maps excluded and
  counted, never guessed.
- CIs: per-arm win rate, mean settled edge, series-clustered bootstrap
  (resample series_id, 1000 reps, seed 17 — continuity with CROSSIN1's
  seed 17 / phase-2's seed 16 lineage; NEVER row-pooled). Report series
  count beside every CI (F6).

### 1.5 Holdout discipline (BOOKPIN1)

- Holdout definition identical to H-MAPENTRY1-BOOKPIN1 (registry 2026-08-31,
  docs/analysis/2026-08-31-bookpin1-holdout-accrual.md): series whose FIRST
  mapping row has ts_ns ≥ 1788206026000000000 (2026-08-31T19:53:46Z).
  Verified live now: 40 holdout series exist in data/pmus/mappings-*.jsonl.
- DESIGN mode (default): pre-holdout series only — what this doc's numbers
  come from. No verdict.
- HOLDOUT mode: refuses to print metrics below the N-FLOOR, pinned here:
  ≥ 40 resolved DEDUPED candidate entries AND ≥ 15 distinct holdout series
  (mirrors CROSSIN1's ≥60/≥20 scaled to this thinner supply: ~70 design-mode
  entries across 7 days ≈ 10/day ⇒ 40 entries ≈ 4 holdout days at full
  accrual). Shortfall = INCONCLUSIVE_STARVED, reported with the accrual
  count.

---

## 2. MECHANISM C DISCRIMINATOR — spread-widening test (describe-only, no amendment)

Mechanism C predicts the stale top is an MM WITHDRAWAL, not a slow belief
update. The discriminator (amendment memo Part 2, mechanism C, falsifiable
prediction): during DOWN-shock gap episodes, bestAsk − bestBid on the
shocked slug must RISE above the slug's own median and RE-TIGHTEN at
convergence. If spreads stay flat through the gap, it is belief-lag (A/B),
not withdrawal (C) — and the 300s CROSSIN window, not the 29s window, would
have been the right instrument after all.

Pinned exactly so an analysis lane runs it verbatim:

- Rows: same mapentry day files; fields per row: `bid`, `ask` (long-side
  BBO, mapentry.py:391–392), `series_id`, `map_number`, `team`,
  `market_slug`, `ts_ns`, `p_map`, `src_ts_ns`.
- Episodes: every DOWN shock from scripts/postshock_lag.py defaults
  (--shock 0.25 --window-s 300 --converge 0.05) with market_slug present
  and n_book_obs > 0 (554 with-book shocks full corpus; 409 DOWN-priced).
- Baseline: the slug's own spread median computed from ALL its two-sided
  live quotes (bid,ask both > 0, ask > bid) in the ENTIRE corpus
  (corpus-wide median ~0.08–0.10 in verified runs; queue15 measured 0.08
  corpus-wide, docs/specs/2026-08-31-amendment-drafts.md Part 2).
- Windows per episode (offsets from shock ts_ns):
  - PRE: [−300s, 0) — baseline-of-baselines sanity.
  - GAP: [0, +30s) — the stale-top window (≈29s convergence).
  - RECONVERGE: [+30s, +300s].
- Metric per episode: median(spread) in GAP and RECONVERGE vs the slug
  baseline median; episode-level ratios, then pooled medians + series-
  clustered CIs (seed 17).
- Confirmation pattern for C: median GAP ratio > 1 (widening at the gap) AND
  median RECONVERGE ratio < GAP ratio (re-tightening), with the paired
  GAP-vs-PRE difference CI excluding zero on the positive side.
- Null pattern (belief-lag): ratios ≈ 1 throughout. Report both directions;
  this join is describe-only and cannot arm anything.
- Caveats to print with the result: spread is only observable when BOTH
  sides are quoted (dead one-sided placeholders excluded), so the test is
  conditional on the book being alive — the same observability selection
  CROSSIN1 F2 flagged; and a withdrawing MM may widen by pulling the BID
  (which is exactly the side we'd sell into), so also report the one-sided
  death rate per window (share of rows losing bid vs ask).

---

## 3. FROZEN-BAR DRAFT — registry row (house style; SIGN-OFF PENDING, not appended)

```json
{"run_ts": "<REGISTRATION_TS_UTC>", "hypothesis_id": "H-POSTSHOCKFADE1", "status": "REGISTERED_NO_RUN", "hypothesis_text": "Post-shock fade: SHORT-side fade of DOWN model shocks (mechanism C, MM withdrawal / stale tops). MECHANISM CLAIM: when the model reprices a fallen favorite DOWN by >=0.25 in one step, the venue's book top lags the repricing (stale high bid on the favorite), the gap is a stale-quote artifact of market-maker withdrawal rather than a slow belief update, and selling the stale top converges toward the new model price inside ~29s; the fade captures that convergence at executable prices. The CROSSIN1 300s executable-price result (edge -0.031 CI [-0.124,+0.061], docs/analysis/2026-08-31-crossin-design.md) already FALSIFIED the slow-decay mechanisms A/B for this family; this instrument tests ONLY the fast mechanism C, and its window, offsets, and gates are sized to it. CANDIDATE RULE, frozen exactly: population = DOWN shocks (|p_map step| >= 0.25 vs previous valid-p_map row, per scripts/postshock_lag.py defaults) on mapentry rows (data/shadow/mapentry-*.jsonl) with prev_p_map >= 0.40 (fallen-favorite filter), not ended_by_restart, market_slug present, shock-row book alive (bid and ask numeric, >0, ask>bid), on BOOKPIN1 holdout series for the verdict; staleness gate bid - p_map >= 0.10 at the shock row; persistence = the next same-sequence live quote within 45s also shows bid - p_map >= 0.05, else no entry; ENTRY PRICE (taker mark) = 1 - bid at the shock row (short the favorite = buy No leg, mapentry.py side_ask convention); maker shadow leg posts (1-bid)-0.01 with the 150s TTL fill proxy and the measured -6.5pt fill-selection cost reported, never inherited into the taker bar; offset cap +30s from shock ts_ns (the ~29s convergence; later marks excluded); quote rows after the grid_winners.py oracle's map-terminal observation for the (series_id, map_number) are excluded as settlement-informed. Dedup one entry per (series_id, map_number, market_slug), earliest qualifying shock wins; persistence-failed shocks do not consume the slot. NEGATIVE CONTROL (pre-registered): UP shocks mirrored - LONG entry at ask with p_map - ask >= 0.10 gate and >= 0.05 persistence, identical offsets/dedup/settlement; expected edge <= 0 (08-27 UP gap was +3.2c vs DOWN +15.1c, STATE.md 11d). Resolution = scripts/grid_winners.py first-observation map-winner oracle (MR12-validated, ambiguous excluded), WON iff the shocked team lost the map; unresolved excluded, never guessed. Statistics = per-arm win rate and mean settled edge with series-clustered bootstrap (resample series_id, 1000 reps, 95% percentile, seed 17), series count printed beside every CI; the two arm CIs are the only verdict outputs. MODEL-ERROR CONFOUND (addressed explicitly, per docs/analysis/2026-08-31-pcal-population-finding.md: book-vs-model disagreement of >=4pts is ~15pts of evidence AGAINST p_map, and this entry conditions on a 10-50pt disagreement): clause (iii) below requires the edge to survive stratification by staleness-gate terciles of (bid - p_map) at entry; if any tercile with >= 20 resolved entries shows edge CI upper bound <= 0 while the pooled CI excludes zero positively, the verdict is FALSIFIED (the edge was concentrated where the model was most contradicted = mechanism D carrying it). FROZEN BAR, evaluated on the BOOKPIN1 holdout (series first-mapped at/after 2026-08-31T19:53:46Z): PASS requires ALL of: (i) >= 40 resolved deduped candidate entries on >= 15 distinct holdout series (else INCONCLUSIVE_STARVED with accrual count printed); (ii) candidate mean settled edge (taker, 1-bid) > 0 with the series-clustered 95% CI lower bound > 0; (iii) staleness-tercile robustness clause above holds; (iv) control arm (UP shocks) mean settled edge CI upper bound <= +0.02 (the asymmetry must be direction-specific, else both arms share a market-wide bias). Any of (ii)-(iv) missed at the n floor = FALSIFIED. SCOPE RAIL - TWO-KEY DISCIPLINE: a PASS licenses NOTHING in the trade path by itself; arming requires a separate pre-registered amendment citing this verdict by run_ts, and dry_run=True until then (iron rail 3). DESCRIBE-ONLY (never verdict inputs): the spread-widening mechanism-C join (this spec section 2), maker-leg EV, per-day tables, q_round staleness at entry (q_round is recorded on every mapentry row; a future amendment may condition on |shock delta| vs q_round step once n allows - at the pinned n floor the joint stratification is not estimable, stated plainly here rather than smuggled in).", "evidence": "docs/analysis/2026-08-31-crossin-design.md; docs/analysis/2026-08-31-pcal-population-finding.md; docs/STATE.md items 11d/11e; data/analysis/postshock-20260827.json; docs/specs/2026-08-31-amendment-drafts.md Part 2", "registry_rule": "new instrument; no engine change; H-MAPENTRY1 recording population and all other frozen bars unchanged"}
```

Bar-clause notes (why these numbers):
- (i) n-floor 40/15: design mode produced **67 deduped candidate entries /
  53 series** over the pinned design corpus (seven day files 08-26→09-01,
  09-01 truncated to its state at 2026-09-01T10:25:03Z — rows with ts_ns ≤
  1788258303057068175; 28,471 rows; spec-literal gates) and 37 / 34 UP
  control entries; 72 / 58 and 38 / 35 on the 09-01 full corpus. The earlier
  108/70 anchor was computed on a pool MISSING the prev_p_map ≥ 0.40 and
  ended_by_restart gates (verification bug,
  docs/analysis/2026-09-01-postshock-anchor-trace.md) and is voided;
  holdout accrual runs ~40 new series/day
  (docs/analysis/2026-08-31-bookpin1-holdout-accrual.md) so the floor is
  reachable in days, not months, but is not a formality.
- (ii) lower-bound > 0 (not point estimate > 0): the CROSSIN1 lesson —
  point estimates flip sign between mid-priced and executable pricing;
  only a CI that excludes zero survives that bias regime. The instrument's
  own coarse-quantization bias (entry book observed at the shock row, not at
  the true repricing instant) cuts AGAINST the candidate (the true stale ask
  was likely higher than the row we see), so a positive lower bound is
  conservative-consistent.
- (iv) control ≤ +0.02: mirrors CROSSIN1's pre-declared-negative-control
  discipline; the memo's mechanism-C prediction is direction-specific
  (DOWN gap +15.1c vs UP +3.2c).

---

## 4. KILL CRITERIA / honest priors — why this is probably another mirage

1. The 44%-converge number is the headline, and the 56% is the kill shot.
   Only 44% of 08-27 DOWN-priced shocks converge within the 300s window
   (postshock-20260827.json: down converge_rate 0.436, 17/39); on the full
   08-26→09-01 corpus it is WORSE — 25.2% (103/409 DOWN priced), with a DOWN
   median ttc of 67s, not 29s (verified re-run, 2026-09-01). The 29s median
   is the median OF THE CONVERGERS. For the non-convergers (75% on the full
   corpus): median gap at window end is still +0.282 with 82% of them still
   above the model — the adverse tail. A fade that sells into a gap that
   never closes loses the full gap, and the gap at shock for non-convergers
   (median +0.333) is LARGER than for convergers (+0.036) — the entry gate
   (bid − p_map ≥ 0.10) preferentially selects exactly the episodes that
   historically did NOT converge. This is the single most damning number in
   this brief and it is pinned into the prior: the instrument is buying
   (selling) the tail it cannot see through. If the gap is a model error
   (mechanism D / pcal finding), the non-converging majority is the market
   being RIGHT and staying right.
2. Mid-price mirage precedent, twice over. (a) CROSSIN1: +16.2pt mid-priced
   edge → −3.1pt at executable ask (docs/analysis/2026-08-31-crossin-design.md);
   this instrument's taker leg has the identical mid→executable failure mode
   baked in, and its maker leg inherits the measured −6.5pt fill-selection
   cost (H-MAPENTRY1-NOTE6). (b) priced-in test: post-quote drift after
   model-discounted states is +0.5–1pt per 120s against 20pt+ discounts
   (docs/analysis/2026-08-28-priced-in-test.md) — the drift could never
   repay the discount, i.e. the market was not slow, we were late-wrong. A
   29s window makes the drift-repayment arithmetic even tighter.
3. Night-3 live precedent. Both losing shorts were mid-band model-flip
   fades entered mid-map — this exact trade shape, already lost at $3 stakes
   (STATE.md 11e(b)); the XDM-NASE loss sat within 10 min of a ≥25pt shock
   (11e(c)). The naive version LOST. entry_quality.py's n=45 follow-up
   contradicted the fills read so no amendment was registered — meaning the
   loss evidence is real but unexplained, which is the worst state: the
   prior is adverse and we don't know why.
4. Observability selection is unfixable from disk (CROSSIN1 F2 carried
   forward): a priced shock exists only if a live book quote exists at the
   shock row; 52/230 (08-27) and 554/1065 (full corpus) shocks were priced
   at all. The instrument conditions on the book being alive exactly when
   mechanism C says it is being withdrawn. Bias direction unknown; it cuts
   against generalization to the live path regardless of sign.
5. Coarse-quantization bias, arm-asymmetric analogue (CROSSIN1 F4): the
   shock-row book is sampled at evaluation cadence, not at the repricing
   instant; here it likely understates the true stale ask (conservative for
   the candidate) — but it also means the +30s offset cap is enforced
   against a clock whose zero is itself fuzzy by up to one cadence interval.
6. PERMANENT-KILL result (what closes the lane, not just starves it): any
   of — (a) holdout candidate edge CI upper bound ≤ 0 at the n floor
   (FALSIFIED, clause ii); (b) edge concentrated entirely in the largest
   staleness tercile while the smallest-tercile CI excludes zero negatively
   (mechanism D verdict, clause iii — the pcal finding made flesh); (c) UP
   control matches candidate within CIs (market-wide bias, clause iv); (d)
   the spread-widening join shows flat spreads through the gap (belief-lag,
   not withdrawal — mechanism C is not the mechanism, and A/B are already
   dead from CROSSIN1, leaving no mechanism for this family). Any one of
   these closes H-POSTSHOCKFADE1 permanently absent a NEW mechanism claim
   (iron rail 2). INCONCLUSIVE_STARVED is not a kill — it is the lane
   waiting; at ~10 candidate entries/day pre-holdout, starvation would be
   surprising, which is itself a warning: if the holdout accrues entries
   FASTER than design mode predicts, the entry gate is looser than intended.

What is NOT demonstrated anywhere in this brief: any holdout evidence (every
number above is design-mode, pre-holdout, describe-only), that the 29s
convergence transfers past 08-27's regime (full-corpus DOWN ttc is 67s at
25% rate — it may already not), that the gap is market error rather than
model error (the pcal finding says bet against the model), and that our
8.5s pipe can actually get an order onto a stale top before it clears
(13–27s lead minus 8.5s pipe leaves ~5–18s effective, amendment memo Part 2
timing math, against a converger population whose fastest deciles converge
in <10s: 44/103 full-corpus convergers close within 10s — a race we may
already lose at the median). Bad news is the product: the prior here is
death, and the instrument exists to make that death cheap, pre-registered,
and unambiguous.

— gridbot-reasoner, 2026-09-01. Verification runs performed this session
(read-only, no writes beyond this file): postshock_lag.detect() over all
seven mapentry day files (28,471 rows → 1,065 shocks / 554 priced / 409
DOWN-priced); design-mode entry pool with the pinned gates (108 no-persist →
70 persisted deduped candidate entries, 55 series; 32 UP control);
postshock-20260827.json re-read; mappings holdout count = 40 series.
