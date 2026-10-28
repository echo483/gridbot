# BUILD SPEC — wire H-MAPENTRY1 into the verdict ledger

Status: SPEC.
Standing dev-lane item #1 for FIVE consecutive nights (08-27/28/29/30 + today).

## Why this is the #1 build

`gridbot.instruments.verdict` reads **only** `data/shadow/instances-*.jsonl`
(`type=="instance"`). H-MAPENTRY1 — the ONLY lane the executor can ever arm —
produces `data/shadow/mapentry-*.jsonl` (`type=="mapentry"`) and
`data/executor/intents-*.jsonl`. Neither is read. Consequence, verified today:

    $ uv run python -m gridbot.instruments.verdict
    ... 8 cells printed, quoted=0 paper=0 on every one, and NO H-MAPENTRY1 ROW AT ALL

So H-MAPENTRY1's frozen bar is **not computable by any code that exists**, which
means iron rail 3 ("dry_run until a pre-registered PASS") can never be lifted by
evidence. That is the blocker. Everything else is downstream of it.

## The frozen bar (registry, H-MAPENTRY1 — DO NOT REINTERPRET)

FALSIFIED on >=150 held-out resolved instances carrying a PMUS book at
detection if ANY of:
  (i)   Brier(P_map) >= Brier(book price as forecast) on the same instances
  (ii)  ECE(P_map) > 0.08 (quintile bins)
  (iii) at >=60 qualifying entries, mean settled edge <= +2 points
        OR by-series bootstrap 95% CI includes 0
STARVATION CLAUSE: if <5% of resolved instances ever show a qualifying quote,
the lane closes as starved.

## Deliverable

Extend `src/gridbot/instruments/verdict.py` so `uv run python -m
gridbot.instruments.verdict` emits an **H-MAPENTRY1 row** reporting all three
clauses plus the starvation ratio, against the bar above. Same table, same
process, read-only, deterministic, offline.

## REUSE — do not rebuild these, they are already correct and landed

| need | existing code | notes |
|---|---|---|
| map-winner oracle | `scripts/grid_winners.py` | first-observation, MR12 terminal-score validated, ambiguous finals EXCLUDED |
| p_map Brier/ECE + populations | `scripts/pmap_calibration.py` | `load_winners`, `load_rows`, `outcome`, `posted_price`, `calibrate` |
| settled edge conditional on fill | `scripts/fill_conditional_edge.py` | `side_ask_at`, `would_fill`, `summarize` |

Import or factor these; do NOT re-derive orientation, oracle, or binning rules.
If a function must move into `src/`, move it and leave the script importing the
new home so both entry points stay working.

## TRAPS — every one of these has already cost a build or an analysis

- **D1 — wrong file, wrong type.** mapentry rows are NOT in `instances-*.jsonl`
  and are NOT `type=="instance"`. Glob `data/shadow/mapentry-*.jsonl`, filter
  `type=="mapentry"`. `load_instances()` will silently return them as zero rows.
- **D2 — there is no `ts` field.** mapentry and intents rows carry `ts_ns` /
  `grid_ts_ns` (integer nanoseconds) only. `row["ts"]` is None everywhere. Any
  day-bucketing or ordering must use `ts_ns`.
- **D3 — `side_ask` is frequently None** (one-sided thin books; 8/12 real-book
  rows on 08-29). Clause (i) is defined over "resolved instances **carrying a
  PMUS book at detection**" — rows with `side_ask is None` are NOT in that
  population. Filter them out; never impute a price.
- **D4 — the honest n is DEDUPED, not the raw qualifying count.** The
  qualifying population counts the same map book dozens of times and inflates
  any edge estimate. `pmap_calibration.py` already implements ALL /
  QUALIFYING / DEDUPED (one entry per series,map,side). Clause (iii)'s ">=60
  qualifying entries" is read on the **DEDUPED** count. Report all three, gate
  on deduped.
