# DRAFT pre-registration — H-CROSSVENUE1

**STATUS: DRAFT. NOT REGISTERED. NOT IN `registry.jsonl`.**

Iron rail 2 says a strategy is frozen in the registry *before* its instrument
runs, and the design (`docs/superpowers/specs/2026-09-07-crossvenue1-design.md`)
says the row is "to be drafted for the user's own approval — never
self-registered." This file is that draft. Filing it is the user's act, exactly
as flipping `dry_run` is (rail 3).

Written 2026-09-08 by the overnight loop, on the user's directive *"start on
rail 2 and hypothesis development."*

---

## ⚠ Read this before filing

**Do not file this row yet.** `docs/analysis/2026-09-08-crossvenue1-readiness.md`
measured the observable population at **5 PMUS match markets on 2026-09-07** (the
one complete day) out of 14 that join — not the 14, and not the 790–1,645
round-ends the design assumed.

> **Corrected 2026-09-08 (twice, same session).** This warning first named two
> independent capture gaps and asked for both to be closed. Measurement
> (`scripts/discovery_attribution.py`) says there is **one**, plus an uptime
> problem that was already fixed:
>
> - **(A) REAL.** `pmus_books` subscribes from GRID `started`, while PMUS lists a
>   market a median 4.46h earlier. Landed behind `config.PMUS_PREMATCH_BOOKS`,
>   **default off** — a rate-budget call, see readiness §6.
> - **(B) WITHDRAWN.** `config.DISCOVERY_WINDOW` was blamed for series the poller
>   never discovered. Testing each one against actual sweep timestamps puts
>   **every** such series in the "no sweep ran at all" bucket and **none** in the
>   "swept and still missed" bucket, on both days measured. The cause was the
>   2026-09-07 host suspend (7h16m), already fixed in the capture unit's
>   inhibitor. **Do not widen the window; it would spend rate budget and recover
>   nothing.**

H-MAPENTRY1 closed after accruing 39 qualifying entries against a bar of 60. At
~5 observable markets/day, a holdout filed against today's capture accrues at
about a third of the design's assumed rate.

**Prerequisite before filing: enable and measure gap (A), and re-measure the
observable rate over several full days with capture actually up, then set clause
(iv) from that number.** The observable rate is a joint measure of coverage AND
uptime; the 09-07 sample was taken across a day that lost 7h16m of tape, so it
understates what today's code can see.

---

## Row (proposed)

- **`hypothesis_id`**: `H-CROSSVENUE1`
- **`run_ts`**: (date of filing)
- **`status`**: `REGISTERED_NO_RUN`
- **`registered_by`**: the user

### Mechanism claim (frozen)

The intl Polymarket CLOB and Polymarket US are structurally segmented markets
settling the same CS2 events. Intl is deep and continuously traded; PMUS is thin.
Because the participant sets are disjoint by regulation, no arbitrageur closes
the gap, so PMUS quotes go **stale** relative to intl after information arrives,
and a PMUS maker order taking intl's side of that staleness has positive
expectancy. The estimator is *swapped, not improved*: we stop betting our model
against a market and start betting a large market against a small one.

`q_round` enters only as an informedness filter — "is this repricing real and
large enough to matter" — and never sets a price. Rail 1 holds: no LLM in this
path.

### Population and unit

One observation per `(series, PMUS match market, GRID information event)`,
deduped to the **first qualifying observation per market per event**. Match books
only; the map book is excluded (NOTE9: p50 spread 0.07–0.12, 22% dead).

### Holdout

Series first mapped at or after **`2026-09-07T06:39:27Z`** (boundary already
stamped, pre-measurement). Everything before it is training/calibration and may
be explored freely. **Single terminal look.** Interim renders are REFERENCE and
never stop accrual.

### Falsification bars — ALL FOUR must pass

**(i) Detector.** `intl_mid` beats `PMUS_mid` on Brier for the same settled
outcome, series-clustered bootstrap (resample `series_id`), 95% CI on the
difference **excluding zero**. If intl is not the better estimator there is no
information to arbitrage.

