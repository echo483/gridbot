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

---

# Addendum 5: would a maker actually get filled? — the first execution-realistic number

`crossvenue_response.maker_quote` + `gridbot.analysis.trade_prints`

A 2.5-tick basis is not a filled order. NOTE8's rule is that no backtest number
exists without a depth/fill model, and NOTE11/NOTE12 established that fills must
come from **actual prints**, never from the BBO bracket. So: for each STALE
event, rest the order a maker would actually have to rest, and ask the trade tape
whether it filled inside the engine's real 150s lifetime.

**Which order.** The basis is `intl (as PMUS Yes) − PMUS`. A positive basis says
the Yes is cheap here, so we buy Yes and rest at the PMUS **bid**. A negative
basis says the Yes is expensive, and on this venue selling Yes *is* buying No —
`BUY_SHORT` at an our-side cost of **1 − ask**, never the ask. That asymmetry is
NOTE7, and it is a tested function rather than two lines at a call site because
sending the Yes price verbatim for a short got **15 of 24 shorts venue-rejected
while the ledger read "placed"**.

## Result — 119 STALE events with a postable quote (48 long, 71 short)

| posting | n | **CERTAIN fill** | possible | any print in window |
|---|---|---|---|---|
| at the touch | 119 | **18.5%** | 33.6% | 95.0% |
| touch + 1 tick | 119 | **33.6%** | 52.1% | 95.0% |

**CERTAIN** means a print traded *through* our resting price, so price priority
guarantees the fill — a floor, not an estimate. **possible** adds prints at
exactly our price, where queue position decides: an upper bound.

Three things follow.

**The market is there.** 95% of STALE events had at least one print inside the
order's lifetime. Non-fills are about price, not absence of trading.

**Resting at the touch fills less than one time in five.** So the signal rate and
the basis both have to be discounted hard: at ~3.5 usable books/day and ~17 STALE
events per book, 18.5% is on the order of **10 fills/day** at current coverage.

**Improving by one tick nearly doubles the fill rate — and costs 40% of the
basis.** 18.5% → 33.6% certain, paid for out of a 2.5-tick median gap. That
tradeoff is the execution decision this lane actually has to make, and it is now
measurable rather than assumed.

## The line this does not cross

This is fill **feasibility**, not expectancy. **No PnL is computed anywhere**, and
a fill at a 2.5-tick basis is not 2.5 ticks of profit — that requires marking
against settlement, which is the edge claim, and H-CROSSVENUE1 is not registered
(iron rail 2). The 12.1% INFORMED rate from addendum 4 is a haircut that sits on
top of whatever expectancy eventually gets measured.

Also inherited from NOTE11: adding our order perturbs the book it is measured
against. The perturbation runs *toward* filling us, so the fill conclusion
survives; the price and residual size do not.

---

# Addendum 6: the orientation risk is zero on the population we would trade

`uv run python -m gridbot.kg build && uv run python -m gridbot.kg --json orphans`

Design §7 calls the join *"the highest-risk component: if intl's Yes resolves to
team A and PMUS's Yes to team B, every number is exactly backwards and looks
excellent."* The knowledge graph now measures that risk instead of asserting it.

| PMUS market kind | orientable | refused |
|---|---|---|
| **match** | **33** | **0** |
| map1 | 0 | 33 |
| map2 | 0 | 33 |

Every refusal is `pmus_sides_incomplete`, and it is systematic rather than
sporadic: across the 2026-09-08 mapping file, **every one of 1,436 map-book rows
declares exactly one side**, and **every one of 722 match-book rows declares
two**. The venue simply does not name the short side of a map book.

**Across 403 intl CS2 markets over two days, `yes_team_unresolved` is ZERO.** The
DRAFT registration says a non-zero count there "is a sign-convention event and
blocks trading until explained". There are none to explain.

## And the refusal census was misreading itself

337 refusals against 66 joins looks alarming until it is split by the intl market
kind — which the census did not record, and now does:

| intl kind | reason | n |
|---|---|---|
| map | no_pmus_book_of_kind | 150 |
| map | no_pmus_match | 118 |
| match | no_pmus_match | 60 |
| match | no_pmus_book_of_kind | 9 |

**268 of the 337 refusals are map markets**, which NOTE9 retired and NOTE13
measured at 81.8% untradeable. On the population that matters the join rate is
**66 of 135 intl match markets = 48.9%**, not the 5% that `66/1268` implies.

Map markets also cannot join *at all*, for a reason worth writing down: intl
names the kind `map` while PMUS keys its books `map1`/`map2`, so the kind
comparison never matches and every map pair is refused one step earlier than the
orientation check. A consequence is that **`pmus_sides_incomplete` is unreachable
for map books in practice** — it is kept as a live guard because it is the one
that fires if a *match* book ever ships one-sided, and that is now commented at
the call site rather than left as a trap for the next reader.

---

# Addendum 7: the unconditional basis is 2.0 ticks, not 1.5 — and the first number was staleness waiting to happen

Coverage grew overnight from 1 usable book to **13**, so the headline figure was
re-measured on ~1,400 points instead of 41 from a single market. Two things came
out of it, and the second is a defect in this instrument.

## The instrument could report staleness as edge

