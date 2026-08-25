# gridbot

Experiment factory trading esports prediction markets (Polymarket) on GRID Open Access
data. Read `docs/superpowers/specs/2026-08-24-gridbot-design.md` before changing anything.

## Iron rails (merge-blocking)

1. **No LLM in the trade path.** q is a calibrated quant model. LLMs live in the slow loop
   (analysis, hypothesis drafts, reports) and never touch orders or raw secrets.
2. **Pre-registration discipline.** Every strategy idea becomes a frozen hypothesis in
   `docs/experiments/registry.jsonl` BEFORE its instrument runs; verdicts come from
   held-out capture data; falsified lanes stay closed absent a new mechanism claim.
3. **`dry_run=True` until a pre-registered PASS.** Flipping it live is the user's own act —
   never Claude's, never an agent's, never a default.
4. **Capture continuity outranks refactors.** The corpus is the moat.
5. **Single account per platform.** Scaling axes: titles × venues × capital.
6. **Secrets live in the OS keyring only** (service `gridbot`; `scripts/setup_secrets.py`).
   Never in files, code, logs, prompts, or commits — see SECURITY.md. The pre-commit
   scanner (`git config core.hooksPath .githooks`) enforces this at the repo boundary.

## Context

- Inherited empirical record lives in `/home/ops/cs2agent` (ADRs, experiment registry,
  corpus). Reference it; don't port code wholesale. A parallel Claude session may be
  operating cs2agent's runtime — coordinate through the user before touching it.
- GRID OA: hosts `api-op.grid.gg`, `x-api-key` header only. Central Data 20/min;
  Series State 180/min global, 6/min per series. Polling only. CS2 title id 28.
- First instrument: H-MODELENTRY1 (see spec).

## Commands

- `uv sync` — install
- `uv run pytest` — tests must pass before any capture/engine change ships
- `uv run python scripts/grid_probe.py` — verify GRID key + title scope (few CD calls)
- `uv run python -m gridbot.capture.supervisor --once` — 45s capture smoke run
- `systemctl --user status gridbot-capture` — the capture daemon
