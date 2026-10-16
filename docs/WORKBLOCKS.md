# Workblocks — the live plan, and who writes what

**Baseline:** 2026-10-16. Supersedes `docs/DECISIONS.md` as the forward plan
(that file stays as the 2026-09-04 snapshot; decisions D1–D7 are folded in
here where they are still live).

**Purpose of this file:** unblock the build and keep it explainable. Every
workblock is small enough to finish, has one named owner, and carries a list of
concepts the owner must be able to whiteboard before it is called done.
The last part is not ceremony — an artifact you cannot derive from first
principles is an artifact you cannot defend in an interview, and this project's
whole value is that its numbers are defensible.

---

## The ownership rule

**The person named writes the code. Review is not authorship.**

| mark | meaning |
|---|---|
| **USER** | you write it. AI reviews, and answers questions, but does not author. |
| **USER+AI** | you own the design and drive; AI scaffolds boilerplate and reviews. |
| **AI** | AI authors; you review and approve. Used only for mechanical or already-decided work. |

Declared intent, recorded so it does not drift:

- **Rust fire path is USER, entirely.** The point is to get good at low-latency
  execution. AI supplies a semantics reference and a benchmark harness, and
  reviews for UB/data races. It does not write the hot path.
- **ML is USER, entirely.** Same reasoning.
- **Backtest core and fill model are USER.** AI scaffolds the harness, the
  data plumbing and the tests.
- **AI authors** the scorecard rewiring (mechanical, equivalence-tested) and the
  presentation drafts.

---

## Kernel findings from the baseline pass

These are the bugs and properties the scorecard pass surfaced. They set the
standard for the rest of the work: a number is not a number until you have
checked which implementation produced it.

1. **The clustered bootstrap existed in ten files, with two different
   percentile conventions.** `mapentry_verdict`, `verdict`, `mapentry2_score`,
   `roundwin2_poolb`, `crossin_backtest`, `queue16_phase2_outcomes`,
   `queue16_bandskip_crossin`, `timing_chain`, `postshock_fade`, `pcal_fit`.
   Two of them indexed the percentile against the *requested* rep count and the
   rest against the *surviving* count, so identical inputs could return
   different intervals with nothing in the tree able to detect it.
2. **"ECE (quintile bins)" meant three incompatible things.** `quant_eval`
   computed equal-**count** bins with the bin mean forecast; `mapentry_verdict`
   computed equal-**width** bins with the bin **midpoint**; `train.py` and
   `mapentry2_score` had further variants. "Quintile" means equal-count, so the
   frozen 0.08 bar did not mean what the module that gates clause (i)
   computed. Pinned in `tests/test_scorecard_consolidation.py`, which asserts
   they disagree.
3. **The midpoint convention reads the model as better calibrated than it is.**
   On a fixture where forecasts cluster at the low edge of their bin, the
   midpoint reading understates the error by more than the 0.08 bar — enough to
   flip a PASS/FAIL if a bar accepted it.
4. **Murphy's three-term identity is not exact for continuous forecasts.**
   `BS = reliability − resolution + uncertainty` assumes forecasts are constant
   within a bin. Ours are quantile slices of a continuous `P_map`, so a
   within-bin variance and covariance term survives. The kernel reports both
   forms and the residual, so a reader can see when the three-term reading is
   trustworthy instead of assuming it.
5. **Equal-count binning of bulk-tied forecasts manufactures resolution.**
   With ties, the bin slice decides which rows land together. The canonical
   kernel sorts on `p` alone; `quant_eval` sorted `(p, y)` tuples, letting the
   label influence bin membership. Low severity — it needs bulk ties — but it
   is a real difference.
6. **`log_growth_stats(p, price, f=1.0)` raised an opaque `math domain error`.**
   Staking the whole bankroll is ruin (`log1p(-1)`), which the kernel now
   rejects with a message that says so.

**Nothing was changed in the legacy paths.** The kernel is additive; the
rewiring below is a separate, equivalence-tested change, and one of its steps
needs your decision first (see *Open decisions*).

---

