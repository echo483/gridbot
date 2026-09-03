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

## Addendum (16:05 PDT) — size-and-horizon read

The first read's "first ≥1-tick move" is mostly noise (55% direction
agreement). The amendment adds, per joined event, the ORIENTED mid move at
+30/+60/+120s after our observation (+ = toward the round winner's side) and
the first ≥3-tick (0.03) move, with series-clustered bootstrap CIs (resample
series_id, 1000 reps, seed 17). Same tape, same 12 series.

| horizon | rows | mean oriented [95% CI] | median |
|---|---|---|---|
| +30s | 343/389 | +0.007 [0, +0.014] | 0 |
| +60s | 340/389 | +0.006 [0, +0.012] | 0 |
| +120s | 330/389 | +0.004 [−0.002, +0.013] | 0 |

- Share of round ends whose oriented move reaches +3 ticks by +120s: **31%** (n=330).
- First ≥3-tick move (n=268): lead_vs_obs p10/p50/p90 = −2.7 / **+34.7** / +170s,
  book first 16%, direction agreement **54.5%** — no better than the 1-tick read.

**What it says.** A round end is worth about half a tick of oriented drift
on this tape: the map book barely reprices on the round we detect, and the
median event does not move at all. The ≥3-tick filter does not rescue
direction, and its p50 lead of 35s with a p90 of 170s means most 3-tick moves
inside the 300s window are driven by LATER rounds (a round lasts ~90–120s),
not the one we observed — the window is longer than the round cadence, so
"direction agreement" is diluted toward a coin flip by construction. The
timing lead is real; the payload per round is small. A single-round
front-run has ~0.5pt to collect before fees and spread; the mechanism that
survives this read is still rail B — act on the model's MAP fair value after
the round, aggregating rounds, rather than trading the round's own reprice.

**Next amendment (queued):** bound each event's horizon at the NEXT round
end in the same map (`move_next_round`), so every move is attributed to
exactly one round; report the same clustered CI and a direction agreement on
that bounded move. Until that lands, the 54.5% figure is not a statement
about the market — it is a statement about the window.

**Measurement notes.** Horizon observations are coverage-aware: the tape is
change-only with a 30s per-slug heartbeat, so a covered slug always has a row
within 35s before any instant; a silent book is a real 0.0 move, and a last
row older than 35s is a coverage gap (None). Requiring a row strictly inside
the window would drop exactly the quiet books and bias every horizon toward
movers (the lane's first cut did; +30s rows 320 → 343 after the fix). A
single-series CI is reported as "no interval", never as a zero-width one.

## Addendum 2 (16:40 PDT) — next-round-bounded move: the round is not the event

Bounding each event's horizon at the NEXT round end of the same map
(`move_next_round`; 334/389 events with a covered bounded row, 12 series):
mean oriented +0.003 [−0.002, +0.007], median 0; 27.5% reach +3 ticks; direction
agreement **50.2%** (n_nonzero = 257). Attributing each move to exactly one round
removes the last of the "signal": the round winner does not predict the book's
next move at all.

Split by which side won the round, the RAW (unoriented) move explains why:

| round winner | n | raw mean move | share raw > 0 (nonzero) |
|---|---|---|---|
| the named (Yes) team | 198 | +0.016 | 0.59 |
| the other team | 136 | +0.016 | 0.62 |

The book moves UP by the same ~1.6 ticks whether the named team or its opponent
won the round. A martingale price cannot drift the same way after both
outcomes, so this book is not pricing rounds — it is catching up to the map
STATE over minutes (the named team led most of these maps). That is the rail-B
mechanism (the book lags the model / the state) seen from the venue side, and
it is why the round-oriented direction figure is a coin flip: the round is the
wrong event to orient on. Per-slug agreement ranges 0.18–0.78 at n = 6–33, in
the pattern "drift toward the eventual map winner" (e.g. leo-tlr map 2: 0.78
agreement, named team won 92% of rounds), not the pattern of a flipped market
orientation (which would put a favorite-dominated slug near 0.2). One day; not
proven.

**Next amendment:** orient on the MODEL, not the round. Join each event to its
mapentry row (round n+1 — the pipeline join already exists), take
d = p_map(Yes) − mid_before, and report: (i) the share of nonzero bounded moves
with sign(move_raw) = sign(d); (ii) the mean move projected on sign(d), with the
series-clustered CI; (iii) the same at +30/60/120s; (iv) all of it by |d| bucket
(≥ 4pt is the entry band). A projected mean above zero with the CI clear of it
is the venue-side evidence a latency / model-momentum instrument would be
pre-registered against. Until then nothing here is a trading number.

## Addendum 3 (17:05 PDT) — model-momentum read: NULL at the round horizon

Orienting every move on the MODEL instead of the round (`toward_model_*` =
raw Yes-price move projected on the sign of p_yes − mid_before, p_yes from
the event's next-round H-MODELENTRY1 row; 292 events joined, 11 series):

| horizon | entry band (|gap| ≥ 0.04), n | mean toward-model [95% CI] | share > 0 (nonzero) |
|---|---|---|---|
| +30s | 237 | +0.003 [−0.003, +0.010] | 0.535 |
| +60s | 237 | +0.003 [−0.006, +0.011] | 0.536 |
| +120s | 232 | −0.005 [−0.014, +0.004] | 0.473 |
| next round | 233 | −0.003 [−0.013, +0.007] | 0.451 |

- 83% of model-joined events sit in the entry band: the model disagrees with
  the book by ≥ 4pt almost always after a round end (the NOTE5 overconfidence
  population, seen from the tape).
- Every interval straddles zero; share-positive is a coin flip. **The book
  does not converge toward p_map within a round on this tape.** Whatever the
  model knows after a round, the book does not price it in the next two
  minutes — nor does it price the opposite.

**What this does to the mechanism.** "The book lags the model" survives only
at the SETTLEMENT horizon: the post-shock instrument's +19pt settled edge
(DOWN arm, in-sample) is the model being right more often than the book on
the resolved outcome, not a reprice we could capture in minutes. That is the
H-MAPENTRY1 thesis (enter ≥ 4pt under fair, hold to settlement) restricted
to fresh ≥ 25pt shocks — and H-MAPENTRY1 is where fill selection ate the
paper edge. So the trading question collapses to one: at the moment of a
large fresh model move, can we actually fill at the stale quote, in size,
before it disappears — and is the fill population the one that wins? The
post-shock diagnostics in flight (top-of-book size, time-to-convergence vs
our ~15s pipe, never-converged share, cross-arm overlap) answer exactly
that. The H-MODELMOMENTUM1 draft brief's premise ("captures part of the gap"
as a reprice) is amended accordingly: the claim, if kept, is a settlement-
horizon claim with an execution-realism gate, not a momentum scalp.

Measurement note: `toward_model` is None when |gap| < 0.005 (no direction) or
when the horizon has no covered observation; n and n_series ride with every
interval as before.
