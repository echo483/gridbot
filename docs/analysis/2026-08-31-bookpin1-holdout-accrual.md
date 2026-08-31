# BOOKPIN1 holdout accrual — 2026-08-31

**Question:** how many distinct `grid_series_id`s have their FIRST mapping row at/after
`2026-08-31T19:53:46Z` (the H-MAPENTRY1-BOOKPIN1 holdout window), what is the per-day
accrual rate of newly-mapped series, and when do we hit 40?

**Method:** scanned all `data/pmus/mappings-*.jsonl` (6 files, 2026-08-26 → 2026-08-31,
36,547 rows, 520 distinct series). First-mapping timestamp = min `ts_ns` per series
across all files. Holdout cutoff = first-mapping `ts_ns >= 1788206026000000000`
(2026-08-31T19:53:46Z). No LLM in the trade path; this is offline corpus analysis.

**Data freshness:** latest row in corpus is `2026-08-31T20:47:13Z`
(`mappings-20260831.jsonl`). Capture on 2026-08-29 ended early (last row 22:32Z);
2026-08-31 is a partial day through 20:47Z.

## Per-day newly-mapped series (by first-mapping day, UTC)

| Day (UTC)   | New series first-mapped |
|-------------|------------------------:|
| 2026-08-26  | 142 |
| 2026-08-27  |  98 |
| 2026-08-28  |  67 |
| 2026-08-29  | 108 |
| 2026-08-30  |  57 |
| 2026-08-31 (partial, →20:47Z) |  48 |
| **Total**   | **520** |

Trend is noisy, mildly declining: last-3-full-day mean ≈ 77/day, last-5 ≈ 94/day.
Mapping rows arrive in periodic bursts (hourly capture dumps), so intra-day accrual
is lumpy, not continuous.

## Holdout tally (first-mapping >= 2026-08-31T19:53:46Z)

**Count so far: 0 / 40.**

The window is 54 minutes old at last data (19:53:46Z → 20:47:13Z) and the most recent
first-mapping in the corpus is `2026-08-31T19:17:25Z` (series 2998327) — no series has
crossed the cutoff yet.

Most recent first-mappings before the window (for proximity context):

| First-mapping (UTC) | grid_series_id | grid_start | teams |
|---------------------|----------------|------------|-------|
| 19:17:25Z | 2998327 | 2026-09-02T01:00:00Z | TBD-1 vs TBD-2 |
| 16:02:27Z | 2994076 | 2026-09-01T22:00:00Z | paiN Academy vs Grêmio |
| 16:02:27Z | 3002411 | 2026-08-31T17:00:00Z | TBD-1 vs TBD-2 |
| 16:02:27Z | 3002410 | 2026-08-31T17:00:00Z | TBD-1 vs TBD-2 |
| 15:02:27Z | 2995880 | 2026-09-01T21:00:00Z | Galorys vs TBD-2 |
| 13:02:28Z | 2994075 | 2026-09-01T19:00:00Z | Procyon vs MEIA NOITE |

Raw holdout series list: **empty** — nothing to list until the next capture dump.

## ETA to 40 series

- At the last-3-full-day mean (~77 new/day): 40 series ≈ 12.5 h → **~2026-09-01T08:00Z**.
- At the last-5-day mean (~94/day): ≈ 10 h → **~2026-09-01T06:00Z**.
- At the slowest full day observed (57/day): ≈ 17 h → **~2026-09-01T13:00Z**.

**Best estimate: 40-series holdout complete around 2026-09-01 06:00–13:00Z**, i.e. roughly
half a day to a day out. First few holdout series should appear at the very next capture
dump. Caveat: mapping discovery lumps around schedule dumps; a quiet overnight schedule
(20 upcoming `grid_start`s already known, mostly 2026-09-01 17:00Z+ matches) could push
accrual toward the low end of the observed daily range.

## Matching-resolution outlook

Holdout series will resolve when their matches finish. Known upcoming starts (from
already-mapped series): 20 series with `grid_start >= 2026-08-31T20:00Z`, spread from
21:00Z tonight through 2026-09-02 — but the holdout series themselves (mapped first
after 19:53:46Z) will skew toward later starts, so expect holdout match resolutions
mostly on 2026-09-01/02.
