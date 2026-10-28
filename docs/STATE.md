# gridbot — current state

**As of 2026-10-28.** This file describes the system as it is now and is
rewritten when that changes. The dated narrative from the first armed night
through the 2026-10-16 baseline is preserved verbatim in
`docs/history/STATE-2026-08-26-to-2026-10-16.md`. What was tried and killed is
in `docs/FALSIFICATION.md`.

## What it is

A research and execution system for esports prediction markets. It captures
GRID live CS2 game state and Polymarket US order books, measures a hypothesis
against held-out capture under a pre-registered bar, and trades only through a
deterministic, capped, post-only executor that never arms itself.

The mechanism every hypothesis has to monetise: **GRID reports round outcomes
before the venue reprices** (p50 +13.1 s, lower bound, n=293 book moves over 682
round ends). It is a timing fact, not yet an edge: the first book move agrees
with the round winner only 55% of the time, and a round end is worth about
half a tick. The book tracks map state over minutes.

## Proven (numbers with their n)

| claim | number | source |
|---|---|---|
| information lead over the venue book | p50 +13.1 s (upper bound +27.5 s); book moved first in 29%; first-move direction agreement 55% | `analysis/2026-09-03-timing-chain-first-read.md` |
| round model is calibrated live | Brier 0.1105 (skill +0.063), ECE 0.031, n=1,984 resolved | `history/STATE-2026-08-26-to-2026-10-16.md` (PROVEN table) |
| wire latency (real order) | place 394 ms, cancel 137 ms, round trip 531 ms (n=1 order; preview 2.2 s once per process) | `LIVE_FIRE.md` |
| our own pipe | ≈4.4 s (GRID rtt 0.28 + detect 0.16 + recorder 1.19 + engine 2.77) plus up to a 12 s poll wait | `analysis/2026-09-03-timing-chain-first-read.md` |
| rested-order fill rate | 13/28 = 0.46 on venue-verified rested orders | registry NOTE7 |
| H-MAPENTRY1 holdout | not falsified; clause (iii) under-powered 39/60, paper edge negative | registry HOLDOUT1 |

**No lane has a pre-registered PASS.** `dry_run=True` is the default. There
are 13 settled real trades, which is not enough to support any PnL claim.

## What runs

Nothing is deployed right now. The workstation-era units in `deploy/` are
stopped, and the corpus is on the external T7 drive, not ingested on any host.
The next host is the always-on homelab (Proxmox VE on an M720q). Capture is
being redesigned for it (`docs/specs/2026-10-28-buildout-spec.md`, M1).

## Code (2026-10-28)

- All Python. The C++ decision core is planned (spec M4); no Rust exists or
  is planned.
- `uv run ruff check .` green; `uv run pytest` = **778 passed, 1 skipped,
  0 failed** on a clean tree without data.
- The Parquet/DuckDB lake (`lake/`) and the research control plane (`lab/`)
  are in, tested on synthetic capture only. They have not yet seen the T7
  corpus (`docs/specs/2026-10-28-research-platform-spec.md` § 8).
- Secrets come from one permission-checked file (`SECURITY.md`); the desktop
  keyring and the Supabase mirror are gone.

## Hard-won facts (keep these)

- **Venue price convention.** The Polymarket US `price` field is always the
  Yes price. A `BUY_SHORT` at P is stored as `SELL @ P` and costs 1−P.
  gridbot prices are our side's cost; the only conversion is
  `pmus.wire_price`, and `fills.fill_row` converts back (`venue_px` keeps the
  raw number).
- **The venue returns an order id for orders it then rejects** (post-only
  would-cross, stale book). Count `placed` only after the order rested.
- **Cancel success is not fill truth.** The venue reports clean cancels on
  already-filled orders. Fill truth is `fills.sync` / `settle_truth.py`.
- **Creates are never retried** (double-order risk); cancels are retried ×3.
  An ambiguous 2xx is `error:ambiguous_ack`, never `rejected`.
- **PMUS discovery**: `events.list` pagination buries esports and the
  `seriesSlug` param is silently ignored. Use `search.query` plus time-boxed
  `events.list(startTimeMin/Max)`. A BBO of zero means a dead book. Thursday
  2–6 am ET maintenance cancels all orders. 20 req/s.
- **PMUS lists match and map winner books only**, no round books. Shorts price
  as 1 − long bid. `participateDontInitiate` is exchange-enforced post-only.
- **Never install `dev-polymarket/clob-client-v2`** (typosquat). The official
  SDK is `polymarket-us` on PyPI.
- **CORPUS-F1 leak filter is binding.** 13% of historical rows were
  post-round transition snapshots (`CORPUS.md`).
- **A capture gap is either a volume gap or a join gap.** When GRID went dark
  but PM books kept recording, the orphaned books could never produce a
  mapentry row. Every capture loss so far came from workstation lifecycle
  (three keyring failures, one idle suspend, one power-off), none from code.
- **A backtest number without a depth/fill model is not a number** (NOTE6,
  and the post-shock dead-book artifact).
