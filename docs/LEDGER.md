# The executor ledger, and where the venue lies

How to read `data/executor/intents-YYYYMMDD.jsonl` correctly. Every rule here
was learned from a live incident, and getting any of them wrong fails
**open** — it silently under-counts exposure or mis-reads a fill.

## Row types

| type | meaning |
|---|---|
| `order_intent` | our decision. Lifecycle: `wire_pending` → `placed` / `error:*` / `rejected:venue:*` / `skipped:*` |
| `cancel` | our cancel attempt and its ack |
| `fill` | a fill attributed to an order |
| `settle` | a resolution or an accounting close |
| `fill_check` | a venue-state probe on a specific order |
| `reconcile` | the periodic ledger-vs-venue diff |

## Mandatory joins

- **`fill` and `settle` rows carry no `us_side`.** The side joins back to the
  `order_intent` row via `dedup_key`. `fills.filled_sides_by_slug` is the one
  shared derivation. Reading `us_side` off a fill row yields `None`, which
  reads as "no position" — a fail-open gate. This is the single most expensive
  mistake available in this file.
- **Settle trust is source-scoped.** Only the trusted sources
  (`venue_resolution`, `venue_resolution_backfill`) are venue truth. A
  `manual_exit` settle is an accounting close: it carries no PnL and must never
  free a dedup slot as if the position had resolved.
- **Exposure counts held contracts, not ledger status.** A partially-filled
  book whose remainder is cleanly cancelled still holds the filled contracts
  until settle. Counting only `status == "placed"` releases exposure that is
  real.

## Where the venue's word is unreliable

1. **A failed venue fetch returning an empty list reads as "no orders".** This
   produced 143 phantom `gone_from_venue` rows for one order across one night,
   every one disproven by a clean cancel; the true anomaly count was zero. The
   fix: a fetch error returns `None` and **skips** the diff. Never treat an
   error as an empty result.
2. **The venue reports cancel success on orders that already FILLED.** All
   three first-night fills were full fills whose TTL cancels returned a clean
   "cancelled". So: never read cancel status as fill truth. `fills.sync` and
   `settle_truth.py` are the truth path, and a partial fill hidden behind a
   clean cancel keeps counting exposure until settlement.
3. **The venue hands back an order id for orders it then REJECTS** — post-only
   would-cross, or a stale book between the read and the wire. 20 of 48
   venue-verified orders never rested while the ledger said `placed`. Any
   placed/filled statistic must be re-cut on **rested** orders only.
4. **`price` is always the Yes/long price.** A short whose cost is `c` is
   stored as `SELL @ c` and costs `1 - c`. Gridbot's prices are our own cost.
   The conversion happens exactly once, in `pmus.wire_price`, and back in
   `fills.fill_row`; `venue_px` keeps the raw number. Before that pin, every
   armed-night short was sent at the Yes price instead of its cost, so shorts
   whose cost was below 0.50 crossed the bid and were rejected post-only.
5. **Creates are never retried** (double-order risk). Cancels are retried ×3.
   An ambiguous 2xx is `error:ambiguous_ack`, never "rejected".

## Invariants worth asserting

- `open_exposure` derived from the ledger equals `stake × concurrent resting
  books`, counting partially-filled-then-cancelled books as held.
- Every `fill` row resolves to exactly one `order_intent` row by `dedup_key`.
- Zero `gone_from_venue` rows survive a clean-cancel subtraction.
- A `settle` row from an untrusted source never decrements open exposure.
