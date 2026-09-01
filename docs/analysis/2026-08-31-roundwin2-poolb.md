# roundwin-v2 Pool B — drift-first accumulation (describe-only)

Run: 2026-09-01T00:02:31Z · days: 20260826, 20260827, 20260828, 20260829, 20260830, 20260831, 20260901 · design: docs/superpowers/specs/2026-08-28-roundwin-v2-design.md (§2.2 Pool B, §2.3 protocol)

ANALYSIS LANE ONLY — describe-only metrics, no registry edit, no
arming, no trade path. Drift-first rule = the engine's own gate
(DRIFT_WINDOW=3, DRIFT_MIN_CONFIRM=0.01, fail-closed),
fed same-book oriented 120s moves strictly earlier than eval time.

**Engine-faithful dedup order** (review fix 2026-08-31): the drift
gate is evaluated over the FULL candidate population; the dedup slot
is consumed ONLY on gate-pass placement (engine.py: self.seen.add
runs on the placement path alone; drift-fail retries keep the key
live). The earlier first-row-wins pre-gate dedup made the gate
unsatisfiable and has been removed.

## Funnel (record-only gates, non-consuming)

- mapentry rows loaded: 27770
- not qualifying: 23386
- no matched book (slug/side/bid): 43
- entry price out of range: 24
- **slate rows excluded** (recording-only, engine slate gate): 779
- **F1 leak filtered** (rounds_played+1 != round_number): 0
- **candidates (Pool B base rows): 3538**
- visible same-book evidence per candidate (engine window 3): {"0": 813, "1": 162, "2": 165, "3+": 2398}

## Placement pass (dedup consumed on gate-pass only)

- band-gated (p_map outside (0.5, 0.8)): 2721
- drift unconfirmed (fail-closed): 659
- gate-passed duplicates (slot already consumed): 114
- **placed (fills-equivalent): 44** (1.2% of candidates)
- settled vs resolved map winners: 44 (excluded: {"no_grid_verdict": 0, "team_not_in_verdict": 0})

## Headline metrics (fills-equivalent, placed)

- win rate: 0.5455 (95% series-clustered bootstrap CI [0.3953, 0.6818])
- mean edge: 0.1055 (= 10.5455 pts) (95% CI [-0.0431, 0.2385])
- n=44 across 37 series, days ['2026-08-26', '2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30', '2026-08-31']

## Contrast — ungated (all candidates, no drift gate, no dedup)

- win rate: 0.6777 (95% CI [0.6142, 0.7361])
- mean edge: 0.0694 (95% CI [0.0068, 0.1261])
- n=3491

## By day (placed)

| day | n | win rate | mean edge (pts) |
|---|---|---|---|
| 2026-08-26 | 3 | 0.6667 | 35.0000 |
| 2026-08-27 | 10 | 0.6000 | 12.2000 |
| 2026-08-28 | 4 | 0.0000 | -41.0000 |
| 2026-08-29 | 2 | 0.5000 | 14.5000 |
| 2026-08-30 | 13 | 0.5385 | 6.9231 |
| 2026-08-31 | 12 | 0.6667 | 23.5000 |
## RECONCILIATION — live evidence path vs replay

Replayed **370** live `skipped:no_drift_confirm` decisions (intents files: intents-20260826.jsonl, intents-20260827.jsonl, intents-20260828.jsonl, intents-20260829.jsonl, intents-20260830.jsonl, intents-20260831.jsonl) against the postquote corpus (12918 rows) under the engine's EXACT evidence path and one-leg-at-a-time relaxants.

Live evidence path being tested: `recent_postquote_moves` — same slug+us_side book, 120s horizon ±1s, rows ordered by WRITE-time ts_ns and visible strictly before the decision, last 3 within the today+yesterday file window — then `drift_confirmed`: mean of the visible samples >= +0.010 (1.0pt), full window of 3 required, fail-closed.

| variant (one leg relaxed) | confirmed | n | empty evidence | <window | >=window | mean move of non-empty (pts) |
|---|---|---|---|---|---|---|
| exact live rule | 0/370 (0.0%) | 370 | 167 | 54 | 149 | -2.9167 |
| drop the last-3 cap (all visible samples) | 20/370 (5.4%) | 370 | 167 | 54 | 149 | -1.6009 |
| drop the 2-day file window | 0/370 (0.0%) | 370 | 167 | 54 | 149 | -2.9167 |
| pool 30/60/120s horizons | 68/370 (18.4%) | 370 | 117 | 32 | 221 | -0.9022 |
| bar 0.5pt instead of 1.0pt | 9/370 (2.4%) | 370 | 167 | 54 | 149 | -2.9167 |
| accept even a partial window (n>=1) | 16/370 (4.3%) | 370 | 167 | 54 | 149 | -2.9167 |

Under the EXACT live rule the replay confirms **0 of 370** (0.0% of replayed decisions) — see the closing paragraph for the verdict on strict-vs-unreachable.

Known asymmetry (relaxes the replay, if anything): the engine tails only the last 1MB of each day-file; the replay reads the whole file.


## Caveats

- Describe-only: no LightGBM is trained here (training is §4, requires
  registry pre-registration H-ROUNDWINV2 first — iron rail 2).
- Drift evidence = the engine's own lookup (recent_postquote_moves
  semantics): same (slug, us_side) book, 120s horizon ±1s, rows
  WRITTEN strictly before the decision (a 120s sample materializes
  ~120s after its eval, so an eval's own outcome can never be its
  own evidence), last 3 within the today+yesterday file window.
- Engine-faithful dedup means a book's drift history ACCUMULATES
  across its rejected crossings (each drift-fail keeps the key
  live), so later crossings CAN confirm — unlike the old
  first-row-wins replay where every book's only candidate was its
  history-less first crossing.
- Winners are the first-observation GRID oracle; entries on series whose
  capture has no finished map are excluded (counted), never guessed.

## Post-commit gap-check addendum (2026-08-31 ops review)

- The commit message's "1088 candidates" appears nowhere in this doc, the
  script, or the tests; the funnel above (3538 candidates → 44 placed,
  arithmetic reconciling exactly) is the number of record.
- "Engine-faithful" is scoped to band gate → drift gate → dedup ONLY. The
  live engine additionally runs REENTRY1 (up to 2 lifetime placements per
  dedup key — this replay allows exactly 1, so some of the 114
  "gate-passed duplicates" could have been real second placements), the
  second-side gate, capacity, and daily-loss gates. n=44 is therefore a
  signal-eligibility replay, not a literal live-placement count.
- Replay decision time is the mapentry row's own ts_ns (strict <); the live
  engine reads whatever is on disk at its processing instant (~2s cycle), so
  the replay can only UNDER-count evidence vs live — conservative direction,
  opposite the documented 1MB-tail asymmetry.
- The ungated contrast (n=3491) drops the BAND gate too, not just
  drift+dedup — it is not a clean drift-only ablation; band composition
  confounds it.
- book_history keys postquote rows on `slug` only; the engine also accepts
  `market_slug` as a defensive fallback (0/12,969 corpus rows currently need
  it — latent divergence only).
