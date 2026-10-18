# gridbot-lab — research layer: options memo (DRAFT for the user, not a spec)

**Status:** decision memo, 2026-09-03. Written after the user chose "research
layer first" for gridbot-lab. Nothing here is built; the design is approved
section by section when the user is back, then a spec is written, then a
plan. Per `CLAUDE.md` (now `docs/ENGINEERING.md`) the next repo takes gridbot's
LESSONS, not its code.

## What "research layer" has to mean here

The user's words: fit it into the framework and the runtime, and know
"what's actually computing — real quant scores." So the deliverable is not
notebooks that produce numbers once. It is a **scorecard library** that
computes the same numbers from three data sources — backtest, shadow, live —
so a strategy's evidence is comparable across its whole life, and the
runtime (NautilusTrader) is one consumer of it rather than a separate world.

What gridbot proved matters (all of it learned the hard way, see
`docs/analysis/2026-09-03-integrity-pass.md` and the registry):

- Rows are not independent: every interval must resample SERIES (the
  row-pooled "+19.8% skill" evaporated under a clustered test).
- Calibration must be read on the TRADED subset (qualifying rows were 21pt
  overconfident while the broad population looked fine).
- Prices must be executable (mid-priced edges did not survive the ask).
- Venue semantics live at the adapter boundary and need a venue-record test
  (the short-side wire bug).
- The verdict rule is frozen BEFORE the instrument runs, and held-out data
  decides it (registry discipline).
- Latency has to be measured on the venue you trade (timing chain).

## The scorecard — what actually computes

One module per family, each a pure function over a typed frame
(polars/pandas) plus a series-clustered bootstrap for every interval:

| family | numbers | source in gridbot to port as LESSONS |
|---|---|---|
| calibration | Brier + skill vs base rate; ECE (quintile AND decile, both reported); reliability table; per-bucket gap on the traded subset | `scripts/quant_eval.py`, `scripts/pmap_calibration.py`, `instruments/mapentry_verdict.py` |
| discrimination | AUC, log loss, Brier decomposition | `model/train.py` metrics |
| edge | settled edge at EXECUTABLE price (ask/1−bid), fill-conditional split, per-tercile robustness | `scripts/fill_conditional_edge.py`, `crossin_backtest.py`, NOTE7's rested-only rule |
| risk | Kelly fraction (quarter), Monte-Carlo bankroll paths, barrier probabilities, drawdown | `quant_eval.py` layer 3, `sizing/kelly.py` |
| latency | GRID-observation → book-move lead distribution, pipe legs | `scripts/timing_chain.py` (new) |
| verdict | frozen bar evaluation on a declared holdout; PASS / FALSIFIED / STARVED with n and CIs printed beside every claim | registry rows + `mapentry_verdict.py` |

Every function returns value + CI + n + n_series. No number prints without
its interval. That is the "real quant score" contract.

## Three ways to fit it into the runtime

**A. Scorecard library + DuckDB lake + NautilusTrader backtests (recommended).**
Capture writes Parquet (one table per stream: grid_state, pmus_bbo,
mappings, orders, fills, settles); DuckDB is the query layer for research
and the user's SQL surface; NautilusTrader's `ParquetDataCatalog` reads the
same Parquet through custom `Data` types (GRID round state, PMUS book), so a
strategy backtests on exactly the rows research scored. Strategy events
(orders, fills) come back out as Parquet and the scorecard evaluates the
backtest with the same functions used on shadow and live. Pros: one data
model, one scorecard, Rust-speed replay, the runtime is the same code path
live. Cons: Nautilus custom data + adapter work is real engineering up
front; Polymarket US and GRID adapters do not exist — we write them.

**B. Notebooks first (Jupyter + pandas over Supabase).**
Fastest to a plot. Cons: numbers are not reproducible artifacts, the runtime
never sees them, and it repeats gridbot's script sprawl — 61 scripts that a
reader cannot compare. Rejected as the primary path; fine as the learning
surface on top of A.

**C. QuantConnect / LEAN style.**
LEAN's research (QuantBook) + backtest engine is mature, but C#-centric,
equity/futures-shaped, with no path to GRID or Polymarket US data types or
a latency-sensitive adapter; we would fight the framework. Take its IDEAS
(research/backtest parity, the algorithm-framework separation of alpha,
portfolio construction, risk, execution) and implement them in A.

## Design questions to settle when you are back (one at a time)

1. Data lake shape: one Parquet dataset per stream partitioned by day (my
   default), or DuckDB tables as the source of truth?
2. Which scorecard family first: calibration (feeds every verdict) or
   latency (feeds the rail-B decision)? My default: calibration, because the
   timing chain already runs in gridbot and its output can be imported.
3. Holdout mechanics: temporal by series-first-seen (gridbot's rule) as the
   library default?
4. Nautilus: custom `Data` types for GRID state and PMUS BBO first, adapters
   later — agree?
5. Visualization: plotly (interactive, notebook + HTML export) vs matplotlib
   (static, paper-grade). Default plotly, one theme, every chart carries n
   and CI.

## Not in scope for the first sub-project

Live trading, the Kuzu knowledge graph, LLM slow-loop agents, Supabase
writes. Reads from Supabase and the local corpus only.
