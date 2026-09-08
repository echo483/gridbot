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

### What this says about the lane that already closed

Extending the classifier to the whole book tape (2026-09-03…09-08, per slug-day):

| book kind | n | live | wide | one-sided | frozen | no data | **usable** |
|---|---|---|---|---|---|---|---|
| match | 98 | 63 | 13 | 6 | 11 | 5 | **64.3%** |
| map | 159 | 29 | 62 | 38 | 22 | 8 | **18.2%** |

`H-MAPENTRY1-CLOSE1` closed that lane partly because it traded the **map** book,
citing *"22% dead/one-sided"*. That 22% was the **row-level** one-sided rule and
counted neither frozen nor pathologically wide books. Per book the untradeable
fraction is **81.8%**. The closure was right and **understated by roughly four
times** — corroboration, not a reversal — and it supplies a mechanism for the
lane's central symptom, a paper edge that lived entirely in orders that never
filled. You cannot fill against a book that never moves. (`H-MAPENTRY1-NOTE13`.)

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

---

# Addendum: does PMUS actually follow intl?

`gridbot.analysis.crossvenue_response` (18 tests) ·
reproduce: `uv run python scripts/crossvenue_response.py --horizon-sweep`

Every other piece of this lane is plumbing for one assertion. Each frozen intl
event (≥2 ticks / 30s) is now labelled by what PMUS did around it:

| label | meaning | |
|---|---|---|
| **STALE** | intl moved, PMUS had not, PMUS followed inside the horizon | the target |
| **INFORMED** | PMUS had **already** moved that way before the event fired | the pick-off |
| **NO_FOLLOW** | PMUS never moved that way inside the horizon | no convergence |

`INFORMED` is what keeps this honest. A study asking only *"does PMUS eventually
follow"* scores a pick-off as a success — and that pick-off is the **−6.5pt
fill-selection cost (NOTE6)** in observable form.

## First read — one usable book, n=11

| | |
|---|---|
| STALE / INFORMED / NO_FOLLOW | **4 / 1 / 6** |
| median \|basis\| at STALE events | **2.0 ticks** (50% ≥ 2t) |
| median follow lag | **62.3s** (p90 141.7s) |

Two things in that table matter more than the counts.

**NO_FOLLOW is the modal outcome, and it is not a truncation artifact.** Stretching
the horizon from 150s to 3600s — **24×** — converts exactly **one** NO_FOLLOW into
STALE (4→5). The gap opens and simply does not close. A maker waiting for
convergence is waiting for something that mostly does not arrive.

**When PMUS does follow, it follows slowly enough to trade.** Median 62.3s against
the engine's own 150s maker order lifetime, so a resting order is still alive. The
p90 of 141.7s sits right at that limit. This half of the mechanism looks fine.

## What it does not establish, and one alternative it cannot yet rule out

- **n=11, one market, one label away from a different story.** Nothing here
  decides anything. It is the instrument working end to end, and a first shape.
- **NO_FOLLOW has (at least) two readings and this instrument cannot separate
  them.** Either PMUS is stale and never corrects — bad for a maker but the gap is
  real — or **intl overshot and reverted**, in which case PMUS was right all along
  and there was never a gap to take. Distinguishing them requires asking whether
  *intl* reverts after an unfollowed event. That test is cheap, is the obvious
  next instrument, and until it is run NO_FOLLOW should not be read as evidence
  for or against the thesis.
- **The `wide` book showed 9/25 INFORMED (36%) against 1/11 (9%) on the live
  book.** Consistent with wide books being adversely selected, and equally
  consistent with the wide book's mid being noise. Not evidence either way; it is
  reported because suppressing it would be worse.
- **A followed gap is not a filled order.** Fills come from `trade_prints`
  (NOTE11/NOTE12), and nothing here models one.

---

# Addendum 2: intl moves substantially unwind — n=229

`gridbot.analysis.crossvenue_signal.move_persistence` ·
`uv run python scripts/crossvenue_signal_rate.py --persistence`

Addendum 1 left NO_FOLLOW with two readings it could not separate: PMUS stale and
never correcting, or **intl overshooting and reverting**. That test needs no PMUS
book, so it runs on the whole intl population — **229 events across 14 training
markets**, roughly twenty times the sample of the response test.

