# Spec — gridbot buildout: lean core, Parquet lake, C++ fire path, live on the homelab

**Date:** 2026-10-28 · **Status:** APPROVED in direction; M0 landed 2026-10-28 (branch `cleanup/prune-2026-10`); M1/M2 decisions pending with the operator
**Supersedes in `docs/WORKBLOCKS.md`:** WB4 (Rust → C++). WB0–WB3, WB5–WB8 are re-sequenced below, not discarded.

## 1. What gridbot is (the bullets)

**One line.** A tick-level research and execution system for esports prediction
markets: it captures GRID live CS2 game state and Polymarket US order books,
replays them through an execution-realistic backtester, and only trades a
hypothesis after it passes a pre-registered, held-out test.

**Resume bullets — true today** (every number is in `docs/STATE.md` / `docs/analysis/`):

- Built a Python capture → signal → order pipeline trading Polymarket US esports
  markets on GRID live data; measured a **~13 s median information lead** of game
  events over book repricing (n=293 book moves over 682 round ends, lower bound).
- Ran it live with real money under hard caps; measured venue **place 394 ms /
  cancel 137 ms** and found a price-convention bug from venue records — **20 of 48**
  orders had never rested — then fixed and re-cut every fill statistic on rested orders.
- Shipped a calibrated LightGBM round-win model (**Brier 0.1105, ECE 0.031,
  n=1,984** live shadow forecasts).
- Ran the research under pre-registration (34 registry rows). **Falsified my own
  best-looking backtests**: a +0.198 "skill" claim dropped to zero once rows were
  clustered by series, and a post-shock edge turned out to sit entirely in books
  with no depth.
- Merged **ten hand-rolled bootstrap implementations** (two different percentile
  conventions) into one tested statistical kernel (110 tests).

**Target bullets — not true yet; each one becomes true at a milestone:**

- C++20 decision core shared by backtest and live (byte-identical on a golden
  tape); tick-to-intent **p50/p99/p999 = X/Y/Z µs** over N events, methodology published. *(M4)*
- End-to-end latency budget, leg by leg (GRID event → receive → decide → wire →
  ack), with n and CIs. *(M5)*
- Parquet/DuckDB lake of N M rows, rebuilt deterministically from raw capture,
  with data-quality gates. *(M2)*
- Event-driven backtester with a depth-limited fill model that reproduces a
  published number from the live record. *(M3)*

Do not put a PnL line on the resume. 13 settled trades cannot carry one.

## 2. Where it stands (2026-10-28)

- **Code:** all Python. 9.8k src / 13.9k scripts / 14.6k tests. 995 pass; the one
  failure is the corpus-absent smoke test. No Rust exists.
- **Data:** the full corpus is on the T7 (not mounted on this laptop). Nothing
  captures right now: no `gridbot*` units are loaded on this machine.
- **Strategy:** no lane has a PASS. H-MAPENTRY1 holdout = not falsified, clause
  (iii) under-powered (39/60). `dry_run=True` stays the default.
- **Known capture defects:** the desktop keyring is a single point of failure
  (three outages); laptop sleep and power-off cost hours of data; the GRID poll is
  12 s; there are separate poll processes joined through files.
- **Host:** the homelab ThinkCentre M720q is up. It will be the always-on host
  (closes Open decision 4 in WORKBLOCKS).

## 3. Target architecture

```
 [M720q, always on, chrony-synced]
 capture (Python)  ──►  raw JSONL, hourly files  ──►  lake build  ──►  Parquet (stream/day)
   GRID poller             SOURCE OF TRUTH            (nightly)          + DuckDB views
   PMUS book poller        append-only, seq#,                                 │
   (later: trades WS)      recv_mono_ns + recv_wall_ns                        ▼
                                                   research: scorecard, registry, notebooks
                                                                              │
                     ┌────────────────────────────────────────────────────────┤
                     ▼                                                        ▼
        C++ core (libgridcore): book + game state ──► signal ──► intent   backtester (replay
           one implementation, two hosts:                                  loop + fill model)
             (a) replay binary / nanobind module ◄── backtest              uses the SAME core
             (b) live fire service ──► wire ──► Polymarket US
                                     │
                              ledger (JSONL) ──► reconcile ──► lake
```

**Principles (binding for this buildout):**

