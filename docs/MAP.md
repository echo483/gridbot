# Codebase map

The floor plan. Orientation order: **`docs/ENGINEERING.md` (the rules) →
`docs/STATE.md` (what is proven, what is dead, what runs) →
`docs/WORKBLOCKS.md` (the live plan) → `docs/experiments/registry.jsonl`
(the frozen hypotheses)**. This file is navigation; the rules live in
ENGINEERING.md and are not repeated here.

Layers follow `docs/ENGINEERING.md` § Layers: **data → research → execution**,
with `shared` primitives and `scripts/` as the evidence log. Dependencies point
inward; a lower layer never imports a higher one.

## src/gridbot — the system

| plane | package | what it is |
|---|---|---|
| shared | `config.py` | keyring-only secrets (5s-timeout reads + bounded last-good cache), non-secret runtime knobs, data dir |
| shared | `jsonl.py` | the one tolerant JSONL iterator (skips and counts torn lines) |
| shared | `graphql.py` | GRID GraphQL client |
| shared | `ratelimit.py` | GRID rate budget |
| shared | `redaction.py` | keeps resolved secrets out of logs |
| shared | `notify.py` | the single egress point for alerts; transport is configuration |
| data | `capture/` | GRID poller, venue book/discovery capture, day-file supervisor, the PMUS BBO tape recorder. **The corpus moat — never break continuity.** |
| data | `corpus/` | day files → archive ingest + row/feature builders |
| research | `scorecard/` | the statistical kernel: Wilson, series-clustered bootstrap, Brier/ECE/Murphy, executable-edge pricing, Kelly/barrier/Monte-Carlo risk. One implementation per primitive, behind a `Score` contract that cannot be built without `n` and `n_series` |
| research | `instruments/` | detectors, the mapentry recorder and its verdict engine, the postquote sampler, shadow lanes. Each armable lane pairs with a frozen registry row |
| research | `model/` | round-win training + dataset (LightGBM `q`). The frozen map-vs-race aggregation lives with the instruments |
| research | `sizing/` | fractional-Kelly stake sizing |
| execution | `executor/` | `engine.py` (cycle loop, gates, dedup), `intents.py` (price/size + band + drift gates), `pmus.py` (the wire), `ledger/limits/fills/reconcile` (accounting, caps, venue truth), `us_mapping.py`. **The only place a wire call happens**, and the seam the Rust fire path replaces |

## scripts/ — the evidence log and the operator tooling

Everything here is cited by a document or a registry row. Read them, rerun
them, do not rewrite them.

**Frozen measurement instruments** (chronological): `baseline_recompute` →
`quant_eval` → `latency_report` → `timing_profile` → `pmap_calibration` →
`fill_conditional_edge` → `priced_in_test` → `entry_quality` →
`flow_features_study` → `postshock_lag` → `wave_detect` + `wave_edge_join` →
`quote_backtest` → `mapentry2_score` → `queue16_bandskip_crossin` +
`queue16_phase2_outcomes` → `roundwin2_poolb` → `crossin_backtest` →
`pcal_fit` → `postshock_fade` → `timing_chain` → `venue_pricing_cut`.

Two are load-bearing beyond their own result: `grid_winners.py` is the
map-winner oracle every instrument resolves against, and `replay_harness.py`
runs real capture through the real `Engine` — the core of the verification
gauntlet.

**Ops tooling** (deploy units and timers point here): `health_check.py` +
`keyring_watch.py` (5-min watchdog and self-heal), `capture_qa.py`,
`backup_raw.py`, `pmus_map_run.py`, `pmus_watch.py`, `eod_report.py`,
`morning_report.py`, `settle_truth.py`, `check_no_secrets.py` (the pre-commit
scanner).

**Armed-night operator tools** (manual, run by the operator): `watch_night.py`
(status panel), `night_alerts.py` (alerter), `live_fire_test.py` (plumbing
check — place → list → cancel → gone, with per-leg latency).

**Setup**: `setup_secrets.py`, `grid_probe.py`.

## Everything else

- **`data/`** (gitignored): the corpus — day-file JSONL per stream, the
  executor ledger, models, analysis output. **The moat is here, not in git.**
- **`deploy/`**: systemd user units. What actually runs is the table in STATE.md.
- **`docs/experiments/registry.jsonl`**: the law. Frozen hypotheses and bars,
  append-only. Nothing trades without a row.
- **`docs/analysis/`**: dated evidence reports — immutable. **`docs/specs/`**:
  design briefs. **`docs/learnings/`**: the nightly narrative.
- **`tests/`**: hermetic — no network, no keyring, no live D-Bus, nothing
  written outside `tmp_path`. `uv run ruff check .` is green repo-wide; the
  deliberate idioms are encoded in `pyproject.toml`, not suppressed inline.
  The one non-green test is
  `test_postshock_lag.py::test_real_day_file_smoke`, which fails **by design**
  when the gitignored `data/` corpus is absent.
- **`.github/workflows/ci.yml`**: lint + secret scan + tests on every push.
- **`rust/`** (planned): the low-latency fire path. The Python `executor/` is
  the reference semantics it must reproduce bit-for-bit on the same tape.

## Two notes on history

- Dated files in `docs/analysis/` and `docs/learnings/` may cite
  `docs/superpowers/specs/…`. That directory was renamed to `docs/specs/` on
  2026-10-16; the historical citations are left exactly as written, because the
  evidence record is immutable.
- The repository was developed and reviewed with parallel automated lanes
  against a pre-registration workflow. That scaffolding has been removed — it
  was a process, not a runtime dependency. The discipline it produced is kept
  as `docs/ENGINEERING.md`.

## Forking for a new strategy (the intended seam)

**Keep:** `capture/`, `corpus/`, the `executor/` lifecycle, `sizing/`, the
`shared/` primitives, `scorecard/`, the registry discipline.

**Replace:** `instruments/` (your detectors + recorder + verdict), `model/`,
the gate constants in `intents.py`, and the registry contents.

The rules in `docs/ENGINEERING.md` travel with the fork.
