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

- Review `clob.place()` together — it is deliberately one small function.
- Verify a market EXISTS for the target title (2026-08-25 evidence:
  tier-3 CS2 has zero PM books; LoL scope from GRID is the unlock).
- Start engine with `--once`, watch one intent land as "dry_run", then arm.