1. **Raw JSONL is the source of truth; Parquet is a build artifact.** You can always
   delete and rebuild it, and a rebuild must give identical row counts.
2. **One decision implementation.** The backtest and the live path call the same C++
   core. If the backtest has a separate copy of the strategy, its numbers say
   nothing about live trading.
3. **A hypothesis is a plug-in, not a fork.** A strategy = a frozen config + one
   `on_event → intents` function behind a stable interface, with a registry row
   pointing at its config hash. Changing the hypothesis must not touch the replay
   loop, the fill model, or the wire.
4. **Every event carries timestamps for every leg.** Latency is measured, not
   estimated: `src_ts` (GRID/venue), `recv_wall_ns`, `recv_mono_ns`, `decide_ns`,
   `wire_send_ns`, `ack_ns`.
5. **Every published number carries n and a CI, clustered by series.** This
   already holds; the lake and the backtester inherit it.
6. **KISS:** no Postgres, no ORM, no streaming framework, no scheduler beyond
   systemd timers, no message bus. Processes are joined through files until a
   measured bottleneck says otherwise.

**Honest latency framing (keep this in every write-up):** the edge is a ~13 s
information lead. Today the pipeline uses ~4.4 s of it plus a 12 s poll wait, and
the venue round trip is ~400 ms over HTTPS. Moving the decision from Python
milliseconds to C++ microseconds will not change PnL. The C++ core is still worth
building for three reasons: backtest/live parity, determinism, and a tail latency
you can measure and defend. The levers that actually move the edge are poll
cadence, cutting the hops between processes, and the venue wire. M5 measures all
of them.

## 4. Milestones

Ownership marks are from WORKBLOCKS: **USER** = you write it; **USER+AI** = you
design, AI scaffolds; **AI** = mechanical, you review. The dispatch team only
takes **AI** items and the AI half of **USER+AI** items.

### Pre-data (can start now, no corpus needed)

**M0 — Prune · AI — LANDED 2026-10-28**
- Tag `evidence-2026-10` (annotated) on the pre-prune HEAD; every removed
  instrument runs from it.
- Removed 21 closed-lane or dead-infra scripts and 11 test files (293 tests):
  postshock_fade, postshock_lag, wave_detect, wave_edge_join, queue16_* (2),
  crossin_backtest, flow_features_study, priced_in_test, intl_reference,
  pcal_fit, roundwin2_poolb, quote_backtest, entry_quality, mapentry2_score,
  baseline_recompute, keyring_watch, capture_qa, eod_report, corpus_pull,
  backup_raw, setup_secrets.
- Supabase removed (sink, pull, backup, the two deploy units). `corpus.ingest`
  now writes local training rows only.
- Keyring removed: `gridbot.config` reads one permission-checked secrets file
  (`SECURITY.md`), tested in `tests/test_config_secrets.py`. `keyring` and
  `secretstorage` left the lockfile. Deploy units no longer anchor to the
  graphical session.
- Docs: `docs/FALSIFICATION.md` (what died and why) is new; `docs/STATE.md`
  is rewritten as current state, with the old narrative, nightly learnings,
  analyst drafts and superseded specs moved to `docs/history/` verbatim.
- Suite: 995 passed + 1 by-design failure → **711 passed, 1 skipped, 0 failed**.
  Accounting: −293 deleted tests, +12 secrets-file tests, −4 keyring-watchdog
  tests.
- **Still open from M0:** the scorecard rewiring (WB3) is blocked on the ECE
  convention decision.

**M1 — Capture redesign + M720q deploy · AI builds, USER reviews and deploys**
- Headless secrets: drop the gnome-keyring dependency on the server. Use systemd
  `LoadCredential=` pointed at a root-owned 0600 file. `gridbot.config` already
  reads `$CREDENTIALS_DIRECTORY/gridbot-secrets` (landed in M0). This removes
  the keyring outage class (08-26, 08-31, 09-03); the always-on host removes
  the sleep/power-off class (08-25, 08-30).
- Capture contract v2, on every row: `schema_v`, `stream`, `seq` (per-process
  monotonic), `recv_wall_ns`, `recv_mono_ns`, `src_ts`. Hourly files, fsync on
  rotate, heartbeat rows so a gap reads as a gap, not as silence.
- One supervised process per source. `Restart=always`, no sleep inhibitors, no
  graphical-session target. chrony on the host; log the clock offset as its own stream.
