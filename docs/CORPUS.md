# Corpus pipeline (verified 2026-08-24)

The durable corpus lives in the Supabase project `cs2agent`
(`pbwchzjwukxngeptmuju.supabase.co`, Postgres 17). Verified with the new
`sb_secret` key (legacy JWT keys are disabled): **training_rounds = 22,273 rows,
events = 153,460** as of tonight. The write key sits in the OS keyring under
service `gridbot`, entry `SUPABASE_SERVICE_ROLE_KEY`.

## Two layers, deliberately different

- **Local raw captures** (`data/grid/*.jsonl`, `data/pmus/*.jsonl`,
  `data/pm/lake-cut/*`): verbatim, append-only, never interpreted at capture
  time. Ground truth for replay, latency measurement, and instrument verdicts.
- **Supabase corpus** (`public.training_rounds`): derived, provenance-tagged
  training rows — one row per `(series, map, round[, bin])`, idempotent UPSERT on
  `row_key` (`series:map:round[:b<n>]`), `features` jsonb + `label`. RLS:
  service-role writes, anon reads. `public.events` is the observability stream.

They are *supposed* to be different: raw is evidence, corpus is features.

## What the data actually is now (2026-10-18)

`data/` is gitignored — a clone has **no data**. The authoritative archive is a
single drive with a `MANIFEST.md`; this section is the repository's copy of that
contract.

| stream | size | status |
|---|---|---|
| `data/grid/series_state-*.jsonl` | 4.8 GB | **irreplaceable** — raw GRID Open Access live feed, 100 day-files, cannot be re-fetched |
| `data/pm/lake-cut/bbo/` | 2.0 GB | 146,444,552 best-bid/ask state changes on the event clock |
| `data/pm/lake-cut/ladders/` | 88 MB | 748,870 full book-depth snapshots (`book` + `rest_books`) |
| `data/pm-catalog/` | 3.9 GB | Polymarket market catalog — needed to join PM markets to GRID series |
| `data/pmus/` | 340 MB | PMUS BBO tape, the execution-side observations |
| `data/shadow/` | 97 MB | shadow-lane predictions vs market |
| `data/corpus/` | 81 MB | round-level corpus (caveats below) |

**The 385 GiB raw tape is gone, and that was measured, not assumed.** A census
over 400 MB of `books-20260927.jsonl` found 614,677 of 615,826 WebSocket frames
were `price_change` events, each repeating the same `market`/`best_bid`/
`best_ask` on every level entry. `scripts/cut_evidence_lake.py` reduced
408.97 GB → 2.154 GB (**190x**) in 15.8 min, keeping the ladders and collapsing
the touch to state transitions.

**What the cut costs, stated plainly:** `price_change` entries that did not move
an asset's BBO were dropped, so per-level replenishment *away from the touch* is
not in the archive. Book state is exact at every ladder timestamp; between
ladders only the touch is. Any queue-dynamics study needs a re-recorded tape on
the always-on host — this archive does not substitute for one.

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

## What the corpus cannot support

Stated here so no later reader has to rediscover it:

- **`prior_round_p` is a constant 0.5 in every row** — a dead feature. The honest
  naive Brier baseline is 0.2500, not whatever an inherited "calibrated" number
  claimed.
- **One snapshot per round, no timestamped book quotes.** Every
  execution-geometry question — entry timing, fill rate, slippage,
  time-to-convergence — is unanswerable from this corpus. Those require the tapes
  (`data/pmus/`, `data/pm/lake-cut/`), not the round rows.
- **`p_map` is retired as a fair-value estimator.** When the model disagreed with
  the book the book was ~90% right in every tercile, so a recalibrated `p_cal`
  can only ever be harm reduction. `q_round` is retained and is still calibrated
  (Brier 0.1105, skill +0.063, ECE 0.031 over 1,984 resolved instances).

Use this corpus for lineage — what the round-win model calibrated to — not as a
feature store.

