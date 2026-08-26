# Live fire — arming checklist

The executor (`gridbot.executor.engine`) is BUILD-READY and runs dry_run by
default. Dry-run and live share one decision path; the only difference is
whether the final wire call in `clob.place()` happens. Going live is the
user's own act (iron rail 3) — every item below must be true, and the gate
re-checks each engine cycle, so removing any item drops it back to dry_run
within seconds.

## The gate (`gridbot.executor.limits.check_armed`)

1. `GRIDBOT_LIVE=1` set in the engine's environment by the user.
2. `POLYMARKET_PRIVATE_KEY` in the OS keyring (`scripts/setup_secrets.py`).
3. `POLYMARKET_FUNDER_ADDRESS` in the OS keyring.
4. `docs/COMPLIANCE.md` present with the eligibility confirmation.

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

## Deferred until BEFORE scaling past $1 tests (deliberate)

- **Stale-maker cancel pass** (M5): engine cancels resting GTC quotes after
  a TTL; today nothing cancels unfilled makers automatically.
- **Venue-side reconciliation** (S3 residue): on engine start, consult
  orders.list/positions so ledger and venue agree about open exposure.