- Shrink the GRID poll to the rate-limit floor; record the actual cadence.
- **Acceptance:** 72 h unattended on the M720q, with a gap report showing zero
  unexplained gaps; kill -9 / reboot / network-drop drills each recover with a
  bounded gap that the heartbeat stream records.
- **Rail 4:** the old capture does not run at all now, so there is nothing to
  break. Once v2 runs, it is never stopped to land a change.

**M2a — Lake code on fixtures · USER+AI**
- AI: `src/gridbot/lake/`: JSONL → Parquet (zstd, hive-partitioned
  `stream=/date=`), schemas per stream (pyarrow), DuckDB views, a `rebuild`
  command, and fixture day-files with hand-counted rows.
- USER: quality gates as SQL (the CORPUS-F1 leak filter, monotonic seq, gap
  windows, stale-book bounds) and the as-of join from book tape to event clock
  with an explicit staleness bound.
- **Acceptance:** fixture round-trip gives identical row counts; gates are
  tested on fixtures that contain planted defects.

**M3a — Strategy interface + backtester skeleton · USER (AI scaffolds)**
- AI: the `Strategy` interface, event types, the config-hash → registry link,
  synthetic tapes with hand-computed answers.
- USER: the replay loop, the fill model (start honest: fill only on a trade or
  touch through our price with depth), and the accounting.

**M4a — C++ scaffold · AI scaffolds, USER authors the core**
- AI: `cpp/` with CMake + Ninja, C++20, `-Wall -Wextra -Wconversion -Wshadow`, an
  ASan/UBSan preset, GoogleTest, a Google Benchmark harness, a nanobind module
  stub, a golden-tape parity test that runs the Python reference and the C++ core
  on the same events and diffs the intents, and CI for all of it.
- USER: everything in `libgridcore`: the event structs, book state, game state,
  the signal, and intent emission. One concept per rep (see cpp-learning-path):
  fixed-size book, no allocation on the hot path, then the SPSC ring, then the
  measurement.

### Data-dependent (after the T7 ingest)

**M2b — Ingest the T7 corpus** into raw storage on the M720q (rsync, preserving
mtimes, no filtering) → lake build → `data-quality.md` with per-stream counts, gap
windows and violations. The smoke test goes green.

**M3b — Backtester reproduces a published number** (the NOTE6 fill-conditional
split or the timing-chain lead) from the lake. That is the backtester's
credibility test.

**M4b — C++ parity + benchmarks:** byte-identical intents vs the Python reference
on the real tape; tick-to-intent p50/p99/p999 with methodology (pinned core,
warm-up discarded, HdrHistogram, n events).

**M5 — Latency budget + metrics sheet · AI drafts:** a leg-by-leg latency table
from the v2 timestamps, a one-page metrics sheet (n + CI on every number), the
falsification ledger, and an architecture diagram.

**M6 — Live:** shadow mode on the M720q with the C++ core deciding and the
Python wire in dry-run; then a pre-registered hypothesis; then live-small, armed
by you only (LIVE_FIRE.md). The C++ wire (libcurl or Beast) replaces the Python
wire only after M5 shows the wire leg is worth it.

## 5. Dispatch plan (pre-data wave)

Parallel lanes, each on its own branch, each reviewed before merge:

| lane | milestone | owner type | merge gate |
|---|---|---|---|
| prune | M0 | AI | grep-clean, suite green, subtraction stated |
| capture-v2 | M1 | AI | contract tests + drill script; you deploy |
| lake-scaffold | M2a (AI half) | AI | fixture round-trip tests |
| cpp-scaffold | M4a (AI half) | AI | builds clean under all presets, empty core, parity harness runs on a stub |

M0 merges first; the other lanes rebase onto it. Not dispatched: anything marked USER.

## 6. Open decisions

1. ~~Supabase~~ — **dropped** (M0). Before deleting the project, confirm the
   T7 holds `data/analysis/training_rounds.jsonl` with the historical rows.
2. ~~Secrets~~ — **one permission-checked file** (M0); on the M720q, delivered
   through systemd `LoadCredential=`. ENGINEERING rule 6 is amended.
3. **M720q access** — Proxmox VE, reachable over SSH. Deploy details are
   deferred to M1.
4. **C++ wire language for M6:** defer (recommended) until M5 numbers exist.
