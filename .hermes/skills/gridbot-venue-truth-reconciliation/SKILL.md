---
name: gridbot-venue-truth-reconciliation
description: Use when interpreting reconcile/ledger drift, gone_from_venue alarms, cancels on filled orders, orphan adoption, or manual exits — anything where the venue's word must be checked against the ledger before believing it.
---

# gridbot venue-truth reconciliation

Ledger vs venue semantics learned across 3 armed nights. Sources:
`docs/learnings/2026-08-2{6,7}.md`, STATE.md #1b/1c/11c/11e, commit 403f62a,
manual-close reconciliation commit 7694553.

## The venue lies in specific, documented ways

1. **`fetch_venue_orders` returning [] on error reads as "venue empty"** →
   phantom `gone_from_venue` reprints every cycle. Night 1: 156 reconcile
   rows, C3RBPBKHTFSK ×143, all disproven by clean cancels. Night 2: same
   spam for C46Q1K6QCFSQ. TRUE anomaly count both nights: **zero**
   (unknown_on_venue=0, partial_fills=0). Fix shipped (403f62a): fetch error
   → None + skip the diff; clean cancels subtracted from open_ledger_orders;
   require gone_before_cancel to persist.
2. **Venue reports cancel success on already-FILLED orders** (confirmed in
   production: all night-1 fills were full fills whose TTL cancels returned
   clean "cancelled"). Never read cancel status as fill truth — fills.sync /
   `settle_truth.py` is the truth path. This is why SECONDSIDE1 terminal
   requires venue-confirmed zero-fill, and why partially-filled-then-cancelled
   books keep counting exposure until settle.
3. **`ledger_open` climbing without decrementing** = the held-slot artifact
   (no fill→settle writer at the time), not real exposure. Actual concurrent
   resting exposure was ≤2 when the ledger showed 3.

## Manual-exit release (validated E2E night 3, incl. netPosition)

The user's manual close at 0.99 (bought 0.42) proved exits need their own
ledger rows and reconcile pairing so PnL stays venue-true. The shipped
manual-close pass went through opus review (2 BLOCKERs + 5 MAJORs fixed:
positions payload shape, availablePositions, resolution precedence,
order-scoped skip, newest-fill aging, revisable settle). RESIDUAL: the only
positions() capture was FLAT — at the next armed fill, pull
`client.portfolio.positions()` read-only WHILE HOLDING and confirm shape;
disable the pass on mismatch.

## Orphan adoption

- Crash between wire ack and outcome row → order no module sees; the
  ~60s pre-adoption window means a fill inside it has ZERO ledger
  representation (audit F9, open). Resting orphans heal via adoption.
- Adopted orphans carry unknown side → SECONDSIDE1 blocks every new side
  (fail closed).
- Ambiguous-ack orders consume a REENTRY slot but no max-open capacity until
  adoption cancels them.

## Procedure when reconcile rows alarm

1. Check the order's terminal row in `data/executor/intents-*.jsonl`
   (status "cancelled" vs "gone_before_cancel", TTL age ~150s).
2. Verify against fills/settle truth, not cancel acks.
3. If fetch-failure-driven, expect the pattern to repeat every cycle until
   the None+skip fix path applies — check commit 403f62a behavior is live.
4. Report true anomalies separately from phantom reprints; zero true
   anomalies for 2 straight nights is the record, don't manufacture them.

## Pitfalls

- night_alert spam ≠ risk: direction of the phantom-gone bug was safe
  (false alarm only; TTL cancel is the real guard) — fix volume, don't panic.
- Shorts fill at 1−price; venue_pnl on settle rows is truth, the ledger's
  maker formula misstates shorts (conservative direction).
- Secret Service failure kills truth ingestion both ways (crash + locked
  keyring); 5s-timeout keyring reads + last-good cache shipped in 403f62a.
- Disarmed RESTART stops truth ingestion (no pmus client → no reconcile/
  fills/adoption until re-arm) — audit M4, accepted.
