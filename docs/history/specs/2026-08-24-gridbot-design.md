# gridbot — Design (approved 2026-08-24)

> **Historical brief (2026-08-24).** This is the original design document.
> Sections describing an orchestration runtime, agent lanes, or chat-transport
> reporting describe a development process that has since been removed and are
> kept as a record. The binding rules are in `docs/ENGINEERING.md`; the current
> structure is in `docs/MAP.md`.

## What this is

A lean **experiment factory** that trades esports prediction markets (Polymarket) on GRID
Open Access data. Many cheap, pre-registered strategy experiments; automatic held-out
verdicts; shadow trading; live capital only for promoted instruments, capped, and armed
only by the user. First instrument: **H-MODELENTRY1**. Capture covers **CS2 + LoL**.
Reporting via **Telegram** through the Hermes gateway. Fresh code; cs2agent's discipline
and corpus carry over, its sprawl does not.

## Inherited ground truth (verified against cs2agent artifacts, 2026-08-24)

- GRID OA Series State is game-server-direct; measured **13–27s median lead** over
  Polymarket repricing on CS2 round outcomes; model threshold-crossings lead by
  **median 39.6s**.
- **Falsified (closed)**: all confirmed-event-timing entries — 5 taker variants
  (loss = spread paid), maker-at-bid (ADR-0029), map-level scaling (ADR-0030).
  A falsified lane stays closed absent a genuinely new mechanism claim.
- **Open**: H-MODELENTRY1 (pre-registered 2026-08-24, instrument not yet built) —
  enter on the served round model's threshold-crossing DURING an unresolved round,
  before the outcome is in the price. Distinct mechanism from the falsified family.
- **Open**: real fillable-liquidity measurement — the previous "0 fillable candidates"
  reading was a wiring artifact (empty `team_to_yes`), not a market fact (H-LEG4-WIRING1).
- Corpus: 3,719 unique rounds / 107 series as of 2026-07-15; **capture dark since
  2026-07-20** (machine migration). Corpus only grows while capture runs.
- Models: 20-feature round-win LightGBM (held-out Brier 0.1514 / ECE 0.0104), retrains
  promoted only on a pinned out-of-sample benchmark, fail-closed.
- Market reality (scanned 2026-08-24): Polymarket esports liquidity is currently in
  **LoL** (live match market $171k/24h, $1.4M liquidity; Worlds Q4). CS2 tier-3 match
  markets exist but ~zero volume; CS2 tier-1 calendar resumes ~September.
- No live trading has ever occurred. Bar: system ✅, latency lead ✅, calibrated model ✅,
  **provable +EV ❌** — the fourth is gridbot's whole job.

## Thesis

Edge = faster predictive model × latency lead × a book that reprices on slower actors'
information. The way to find where (title, tier, entry mechanic) that product is positive
is **volume of pre-registered experiments**, not conviction. Misses are free in backtest
and shadow; they are only expensive live — so nothing reaches live unpromoted.

## Architecture

```
GRID OA poll (CS2+LoL) ──► capture ──► corpus (append-only) ──► models ──► instruments
Polymarket L2 (public) ──┘                                                    │
                                             verdict engine (pre-registered, held-out)
                                                                              │ pass
                                        shadow trades ──► promotion ──► executor
                                                              (dry_run → live, capped, user-armed)
slow loop (Hermes cron + agents): EOD Telegram report, corpus mining, hypothesis drafts,
retrain proposals — never order authority, never raw secrets
```

Components — one module, one responsibility, each testable alone:

| Component | Responsibility |
|---|---|
| `capture` | GRID Series State pollers (≤6/min per series, 180/min global) + Polymarket L2 book recorder; timestamped append-only files |
| `resolver` | Polymarket market ↔ GRID series binding (teams + schedule). Historically the weakest join — gets its own accuracy eval |
| `corpus` | SQLite + parquet store; imports the cs2agent corpus read-only as lineage |
| `models` | round-win q (LightGBM lineage) + BO-N aggregation; pinned-benchmark promotion, fail-closed. q is never an LLM |
| `factory` | instrument plugin API: `register()` (frozen hypothesis → `docs/experiments/registry.jsonl`), `run()`, `verdict()` on held-out capture. Statuses: REGISTERED_NO_RUN → RUNNING → PASS / FALSIFIED |
| `shadow` | paper execution with real spread/fees/latency from captured L2 |
| `executor` | Polymarket CLOB submitter; BUILD-READY mocked; `dry_run=True`; per-trade + daily caps; kill switch; staleness flatten |
| `report` | `hermes send` → Telegram: live alerts, EOD digest, weekly review |

## Instrument #1 — H-MODELENTRY1

Build exactly what the registry pre-registered (2026-08-24T12:00Z): entry on served-model
threshold-crossing mid-round (median 39.6s before reprice), exit on convergence/inversion
per the standing exit set. Alongside it, fix the leg-4 wiring so fillable liquidity is
measured for real. Verdict on fresh held-out captures with fees + fill realism included.

## Phases and gates

- **P0 — foundations (no keys needed to start):** repo + factory skeleton; PM L2 recorder
  (public endpoints); corpus import; resolver v1; LoL title probe against the GRID key;
  coordinate cs2agent capture revival (owned by the other session — corpus resumes growing).
- **P1 — first verdicts:** H-MODELENTRY1 instrument + liquidity measurement; shadow-trade
  live CS2/LoL events. **Gate:** pre-registered PASS on held-out captures, net of spread,
  fees, and fill realism.
- **P2 — small live:** user confirms Polymarket eligibility (California / current US
  status) and GRID terms for trading use; funds small; **user flips dry_run himself.**
  **Gate:** live tracks shadow within tolerance and is +EV over the pre-registered window.
- **P3 — scale:** size within measured depth; more instruments from the backlog; Dota /
  additional venues; maker lane; deeper Hermes automation of the slow loop.

Kill rules at every phase: calibration drift alarm blocks trading; capture staleness
flattens positions; a falsified instrument closes.

## Roles

- **Claude (this harness):** orchestrator, architect, reviewer; writes and verifies the
  load-bearing code.
- **Hermes runtime:** cron (EOD/retrain/health), Telegram gateway, agents (glm-5.3-flash now,
  OpenRouter `gridbot` workspace models when the key exists) for corpus mining, hypothesis
  drafting, EOD narrative. Skills authored in-repo, synced via `hermes skills`.
- **LLMs are slow-loop only.** No model touches an order or a raw secret.

## Compliance and security rails

1. No LLM in the trade path. 2. Pre-registration + held-out verdicts + gated promotion,
always. 3. `dry_run=True` until a PASS; arming is the user's own act. 4. Before arming:
Polymarket eligibility from CA confirmed + GRID OA trading-use terms confirmed; **single
account per platform** — scaling is titles × venues × capital, never duplicate accounts.
5. Capture continuity outranks refactors. 6. Secrets in keyring/.env only; agents see the
seam, never the key.

## Provisioning needed from the user

1. OpenRouter **gridbot workspace** API key → `hermes` config (slow-loop agents).
2. GRID key access for gridbot (share keyring entry or issue a second OA key); if the LoL
   probe fails, request LoL scope from GRID.
3. Telegram pairing: run the interactive `hermes` gateway setup once.
4. **P2 only:** Polymarket account (eligibility confirmed), exported key, small USDC.

## Out of scope for v1

Dashboards, graph/agent frameworks in the runtime, Supabase, any multi-account mechanics,
sportsbook automation, LLM fine-tuning, tick/vision data, venues beyond Polymarket.
