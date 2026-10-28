# Spec — the research control plane: a hypothesis factory, not a hypothesis

**Date:** 2026-10-28 · **Branch:** `research/lab-v1` (worktree `~/devlabs/gridbot-lab`)
**Status:** DRAFT — the scaffold is built and tested; § Open decisions gates the rest.
**Companion to:** `docs/specs/2026-10-28-buildout-spec.md` (that one builds the
plumbing: capture v2, C++ core, host). This one builds the *search* on top of it.

---

## 1. The diagnosis (why the search stalled)

The baseline is not short of discipline. It is short of **throughput of ideas**.
Five facts from the tree, each with its evidence:

1. **The registry is prose.** `docs/experiments/registry.jsonl` has three keys —
   `hypothesis_id`, `hypothesis_text`, `run_ts`. The claim, the population and the
   falsification bar are all inside one English paragraph. No program can read
   them, so no program can check that an instrument tested the registered thing.
2. **One hypothesis = one bespoke instrument.** 34 registry rows, ~25 frozen
   instruments, 29 analysis documents. `docs/MAP.md` § *Forking for a new
   strategy* says to **replace** `instruments/` and the registry — i.e. a repo
   fork per idea. The marginal cost of idea N never falls, so breadth is
   impossible by construction.
3. **The verdict is a human act.** Falsification is a person reading a paragraph
   and writing a dated document. Correct, and unscalable.
4. **No beta, no exposure accounting.** `scorecard/` measures oriented edge,
   calibration and Kelly. Nothing regresses strategy returns on a reference, so
   "how much of this is just the market moving" is unanswerable.
5. **No multiple-testing control.** 34 rows plus their amendments (`-NOTE1`…
   `-NOTE9`, `-AMEND1/2`, band-gate changes) are all trials. Nothing deflates for
   the search that produced them. This is the mechanism behind the two recorded
   falsifications: a `+0.198` "skill" that evaporated under series clustering, and
   a post-shock edge that lived entirely in books with no depth.

Fact 5 is the one that matters most, and it is a *math* problem, not a discipline
problem. With `k` trials the expected maximum Sharpe under a true null grows like
`sqrt(2 ln k)`; at k=30 it is already large. A breadth-first search without
deflation does not find edges, it manufactures them — and this project has
already paid for that lesson twice.

## 2. What was built (this branch, tested)

```
src/gridbot/lake/     ETL: raw JSONL -> Parquet -> typed event tape -> gates
src/gridbot/lab/      research: registry v2, scan engine, fill model, metrics, report
tests/synth_tape.py   synthetic capture writer: every test runs with no key, no corpus
```

**`lake/` — the ETL control plane.** `raw` copies day files to Parquet faithfully
and *reports* what it dropped (a torn line is a counted drop, never a silent one);
`events` makes one typed, title-agnostic projection (`a_*`/`b_*` team slots with
explicit side labels, so CS2's CT framing and Dota's Radiant/Dire both work with
no code change); `gates` answers six questions with numbers, and a gate that
cannot run reports **not-run**, never pass. `inventory` is read-only, so the T7
can be inspected before a byte is written.

**`lab/registry.py` — hypotheses as data.** A v2 row carries `population` (SQL),
`metric`, `bar`, `direction`, `cluster_key`, `n_min`, `holdout`, `config_hash`,
`n_trials`. Registration **refuses** a row without a bar or a population. The 34
historical rows import as `machine_checkable=False` with their prose intact and
are **not** auto-upgraded — inventing a structured bar from a paragraph written
months earlier is post-hoc selection with extra steps.

**`lab/scan.py` — the breadth engine.** One implementation evaluates any
registered hypothesis over a documented `scan_input` view. It reports the
**opportunity** population and the **filled** population separately, always,
because the gap between them is the −6.5pt selection cost that killed the
post-shock lane. `PASS` requires the registered bar **and** an interval that
excludes zero **and** the registered `n_min`.

**`lab/fills.py` — the honest fill model.** A resting order fills when the tape
comes to it, inside the TTL. It is optimistic about the touch and silent about
nothing; the direction of the bias is documented rather than hidden.

**`lab/metrics.py` — measurement that cannot flatter.** Series-clustered
intervals throughout; `beta_alpha` against a reference series; `deflated_sharpe`
which **cannot be called without `n_trials`**; `minimum_detectable_effect` and
`required_n` so "is this n enough" has an answer. A non-finite statistic becomes
`None`, never a number that prints; a degenerate population (every trade
identical) yields no Sharpe and no deflation, because a zero-width interval is
arithmetic, not evidence.

**`lab/report.py` — the visualization.** One self-contained HTML page per batch:
verdict pills, equity curves, interval bars, and the two facts a reader must not
hunt for — the search size (which deflates every Sharpe on the page) and the fill
rate.

**Verification.** 60 new hermetic tests; repo total **1055 passed / 1 failed / 1
skipped**, the single failure being the known corpus-absent smoke test. `ruff
check .` green. End-to-end demo: synthetic capture → lake → gates → join → fill
model → scan → HTML, with a real `PASS` (mean +0.26, CI [+0.064, +0.409],
n=280/20 series) and a real `FAIL` on an unmet bar.

## 3. Target architecture

