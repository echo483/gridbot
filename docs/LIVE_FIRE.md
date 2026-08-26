# Live fire — arming checklist

The executor (`gridbot.executor.engine`) is BUILD-READY and runs dry_run by
default. Dry-run and live share one decision path; the only difference is
whether the final wire call in `pmus.place()` happens. Going live is the
user's own act (iron rail 3) — every item below must be true, and the gate
re-checks each engine cycle, so removing any item drops it back to dry_run
within seconds.

## The gate (`gridbot.executor.limits.check_armed`)

1. `GRIDBOT_LIVE=1` set in the engine's environment by the user.
2. `POLYMARKET_US_KEY_ID` in the OS keyring (`scripts/setup_secrets.py`;
   Ed25519 keypair from https://polymarket.us/developer).
3. `POLYMARKET_US_SECRET_KEY` in the OS keyring.
4. `docs/COMPLIANCE.md` present with the explicit affirmation line
   "eligibility confirmed: yes".

## First fire — 2026-08-25 20:27 PDT (VERIFIED, user-armed)

One $0.84 far-from-touch post-only order on `tec-mlb-nlchamp-2026-09-27-lad`
(0.21 × 4 vs bid 0.438), placed and cancelled by the user running
`GRIDBOT_LIVE=1 uv run python scripts/live_fire_test.py`:

| leg | latency |
|---|---|
| preview (incl. one-time auth setup) | 2246 ms |
| **place ack** (order `C3KCSKP1PFSG`) | **394 ms** |
| visible in orders.list | 101 ms |
| **cancel ack** | **137 ms** |
| confirmed gone | 120 ms |

Place+cancel round trip **531 ms** — 2–4% of the 13–27 s information lead.
Fill honesty confirmed (cumQuantity 0). Machine-readable copy:
`data/executor/wire-baseline.json`. Steady-state order ops are the
100–400 ms legs; the 2.2 s preview cost is paid once per process.

## Caps (env-overridable, hard-clamped in code)

| knob | default | ceiling |
|---|---|---|
| `GRIDBOT_MAX_STAKE_USD` (per order) | $25 | $100 |
| `GRIDBOT_MAX_OPEN` (open positions) | 3 | 10 |
| `GRIDBOT_MAX_DAILY_LOSS` | $50 | $500 |

## What live-small is FOR

Mechanism validity comes from data (shadow cells vs frozen bars — free).
Live $25 clips answer the only questions data can't: real fills, queue
position, true slippage. Fill/PnL evidence from live orders feeds the SAME
frozen bars the shadow engine reports against.

## Before the first live order (joint session)

- Review `pmus.place()` together — it is deliberately one small function.
- Verify a market EXISTS for the target title (2026-08-25 evidence: the US
  venue lists real CS2 events (early "empty" read was a discovery-pagination artifact); the pmus
  watch timer alerts when that changes).
- Run `GRIDBOT_LIVE=1 uv run python scripts/live_fire_test.py` — one $1
  far-from-touch post-only order: place → list → cancel → gone, with
  latency per leg and a MANUAL ACTION block if cancel ever fails.

## Adversarial review 2026-08-25 (ox-alpha, card t_6fd66ca8) — all fixed

B1 cancel-abandonment (retry+manual block) · S1 create-retry double-order
(creates are never resent) · S2 ambiguous ack ≠ rejection · S3 open
exposure from ledger, restart-safe · S4 bid-anchored far-from-touch +
fill honesty · M1 explicit compliance affirmation · M2 honest gate wording
· M3 floor price rounding · M4 client timeout · M6 capacity skips retry.

## Closed pre-live-small gates (2026-08-26)

- **Stale-maker cancel pass** (M5): DONE — armed engine cancels its resting
  quotes after 150s (clean cancel releases the exposure slot; "gone" =
  likely fill, slot stays held; failed cancel → MANUAL ACTION log + retry).
- **Venue-side reconciliation** (S3 residue): running skeleton — armed
  engine diffs ledger vs orders.list every ~60s (gone-from-venue, orphans,
  partial fills) and writes reconcile rows.

## Arming runbook — live-small session (H-MAPENTRY1 clips)

The systemd executor NEVER arms (its unit sets no GRIDBOT_LIVE). An armed
session is a foreground terminal the user starts and can Ctrl-C:

```
systemctl --user stop gridbot-executor      # one engine at a time
GRIDBOT_LIVE=1 GRIDBOT_MAX_STAKE_USD=3 GRIDBOT_MAX_OPEN=2 \
  GRIDBOT_MAX_DAILY_LOSS=10 uv run python -m gridbot.executor.engine
# ... Ctrl-C to disarm ...
systemctl --user start gridbot-executor     # back to dry-run daemon
```

**Band gate (frozen 2026-08-26 pre-first-fire, H-MAPENTRY1-BANDGATE1):**
armed placements only while 0.50 ≤ P_map ≤ 0.80. Out-of-band qualifying
rows are ledgered as `skipped:band_gate` and never placed. **HARM
REDUCTION, not edge selection** (cs2agent ADR-0038 + H-BAND1 on our
32-series holdout): above P_map 0.8 the aggregation measures −8.4/−9.4/
−9.5pts settled edge with by-series CIs excluding zero; the kept band has
NO demonstrated positive edge (all holdout CIs include zero — the earlier
+0.026 was post-hoc selection, corrected by its own author). In-band clips
buy fill-mechanics evidence. Expect FEWER clips (~27% of crossings
in-band) and RARE fills. **Pre-registered read of first-night data (frozen
before any arrived):** a thin in-band ledger is CONSISTENT with the
mechanism (model under-confident in-band → book rarely sits below our
too-low fair); in-band entries filling READILY is the adverse-selection
warning. Judge fill RATE per in-band crossing, not fill count.

Cap rationale at the $26 bankroll: $3/order ≈ quarter-Kelly on the
strongest calibrated cell; 2 open max ≈ 23% deployed worst-case. Every
order the armed engine can place is: H-MAPENTRY1 (registered), frozen
race-formula fair value, price ≥ 4 points below fair AND ≤ ceiling,
post-only (exchange rejects if it would match), integer contracts, one
entry per map book, auto-cancelled at 150s if unfilled.

KNOWN OPEN ITEM: settles are not auto-written yet, so the daily-loss cap
does not bind on realized PnL — total risk is bounded instead by
stake × max_open (~$6 resting at any moment). Do not raise caps until
fills→settles reconciliation writes real rows.
