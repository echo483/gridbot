# gridbot

A research and execution system for esports prediction markets. It captures
live CS2 game state from GRID and Polymarket US order books at tick level,
tests trading hypotheses against held-out data under pre-registered pass/fail
criteria, and trades only through a capped, post-only executor that starts in
dry-run and never arms itself.

**The question it asks:** GRID reports round outcomes before the market
reprices (median lead ≥ 13.1 s, n = 293 book moves over 682 round ends). Is
there a tradeable edge in that gap after realistic fills, or only a timing fact?

## Layout

| layer | path | what it does |
|---|---|---|
| capture | `src/gridbot/capture/` | GRID series state + Polymarket US books → append-only JSONL day files (the source of truth) |
| lake | `src/gridbot/lake/` | JSONL → lossless Parquet → typed event / result tables → data-quality gates (DuckDB) |
| lab | `src/gridbot/lab/` | hypotheses as data, one scan engine, fill model, clustered and deflated metrics, HTML report |
| scorecard | `src/gridbot/scorecard/` | statistics kernel: Wilson intervals, series-clustered bootstrap, Brier / ECE, executable-edge pricing, Kelly and risk |
| model | `src/gridbot/model/` | LightGBM round-win model, calibrated |
| instruments | `src/gridbot/instruments/` | pre-registered hypothesis lanes and the verdict engine |
| executor | `src/gridbot/executor/` | order lifecycle, gates, caps, ledger, venue reconciliation |
| corpus | `src/gridbot/corpus/` | derived training rows and the map-winner oracle |

## Status

Research stage. No hypothesis has passed its pre-registered bar, so live
trading stays off. The strongest lane (`H-MAPENTRY1`) survived its holdout but
is under-powered. Ideas that looked good in-sample and failed honest
measurement are recorded with the test that killed them in
[`docs/FALSIFICATION.md`](docs/FALSIFICATION.md).

In progress: an always-on capture host, an execution-realistic backtester, and
a C++ decision core shared by backtest and live, with measured tail latency.

## Run

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run pytest            # hermetic: no network, no credentials, no data
```

With capture data on disk:

```bash
uv run python -m gridbot.lake all        # build the Parquet lake and run quality gates
uv run python -m gridbot.lab registry    # list registered hypotheses
```

Credentials, when needed, live in one permission-checked file and nowhere
else; see [`SECURITY.md`](SECURITY.md).

## Docs

- [`docs/ENGINEERING.md`](docs/ENGINEERING.md): the rules the code follows
- [`docs/STATE.md`](docs/STATE.md): what is measured, what runs
- [`docs/MAP.md`](docs/MAP.md): codebase map
- [`docs/experiments/registry.jsonl`](docs/experiments/registry.jsonl): every pre-registered hypothesis (append-only)
