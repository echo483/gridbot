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

## Book liveness had to be built first

Pooling a basis over whatever books happen to be present is how H-MAPENTRY1 got a
phantom "+19pt post-shock edge": never-repricing books won 95% of that comparison
and paid nothing (NOTE8). `gridbot.analysis.book_liveness` (18 tests) classifies a
book on **three** axes, because any one alone passes something untradeable:

- **two-sidedness** — can a mid be formed at all (a `0` is this venue's dead
  placeholder, min tick being 0.01);
- **activity** — does the quote ever move;
- **width** — is the mid economically meaningful.

Width is the axis that is easiest to skip and it is the one that mattered here.
Measured over 2026-09-06/08:

| book kind | live | wide | one-sided | frozen | no data | **usable** |
|---|---|---|---|---|---|---|
| match (49) | 29 | 11 | 3 | 3 | 3 | **59.2%** |
| map (85) | 8 | 34 | 31 | 9 | 3 | **9.4%** |

Three match books held a perfect two-sided quote **completely still for ~3.5
hours across ~300 observations**. And the map-book row independently corroborates
NOTE9's decision to retire map books, arrived at by a different route: per book,
**90.6% of them cannot be traded**.

> **This also qualifies NOTE9's headline.** "Match books p50 spread 0.02" is true
> of the ROWS. Per *book*, the same tape carries median spreads of 0.31, 0.37,
> 0.45, 0.58, 0.64, 0.90, 0.92, 0.94. Pooling rows across books hides the
> untradeable minority entirely — the same shape of error as summing a per-event
> quantity from column medians.

## The measurement

46 training markets after pinning the population (below); holdout **not read**.

| coverage over 46 training markets | |
|---|---|
| intl series present | 14 (intl CS2 capture began 2026-09-07) |
| PMUS book present | 12 |
| **BOTH — a basis is computable** | **2** |

| pooled basis | all books | **live/sluggish only** |
|---|---|---|
| n | 98 | **41** |
| median \|basis\| | 3.0 ticks | **1.5 ticks** |
| p90 \|basis\| | 23.0 ticks | **2.5 ticks** |
| fraction ≥ 2 ticks | 0.68 | **0.32** |
| median intl quote age at the PMUS sample | 0.2s | 0.9s (p90 8.9s) |

**More than half the apparent basis was contamination.** One wide book
(`aec-cs2-oldm-pre-2026-09-07`, median spread **0.37 — 37 ticks**) supplied 57 of
the 98 points and doubled the median while inflating the p90 ninefold. The
classifier flags it `wide` without being told which market to distrust.

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
- **The clean number rests on ONE market.** n=41 from `aec-cs2-inox-pcific-2026-09-07`
  alone. A single market cannot establish a distribution, and no series-clustered
  interval is meaningful on a cluster count of one.
- **It nonetheless points the wrong way for the proposed cost bar, and that is
  worth saying now rather than later.** The DRAFT registration proposes clause
  (ii) as *median \|basis\| at signal ≥ 2 ticks*. The clean **unconditional**
  median is **1.5 ticks** — below it. That is not a falsification: clause (ii) is
  about the basis *at a signal*, and no signal exists yet, so the conditional
  distribution is unmeasured and could sit well above the unconditional one. But
  the burden is now explicit — the lane needs a signal that selects the tail, not
  merely a gap that exists on average.
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
3. ~~Classify dead PMUS books before any pooled basis number is quoted~~ —
   **done**, `book_liveness`, and it removed more than half the apparent basis.
4. Build the signal. The unconditional basis (1.5 ticks) does not clear the
   proposed 2-tick cost floor, so the lane lives or dies on whether a signal
   selects the tail. That is design §11 step 3's classifier, and it is the next
   real piece of strategy work.

## A reproducibility defect found and fixed in this instrument

The first version keyed each series to its **newest** mapping row. The mapper
re-snapshots every 5 minutes and a settled market drops out of `us_markets`, so
the join decayed as matches finished: the same command returned **18** joined
markets and then **8** an hour later. An instrument that answers differently each
time it runs cannot support a holdout read. The population is now pinned to the
newest mapping row that still carries `us_markets` — the market as it stood while
it was actually quoted.
