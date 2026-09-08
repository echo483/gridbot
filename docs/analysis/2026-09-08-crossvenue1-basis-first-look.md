# First intl-vs-PMUS basis: the instrument works, the coverage does not

**2026-09-08 · overnight loop · TRAINING SERIES ONLY · no edge claim, no PnL,
no verdict.** H-CROSSVENUE1 is not pre-registered (iron rail 2). This measures
the distribution of a price difference and nothing else.

Reproduce: `uv run python scripts/crossvenue_basis.py`

## What was built

`gridbot.analysis.crossvenue_basis` (23 tests) — an as-of join between the two
venues with three properties that are the whole point of having a module rather
than a script:

1. **The sign is settled before the subtraction.** Every point goes through
   `intl_price_as_pmus_yes`, which raises on an unresolved orientation. NOTE7 is
   why: 15 of 24 shorts were venue-rejected from this class of error while the
   ledger read "placed".
2. **The join is backward-looking on the PMUS clock.** For each PMUS
   observation, what intl believed *at or before* that instant. `as_of` returns
   `None` rather than reaching forward, and a test pins that.
3. **The holdout gate raises, it does not filter.** `require_training_only`
   fails **closed** — a series whose first-mapped time is unknown is holdout, not
   training — and refuses the whole call rather than silently dropping rows. A
   silent filter teaches its caller nothing and the next caller repeats the
   mistake.

## The measurement

18 markets join; 14 are training, and the 4 holdout series were **not read**.

| | |
|---|---|
| training markets with **any** PMUS overlap | **2 of 14** |
| pooled basis points | **98** |
| median \|basis\| | **3.0 ticks** |
| p90 \|basis\| | 23.0 ticks |
| fraction ≥ 2 ticks | 0.68 |
| median intl quote age at the PMUS sample | **0.2s** (p90 8.2s) |

## What this actually shows

**The coverage problem dominates every other quantity here.** Per market, intl
supplied 6,661–173,487 events while PMUS supplied 0–85 observations, and twelve
of fourteen markets produced *no overlap at all*. That is the readiness finding
(`docs/analysis/2026-09-08-crossvenue1-readiness.md`) reappearing as a hard
constraint on the lane's own instrument: you cannot price a basis against a venue
you never observed.

The channel asymmetry is now measured rather than assumed. intl arrives as a
WebSocket stream — event-driven, complete, and **0.2s stale at the median** when
we sample PMUS. PMUS is polled, and per `H-MAPENTRY1-NOTE11`/`NOTE12` its price
series cannot see an execution that does not move the touch. The thesis needs the
fast side to be fast; it is.

## What this does NOT show

- **No edge, and not even a credible basis distribution.** n=98 from **two**
  markets is not a sample; it is an existence proof that the pipeline runs
  end-to-end. Nothing here should be quoted as "the basis is 3 ticks".
- **One of the two markets is suspect.** `aec-cs2-oldm-pre-2026-09-07` shows a
  median \|basis\| of **12.5 ticks with 95% of points ≥2 ticks**, which is what a
  **dead or never-repricing PMUS book** looks like, not what an exploitable gap
  looks like. NOTE8 was written about exactly this artifact: a stale book wins
  every comparison against a live one and pays nothing. Until that market is
  classified, it must not be pooled with the other; the 3.0-tick pooled median is
  reported here only because suppressing it would be worse, and it is not to be
  used.
- **No cost model.** A basis is not an edge until it clears the spread it must
  cross plus the fill probability of resting inside it (NOTE8, NOTE9).
- **Nothing about the holdout.** Untouched, by construction and by test.

## What it changes

Design §11 step 3 ("classifier + basis instrument on training data") is now
*buildable* but not yet *powered*. The gating item is unchanged and is the same
one the readiness doc names: **PMUS coverage**. Concretely —

1. Enable `config.PMUS_PREMATCH_BOOKS` and decide `MAX_SLUGS_PER_CYCLE`
   (readiness §6). The 12 zero-coverage markets are the return on that decision.
2. Re-run this script after several full days of capture. The instrument does not
   need to change; only the data under it does.
3. Classify dead PMUS books **before** any pooled basis number is quoted, or
   NOTE8's artifact walks straight back in.
