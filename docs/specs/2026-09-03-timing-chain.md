# Timing chain — measure the GRID→book lead on the books we actually trade

**Status:** spec for build (2026-09-03). Diagnostic instrument, not a strategy:
no registry bar, no trading, no keys. Its output is the evidence base for a
future pre-registered rail-B (latency / model-momentum) instrument — see
`docs/analysis/2026-09-01-strategy-brief.md`.

## Why

Every lead number we quote (13–27s median, ~40s at threshold crossings) was
measured against the international Polymarket CLOB in the cs2agent era. The
venue we can execute on is Polymarket US, whose map/match books we have never
timed against GRID. Our own pipe adds a measured 8.5s median (signal→ledger,
`scripts/timing_profile.py`). "Do we have a data/latency edge over the books
we trade, and how much of it does our pipe eat?" is unanswered. This
instrument answers it with a per-round-end measurement on captured data, and
it is the number a rail-B instrument would be pre-registered against.

Two parts: a read-only PMUS book recorder (there is no continuous PMUS book
capture today — only the 15-min mapping snapshot, the detection-time BBO on
mapentry rows and the +30/60/120s postquote samples) and an offline analysis
script that joins GRID round ends to the first book move.

## Iron rails that apply

- Read-only. Public gateway only (`polymarket_us.PolymarketUS(timeout=4.0)`
  with NO keys, exactly like `gridbot.instruments.mapentry_recorder.BboCache`).
  The SDK's WebSocket needs Ed25519 auth — do NOT use it here.
- New systemd unit. Never modify `gridbot-capture.service` or any running
  unit (capture continuity outranks refactors). Do not run `systemctl`; the
  ops session enables the unit after review.
- No imports from `gridbot.executor`. Nothing here may reach an order path.
- Deterministic analysis: local files in, markdown + json out, no network.

## Part A — recorder: `src/gridbot/capture/pmus_books.py`

Run: `uv run python -m gridbot.capture.pmus_books [--once]` (`--once` = one
poll cycle then exit; used as the smoke test). Cycle every `CYCLE_S = 2.0`.

### Which slugs to poll

1. Mappings: reload every `MAPPING_RELOAD_S = 60` via
   `gridbot.instruments.mapentry.load_latest_mappings(config.DATA_DIR / "pmus")`
   → `{grid_series_id: mapping_row}`. A mapping row's `us_markets` looks like
   `{"match": {"market_slug": "...", "kind": "match", "sides": {...}},
     "map1": {"market_slug": "...", "kind": "map", "sides": {"TeamName": "long"}},
     "map2": {...}}` (see `gridbot.executor.us_mapping.us_markets_for_event`).
2. Liveness from GRID: tail `data/grid/series_state-*.jsonl` with
   `gridbot.instruments.shadow.Follow(config.DATA_DIR / "grid", "series_state",
   from_start=False)`. Each row: `{"type": "ss", "series_id": "2995296",
   "title": "cs2", "req_ns": int, "latency_ms": int, "ts_ns": int,
   "data": {"seriesState": {"id", "format", "started", "finished", "valid",
   "teams", "games": [...]}}}`. Keep the latest state per series_id. A series
   is LIVE when `started and not finished`. The live map number is
   `gridbot.corpus.rows.current_game(state)["sequenceNumber"]` (None → skip).
   Skip `config.GRID_TEST_SERIES_IDS`. Drop a series from the live set when
   its last state is `finished`, or when no ss row has arrived for it in
   `STALE_S = 900` seconds.
3. Poll set per cycle: for each live mapped series, the live map's slug
   (`us_markets[f"map{n}"]["market_slug"]`) and the match slug
   (`us_markets["match"]["market_slug"]`), when present. Cap at
   `MAX_SLUGS_PER_CYCLE = 8` (≤ 4 req/s against the venue's 20 req/s; the
   mapentry recorder and postquote sampler share that budget). If more are
   live, round-robin across cycles so every slug is polled at least every
   few seconds; never burst.

### What to write

`DayWriter(config.DATA_DIR / "pmus", "books")` → `data/pmus/books-YYYYMMDD.jsonl`.
Parse with `gridbot.instruments.mapentry.parse_bbo(payload)` → `(bid, ask)`
of the LONG (Yes) side; zero prices are dead-book placeholders and come back
as None.

Row shape (DayWriter adds `ts_ns` = write time):

```
{"type": "pmus_bbo", "slug": "astatc-cs2-aur-g2-2026-08-26-map2",
 "series_id": "2995296", "map_number": 2, "kind": "map",
 "bid": 0.47, "ask": 0.55, "req_ns": 1787781698043707694, "latency_ms": 83,
 "heartbeat": false}
```

