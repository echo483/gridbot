# The EXEC1 fill bracket does not contain the truth

**2026-09-08 · overnight loop · read-only, no orders, no arming**

Reproduce: `uv run python scripts/fill_print_audit.py`

## The finding in one table

Every BBO observation on 2026-09-07/08 is treated as a hypothetical posting
instant for a resting BUY with the engine's real 150s lifetime. Two channels are
asked the same question over the same window.

| posting | n | BBO strict | **BBO "any touch" (the old UPPER)** | **print through (CERTAIN)** | print any |
|---|---|---|---|---|---|
| at the bid | 2,627 | 13.6% | **19.0%** | **30.7%** | 49.0% |
| bid + 1 tick | 2,627 | 19.0% | **37.2%** | **49.0%** | 55.7% |

The old UPPER bound fires *less often than a fill that price priority
guarantees*. A ceiling below a floor is not a bracket. Every fill-rate and
per-fill number EXEC1 produced is therefore invalid — and invalid in the
direction that decides the strategy, because it understates how often a resting
order trades.

## Why a print through our price is certain, not estimated

All venue prices are Yes prices (`H-MAPENTRY1-NOTE7`); both sides are converted
to our-side cost, so a LONG's cost is the Yes price and a SHORT's is `1 - yes`.
For a resting order at cost `P`:

- a print **strictly below `P`** means a counterparty accepted a *worse* price
  than we were publicly offering. Price priority puts our order ahead of theirs.
  We fill. This is market mechanics, not a model.
- a print **at exactly `P`** shares our price, so time priority in the queue
  decides. Evidence, not proof — an upper bound.

That asymmetry is the point: `through` is a floor you can stand on, which is
precisely what the BBO channel could never produce.

## The mechanism: a 30s poll of a continuous process

`gridbot-pmus-books` polls at a **median 30.7s** (p90 32.0s). Executions are
continuous. So:

- **14.0% of prints (18.4% of notional, $34,343 of $187,144)** are priced
  outside the quoted range of *both* the poll before them and the poll after
  them. The book moved and reverted entirely between two looks. The BBO tape
  cannot see these trades in principle.
- Median 1.55 prints fall inside each poll gap; the busiest slug ran 1,708
  prints against 296 polls.

This is **model misspecification, not estimation error** (Quantopian lecture 17).
The observation channel does not span the process it is used to measure, so no
quantity of additional 30s BBO data corrects it. Only a different channel does —
which is what `gridbot-pmus-trades` began recording on 2026-09-07.

## This corroborates EXEC1's own calibration failure

`EXEC1-NOTE3` already recorded the instrument failing its calibration gate:
**UPPER recall 0.4615 against a 0.60 bar** on the armed nights' real orders.
That was read as the instrument being under-powered. It was not — it was the
channel being blind. The two measurements agree from opposite directions:

| | |
|---|---|
| EXEC1 calibration (real orders, 08-26/27) | detects 46% of real fills |
| this audit (hypothetical postings, 09-07/08) | BBO catches 39% (at bid) to 67% (bid+1t) of print-evidenced fills |

An independent channel, a different population, and a different day range land
in the same band. The recall failure was a symptom with a mechanism, and the
mechanism is now measured.

## What this invalidates

1. **The EXEC1 held-out renders** (`−0.167 [−0.282, −0.070]` lower,
   `−0.142 [−0.203, −0.088]` upper, per filled contract, 130 entries / 45
   series). Computed through the broken bracket. The *number* is void; the
   *sign* is not established either way by this audit, because a larger and
   differently-selected fill population can move it in either direction. It must
   be re-rendered on a valid channel before it means anything.
2. **`scripts/fill_conditional_edge.py`'s three-snapshot fill proxy**, whose
   docstring already predicted this ("*three snapshots miss a touch that happens
   between them*"). Now quantified.
3. **`H-MAPENTRY1-NOTE5/NOTE6`, the −6.5pt fill-selection cost.** Partially. The
   "never filled" bucket is contaminated with orders that *did* fill and were
   not detected. Contamination of the comparison bucket **dilutes** a real
   difference rather than manufacturing one, so the direction survives; the
   magnitude is a lower bound on the true separation and the significance
   (already `z=1.29, p≈0.20`) is unchanged in its inability to resolve.

## What this does NOT establish

- **No edge claim.** The postings audited here are unconditional — every book
  observation, no signal, no qualification. This says how often a resting order
  *trades*, never whether trading was profitable.
- **No H-CROSSVENUE1 measurement.** That lane is not pre-registered (iron rail
  2). Nothing here touches its basis, lead or edge.
- **Not back-validated against our own fills.** The trades tape starts
  2026-09-07; the armed nights were 2026-08-26/27. There is **no overlap**. The
  argument above is market mechanics, and it is the only kind available until a
  live-small session puts our own orders and the print tape on the same clock.
- **The counterfactual caveat.** Adding our order to the book perturbs the book
  it is measured against: a taker who sold below `P` would have hit our bid
  first, so that print would not have printed where it did. The *fill*
  conclusion survives (the perturbation runs toward filling us); the price, the
  residual size, and anything about the subsequent book do not. This is why the
  module reports fill evidence and volume only.

## Consequence

The fill model is the first work item under every path in `STATE.md`, and it is
now known to be wrong rather than merely wide. `gridbot.analysis.trade_prints`
is the replacement channel (21 tests). Re-rendering EXEC1 through it is a
measurement on an already-rendered holdout and therefore needs its own
pre-registered amendment — **not** taken in this loop.