- **D5 — shorts invert.** `us_side=="short"` buys the No leg: effective ask is
  `1 - bid`, and `p_map` is the probability for the row's `team`. Copy the
  orientation from `pmap_calibration.outcome()` / `posted_price()` verbatim.
  Never pool unoriented across sides.
- **D6 — the oracle excludes, never guesses.** Rows whose team matches neither
  the winner nor the loser are EXCLUDED. Ambiguous finals are excluded. Do not
  fall back to a name-similarity match.
- **D7 — engine gates are NOT verdict filters.** The band gate, drift gate,
  slate gate and second-side gate live in the ENGINE (intents rows). Registry
  entry H-MAPENTRY1 ENGINE_GATE_AMENDMENT says verbatim: "engine gate only;
  H-MAPENTRY1 recording population and frozen verdict bars unchanged." The
  verdict population is the RECORDING population from mapentry rows. Do NOT
  filter it by band/drift/slate.
- **D8 — CORPUS-F1 leak filter is binding.** 13% of rows were post-round
  transition snapshots; every pre-filter calibration number is suspect. Apply
  the same filter the existing corpus readers apply.

## Verification the builder MUST run and paste

1. `uv run pytest` — currently **589 passed**. Must still be >=589, zero failures.
2. `uv run python scripts/pmap_calibration.py` — baseline from 08-28 was
   Brier 0.1920 vs base-rate 0.2394, skill +0.198, 277 resolved maps. Today the
   map count must be **HIGHER** (more capture has landed). If Brier moves more
   than ~0.02 from 0.1920, you broke the join — stop and report.
3. `uv run python -m gridbot.instruments.verdict` — must now print an
   **H-MAPENTRY1** row. Paste the full table.
4. State the three clause values and the starvation ratio explicitly, with n
   for each, and say plainly whether the bar is met, not met, or under-powered.

## Output contract

- Modify `src/gridbot/instruments/verdict.py` (+ new tests under `tests/`).
- Add tests that would FAIL against the current code: at minimum one that
  asserts an H-MAPENTRY1 row is produced from a fixture of real-shaped
  mapentry rows, and one per trap D1/D3/D4/D5.
- Do NOT touch the executor, engine, capture, or any gate. This is a
  reporting-layer build. If you believe a gate is wrong, write it in your
  report — do not change it.
- Do NOT change the bar. The registry is frozen. Report against it.

## Real row shapes (verbatim from today's capture)

mapentry (qualifying):
{"type":"mapentry","hypothesis_id":"H-MAPENTRY1","src_hypothesis_id":"H-MAPENTRY1-SLATE",
"series_id":"2997461","map_number":2,"round_number":1,"side":"ct","team":"Total Domination",
"opponent":"Omega","a":0,"b":0,"q_round":0.4986635823732281,"p_map":0.4998,
"dedup_extra":"slate","market_slug":"astatc-cs2-omg-td-2026-08-30-map2","us_side":"short",
"bid":0.57,"ask":0.6,"side_ask":0.43,"entry_price_max":0.4598,"qualifies":true,
"skip_reason":null,"src_ts_ns":null,"grid_ts_ns":1788074727253758998,"ts_ns":1788074728271983142}

intents (gate-passing, dry_run):
{"type":"order_intent","hypothesis_id":"H-MAPENTRY1","series_id":"2995312","map_number":1,
"round_number":8,"market_condition_id":"","token_id":"","team":"G2","price":0.5,"shares":50.0,
"stake_usd":25.0,"dedup_key":"H-MAPENTRY1:2995312:1:long:astatc-cs2-g2-navi-2026-08-30-map1",
"market_slug":"astatc-cs2-g2-navi-2026-08-30-map1","us_side":"long","status":"dry_run",
"order_id":null,"p_map":0.7529,"ts_ns":1788082116760833375,"live":false}

Note both carry `ts_ns` and NO `ts`. Note `side_ask` present here but often null.
