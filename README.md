# gridbot

A tick-level research and execution platform for esports prediction markets:
capture GRID Open Access game state, price it against Polymarket US books, and
pre-register every hypothesis before its instrument runs. Successor to
`cs2agent` — same empirical discipline, fraction of the code.

## What is actually here

| layer | location | what it is |
|---|---|---|
| capture | `src/gridbot/capture/` | GRID SS/CD + Polymarket US book capture into day-file JSONL — the corpus moat |
| corpus | `src/gridbot/corpus/` | day files → archive ingest + row builders |
| scorecard | `src/gridbot/scorecard/` | the statistical kernel: one implementation each of Wilson, series-clustered bootstrap, Brier / ECE / Murphy decomposition, executable-edge pricing, Kelly + barrier + Monte-Carlo risk |
| instruments | `src/gridbot/instruments/` | pre-registered hypothesis lanes and the verdict engine |
| executor | `src/gridbot/executor/` | order lifecycle, gates, ledger, venue reconciliation |
| model | `src/gridbot/model/` | LightGBM round-win model (`q`) and the frozen map aggregation |
| sizing | `src/gridbot/sizing/` | fractional-Kelly stake sizing |
| evidence | `scripts/`, `docs/analysis/` | frozen measurement instruments; their outputs are cited by the registry |

## Status

Research and execution platform, pre-PASS. `H-MAPENTRY1` is the only lane with
a pre-registered bar and a rendered holdout verdict; that verdict is
**not-falsified** (the model beats the book mid as a forecaster and is
calibrated) with clause (iii) under-powered at 39/60. **No lane has a
pre-registered PASS**, so `dry_run=True` remains the default. Arming for real
money is the operator's own act and is documented in `docs/LIVE_FIRE.md` —
the executor never arms itself.

### Verified baseline (fresh clone, 2026-10-16)

```
uv run ruff check .   # All checks passed
uv run pytest         # 1009 passed, 1 failed, 1 skipped
```

The single failure is `tests/test_postshock_lag.py::test_real_day_file_smoke`.
It fails **by design**: the capture corpus lives in the gitignored `data/`, so
on a fresh clone the end-to-end smoke test refuses to skip silently and says
so. Restoring the corpus is workblock WB0 in `docs/WORKBLOCKS.md`.

## Read in this order

1. `CLAUDE.md` — the iron rails (merge-blocking)
2. `docs/STATE.md` — what is proven, what is dead, what is running
3. `docs/WORKBLOCKS.md` — the current plan, ownership, and open decisions
4. `docs/experiments/registry.jsonl` — every frozen hypothesis (append-only)
5. `docs/MAP.md` — the codebase floor plan