Retrace is signed so direction is already handled: **0.0** = the move fully held,
**1.0** = the mid returned to where the move started, **negative** = the move
continued further.

| horizon | n | median retrace | ≥half reverted | held (≤0) |
|---|---|---|---|---|
| 30s | 227 | 0.250 | 0.414 | 0.410 |
| 60s | 226 | 0.286 | 0.447 | 0.403 |
| **150s** (order TTL) | 221 | **0.321** | 0.462 | 0.367 |
| 300s | 216 | 0.400 | 0.491 | 0.384 |
| 900s | 191 | **0.538** | 0.524 | 0.393 |

**A ≥2-tick intl repricing is substantially noise.** Inside the engine's own 150s
order lifetime the median move has already given back **32%** of itself, and by
15 minutes it has given back **54%**. Only ~37–41% of moves hold at all.

## What this does to the earlier reads

- **NO_FOLLOW is no longer readable as "PMUS is stale".** If intl gives back a
  third of the move within the order's lifetime, then in a substantial share of
  cases PMUS *not* following was PMUS being **right**. The two readings are still
  not fully separated per-event, but the population is now known to contain a lot
  of the second one.
- **The basis at signal overstates the tradeable gap.** Clause (ii) proposes
  `median |basis| at signal ≥ 2 ticks`. Whatever that number turns out to be, a
  third of it is expected to evaporate before a 150s maker order expires — so the
  bar should be read against the *decayed* gap, not the gap at the event.
- Larger moves persist better (**median retrace 0.297 at ≥5 ticks vs 0.400 below
  it**), which is the expected direction: more size, more information.

## Stated limits

- **This is mid-based, and bid-ask bounce is a live confound.** A 2-tick "move"
  of the mid on a 2-tick-wide book can be one side of the book flickering. That
  is the most likely reason small moves revert more than large ones, and it means
  the small-move retrace is an upper bound on genuine reversion. Distinguishing
  them needs the intl depth, not just the touch.
- **It argues for a higher threshold, and the threshold is NOT being changed.**
  ≥5 ticks persists better, and moving the frozen trigger now — after seeing a
  measurement — is precisely the tuning the freeze exists to prevent. It is
  recorded as a decision for the user **before** registration, not taken here.
- No PMUS book is read anywhere in this addendum, and no edge is claimed.

---

# Addendum 3: the reversion was mostly bid-ask bounce — and the frozen trigger has a defect

`gridbot.analysis.crossvenue_signal.move_quality` ·
`uv run python scripts/crossvenue_signal_rate.py --persistence`

Addendum 2 named bid-ask bounce as the likely confound behind small moves
reverting more than large ones. The mid cannot separate a repricing from one side
of the book flickering — but the **two sides of the touch** can, and they were
already on the tape.

- **REPRICING** — bid *and* ask both moved the way the mid moved. The book shifted.
- **ONE_SIDED** — only one side moved. The mid moves half as far as the quote that
  actually changed. This is the shape a flicker takes.
- **SPREAD_CHANGE** — the sides moved oppositely. Nothing was repriced.

| quality | n | retrace@30s | @150s | @900s | held@150s |
|---|---|---|---|---|---|
| **repricing** | 72 | 0.050 | **−0.000** | 0.500 | **0.522** |
| one_sided | 135 | 0.292 | **0.500** | 0.661 | 0.267 |
| spread_change | 22 | 0.000 | 0.400 | 0.083 | 0.476 |

**A two-sided repricing holds.** Median retrace at the 150s order TTL is
**−0.000** — the median two-sided move has given back *nothing* by the time a
maker order expires, and 52% are still at or beyond their post-move level. A
one-sided move gives back **half** of itself and only 27% hold.

**And it is not size in disguise.** Holding size fixed, quality still separates:

| | n | retrace@150s | held |
|---|---|---|---|
| repricing < 5t | 50 | 0.167 | 0.500 |
| one_sided < 5t | 98 | 0.500 | 0.316 |
| repricing ≥ 5t | 19 | −0.015 | 0.579 |
| one_sided ≥ 5t | 33 | 0.426 | 0.121 |