Write policy — CHANGE-ONLY plus heartbeat: write a row when `(bid, ask)`
differs from the last written pair for that slug, and otherwise at most one
row per `HEARTBEAT_S = 30` per slug with `"heartbeat": true`. The heartbeat
makes "book did not move" distinguishable from "recorder was not running".
A fetch failure writes `{"type": "pmus_bbo_error", "slug": ..., "error":
"<ExceptionClassName>"}` (never the message body — it may echo request
details), logs at WARNING, and never raises out of the loop.

`req_ns` is `time.time_ns()` taken immediately before the gateway call;
`latency_ms` is the call's round trip. Analysis uses `req_ns`.

### Deploy: `deploy/gridbot-pmus-books.service`

Copy the shape of `deploy/gridbot-postquote.service` (same `WorkingDirectory`,
`ExecStart` pattern via `uv run python -m ...`, same `Restart=` and
`After=`/`WantedBy=` lines, description "gridbot PMUS book recorder
(read-only public BBO; timing evidence)"). Do NOT enable or start it.

### Tests: `tests/test_pmus_books.py` (hermetic — no network, no keyring)

- live-set derivation: started/finished/stale/test-series rules; live map
  number from a real-shaped `seriesState` (copy the shape from a row in
  `data/grid/series_state-20260903.jsonl`, trimmed to the fields used).
- slug selection: map + match slugs, missing entries skipped, cap + round-robin.
- change-only + heartbeat writer logic with a fake clock and a fake writer.
- parse path via a fake client whose `markets.bbo` returns the real payload
  shape `{"marketData": {"bestBid": {"value": "0.47"}, "bestAsk": {"value": "0.55"}}}`
  and once raises → error row, loop continues.

## Part B — analysis: `scripts/timing_chain.py`

Run: `uv run python scripts/timing_chain.py YYYYMMDD [YYYYMMDD ...]`
(bare digits, like every other evidence script; `scripts/_jsonl.py` has the
tolerant reader). Exit 1 with a loud message when no `pmus_bbo` rows exist for
the day — an empty measurement must never print a green summary (the replay
harness learned this the hard way).

### Step 1 — round-end events from GRID

Per series (skip `GRID_TEST_SERIES_IDS`), walk `ss` rows in `req_ns` order.
For the current game (`rows.current_game`), `n = len(rows.round_winners(game))`
— the number of resolved rounds — and `map_number = game["sequenceNumber"]`.
When `n` increases by exactly 1 versus the previous poll of the same
(series, map), emit an event:

```
{"series_id", "map_number", "round_number": n,
 "winner_team": <name>, "loser_team": <name>,
 "t_prev_ns": <req_ns of the previous poll>, "t_obs_ns": <req_ns of this poll>,
 "poll_gap_s": (t_obs - t_prev) / 1e9}
```

TRAP D1 (side swap): `round_winners` returns SIDE strings ("ct"/"t"), and the
poll that first shows round n resolved is a snapshot of round n+1 — after
the halftime swap (round 12→13) the sides already point at the other teams.
Do NOT map side→team through `sided_teams` on the observing snapshot. Take
the winner NAME from the segment itself: in `game["segments"]` the entry with
`sequenceNumber == n` and `type == "round"` carries
`teams: [{"name": ..., "side": ..., "won": bool, ...}, ...]`; the team with
`won == true` is the winner. No single `won == true` → skip the event
(`skipped:ambiguous_winner`). An increase by more than 1 → skip
(`skipped:multi_round_gap`); the first observation of a map has no previous
poll → no event.

### Step 2 — join to the book and find the first move

Slug + orientation: `mapentry.market_for_instance(mapping, map_number,
team=winner_team, opponent=loser_team)` → `(slug, us_side)` for the map
book, where `us_side == "long"` means the winner is the book's named (Yes)
team → the expected reprice is mid UP; `"short"` → mid DOWN. No mapping /
no map book → `skipped:no_book`.

From `pmus_bbo` rows for that slug (sorted by `req_ns`, heartbeat rows
included — they are valid observations):

- `mid_before` = mid of the last row with `req_ns <= t_prev_ns` (the round
  had not ended yet at t_prev). None → `skipped:no_pre_book`.
- `t_move` = `req_ns` of the earliest row with `req_ns > t_prev_ns` and
  `|mid - mid_before| >= 0.01` (one tick), searched up to
  `t_obs_ns + 300s`. None → outcome `no_move_300s`.
- `lead_vs_obs_s = (t_move - t_obs_ns) / 1e9` — NEGATIVE means the book
  repriced before our poll even showed the round end; positive is the time
  we had. `lead_vs_prev_s = (t_move - t_prev_ns) / 1e9` is the upper bound.
- `direction_agrees` = (mid moved UP and us_side == "long") or (DOWN and
  "short").
- Also record `mid_at_obs` (last row ≤ t_obs) and `move_size = mid_after -
  mid_before` for the row at `t_move`.

TRAP D2: `lead_vs_obs_s` is a LOWER bound on the true GRID lead — we see
round ends at poll granularity (12s live cadence) and the book at 2s
granularity. Say so in the output header. Never compare these numbers to the
intl 13–27s figure as if they were the same measurement.

### Step 3 — our own pipeline legs (same events)

For each event, the instance rows that fire on the next round's first
coherent snapshot carry `series_id`, `map_number`, `round_number == n + 1`,
`grid_ts_ns` (the ss row's write time) and `ts_ns` (instance write time) —
files `data/shadow/instances-YYYYMMDD.jsonl`. The mapentry row for the same
instance carries `src_ts_ns` (= instance ts_ns), `grid_ts_ns`, `ts_ns`
(recorder write time), joined on `(src_hypothesis_id == instance.hypothesis_id,
series_id, map_number, round_number, dedup_extra)`. Intent rows
(`data/executor/intents-YYYYMMDD.jsonl`, `type == "order_intent"`) carry
`ts_ns` and the dedup key `"{hyp}:{series_id}:{map_number}:{us_side}:{slug}"`.
Report per-leg medians where joinable: GRID round trip (`ss.ts_ns - req_ns`),
detect lag (`instance.ts_ns - grid_ts_ns`), recorder lag (`mapentry.ts_ns -
src_ts_ns`), engine lag (`intent.ts_ns - mapentry.ts_ns`), and the total
`intent.ts_ns - t_obs_ns`. Missing legs are reported as missing, never
imputed.

### Output

Markdown summary to stdout: n round ends, n joined to a book, outcomes
(`moved`, `no_move_300s`, each `skipped:*` reason with counts), for `moved`:
p10/p50/p90 of `lead_vs_obs_s`, share with `lead_vs_obs_s < 0` ("book first"),
direction agreement rate, and a split by map phase (round ≤ 6, 7–18, ≥ 19).
Then the pipeline legs table. Write per-event rows to
`data/analysis/timing_chain-YYYYMMDD.json` (`{"day", "events": [...],
"summary": {...}}`).

### Tests: `tests/test_timing_chain.py` (synthetic day files under tmp_path)

- two polls with n 3→4 produce one event with the winner name taken from
  the round-4 segment (build the segment with `won` flags, and make the
  observing snapshot's `sided_teams` DISAGREE with the segment so a
  side-swap bug would fail the test);
- n 3→5 skipped as multi_round_gap; first poll of a map emits nothing;
- first ≥1-tick move after t_prev is picked, a sub-tick jitter is not;
- negative lead when the move precedes t_obs; `no_move_300s` when quiet;
- orientation via a mapping whose named team is the LOSER → us_side
  "short" → a downward move agrees;
- empty `pmus_bbo` input → exit code 1.

## Verification the builder must run (and paste the output of)

```
uv run pytest -q tests/test_pmus_books.py tests/test_timing_chain.py
uv run pytest -q                       # whole suite stays green (750+ before this work)
uv run ruff check src/gridbot/capture/pmus_books.py scripts/timing_chain.py tests/test_pmus_books.py tests/test_timing_chain.py
uv run python -m gridbot.capture.pmus_books --once   # real gateway, ~10s: prints the slugs it polled and the rows written
uv run python scripts/timing_chain.py 20260903        # must exit 1 loudly until the recorder has run
```

Do not `git add` or commit; do not touch `src/gridbot/executor/**`,
`tests/test_executor*.py`, `docs/STATE.md`, any existing deploy unit, or
`systemctl`. The ops session reviews and lands.

## Reading the result (for the ops session, not the builder)

If p50 `lead_vs_obs_s` is materially positive and direction agreement is
high, the lead survives on the executable venue and the pipe legs show what
to cut (poll cadence 12s→10s is the per-series 6/min ceiling; file follows +
the 2s engine cycle are the rest). If the share of "book first" is large,
the venue is already faster than a 12s poll can see and the rail-B claim
needs a faster observation path before any registration. Either way this is
the number a rail-B instrument gets pre-registered against.