**(ii) Cost.** Basis at signal exceeds measured round-trip execution cost, with
the spread it must cross stated alongside every number (NOTE8). PENDING USER
CONFIRMATION: proposed bar `median |basis| at signal ≥ 2 ticks` on qualifying
observations.

> **The trigger is frozen here, deliberately before it can be tuned.** "At
> signal" now has a definition, and it was fixed from the intl side's own
> dynamics with no PMUS book in scope and no basis computable from anything the
> instrument could see (`gridbot.analysis.crossvenue_signal`, 21 tests;
> `scripts/crossvenue_signal_rate.py`):
>
> **An intl repricing event fires when the intl mid moves ≥ 2 ticks against the
> intl quote as of 30s earlier, with a 60s cooldown so one repricing is one
> event.** Causal by construction, and it fails closed for the first 30s of a
> market rather than measuring over whatever history exists.
>
> The parameters are round numbers with stated provenance, not fitted ones: 2
> ticks because that is this clause's own cost floor, 30s because it is the order
> of the measured GRID round-end lead (13–27s), 60s because a repricing should
> count once. Measured on the 14 training markets with an intl series, over 168.8
> market-hours:
>
> | threshold | events | rate | median size | p90 |
> |---|---|---|---|---|
> | ≥2 ticks / 30s | 229 | **1.36/h** | 2.5t | 14.0t |
> | ≥3 ticks / 30s | 177 | 1.05/h | 3.5t | 17.5t |
> | ≥5 ticks / 30s | 113 | 0.67/h | 6.5t | 23.5t |
>
> Why this matters for clause (ii): the **unconditional** clean basis is **1.5
> ticks**, below this clause's own 2-tick bar
> (`docs/analysis/2026-09-08-crossvenue1-basis-first-look.md`). The lane
> therefore rests entirely on the conditional basis at these events exceeding the
> unconditional one. That is the single measurement that decides the lane, it has
> **not** been taken, and freezing the trigger now is what keeps it a test rather
> than a search.
>
> Rates are per market-hour and vary by an order of magnitude (0.00–13.67/h): a
> ~4h span is an in-play window, a ~24h span is listing-to-settlement and mostly
> dormant. Clause (iv) must be derived from IN-PLAY hours with a live PMUS book,
> not from pooled market-hours.

**(iii) Execution.** Fill-modelled PnL per contract, **depth-limited,
quote-lifetime aware, series-clustered**, with 95% CI excluding zero on the
favourable side.

> **Binding change from the design.** Clause (iii) must be computed on
> `gridbot.analysis.trade_prints` (actual prints), NOT on `exec_fills` alone.
> `H-MAPENTRY1-NOTE11` established that the BBO channel is misspecified: its
> frozen UPPER bound (19.0% at the bid) fires *less often* than the print-through
> floor (30.7%) that price priority guarantees, so the bracket does not contain
> the truth. EXEC1's machinery is reused for its **calibration-gate discipline**
> only, never for its observation channel.

> **Calibration gate, and it must actually pass this time.** EXEC1 failed its own
> gate at 0.4615 recall vs a 0.60 bar and rendered anyway (NOTE3). The gate here
> is pre-registered as **blocking**: no held-out read is taken until it passes.
> PENDING USER CONFIRMATION on the numbers.

**(iv) Starvation clause.** A minimum count of deduped qualifying held-out
observations, below which the lane renders **INCONCLUSIVE** rather than PASS or
FAIL. **This number cannot be set today** — see the warning above. It must be
derived from the observable rate measured *after* the pre-match capture fix, and
must be reachable inside a stated window at that rate. A bar that the measured
accrual cannot reach is how H-MAPENTRY1 died.

### Refusals are part of the record

A pair whose orientation cannot be resolved to a named team on **both** venues is
REFUSED, never guessed, and refusals are counted and reported per day
(`scripts/crossvenue_coverage.py`). A non-zero `inverted` count is expected and
handled (`intl_price_as_pmus_yes`); a non-zero `yes_team_unresolved` count is a
sign-convention event and blocks trading until explained. NOTE7 is why: 15 of 24
shorts were venue-rejected from this class of error while the ledger read
"placed".

### What a PASS does and does not license

