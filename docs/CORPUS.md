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

## gridbot ingest (to build — the loop's next work item)

`gridbot.corpus.ingest`: walk finished CS2 series in `data/grid/series_state-*.jsonl`,
build training rows (port the row/feature semantics faithfully from cs2agent's
`data/grid_history.py::build_training_rows`; same `row_key` convention so the two
corpora merge seamlessly), UPSERT with `on_conflict=row_key`, tag
`source_file`/`source_sha256`/`captured_at`. Never DDL from the app; schema
changes go through migrations. Dota rows wait until the generic query captures
enough shape to define features honestly.
