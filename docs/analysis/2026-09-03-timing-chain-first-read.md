# Timing chain — first read on the venue we trade (2026-09-03)

Instrument: `scripts/timing_chain.py` over the first PMUS book tape
(`data/pmus/books-20260903.jsonl`, recorder `gridbot.capture.pmus_books`,
trial unit started 03:24 PDT). Diagnostic only — no registry bar, nothing
armed. Spec: `docs/superpowers/specs/2026-09-03-timing-chain.md`.

**Read this first.** `lead_vs_obs_s` is a LOWER bound on the true GRID lead:
round ends are seen at the 12s live poll cadence and the book at the 2s
recorder cadence. NEGATIVE means the book repriced before our poll even
showed the round end. It is not comparable to the cs2agent-era intl 13–27s
figure — different venue, different observation path.

## The numbers (682 round ends, 293 with a book move)

| statistic | value |
|---|---|
| round ends observed (test series excluded) | 682 |
| joined to a PMUS map/match book | 561 |
| first ≥1-tick move within 300s | 293 |
| no move within 300s | 96 |
| skipped: no book / no pre-event book / gap | 120 / 172 / 1 |
| lead_vs_obs_s p10 / p50 / p90 | −8.3s / **+13.1s** / +109s |
| lead_vs_prev_s p50 (upper bound) | +27.5s |
| book moved BEFORE our poll showed the round end | **29%** |
| first-move direction agrees with the round winner | **55%** |

By map phase (moved only): early (≤6) n=86 p50 +12.6s, book-first 31%;
mid (7–18) n=152 p50 +17.0s, book-first 24%; late (≥19) n=55 p50 +6.4s,
book-first 40%.

Our own pipe, median over the same events (never imputed):

| leg | n | median |
|---|---|---|
| GRID round trip (`ss.ts_ns − req_ns`) | 682 | 278 ms |
| detect lag (instance − grid_ts) | 608 | 161 ms |
| recorder lag (mapentry − instance) | 608 | 1,188 ms |
| engine lag (intent − mapentry) | 149 | 2,772 ms |
| total (intent − t_obs) | 149 | 45.9 s |

## What it says

1. **A lead exists on the executable venue, and it is of the order the
   registry assumed** — p50 +13s lower bound, +27.5s upper bound — but it
   is far from uniform: in 29% of round ends the book had already moved
   before a 12s poll could show us the round, and late-map rounds (≥19,
   the ones that decide maps) are the worst at 40% book-first.
2. **The "first move" is mostly noise.** Direction agreement of 55% means
   the first ≥1-tick mid move after a round end is nearly a coin flip
   relative to the winner. The instrument needs a size-and-horizon read
   (oriented mid move at +30/+60s, first move of ≥3 ticks) before the lead
   number is a trading number — queued as the next amendment to the script.
3. **Our controllable pipe is ~4.4s of that lead** (0.28 + 0.16 + 1.19 +
   2.77 s), plus a mean 6s wait on the 12s poll. The 45.9s "total" is NOT
   pipe latency: intents fire at the next model threshold crossing, which
   happens mid-round, so the number measures signal timing, not code.
4. **Ways to beat it, in order of cost:** (a) poll live series at the
   6/min per-series ceiling (10s, not 12s) and stagger series so the global
   180/min budget is used — cuts the mean observation wait by 1s; (b) fuse
   the mapentry recorder + engine into one in-process pipeline (no 2s file
   follows, no 2s engine sleep) — cuts ~3.5s; (c) act on the GRID
   observation itself (round outcome → map fair value) instead of waiting
   for a threshold crossing mid-next-round — this is the rail-B mechanism
   and is what the 45.9s total is really measuring; (d) a PMUS WebSocket
   feed (needs the trading keys) to know within ~100ms when the book has
   already moved and abstain. None of (a)–(c) needs a new venue; all are
   gridbot-lab runtime work.

## Caveats on this tape

- The recorder ran 03:24–09:15 and 14:50– PDT; GRID capture itself was dark
  09:00–14:50 (wedged keyring read; see the integrity-pass doc), so the
  tape covers the European/Asian morning slate only.
- Map-3 books were mostly absent from the mapping snapshots (the venue
  lists them late), so decider maps are under-represented (`skipped:no_book`
  = 120).
- One day, one slate. Re-run nightly; the numbers become citable once ≥5
  nights and ≥1,000 moved events accrue, series-clustered.

Repro: `uv run python scripts/timing_chain.py 20260903` (exit 1 on a day
without book rows). Per-event rows: `data/analysis/timing_chain-20260903.json`.