A PASS licenses the user to consider live-small under `H-LIVESMALL1`. It does not
flip `dry_run`, which is the user's own act under rail 3, never Claude's, never
an agent's, never a default.

### Pre-registered kill conditions

- Segmentation closes (PMUS deepens, or makers bridge both venues and basis
  compresses) — monitored, not assumed static.
- Join coverage or refusal rate degrades past a stated floor.
- The calibration gate fails — no held-out read is taken at all.

---

## Open decisions for the user

1. **Do not file until the capture gap is closed** — or file with clause (iv)
   explicitly deferred, which is unusual and should be a deliberate choice.
2. **Clause (ii) bar**: is 2 ticks the right cost floor?
3. **Clause (iii) calibration gate**: what recall/precision bar, given NOTE3's
   lesson that a soft gate gets rendered past?
4. **Clause (iv)**: set after re-measurement. The arithmetic is now partly
   known: intl supplies **~1.36 signal events per market-hour** at the frozen
   threshold, so an in-play match (~4h) offers roughly 5–25 events. The binding
   term is not the signal rate but the fraction of those hours carrying a **live**
   PMUS book — today 3 of 46 training markets have both venues at all, and only
   ~64% of match books are live (`H-MAPENTRY1-NOTE13`). Clause (iv) should be set
   from `observable in-play hours × 1.36 × P(live book)` once coverage is real.
6. **Is the frozen trigger the right one?** It is deliberately unfitted. If you
   want a different definition, change it **now** — changing it after the
   conditional basis is measured turns the lane into a search.
5. **One capture widening is a policy call**, not two. Subscribing PMUS books
   from listing rather than kickoff is live behind `config.PMUS_PREMATCH_BOOKS`
   (default off). The venue ceiling is not the constraint — measured 0.9 req/s
   against a 20 req/s limit — but `MAX_SLUGS_PER_CYCLE` is a round-robin cap, so
   more candidates means each slug is polled less often, and NOTE11 is a finding
   about exactly that resolution. Raising the cap alongside the flag is the half
   that actually spends budget, and it is left to the user.
   The GRID discovery horizon is **withdrawn** from this list — see the corrected
   warning above.
7. **AMEND THE FROZEN TRIGGER? — decide before any conditional basis is measured.**
   > **Corrected.** An earlier revision of this item said the trigger "fires
   > mostly on flicker (59% one-sided)". That came from a 14-market slice. Over
   > the full training population it is **39.3% one-sided of 1,685 events (46
   > markets)**, and over markets with a usable PMUS book just **14.1% of 419
   > events (7 markets)**. The trigger fires MAJORITY TWO-SIDED. The amendment
   > below is still worth considering, but it is an efficiency question, not a
   > repair of a broken trigger.

   One-sided moves are a real minority that reverts about twice as much as
   two-sided ones — only one side of the touch moved, so the mid moved half as far
   as the quote that actually changed. Measured consequence
   (`docs/analysis/2026-09-08-crossvenue1-basis-first-look.md`, addendum 3):

   | quality | n | median retrace @150s (order TTL) | held |
   |---|---|---|---|
   | both sides moved | 72 | **−0.000** | 0.522 |
   | one side moved | 135 | **0.500** | 0.267 |

   A two-sided repricing holds past the order lifetime; a one-sided one gives back
   half. The separation survives holding move size fixed, so it is not size in
   disguise.

   **Proposed amendment: require both bid and ask to move in the direction of the
   mid.** Cost on the full training population: events fall **1,685 → 924
   (−45%)**; on usable-book markets only **419 → 349 (−17%)**, because those
   markets are already overwhelmingly two-sided. Clause (iv) must be set against
   whichever population the holdout will actually draw from.

   Note the counter-argument, now that n is larger: at n=364 the basis at STALE
   events is **2.5 ticks for two-sided and 3.0 ticks for one-sided** — the
   one-sided events are not obviously worse *on the basis*, only on persistence.
   The amendment buys signal quality and costs accrual, and accrual is this
   lane's historical killer (H-MAPENTRY1 died at 39/60).

   This was found by a measurement that never touched the basis, PMUS, or any
   outcome — which is why it can still be fixed without burning the
   pre-registration. Once the conditional basis is measured, changing this turns
   the lane into a search.