An as-of join takes the last intl quote at or before each PMUS sample. When intl
stops updating — the market settled, or the extraction window ended — that quote
can be **hours** old, and the "basis" it produces is staleness, not a gap anyone
could trade. `basis_series` has a `max_intl_age_ns` bound and this script left it
`None`, on the reasoning that what counts as stale is a pre-registration
parameter rather than an implementation detail. That reasoning was right about
the *parameter* and wrong about the *report*:

| max intl age | n | median \|basis\| | p90 | ≥2t | median intl age |
|---|---|---|---|---|---|
| **unbounded** | 2,959 | **6.50 ticks** | 29.0 | 0.73 | **1,933s** |
| 3600s | 1,643 | 2.00 | 23.0 | 0.56 | 0.5s |
| 600s | 1,422 | 1.95 | 24.0 | 0.50 | 0.3s |
| 60s | 1,408 | 2.00 | 24.0 | 0.50 | 0.3s |
| 10s | 1,374 | 2.00 | 24.0 | 0.51 | 0.3s |
| 2s | 1,136 | 2.00 | 26.0 | 0.53 | 0.1s |

The unbounded row is joining against quotes a median **32 minutes** old. Printing
one pooled figure would have shipped **6.50 ticks** as the basis. The script now
prints the curve and cannot present a single number; the bound stays
unparameterised because it genuinely is a registration choice.

## The corrected figure, and what it does to clause (ii)

**Once staleness is excluded the basis is remarkably stable: 1.95–2.00 ticks
across an 1800× range of freshness bounds**, with 50–56% of observations at or
above 2 ticks. That is the number to use.

> **Correction to a claim repeated several times tonight.** Addendum 2 onward
> reported the clean unconditional basis as **1.5 ticks, below clause (ii)'s
> proposed 2-tick bar**, and built an argument on it: that the lane "lives or
> dies on whether a signal selects the tail". That 1.5 came from **n=41 on one
> market**. At n≈1,400 across 7 markets the unconditional basis is **2.0 ticks —
> at the bar, not below it.**

The conditional basis at STALE events (2.5 ticks, 61% ≥2t) therefore still
exceeds the unconditional one, but by **0.5 ticks rather than 1.0**. The lane's
case is less dependent on the signal than the earlier framing implied, and
correspondingly less dramatic: an unconditional 2-tick gap that a signal widens
to 2.5, against a 2-tick cost floor and an 18.5% fill rate.

## Stated limits

- 13 usable books over two days, and the p90 of 24–29 ticks says the
  distribution has a long tail that a median hides.
- Still no PnL. A 2.0-tick gap crossed at an 18.5% fill rate with a 12.1%
  pick-off rate is not 2.0 ticks of profit, and nothing here computes what it is.

---

# Addendum 8: the same staleness guard, applied to the sibling instruments — and why only one was contaminated

Addendum 7 found the basis instrument capable of reporting staleness as edge. The
obvious next question is whether `crossvenue_response` had the same hole. It did —
it took the PMUS quote as-of each intl event with no bound on how old that quote
could be — and the bias runs **toward the thesis**: a PMUS quote we have not
refreshed looks like a book that has not moved, which pushes events toward the
STALE label and inflates the basis measured against it.

So the bound was added, the age is now recorded on every `Response`, and the
numbers were re-measured under it.

| max PMUS age | n | STALE | INF | NO_FOLLOW | \|b\| STALE | ≥2t | lag p50 | age p50 |
|---|---|---|---|---|---|---|---|---|
| unbounded | 364 | 121 | 44 | 199 | 2.5t | 0.61 | 35.8s | 16.8s |
| 600s | 363 | 121 | 44 | 198 | 2.5t | 0.61 | 35.8s | 16.7s |
| 60s | 357 | 118 | 44 | 195 | 2.5t | 0.60 | 35.8s | 16.4s |
| 30s | 345 | 115 | 43 | 187 | 2.5t | 0.60 | 35.7s | 15.5s |

| PMUS age cap | posting | n | CERTAIN | possible |
|---|---|---|---|---|
| unbounded | at the touch | 119 | 18.5% | 33.6% |
| **60s** | at the touch | 116 | **19.0%** | 34.5% |
| unbounded | touch +1 tick | 119 | 33.6% | 52.1% |
| **60s** | touch +1 tick | 116 | **34.5%** | 53.4% |

**Both instruments are robust.** Median PMUS age at an event is **16.8s**, and
squeezing the bound to 30s moves STALE from 121 to 115 while leaving the basis at
2.5 ticks and the follow lag at 35.7s. The guard confirms the numbers rather than
overturning them.

## Why the basis was contaminated and these were not

It is the clock each instrument joins on.

- **The basis joins on the PMUS clock** — one observation per PMUS sample, over
  the whole life of the book. Intl can stop updating (market settled, extraction
  window ended) while PMUS keeps being polled, so an as-of intl quote drifts to
  hours old and the pooled figure becomes staleness.
- **Responses and fills join on the intl event clock.** An event only exists
  because intl printed a quote, so the intl side is fresh *by construction*, and
  the PMUS side is polled frequently on exactly the books `book_liveness` admits.

That is a property worth remembering rather than a coincidence: **an as-of join is
only as trustworthy as the freshness of the side it reaches backwards into**, and
which side that is depends on whose clock indexes the series.

