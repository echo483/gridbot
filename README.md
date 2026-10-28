# gridbot

A tick-level research and execution system for esports prediction markets.
It captures GRID live CS2 game state and Polymarket US order books, tests
each hypothesis against held-out capture under a pre-registered bar, and
trades only through a deterministic, capped, post-only executor that never
arms itself.

**The mechanism:** GRID reports round outcomes before the venue reprices.
Measured lead p50 +13.1 s (lower bound) on 293 book moves over 682 round ends.

## What is here

| layer | location | what it is |
|---|---|---|
| capture | `src/gridbot/capture/` | GRID series state + Polymarket US book capture into day-file JSONL — the corpus, and the source of truth |
| corpus | `src/gridbot/corpus/` | raw day files → derived training rows, under a binding leak filter |
| scorecard | `src/gridbot/scorecard/` | the statistical kernel: one implementation each of Wilson, series-clustered bootstrap, Brier / ECE / Murphy decomposition, executable-edge pricing, Kelly + barrier + Monte-Carlo risk |
| instruments | `src/gridbot/instruments/` | pre-registered hypothesis lanes and the verdict engine |
| executor | `src/gridbot/executor/` | order lifecycle, gates, caps, ledger, venue reconciliation |
| model | `src/gridbot/model/` | LightGBM round-win model (`q`), calibrated live |
| sizing | `src/gridbot/sizing/` | fractional-Kelly stake sizing |
| evidence | `scripts/`, `docs/analysis/` | measurement instruments and the dated reports they produced |

## Status

Pre-PASS. `H-MAPENTRY1` is the only lane with a rendered holdout verdict:
**not falsified** (the model beats the book mid and is calibrated), with
clause (iii) under-powered at 39/60. **No lane has a pre-registered PASS**, so
`dry_run=True` stays the default, and arming is the operator's own act
(`docs/LIVE_FIRE.md`). Twelve ideas that looked good in-sample were killed by
more honest measurement; they are recorded in `docs/FALSIFICATION.md`.

Next: an always-on capture host, a Parquet/DuckDB analytical lake, an
execution-realistic backtester, and a C++ decision core shared by backtest
and live, with a published latency budget
(`docs/specs/2026-10-28-buildout-spec.md`).

### Verified baseline (clean tree, 2026-10-28)

```
uv run ruff check .   # All checks passed
uv run pytest         # 711 passed, 1 skipped
```

The suite is hermetic: no network, no secrets, no capture data needed.

## Read in this order

1. `docs/ENGINEERING.md` — the binding rules
2. `docs/STATE.md` — what is proven, what runs
3. `docs/FALSIFICATION.md` — what was tried, what killed it, what survived
4. `docs/WORKBLOCKS.md` — the plan, ownership, and open decisions
5. `docs/experiments/registry.jsonl` — every frozen hypothesis (append-only)
6. `docs/MAP.md` — the codebase floor plan
