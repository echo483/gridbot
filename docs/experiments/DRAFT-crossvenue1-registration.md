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
round-ends the design assumed. The 6 genuine losses split evenly across two
independent capture gaps:

- **(A)** `pmus_books` subscribes from GRID `started`, while PMUS lists a market
  a median 4.46h earlier — 3 of 14.
- **(B)** `config.DISCOVERY_WINDOW` is −4h/+1h while the PMUS mapper queries
  −6h/+30h, so the GRID poller never discovered the series at all (verified
  against `discovery-*.jsonl`: never seen, not pruned) — 3 of 14.

H-MAPENTRY1 closed after accruing 39 qualifying entries against a bar of 60. At
~5 observable markets/day, a holdout filed against today's capture accrues at
about a third of the design's assumed rate.

**Prerequisite before filing: close BOTH capture gaps — fixing one leaves roughly
half the loss — then re-measure the observable rate over several full days and
set clause (iv) from that number.**

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
4. **Clause (iv)**: set after re-measurement.
5. **Both capture widenings are policy calls** — subscribing PMUS books from
   listing rather than kickoff, and widening the GRID discovery horizon, each
   increase poll load against published rate limits (GRID Central Data 20/min,
   Series State 180/min global and 6/min per series). The trades tape already
   required a credential decision; these require a budget one.