```
 [M720q, always on]                    [dev laptop / homelab]
 capture v2 (py)  --raw JSONL-->  lake: raw -> events -> gates   (Parquet + DuckDB)
   GRID poller        SOURCE OF TRUTH         |
   PMUS books                                 v
   (later: trades WS)               lab: registry v2 -> scan engine -> metrics
                                    |                              |
                                    v                              v
                            report.html (visual)          verdicts (data, not prose)
                                    |
                    ┌───────────────┴────────────────┐
                    v                                v
        SYSTEM 1 (fast, frozen)            SYSTEM 2 (slow, broad)
        C++ libgridcore: book+state        python: hypothesis generation,
        -> signal -> intent, µs            scan over the whole market,
        one implementation, shared         beta/alpha, deflation, human
        by backtest and live               judgement, registry writes
                    │
                    v
              wire -> Polymarket US -> ledger -> lake
```

**Binding principles** (inherited from `docs/ENGINEERING.md`, extended):

1. Raw JSONL is the source of truth; Parquet is a rebuildable artifact. A rebuild
   must reproduce identical row counts, and the gates assert it.
2. **One decision implementation.** Backtest and live call the same core.
3. **A hypothesis is a row, not a fork.** Adding idea N costs a registry row.
   Only path-dependent strategies need code, and they plug in behind one interface.
4. Every published number carries `n`, `n_series`, and — for anything Sharpe-like
   — the search size it was selected from.
5. KISS: no Postgres, no bus, no scheduler beyond systemd timers.

## 4. System 1 / System 2 — where the decision model fits

The dual-process split is not a metaphor here; it is the existing rail structure
with names attached.

- **System 2 (slow, deliberate, python).** The research control plane. It reads
  the whole market, generates candidate populations, scans them, measures beta and
  alpha, deflates for the search, and writes registry rows. It is allowed to be
  wrong, slow and broad. It never places an order and never reads a secret.
- **System 1 (fast, frozen, C++).** The distilled decision: a book + game state
  in, an intent out, in microseconds, with no reasoning in the path. Its
  "intuition" is a **precomputed table** — calibrated probabilities and thresholds
  compiled from whatever System 2 validated — so it is deterministic, auditable
  and replayable. An LLM in this path would violate rail 1 outright.
- **The contract between them is one frozen artifact**: a registry row plus the
  config hash it points at. System 2 produces it; System 1 carries it. A mismatch
  means the thing that ran is not the thing that was registered.

This is also the honest answer to "where does a *model* fit": a small calibrated
classifier (or a lookup table distilled from one) is System 1's decision function,
and its training is System 2's job. The ML stays the user's, per the ownership rule.

## 5. Milestones

| # | milestone | owner | acceptance |
|---|---|---|---|
| **L0** | lake on the T7 corpus | AI built, **USER** runs | `inventory` shows the corpus; `all` green; gates clean; row counts reconcile |
| **L1** | registry v2 import + first structured rows | **USER** | the 34 prose rows imported as non-runnable; ≥3 real hypotheses registered with bars |
| **L2** | `markets()` mapping from `executor/us_mapping` | **USER+AI** | join produces rows on real data; unmapped slugs dropped, never guessed |
| **L3** | fill model refinement (queue position, depth) | **USER** | reproduces the NOTE6 fill-conditional split from the lake |
| **L4** | Dota 2 lane | **USER+AI** | a second title flows through the same tape and scan with no schema change |
| **L5** | walk-forward + deflation wired to the real search | **USER+AI** | every verdict carries `n_trials`; the trial-variance term comes from the recorded candidates |
| **L6** | System 1 seam: distilled table + golden-tape parity | **USER** | C++ core reproduces the python reference byte-for-byte on a tape |

## 6. Open decisions

1. **Registry file name and location.** `docs/experiments/registry_v2.jsonl` next
   to the untouched v1, or fold v2 into the existing file? *Recommendation: a
   separate file.* The v1 file is append-only evidence and the two have different
   schemas; merging them would rewrite history.
2. **`markets()` ownership.** The slot mapping lives in
   `executor/us_mapping.py` + `market_match.py` today. Expose it as a lake view
   (one implementation, no duplication) or keep the join reading a table the
   operator supplies? *Recommendation: a view, built from the existing module.*
3. **Deflation input.** `variance_of_trial_sr` is currently passed by hand. Should
   the scan engine compute it from the recorded candidate Sharpes (requires
   storing every trial's Sharpe, which the registry does not do yet)? *Recommendation:
   yes, and make it a registry field — a search that cannot state its own spread
   cannot deflate.*
4. **Where the lab lives.** This branch is a worktree off `main`. Merge to `main`
   after the cleanup branch lands, or keep `research/lab-v1` as a long-lived
   branch? *Recommendation: merge after cleanup; it is additive and touches no
   existing module.*
5. **Dota 2 timing.** GRID titles are discovered by `scripts/grid_probe.py` and
   `GRIDBOT_EXTRA_TITLES` already accepts `dota2=<id>`. Start capturing Dota the
   moment the id is confirmed, or wait for the CS2 lanes to close? *Recommendation:
   start capturing immediately — the corpus is the moat and capture is cheap; the
   research can follow.*

## 7. What this does NOT claim

No PnL. No lane passed on real data — the demo numbers are synthetic and are
labelled as such. The platform's contribution is that it makes a false edge hard
to publish: every number on the report carries its n, its series-clustered
interval, its fill rate and the size of the search that produced it.