## Data layer decision: DuckDB/Parquet, not Postgres (for research)

Short answer: **both, split by access pattern — and you already own both.**

| need | right tool | why |
|---|---|---|
| research + backtest over the tick streams | **Parquet + DuckDB** | columnar scans and predicate pushdown over tens of millions of rows with no server; files are immutable snapshots, so a number computed in March is reproducible in October; `read_json_auto` ingests the existing day-file JSONL directly; zero-copy into Arrow/Polars; plays with a Rust reader later |
| transactional runtime state (live ledger, dedup keys, caps, position state, reconcile) | **Postgres** — or keep the current append-only JSONL ledger | ACID, unique constraints for dedup, concurrent writers, crash recovery, point updates. DuckDB is single-writer and immutable-file-shaped; it is the wrong shape for an order lifecycle |
| cross-device durability / archive | **Supabase (Postgres)** — already wired | `gridbot-ingest` pushes the corpus hourly and `gridbot-backup` writes raw offsite daily. You are not choosing between them; Postgres is already in the stack as the archive. |

**Recommendation, KISS:** add Parquet + DuckDB as a local analytical lake and
change nothing else. Do **not** introduce a new Postgres instance for research.
Revisit Postgres for runtime state only when one of these is true: (a) more than
one process must write the ledger concurrently, (b) the Rust fire path needs a
durable state store with transactional recovery, or (c) the JSONL ledger read
becomes the measured bottleneck. Until then it is a new dependency and a new
failure mode for no measured gain.

The consequence to internalise: **the lake is a build artifact, not a source of
truth.** `data/` JSONL is the source of truth. Parquet can always be rebuilt
from it, which is what makes it safe to version, cache and throw away.

---

## Workblocks

### WB0 — restore the corpus · **USER** (AI writes the inventory)

- **Do:** `rsync` the whole `data/` tree from the old workstation preserving
  mtimes. Nothing else. Do not filter, do not prune, do not "clean it up on the
  way over".
- **Deliverable:** `data/` present; `tests/test_postshock_lag.py::test_real_day_file_smoke`
  green; a row-count and gap inventory written to `data/analysis/`.
- **Whiteboard:** why the corpus outranks any refactor; what a capture gap
  costs (three prior losses: two keyring, one power-off — and the difference
  between a *volume* gap and a *join* gap, where orphaned books with no game
  state can never produce a row).
- **Acceptance:** `uv run pytest` = 1010 passed, 0 failed.
- **Not doing:** deleting old-format files, reformatting anything in place.

### WB1 — evidence lake (Parquet + DuckDB) · **USER+AI**

- **Do:** one Parquet dataset per stream, partitioned by day, built from the
  JSONL; DuckDB views over them; a data-quality report.
- **AI scaffolds:** the ingest script, the schema definitions, the DuckDB views.
- **USER writes:** the quality gates and the queries. This is the SQL and
  data-wrangling rep — the gates are where you learn what is actually wrong
  with the data.
- **Deliverable:** `src/gridbot/lake/` + a generated `data-quality.md` with
  per-stream row counts, gap windows, and violation counts.
- **Whiteboard:** columnar vs row storage and why it matters for
  "group by series"; predicate pushdown and partition pruning; schema
  versioning; **as-of joins and time alignment** — how to put a 2-second book
  tape onto an irregular event clock; forward-fill with an explicit staleness
  bound and why naive fill silently manufactures edges; clock skew between the
  GRID and Polymarket clocks.
- **Acceptance:** every stream reconstructs to identical row counts from JSONL;
  the leak filter (CORPUS-F1, which caught 13% of rows as post-round transition
  snapshots) is re-expressed as a lake-level gate and reproduces its count.
- **Not doing:** Postgres, an ORM, a server, streaming ingest, a scheduler.

### WB2 — execution-realistic backtester · **USER** (AI scaffolds)

This is the highest-credibility artifact in the project. Everything in
`docs/analysis/` that died, died here: the post-shock edge was a dead-book
artifact, and `H-CROSSIN1`'s edge did not survive repricing to the ask.

