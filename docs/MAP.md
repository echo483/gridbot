# Codebase map

The floor plan. Orientation order: **`docs/ENGINEERING.md` (the rules) →
`docs/STATE.md` (what is proven, what runs) → `docs/FALSIFICATION.md` (what
died and why) → `docs/WORKBLOCKS.md` (the live plan) →
`docs/experiments/registry.jsonl` (the frozen hypotheses)**. This file is
navigation; the rules live in ENGINEERING.md and are not repeated here.

Layers follow `docs/ENGINEERING.md` § Layers: **data → research → execution**,
with `shared` primitives and `scripts/` as the evidence log. Dependencies point
inward; a lower layer never imports a higher one.

## src/gridbot — the system

| plane | package | what it is |
|---|---|---|
| shared | `config.py` | the single permission-checked secrets file, non-secret runtime knobs, data dir. `python -m gridbot.config --check` |
| shared | `jsonl.py` | the one tolerant JSONL iterator (skips and counts torn lines) |
| shared | `graphql.py` | GRID GraphQL client |
| shared | `ratelimit.py` | GRID rate budget |
| shared | `redaction.py` | keeps resolved secrets out of logs |
| shared | `notify.py` | the single egress point for alerts; transport is configuration |
| data | `capture/` | GRID poller, venue book/discovery capture, day-file supervisor, the PMUS BBO tape recorder. **The corpus moat — never break continuity.** |
| data | `corpus/` | raw day files → derived training rows (`build`, `rows`, `ingest`), and `winners`, the one map-winner oracle |
| data | `lake/` | the analytical lake: raw JSONL → lossless bronze Parquet (`ingest`) → typed silver tables `events` / `round_results` / `map_results` (`events`) → quality `gates`; `inventory` is read-only. `python -m gridbot.lake all` |
| research | `scorecard/` | the statistical kernel: Wilson, series-clustered bootstrap, Brier/ECE/Murphy, executable-edge pricing, Kelly/barrier/Monte-Carlo risk. One implementation per primitive, behind a `Score` contract that cannot be built without `n` and `n_series` |
| research | `instruments/` | detectors, the mapentry recorder and its verdict engine, the postquote sampler, shadow lanes. Each armable lane pairs with a frozen registry row |
| research | `model/` | round-win training + dataset (LightGBM `q`). The frozen map-vs-race aggregation lives with the instruments |
| research | `sizing/` | fractional-Kelly stake sizing |
| research | `lab/` | the research control plane: registry v2 (hypotheses as data), `joins` (events × books, bounded as-of, team-keyed), `fills` (touch fill model, USER-owned), `scan` (one engine for every registered hypothesis), `metrics` (clustered Sharpe, beta/alpha, deflated Sharpe, MDE), `report` (HTML). `python -m gridbot.lab` |
| execution | `executor/` | `engine.py` (cycle loop, gates, dedup), `intents.py` (price/size + band + drift gates), `pmus.py` (the wire), `ledger/limits/fills/reconcile` (accounting, caps, venue truth), `us_mapping.py`. **The only place a wire call happens**, and the seam the C++ decision core plugs into |

## scripts/ — measurement instruments and operator tooling

Only instruments that feed a live lane, the current plan, or a still-binding
registry row stay on `main`. Instruments for closed lanes run from tag
`evidence-2026-10`; `docs/FALSIFICATION.md` lists what each one decided.

**Measurement instruments:** `quant_eval` (metrics layer), `pmap_calibration`,
`fill_conditional_edge` (the NOTE6 selection split), `venue_pricing_cut`
(NOTE9), `timing_chain` (GRID round ends vs the PMUS book), `timing_profile`
(rested-order fill statistics), `latency_report` (our own pipe).

**Load-bearing beyond one result:** `grid_winners.py` is the map-winner
oracle every instrument resolves against; `replay_harness.py` runs real
capture through the real `Engine`, the seed of the backtester.

**Ops tooling:** `health_check.py` (5-min watchdog and self-heal),
`morning_report.py` (daily digest), `pmus_map_run.py`, `pmus_watch.py`,
`settle_truth.py`, `promote_model.py` (gates model retrains),
`check_no_secrets.py` (pre-commit + CI scanner), `grid_probe.py` (GRID key and
title scope).

**Armed-session tools (manual):** `watch_night.py` (status panel),
`night_alerts.py` (alerter), `live_fire_test.py` (place → list → cancel →
gone, with per-leg latency).

## Everything else

- **`data/`** (gitignored): the corpus — day-file JSONL per stream, the
  executor ledger, models, analysis output. **The moat is here, not in git.**
- **`deploy/`**: workstation-era systemd user units, being replaced for the
  homelab host (spec M1).
- **`docs/experiments/registry.jsonl`**: the law. Frozen hypotheses and bars,
  append-only. Nothing trades without a row.
- **`docs/analysis/`**: dated evidence reports, immutable.
  **`docs/specs/`**: live design docs. **`docs/history/`**: the dated
  narrative, superseded specs, nightly notes (see its README).
- **`tests/`**: hermetic — no network, no real secrets, nothing written
  outside `tmp_path`. `uv run ruff check .` is green repo-wide; the deliberate
  idioms are encoded in `pyproject.toml`, not suppressed inline.
- **`.github/workflows/ci.yml`**: lint + secret scan + tests on every push.
- **`cpp/`** (planned, spec M4): the C++ decision core. The Python `executor/`
  is the reference semantics it must reproduce byte-for-byte on the same tape.

## Forking for a new strategy (the intended seam)

**Keep:** `capture/`, `corpus/`, the `executor/` lifecycle, `sizing/`, the
`shared/` primitives, `scorecard/`, the registry discipline.

**Replace:** `instruments/` (your detectors + recorder + verdict), `model/`,
the gate constants in `intents.py`, and the registry contents.

The rules in `docs/ENGINEERING.md` travel with the fork.
