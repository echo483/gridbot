---
name: gridbot-night-to-code-pipeline
description: Use when turning a live trading night's observations into a queue spec, pre-registered registry amendment, or dispatched agent build in gridbot — the meta-procedure for converting nights into code without breaking pre-registration rails.
---

# gridbot night → code pipeline

How 3 nights of live-fire became shipped, tested changes (queues 13–17,
SECONDSIDE1, manual-close pass) while keeping iron rail 2 (pre-registration)
intact. Pattern proven ≥2 nights: user/night observation → spec doc →
backtest → registry amendment → dispatched build → review → verify.

## The pipeline (each step cites its real instance)

1. **Capture the observation same-night** in the learning lane
   (`.hermes/skills/gridbot-learning`) — numbers, not adjectives. Example:
   user's night-2 "27c quote on a 7–9% book never fills" went into the
   learning doc verbatim, then into a queue spec.
2. **Write a queue spec BEFORE code**: `docs/superpowers/specs/YYYY-MM-DD-*.md`
   with (a) the question to answer with data, (b) candidate levers ordered by
   evidence needed, (c) the rails section (maker-only, registry amendment,
   shadow-first). See `2026-08-27-entry-gate-loosening.md` queues 13–15.
3. **Backtest before any PR**: e.g. Queue 13's would-fill study (n=37) produced
   numbers WITHOUT licensing a rule change — adverse-selection signature
   found, nothing flipped. Analysis lanes never touch the trade path.
4. **Pre-register the amendment in `docs/experiments/registry.jsonl` BEFORE
   any armed session runs under it** (SECONDSIDE1 20:40Z) or BEFORE data
   exists (H-MAPENTRY2 registered 08:13Z; data window opened 09:00Z). Late
   registration = post-hoc selection (the H-BAND1 lesson).
5. **Dispatch alternate builders for code** (deepseek/glm via A2A relay), then
   **adversarial review by a different model** (opus/glm), then Claude
   line-by-line verify + `uv run pytest`. Every registry entry records this
   tooling provenance — including rejected attempts (glm's unchanged files
   for SECONDSIDE1; opus's 5 MAJOR scorer defects found pre-verdict).
6. **Verdict from held-out capture only**, at the frozen n and criterion;
   underpowered nulls are read as underpowered, never as falsifications
   (H-MAPENTRY2-AMEND1 pin). Tuning data is training and excluded from the
   verdict window (H-WAVE1).

## Concrete commands

- New spec: `docs/superpowers/specs/$(date +%F)-<topic>.md`
- Registry append (one JSON row: run_ts, hypothesis_id, status, hypothesis_text)
  → `docs/experiments/registry.jsonl`
- Tests gate: `uv run pytest` (524 green at dfba31c) before any capture/engine
  change ships.
- Scoring scripts pattern: `scripts/<lane>_score.py` + `scripts/grid_winners.py`
  (first-observation oracle, validate finals against impossible-score
  patterns like 13-12 regulation before scoring).

## Pitfalls

- **Verdict table starvation**: two armed nights scored quoted=0/paper=0 with
  no H-MAPENTRY1 row — wire intents/fills into the verdict ledger FIRST, or
  nothing can score against its pre-registered bar.
- **Post-hoc readbacks are not amendments**: night-3 fill splits (≥10pt floor
  separates winners) looked decisive at n=10, but the n=45 population study
  contradicted them — no amendments were registered. Always run the
  full-population study before citing a split.
- Register param candidates as frozen cells BEFORE reading their live
  outcomes; 0.50–0.65 band claims stayed open because the CI spanned zero.
- A user directive mid-session ("loosen the gate") becomes an explicitly
  labeled USER_DIRECTED_TEST_AMENDMENT (BANDGATE1-AMEND1 6ce3c35), never a
  silent edge claim.
- Daemon restart discipline: SIGTERM for arming is clean, but the executor
  must be restarted at session end — night 1 left it down 2h44m; consider a
  disarm script that auto-restarts.
