---
name: ledger-forensics
description: Use when reading gridbot's executor ledger for performance inference, incident triage, or venue-truth reconciliation — row semantics, the joins that are mandatory, and the venue's documented lies.
---

# Executor ledger forensics (gridbot)

Files: `data/executor/intents-YYYYMMDD.jsonl` (bare date). Row types:
order_intent (wire_pending → placed/error:*/rejected:*/skipped:*),
cancel, fill, settle, fill_check, reconcile.

## Mandatory joins (getting these wrong fails silently)

- **fill/settle rows carry NO us_side.** Side joins via dedup_key back
  to the order_intent row (fills.filled_sides_by_slug is THE shared
  derivation). Reading us_side off a fill row = fail-open gate.
- **Settle trust is source-scoped**: only TRUSTED_SETTLE_SOURCES
  (venue_resolution, venue_resolution_backfill) are venue truth.
  manual_exit settles are accounting closes — None pnl, never free a
  slug, never count as resolution.
- **fill_check rows are durable venue zero-fill proofs** (restart-safe
  _fill_checked). A cancel ACK alone is NOT venue proof.
- **wire_pendings pair with the outcome that FOLLOWS them** in file
  order; a dangling pending = crash window = possibly live on venue.

## The venue's documented lies

1. Cancel returns success on already-FILLED orders (all night-1 fills
   hid behind "cancelled" acks). Never trust cancel status for PnL.
2. fetch errors can read as "venue empty" → phantom gone_from_venue.
3. error:InternalServerError MAY have executed (fail-closed inflight);
   empirically 0/4 did on 08-27 — but pool across days before trusting.

## Funnel census (the health check)

Count (type, status). The funnel must reconcile EXACTLY, e.g. 08-27:
379 intents = 254 band_gate + 54 capacity + 37 reentry_cap + 33 placed
+ 1 dry_run; wire_pending 37 = 33 placed + 4 error. A bucket that
doesn't close = a writer you don't know about — stop and find it.

Known baselines: fill-in-place ~30%; gone_before_cancel ~20% of cancels
(= exit-path adverse selection, tracked, not yet actioned); rails bind
72.8% of band survivors. Verdicts on trades ONLY from venue-verified
settles (scripts/settle_truth.py) — never from ledger inference alone.
