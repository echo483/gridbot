---
name: gridbot-maker-entry-thin-books
description: Use when designing, changing, or debugging maker-entry behavior on thin PMUS map books (TTL, quote level, gates, re-entry, venue outages). Distills 3 armed nights of live-fire evidence; read before any Queue-13-style amendment PR.
---

# gridbot maker entry on thin books

The playbook that took 3 armed nights (2026-08-26→08-27) to learn. Facts
first: `docs/learnings/2026-08-26.md`, `2026-08-27.md`, queue spec
`docs/superpowers/specs/2026-08-27-entry-gate-loosening.md`, STATE.md #11d/11e.

## Verified facts (live, not theory)

- **Fill rate at fair−4pts was 0/3 night 1, 2/9 night 2** — fills, not edge,
  are the binding constraint. A 27c maker quote on a book trading 7–9% never
  fills in any TTL.
- **TTL cancel fires on target** (150.4–152.0s across all unfilled orders,
  both nights) — stale-maker cancel is proven; do not re-verify.
- **Fills came fast or never**: both night-2 fills landed ≤42.5s vs a 150s
  TTL; quotes resting longer stayed dead.
- **Queue-13 backtest (n=37, STATE #11d)**: rule (a) bid+tick 20% fill /
  −0.20 edge-per-fill; inside-spread (b) 37% / −0.13 — adverse-selection
  signature (would-filled quotes lose on average). Numbers only; no rule
  change licensed without pre-registration.
- **Night-3 splits (n=10 fills, describe-only)**: every winner ≥11pts under
  fair, every loser <9pts; both losing shorts were mid-band (~0.59) model-flip
  fades. But the entry_quality study (n=45 full population) CONTRADICTS the
  fills read — ≥20pt discounts were WORST, 10–20pt best — so treat splits as
  directional and never as an amendment justification.

## Rules of the playbook

1. **Quote distance must be measured against the ACTIVE book side**, not just
   frozen fair. Candidate rule on record: `max(bid + tick, min(fair−4pts,
   ask − tick))` — stay inside the spread when the spread is inside the
   discount envelope; the fair−4 edge floor never moves.
2. **TTL calibration is STATE #11, a user/registered decision.** 150s is
   aggressive on thin books (the book must travel to us). Longer TTL or
   mid-quote lever (L4/L3) needs the cancel-audit: did the book reach our
   price <300s?
3. **Second-side gate (H-MAPENTRY1-SECONDSIDE1, registry #21)**: never enter
   a side while the opposite side (or unknown-side adopted orphan) has
   non-terminal presence on the slug. Live loss it prevents: held LONG
   0.66×4 then entered SHORT 0.52×5 = 1.18/pair cost, structurally
   guaranteed loss. Terminal requires settle or clean cancel + venue-confirmed
   zero-fill — the cancelled ack lies.
4. **Re-entry-after-clean-cancel** is a pre-register-first amendment
   (STATE #8): observed cost was 3 better-price chances blocked after a clean
   TTL cancel; churn risk against.
5. **All gates are maker-only; we never cross the ask.** Widening levers
   (band width L1, discount L2, quote level L3, TTL L4) each name the data
   that must exist BEFORE the flip — see the spec's lever table.
6. **Shadow-first rails**: run widened gates as shadow cells ≥1 session,
   compare vs frozen gates on the same stream, before touching armed params.
   Any flip = registry amendment row → engine PR → arm (user's act).

## Venue 500-outage behavior (live-evidenced night 2/3)

- 4/12 wire attempts → `InternalServerError` ate a whole quote window; one
  retry w/ backoff is the smallest fix.
- Night 3: create endpoint down ~4.5h; fail-soft worked (4 triggers lost, no
  wedge). Do NOT retry creates (double-order risk); cancels ARE retried ×3.
- Ambiguous 2xx = `error:ambiguous_ack`, never "rejected".

## Pitfalls

- Fills arriving READILY at fair−4 = the pre-registered adverse-selection
  warning, not a win — judge fill rate per crossing.
- "cancelled" status is NOT fill truth — venue reports cancel success on
  already-filled orders (confirmed all night 1; fills.sync / settle_truth is
  the truth path). Shorts fill at 1−price: use venue_pnl, not ledger formula.
- 67% of intents die at band gate; 72.8% of survivors at caps — Queues 16/17
  are measurement lanes, not change licenses.
- Never pool postquote drift unoriented across sides.
