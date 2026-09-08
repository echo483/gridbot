# Codebase map — what everything is, and what to fork

One page for navigation and forkability. Four tiers: **WIRING** (the
strategy-agnostic machine you'd fork for a new venue/strategy), **STRATEGY**
(H-MAPENTRY1-era facets — replaceable per experiment), **EVIDENCE** (frozen
analysis instruments; their outputs are cited by the registry and
docs/analysis — read them, rerun them, never rewrite them), and **OPS**
(keeping the machine alive). Orientation order stays: CLAUDE.md → STATE.md →
registry → LIVE_FIRE.md; this file is the floor plan.

## src/gridbot — the system (56 files)

| package | tier | what it is |
|---|---|---|
| capture/ | WIRING | GRID poller + PM books/discovery + day-file supervisor (the corpus moat — never break continuity, rail 4); pmus_books.py = the PMUS BBO tape recorder (own unit, read-only). |
| corpus/ | WIRING | day files → Supabase ingest + row builders. |
| executor/ | WIRING+STRATEGY | engine.py (cycle loop, gates, dedup), intents.py (price/size + band + drift gates), pmus.py (wire), ledger/limits/fills/reconcile (accounting + caps + venue truth), us_mapping.py. The gate CONSTANTS and qualification rule are strategy; the order lifecycle is wiring. |
| instruments/ | STRATEGY | detectors, mapentry recorder + verdicts, postquote sampler, shadow lanes. Each armable lane pairs with a frozen registry row. |
| model/ | STRATEGY | roundwin training/dataset (LightGBM q). Frozen mapwin-race-v1 aggregation lives with the instruments. |
| sizing/ | WIRING | kelly + params (quarter-Kelly caps). |
| analysis/ | OPS | slow-loop Claude analyst (daily, managed) — LLM territory, never trade path (rail 1). Also the offline quant instruments: `exec_*` (the EXEC1 replay machinery, retained after H-MAPENTRY1-CLOSE1 as reusable execution modelling) and `crossvenue_join.py` (H-CROSSVENUE1 plumbing — resolves intl↔PMUS markets AND their Yes-side orientation; computes no basis or edge), `crossvenue_basis.py` (the basis itself: as-of join on the PMUS clock, sign settled before subtraction, and a holdout gate that RAISES and fails closed — the lane is unregistered), `trade_prints.py` (the fill-observation channel that replaced the BBO bracket, NOTE11/NOTE12), and `book_liveness.py` (three-axis frozen/one-sided/wide classifier — keeps NOTE8's dead-book artifact out of a population BEFORE a number is quoted). |
| config.py / redaction.py / ratelimit.py / graphql.py | WIRING | keyring-only secrets (5s-timeout reads + last-good cache), log redaction, GRID rate budget, GQL client. |

## scripts/ (48 files) — by tier

**WIRING (fork these with the system):** dispatch.py + _anthropic_lane.py
(agent-team routing; bot-mode profile lanes), _jsonl.py (tolerant reads),
grid_winners.py (map-winner oracle — the settlement truth for every
instrument), replay_harness.py (real capture through the real Engine —
the verification gauntlet's core), setup_secrets.py, grid_probe.py.

**OPS (live units/crons point here):** health_check.py + keyring_watch.py
(5-min watchdog), capture_qa.py (**cron wiring is STALE — the wrapper
`~/.hermes/scripts/gridbot-capture-qa.sh` exists but no job in
`~/.hermes/cron/jobs.json` invokes it; audit 2026-09-07**), backup_raw.py
(gzips finished day files OFFSITE; does NOT compress or prune locally — see the
disk item in the 09-07 sitrep), pmus_map_run.py, pmus_watch.py, eod_report.py,
morning_report.py, settle_truth.py, check_no_secrets.py (pre-commit hook),
setup_managed_agent.py.
**evidence_qa.py** (added 09-07) is the EVIDENCE-FLOW watchdog: every other
alarm here checks process liveness or directory mtime, none reads row content,
which is how a 13× venue collapse passed with `ok:true` everywhere. It is **not
yet wired to a timer** — see the sitrep. Note systemd `WatchdogSec=` must NOT be
used as an alternative: all units are `Type=simple`/`NotifyAccess=none` with no
`sd_notify`, so a watchdog would kill capture on a timer.
**Armed-night operator tools (manual):** watch_night.py (status panel),
night_alerts.py (Telegram alerter), live_fire_test.py (first-fire plumbing
check — LIVE_FIRE.md).

**EVIDENCE (frozen instruments; chronological, each cited by a doc or
registry row):** baseline_recompute → quant_eval → latency_report →
timing_profile → pmap_calibration → fill_conditional_edge → priced_in_test →
entry_quality → flow_features_study → postshock_lag → wave_detect +
wave_edge_join → quote_backtest → mapentry2_score → queue16_bandskip_crossin +
queue16_phase2_outcomes → roundwin2_poolb → crossin_backtest → pcal_fit →
postshock_fade (reconciled 2026-09-03 to its spec-literal funnel; its
review diagnostics showed the in-sample edge is a dead-book artifact —
registry NOTE8) → timing_chain (GRID round end → PMUS book: first move,
oriented 30/60/120s and next-round-bounded moves, model-oriented read,
series-clustered CIs, our pipe legs; reads the pmus_books tape; nightly via
gridbot-timing-chain.timer). corpus_pull.py pulls
training tables for model work. promote_model.py gates any retrain.
intl_reference.py samples the intl venue read-only.

**SCRATCH:** scripts/_tmp_* — gitignored by policy, on-disk only; when an
evidence doc cites one, it moves next to the data it produced (Q15 set:
data/analysis/q15_scratch/). docs/archive/ holds superseded plans (AWS
migration, routing-layer analysis) — history, not guidance.

## Everything else

- **data/** (gitignored): the corpus. day-file JSONL per stream; executor
  ledger; models; analysis outputs. The moat is here, not in git.
- **deploy/**: systemd user units — what actually runs (STATE.md table).
- **agents/**: dispatch-lane preambles + profiles/ SOUL snapshots (canonical
  copies are hermes-side).
- **docs/experiments/registry.jsonl**: the law. Frozen hypotheses/bars;
  append-only; nothing trades without a row.
- **docs/analysis/**: dated evidence reports. docs/learnings/: nightly ops
  notes. docs/superpowers/specs/: design briefs.
- **tests/** (872): hermetic — no network, no keyring, no live D-Bus; nothing
  writes outside tmp_path. `uv run ruff check .` is green repo-wide (the
  deliberate idioms are encoded in pyproject, not suppressed inline).

## Forking for a new strategy (the intended seam)

Keep: capture/, corpus/, executor/ lifecycle, sizing/, config/redaction/
ratelimit, dispatch tooling, replay_harness, the registry discipline.
Replace: instruments/ (your detectors + recorder + verdict), model/, the
gate constants in intents.py, and the registry contents. The iron rails
(CLAUDE.md) travel with the fork.
- `scripts/venue_pricing_cut.py` — round-leverage × spread read of the map/match books (NOTE9): does the venue price the round at all; taker-at-stale-quote marks.