- **Do:** replay the lake through a strategy interface with an explicit fill
  model. The fill model is the deliverable; the strategy is a stub.
- **USER writes:** the replay loop, the fill model, the accounting.
- **AI scaffolds:** the strategy interface, the test fixtures, the scorecard
  wiring.
- **Deliverable:** `src/gridbot/lab/` — replay harness + fill model + a
  scorecard report over a declared holdout.
- **Whiteboard:** queue position and why it dominates the observed fill rate;
  depth-limited fills; post-only maker semantics and what the venue rejects;
  adverse selection and how `fill_conditional_edge` exposes it; look-ahead
  bias and how a leak filter differs from a train/test split; walk-forward vs a
  declared holdout; what the clustered bootstrap is doing and why rows are not
  independent.
- **Acceptance:** known-answer tests — a synthetic tape with a hand-computed
  fill count; the NOTE6 fill-conditional split reproduced from the lake; the
  harness reproduces a named number already published in `docs/analysis/`.
- **Not doing:** a queue simulator with a microstructure model. Start with the
  simplest fill rule that is *honest*: fill only when the tape trades through
  our price with depth available. A number from a simple honest model beats a
  precise wrong one.

### WB3 — rewire `scripts/` onto the kernel · **AI** (after one decision)

- **Do:** point the ten legacy implementations at `gridbot.scorecard`, deleting
  the duplicates. Each rewiring keeps its legacy convention via an explicit
  argument (`method="nearest_rank"`, `forecast="midpoint"`) so **behavior does
  not change in this block**.
- **Deliverable:** ~10 scripts slimmer; identical published numbers; the
  equivalence tests already written in
  `tests/test_scorecard_consolidation.py` extended to each script.
- **Blocked on:** *Open decision 1* below.
- **Not doing:** changing any convention here. Behavior freeze first, then the
  convention decision as its own pre-registered change.

### WB4 — Rust fire path · **USER, entirely**

- **Do:** a Rust crate that takes the book + game state, computes the signal,
  and emits an order intent — with a deterministic replay that matches the
  Python reference, and criterion benchmarks.
- **AI provides:** the semantics reference (what the Python does, exactly), the
  benchmark harness, and a review for data races and UB. No hot-path code.
- **Deliverable:** `rust/gridbot-fire/` with `cargo bench` output published as a
  latency table; a cross-validation test against the Python reference.
- **Whiteboard:** SPSC ring buffers and why single-producer/single-consumer
  removes the lock; memory ordering (acquire/release) and what breaks without
  it; false sharing and cache-line padding; why allocation and syscalls in the
  hot path destroy tail latency; L2 book reconstruction and what to do with
  out-of-order updates; measuring p50/p99/p999 and the difference between
  throughput and tail latency; why a deterministic replay is a correctness tool
  and not just a benchmark.
- **Acceptance:** identical outputs to the Python reference on the same tape,
  byte for byte; a published p99 with the measurement methodology beside it.
- **Not doing:** rewriting the capture, the executor, or the analysis in Rust.
  The hot path is the seam.

### WB5 — risk engine · **USER+AI**

- **Do:** a real risk layer. The current story is thin in a specific, named
  way: caps are `stake × max_open` because settles are not auto-written, so the
  daily-loss cap cannot bind on realized PnL.
- **Deliverable:** settles writer from venue truth; a rolling-24h loss window
  (the current UTC-day bucket resets the cap at 17:00 PT mid-evening); gross and
  net exposure; per-series and per-market concentration limits; Kelly sizing off
  the **calibrated** probability, not raw `p_map`; a kill switch. Fail closed on
  any unreadable state.
- **Whiteboard:** why realized-only caps beat notional caps; why quarter-Kelly
  and not full; what a drawdown limit does to geometric growth; why a
  fail-closed read is the correct default for a risk control.
- **Not doing:** portfolio optimisation, VaR, or anything that needs a
  covariance matrix we cannot estimate at this sample size.

### WB6 — execution ML · **USER, entirely**