Within both buckets the two-sided/one-sided split dominates. Addendum 2's
size effect was largely quality leaking through size.

## This rehabilitates the signal — and indicts the frozen trigger

The pessimistic read in Addendum 2 was driven by the one-sided majority. On the
two-sided subset the intl move is genuine information that persists past the
order lifetime, which is what the lane needs.

But **the frozen trigger does not require two-sidedness**, and **135 of its 229
events (59%) are one-sided**. As frozen, it fires mostly on flicker.

Fixing it costs accrual: two-sided events are **72 of 229**, so the effective rate
falls from **1.36/h to ~0.43/h**, and clause (iv) must be set against the lower
number.

**The trigger is NOT being changed here.** It sits in the draft pre-registration,
the amendment is a user decision, and it must be made *before* the conditional
basis is measured — after that, it is a search. This is recorded as open decision
(7). That the defect was found by a measurement which never touched the basis,
PMUS, or any outcome is the reason it can be fixed at all without burning the
pre-registration.

---

# Addendum 4: correcting addendum 3's magnitude, and the response read at n=364

## The correction first

Addendum 3 reported *"135 of its 229 events (59%) are one-sided — as frozen, it
fires mostly on flicker."* **The direction holds; the magnitude was wrong and
"mostly" is not supported.** That 59% came from a 14-market slice. Recomputed
over the full training population, and over the subset that actually matters:

| population | markets | events | repricing | one-sided | spread change |
|---|---|---|---|---|---|
| all training markets with an intl series | 46 | 1,685 | **54.8%** | 39.3% | 5.8% |
| markets with a **usable PMUS book** | 7 | 419 | **83.3%** | 14.1% | 2.6% |

So the frozen trigger fires **majority two-sided**, not majority flicker — and on
the markets where we can actually trade, five events in six are genuine
repricings. One-sided moves remain a real minority that reverts twice as much
(addendum 3's persistence table stands), but the trigger is not the liability
that addendum said it was.

The selection is itself informative: a market liquid enough for us to capture a
usable PMUS book is also one where intl is tight and moves two-sided.

*(Checked before publishing: `move_quality` was changed to a binary search
between the two runs. It is not the cause — **0 of 5,095,540** intl quotes share
a timestamp with another, so exact-match selection is unambiguous and the edit is
semantically identical.)*

## The response read, now n=364 across 7 usable books

Coverage accrued during the night: usable training books went **1 → 7**, and the
STALE/INFORMED/NO_FOLLOW read is now 33× the sample addendum 1 had.

| | n | STALE | INFORMED | NO_FOLLOW | \|basis\| at STALE | ≥2t | lag p50 | p90 |
|---|---|---|---|---|---|---|---|---|
| all events | 364 | 121 | 44 | 199 | **2.5t** | **0.61** | 35.8s | 172.5s |
| two-sided | 318 | 108 | 42 | 168 | 2.5t | 0.60 | 35.8s | 146.1s |
| one-sided | 46 | 13 | 2 | 31 | 3.0t | 0.69 | 39.5s | 195.8s |

**The basis at STALE events clears the proposed bar.** 2.5 ticks median with
**61% at or above 2 ticks**, against clause (ii)'s proposed floor of 2. That is
the first evidence the conditional basis exceeds the 1.5-tick unconditional one —
which is exactly what the lane needed and what addendum 2 put in doubt.

**Follow latency fits inside the order.** 35.8s median against the engine's 150s
maker TTL; even p90 (172.5s) is close to it.

**INFORMED is 12.1%** — one event in eight is a pick-off, and that is the −6.5pt
cost (NOTE6) in its observable form. Not fatal, but it is a real haircut that any
expectancy calculation must carry.

**NO_FOLLOW is still modal at 55%**, and addendum 2 says a substantial share of
those are intl reverting rather than PMUS being stale.

## Still not a verdict

Seven books, two days, one venue pair, and no fill model anywhere in it. A STALE
label with a 2.5-tick basis is not a filled order at 2.5 ticks — fills come from
`trade_prints` (NOTE11/NOTE12), adverse selection is already visible at 12%, and
the holdout has not been touched.

