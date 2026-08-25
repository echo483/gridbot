# Corpus pipeline (verified 2026-08-24)

The durable corpus lives in the Supabase project `cs2agent`
(`pbwchzjwukxngeptmuju.supabase.co`, Postgres 17). Verified with the new
`sb_secret` key (legacy JWT keys are disabled): **training_rounds = 22,273 rows,
events = 153,460** as of tonight. The write key sits in the OS keyring under
service `gridbot`, entry `SUPABASE_SERVICE_ROLE_KEY`.

## Two layers, deliberately different

- **Local raw captures** (`data/pm/*.jsonl`, `data/grid/*.jsonl`): verbatim,
  append-only, never interpreted at capture time. Ground truth for replay,
  latency measurement, and instrument verdicts.
- **Supabase corpus** (`public.training_rounds`): derived, provenance-tagged
  training rows — one row per `(series, map, round[, bin])`, idempotent UPSERT on
  `row_key` (`series:map:round[:b<n>]`), `features` jsonb + `label`. RLS:
  service-role writes, anon reads. `public.events` is the observability stream.

They are *supposed* to be different: raw is evidence, corpus is features.

## gridbot ingest (BUILT 2026-08-24, e2e-verified)

`gridbot.corpus.ingest`: walks finished CS2 series in `data/grid/series_state-*.jsonl`,
builds training rows (spec-faithful port of cs2agent's semantics: winner oracle with
first-observation-wins labels, n_bins=4, full-buy cut pinned at 13,400; same `row_key`
convention so the two corpora merge seamlessly), UPSERTs with `on_conflict=row_key`,
tags `source_file`/`source_sha256`/`captured_at`/`ingested_at`. Idempotent per series
(`data/corpus/.ingested.json`); local audit copy under `data/corpus/`. Runs hourly via
`gridbot-ingest.timer`. GRID test fixtures (series 2/28/99) are never ingested. Never
DDL from the app. Dota rows wait until the generic query captures enough shape to
define features honestly.

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