- **Do:** model **fills**, not prices. A fill-probability model and a
  quote-placement policy: *where* to post, not just whether to trade. This
  attacks the measured −6.5pt selection cost directly.
- **AI provides:** the data contract and a leakage checklist. Nothing else.
- **Prerequisite:** the trades tape (authenticated WebSocket `subscribe_trades`,
  read-only) — the missing sensor. Fill probability is the single biggest
  unknown and cannot be modelled without it.
- **Whiteboard:** why an unfillable edge is a selection effect; class imbalance
  in fill labels; why time-series CV differs from k-fold; probability
  calibration and how isotonic regression differs from Platt scaling.
- **Not doing:** retraining `q`. It is calibrated live and `promote_model.py`
  gates retrains for a reason. A better round model does not fix fill
  selection.

### WB7 — Kalshi adapter + cross-venue · **USER+AI**

- **Do:** two venue adapters behind one normalised event/book model, a
  per-venue fee model, and an arbitrage scanner measuring locked spread net of
  fees and how long it persists.
- **Deliverable:** a measured answer, published either way.
- **Expectation, stated honestly:** this is most likely fee-bound and
  ephemeral, and the overlap is narrow — Polymarket US lists match/map winner
  books only (no round books) and Kalshi's contract structure differs. A
  net-of-fee arb that does not exist is still a real result backed by real
  infrastructure. **Do not build this expecting a PnL line.**
- **Whiteboard:** contract normalisation across venues; why the mid is not a
  tradeable price; fee and rebate modelling; what "locked" spread means once
  both legs have latency.

### WB8 — presentation · **AI drafts, USER edits**

- **Do:** README (done), a one-page metrics sheet where every number carries n
  and its interval, an architecture diagram, the latency table, and a
  **falsification ledger** — what was killed and the test that killed it.
- **Not doing:** a PnL chart. There are 13 settled trades; there is no PnL
  claim available and any attempt at one dies in the first interview question.

---

## Open decisions (blocking)

**1. Which ECE convention does the registry bar mean?** *(blocks WB3)*
The bar says `ECE(P_map) > 0.08 (quintile bins)` and gates clause (i).
`mapentry_verdict` computes equal-width joints with the bin midpoint;
`quant_eval` computes equal-count bins with the bin mean. These are different
numbers on the same data (pinned in the test suite). Choosing the equal-count
reading is the statistically correct interpretation of "quintile", but it
**can flip clause (i)** — so per rail 2 it needs a pre-registered amendment row
before any code changes. Decide, then register, then rewire.

**2. The clause-(iii) holdout extension.** Drafted as `H-MAPENTRY1-HOLDOUT2`
(registry row 33, pre-registered) and awaiting your approval to run.

**3. D1 from the old snapshot: close `H-MODELMOMENTUM1`?** It was never
registered, the timing chain shows the book does not move toward `p_map` within
a round, and the post-shock diagnostics show the only apparent edge sat in books
with no depth. Recommendation: close it, and carry forward the two reusable
parts — the depth-limited fill gate and the artifact checks.

**4. The always-on host.** Two of the three prior capture losses came from
workstation session lifecycle, zero from code. This is the highest-value ops
decision outstanding and it is cheap relative to another multi-hour capture gap.

---

## Baseline commands

```
uv sync                                   # install
uv run ruff check .                       # must be green
uv run pytest                             # 1010 passed, 0 failed (after WB0)
uv run pytest tests/test_scorecard_*.py   # the kernel, 110 tests, ~1s
```

## Document maintenance (anti-staleness)

- `docs/STATE.md` gets a dated `**Updated YYYY-MM-DD**` addendum per baseline.
  Never rewritten in place — it is the narrative.
- `docs/WORKBLOCKS.md` (this file) is the only **live** plan. When a workblock
  closes, strike it here in place and record it in STATE.md.
- `docs/analysis/` is immutable evidence. Superseded numbers get a new dated
  document that cites the old one; the old one is never edited.
- `README.md` carries the status line and the verified-baseline block. If you
  run the commands and get different numbers, fixing the README is part of the
  change, not a follow-up.
