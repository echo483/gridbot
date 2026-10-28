# Corpus pipeline

## Two layers, deliberately different

- **Raw capture** (`data/grid/*.jsonl`, `data/pm/*.jsonl`, `data/pmus/*.jsonl`):
  verbatim, append-only, never interpreted at capture time. **This is the
  source of truth**: replay, latency measurement, instrument verdicts and the
  analytical lake are all derived from it and can be rebuilt from it.
- **Derived training rows** (`data/corpus/training_rounds-YYYYMMDD.jsonl`):
  one row per `(series, map, round[, bin])` with a `features` object and a
  `label`, keyed by `row_key` (`series:map:round[:b<n>]`) and tagged with
  `source_file` / `source_sha256` / `captured_at` / `ingested_at`. The round
  model trains on these.

Raw is evidence and the derived layer is features. Keep them separate.

## Building training rows

`uv run python -m gridbot.corpus.ingest [--days N] [--dry-run] [--series ID]`
walks finished CS2 series in `data/grid/series_state-*.jsonl` and builds rows
with `gridbot.corpus.build`. Semantics: the winner oracle labels by first
observation, n_bins=4, and the full-buy cut is pinned at 13,400. Ingest is
idempotent per series (`data/corpus/.ingested.json`). It runs by hand for
now; the hourly timer left with the Supabase sink, and scheduling returns with
the homelab deploy (spec M1). GRID test fixtures
(series 2/28/99) are never ingested. Dota rows wait until the generic query
captures enough shape to define features honestly.

**History.** Until 2026-10 the rows were also UPSERTed hourly to a Supabase
Postgres project (shared with the predecessor `cs2agent`, 22,273 historical
rows on 2026-08-24), and raw day files were backed up there nightly. Both were
removed. The planned Parquet/DuckDB lake takes over the research role, and the
corpus has one home plus a backup that is a copy of it, not a second database.
The removed sink, pull and backup scripts remain at tag `evidence-2026-10`.

## Leak filter (BINDING — corpus-inference-v1 F1, verified 2026-08-24)

~13% of the historical 22k rows (47% of bin-3) are post-round transition snapshots
whose `score_lead`/`rounds_played`/`man_advantage` already contain the labeled round's
outcome (GRID updates scoreboard and segment flags non-atomically at the round
boundary). Consequences and rules:

- **New rows are clean at the source**: `gridbot.corpus.build` drops any candidate
  where `rounds_played != round - 1` or the current segment isn't the labeled round.
- **Historical rows must be filtered at training/backtest time**: drop rows failing
  `rounds_played + 1 == round_number` against BOTH `features.round_number` and the
  top-level `round_number` (2,898 rows → 19,375 honest rows).
- **Every inherited calibration number (incl. Brier 0.1514) is suspect** until
  recomputed under this filter. Registry entry: `CORPUS-F1-LEAKFILTER`.
  Full report: `docs/experiments/2026-08-24-corpus-inference-v1.md`.
