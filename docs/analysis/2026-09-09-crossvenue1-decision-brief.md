# H-CROSSVENUE1 — decision brief

Registry provenance: `H-CROSSVENUE1-PRENOTE1` (the three pre-registration
measurements), `H-MAPENTRY1-NOTE12` and `H-MAPENTRY1-NOTE13` (the corrections).

**Written by the overnight loop, 2026-09-08/09. Nothing here is registered,
armed, or live. `dry_run` is untouched; no capture unit was restarted.**

Everything below is **training-side only**. The pre-registered holdout boundary
(`2026-09-07T06:39:27Z`) was never read, and the instruments raise rather than
filter when handed a holdout series.

You have seven decisions. Each one now has a measured number behind it, which is
the only thing that changed tonight.

---

## The chain, end to end

The lane's claim: intl and PMUS settle the same CS2 event with disjoint
participant sets, so PMUS goes stale after information arrives and a maker taking
intl's side of that staleness has positive expectancy. Every link is now measured
except the last.

| link | measured | verdict |
|---|---|---|
| intl reprices | **1.36 events/h** (≥2t/30s, 168.8 market-hours) | works |
| the move is real, not flicker | **54.8% two-sided** overall; **83.3%** on tradeable markets | works |
| real moves carry information | two-sided retrace **−0.000 @150s** (52% held) vs one-sided **0.500** (27%) | works |
| PMUS is stale at those moments | **121 STALE / 44 INFORMED / 199 NO_FOLLOW** (n=364) | partial |
| the gap is worth crossing | basis at STALE **2.5 ticks**, **61% ≥2t** | clears the proposed bar |
| PMUS reacts slowly enough to rest | follow lag **35.8s** p50 vs a **150s** order | works |
| a maker actually gets filled | **18.5%** at the touch, **33.6%** one tick inside | binding constraint |
| **the fill is profitable** | **NOT MEASURED** — that is the edge claim, and it needs registration first | **open** |

Two haircuts sit on top of anything that follows: **12.1% of events are INFORMED**
(PMUS moved first — the −6.5pt pick-off, NOTE6, in observable form), and
**NO_FOLLOW is modal at 55%**, of which a substantial share is intl reverting
rather than PMUS being stale.

### Two risks measured after this brief was first written, both favourable

**The pick-off is not our latency, so it cannot be engineered away.** Our intl
feed runs at a **median 83.3ms** venue-stamp to receive (7,092,340 messages) —
0.2% of the 35.8s window. Split by outcome, the medians are *identical*: **82.5ms
on the events that picked us off, 82.1ms on the ones that did not.** Constant
clock skew cancels out of that comparison, which is why it carries a conclusion
the absolute number cannot. The 12.1% is genuine adverse selection: **a cost to
price, not a bug to fix with faster infrastructure.**

**Orientation risk — the design's named highest-risk component — is zero on the
tradeable population.** Match markets are **33/33 orientable**; the 66 refusals
are all map books, which the venue ships with only one side declared (1,436 of
1,436 rows) and which NOTE9 retired anyway. Across **403 intl CS2 markets,
`yes_team_unresolved` is 0** — the registration's own blocking condition has
nothing to block on.

**And coverage reads better than it looked.** The refusal census did not record
the intl market kind; split by it, **268 of 337 refusals are map markets**. On
the match population the join rate is **66 of 135 = 48.9%**, not the 5% that
66/1268 implies.

**The honest summary: no link is broken, and no link is strong.** The lane is
plausible and underpowered, and the thing that decides it — expectancy on filled
orders — is exactly the thing that cannot be measured before you register it.

---

## Decision 1 — the rate budget (unblocks everything else)

`config.PMUS_PREMATCH_BOOKS` shipped **default off**. It polls the match book of
a mapped series GRID is tracking but that has not started, so the pre-match window
the design calls most mispriced stops being invisible.

- The venue ceiling is **not** the constraint: measured **0.9 req/s** against the
  recorder's own 4 req/s design cap and Polymarket's 20 req/s.
- `MAX_SLUGS_PER_CYCLE` is a **round-robin** cap, so more candidates does not add
  polls — it divides the same polls among more slugs. That is the real price, and
  it is paid in the resolution of the in-play tape.

**Ask:** enable the flag, and decide whether to raise the cap alongside it. The
measurement says there is headroom; spending it is your call.

## Decision 2 — restart capture to activate four dormant changes (rail 4)

Nothing was restarted tonight. Three shipped changes are dormant until you do:

- the order-book **depth ladder** (free — the recorder was already fetching and
  discarding it; queue position is the last unknown in the fill model);
- the **`discovery_sweep` marker**, so a sweep that finds nothing is
  distinguishable from a sweep that never ran (this ambiguity produced a wrong
  published cause tonight — see `H-MAPENTRY1-NOTE12`);
- **`PMUS_PREMATCH_BOOKS`**, if you enable it.

Until that restart, `evidence_qa` check 5 (capture gaps) reports **pending** for
every day — it watches the `discovery_sweep` heartbeat, which does not exist yet.
Pending is deliberately not green.

## Decision 3 — amend the frozen trigger, or leave it

Frozen: **≥2 ticks of the intl mid over a trailing 30s, 60s cooldown.** Fixed
from the intl side alone, before any basis could be looked at.

One-sided moves revert about twice as much as two-sided ones. Requiring both bid
and ask to move would cost **1,685 → 924** events overall, but only **419 → 349**
on markets with a usable book, since those are already 83% two-sided.

**This must be decided before any conditional basis is measured.** After that,
changing it turns the lane from a test into a search.

## Decision 4 — clause (ii), the cost bar

Proposed: `median |basis| at signal ≥ 2 ticks`. Measured at STALE events: **2.5
ticks median, 61% at or above 2**. It clears — but read it against the **decayed**
gap, since about a third of an intl move unwinds inside a 150s order.

## Decision 5 — clause (iii), the posting rule

This is a genuine tradeoff and it is now quantified: **+1 tick nearly doubles the
fill rate (18.5% → 33.6% certain) and spends 40% of a 2.5-tick median basis.**
Clause (iii) has to name one posting rule and be rendered on filled orders only,
computed from `trade_prints` — never `exec_fills`, whose bracket does not contain
the truth (NOTE11).

## Decision 6 — clause (iv), the starvation floor

At current coverage: ~3.5 usable books/day × ~17 STALE events × 18.5% fill ≈
**10 fills/day**. H-MAPENTRY1 died accruing 39 against a bar of 60. Set the floor
from a rate you have actually measured **after** decisions 1 and 2, and make it
reachable inside a stated window.

## Decision 7 — file the row, or wait

Filing is your act, never mine (rail 2), exactly as flipping `dry_run` is (rail 3).

---

## What tonight also invalidated

Three published claims were corrected, all of mine, all before they could be
built on:

1. **NOTE11's mechanism.** "A 30s poll" was the row cadence, not the poll rate.
   The real mechanism is stronger: **27.2% of prints leave the BBO price
   identical**, so no poll rate at all recovers them.
2. **The CROSSVENUE1 "second capture gap".** Blamed on `DISCOVERY_WINDOW`;
   actually the 09-07 host suspend, already fixed. **Do not widen the window** —
   it would spend rate budget and recover nothing.
3. **"The trigger fires mostly on flicker."** A 14-market artifact; it fires
   majority two-sided.

And `H-MAPENTRY1-CLOSE1`'s structural ground was **understated ~4×**: map books
are **81.8%** untradeable per book, not the 22% the row-level rule reported. That
corroborates the closure and supplies its mechanism — you cannot fill against a
book that never moves.
