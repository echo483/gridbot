# roundwin-v2 design — flow-feature integration with anti-leakage protocol

Status: DESIGN ONLY (no src/ changes, no registry changes). Written 2026-08-28
after the priced-in test (`docs/analysis/2026-08-28-priced-in-test.md`) and the
flow-features study (`docs/analysis/2026-08-28-flow-features-study.md`), in
response to the queue task "v2 model design: flow-feature integration plan with
anti-leakage protocol." Implementation requires a registry pre-registration
(H-ROUNDWINV2) per iron rail 2.

## 0. Problem statement

- roundwin-v1 (LightGBM, 20 game-state features, CORPUS-F1-filtered) is well
  calibrated for **round outcome** (holdout Brier 0.1499, AUC 0.864, n=4,276)
  but has **~zero short-horizon price signal**: its fair value on postquote
  samples scores AUC 0.415–0.425 vs the next 30/60/120s oriented move, with
  Pearson corr −0.006..+0.018 (flow study). Between rounds q is blind.
- Book-flow momentum is real and incremental: `mom120` AUC 0.578, `vel` 0.579
  at 120s; per-day corr +0.037 (t≈1.7, 08-27) and **+0.158 (t≈3.6, 08-28)** —
  same sign both days, working hardest on the adverse day. Partial correlation
  controlling for p_map equals raw correlation because p_map contributes
  nothing short-horizon — the flow signal is fully additive.
- The engine now requires drift confirmation, but that only patches the
  decision surface; the underlying q still misses flow pressure. v2's job:
  give q flow features it can see at eval time, without ever looking forward.

## 1. Data reality (inspected 2026-08-28, exact counts)

### 1.1 Round corpus (roundwin-v1 substrate)
`data/analysis/training_rounds.jsonl`: **22,273 rows, 159 series**, labels
10,522×0 / 11,751×1 (CT-framed). Schema: `captured_at, ingested_at, series_id,
map_number, round_number, ct_team, t_team, features{20 names from
gridbot.corpus.rows.FEATURE_NAMES}, label, row_key, source_file,
source_sha256`. v1 split (from `data/models/roundwin-v1/metrics.json`):
CORPUS-F1 leak filter drops **2,898** rows, 49 missing-key rows more →
**19,326 trained**; train 107 series/12,684 rows, valid 19/2,366, pinned
holdout **32 series/4,276 rows**; best_iteration=96; calibrated Brier 0.1501,
ECE 0.0296, AUC 0.8640; Platt a=0.96828, b=−0.12790.

### 1.2 Shadow eval corpus (where flow features and drift outcomes live)
`data/shadow/` (three capture days):

| file | 08-26 | 08-27 | 08-28 | notes |
|---|---|---|---|---|
| mapentry-*.jsonl rows | 4,674 | 4,851 | 1,619 | series 43/41/28; maps 85/86/42 |
| mapentry with bid≠null | 870 | 2,278 | 928 | rows matched to a PMUS market (4,076 total) |
| mapentry qualifies=true | 291 | 900 | 265 | |
| postquote-*.jsonl rows | 855 | 2,663 | 747 | series 9/23/16 |
| postquote per dt_s | 285×3 | 888/888/887 | 253/249/248 | 30/60/120s |
| instances-*.jsonl | 997 | 4,720 | 1,572 | eval-time snapshots; `books: {}` today |

Schemas (field-exact):
- **mapentry**: `type, hypothesis_id, series_id, map_number, round_number,
  side, team, opponent, a, b, q_round, p_map, dedup_extra, market_slug,
  us_side, bid, ask, side_ask, entry_price_max, qualifies, skip_reason,
  src_ts_ns, grid_ts_ns, ts_ns`. Carries p_map + top-of-book at eval time.
- **postquote**: `type, slug, us_side, mid0, src_ts_ns, series_id, map_number,
  dedup_extra, dt_s, bid, ask, mid, oriented_move, ts_ns`. Each row IS a quote
  at eval time (`mid0`/`bid`/`ask`), and `oriented_move` at dt_s ∈ {30,60,120}
  is the outcome. Dense same-book history lives in this stream.
- **join keys**: mapentry ↔ postquote via `(series_id, map_number, dedup_extra)`;
  grid↔PMUS via `data/pmus/mappings-*.jsonl` (6,761 / 8,523 / 2,863 rows).
- **pmus_bbo depth**: `data/pmus/watch-20260827.jsonl` only — **333**
  `pmus_bbo` records, 08-27 pre-market only, 0 on 08-28. n=267 usable for the
  imbalance study → **indicative, not licensable**.

### 1.3 Hard numbers for the risk section
- Flow-feature training pool today: **3,315 joined samples at 120s** (08-27+
  08-28, per the flow study's own reported n; the underlying files hold 887+
  248=1,135 postquote rows at dt_s=120 across those two days, and ≈3,410
  postquote rows total — the study's 3,315 is its join-survivor count). Only
  samples with a prior same-book quote ≤120s carry momentum features.
- Round-outcome pool with flow join: 0 — training_rounds rows carry no books,
  and `instances.books` is `{}` today. **This is the capture-side gap**
  §3 exists to fill.

## 2. Feature plan

### 2.1 v2 feature vector
Append-only on the 20-name contract (`FEATURE_NAMES` is shared with the 22k
corpus — never reorder/rename). v2 = 20 v1 features + flow block:

| group | features | definition (all oriented to our side; points = 100×price) |
|---|---|---|
| momentum windows | `mom30, mom60, mom120` | mid(now) − mid(latest same-book quote ≤ w s before now) |
| velocity | `vel120`, `vel_norm` | mom120 / gap_s; mom120 / (spread · gap_s) |
| spread dynamics | `spread`, `spread_ch` | current ask−bid; Δ(ask−bid) vs previous same-book quote |
| staleness | `gap_s`, `qcount120` | seconds since previous same-book quote; count of same-book quotes in prior 120s |
| depth | `imb`, `imb_age_s` | (bidDepth−askDepth)/(bidDepth+askDepth) from latest pmus_bbo ≤ 300s old; its age |

Rules:
- `imb` enters the feature space **as NaN (missing), not as 0**, whenever no
  pmus_bbo snapshot exists within 300s — LightGBM learns missing natively and
  the pre-persistence scarcity (333 records) cannot fake signal. After the
  capture-side persistence of §3.2 lands, the NaN rate is monitored and `imb`
  is only *trusted* once persistence coverage ≥ 50% of eval rows.
- `spread` is carried but **downweighted/flagged as a state feature** — the
  flow study showed its tercile drift (+1.22pt) is confounded with
  wide/unquoted markets, not flow.
- No derived feature may consume anything with a timestamp after the eval
  row's `src_ts_ns` (§4 protocol).

### 2.2 Join to training rows (two pools, one schema)

**Pool A — round-outcome head (primary target).** Base rows: training_rounds
rows whose (series, map, round) eval event also appears in `mapentry-*.jsonl`
(same round_number, same series/map, |ts_ns − mapentry.ts_ns| ≤ 60s), carrying
q_round and a matched PMUS slug via mappings + dedup_extra. Flow features are
computed as-of the mapentry's `src_ts_ns` from that slug's quote history.
Round outcome label comes from the existing oracle (`round_winners_by_seq`,
never the positional list — cs2agent F6). Rows with no matched book get flow
features = NaN; they still train the v1 head. CORPUS-F1 filter applies
unchanged (it gates base rows, not flow joins).

**Pool B — drift head.** Base rows: postquote rows (one sample per
(slug, us_side, src_ts_ns) eval moment). Features: flow block computed
as-of `src_ts_ns` from strictly earlier same-book postquote rows, plus p_map
joined from the latest mapentry row ≤ src_ts_ns for (series_id, map_number,
dedup_extra) — exactly the study's join. Target: `oriented_move` at 60s and
120s (two horizons; 30s excluded from training as noise-dominated).

**Pool B is the only pool licensable today.** Pool A requires books at every
game-state eval, which the capture layer does not yet persist (instances
`books: {}`). v2 trains drift-first on Pool B; the round head is activated in
v2.1 once §3 capture accrues.

### 2.3 Anti-leakage protocol (binding, mirrors CORPUS-F1 discipline)

The repo was burned once (13.0% of the corpus leaked round outcome into
features). The v2 build repeats that discipline for time:

1. **As-of join, strictly earlier.** Every flow feature value at eval time t
   comes from a row r with `r.src_ts_ns < t` (strictly less; never equal).
   Implemented as one deterministic function `flow_features_at(slug, us_side,
   t)` consumed identically by the offline builder and the live scorer.
2. **No future rows on disk.** The builder must sort quote history by
   (src_ts_ns, file-append order) and treat files as append-only; rows written
   after t are invisible even if physically present. Enforced by construction:
   the join iterates the history with a cursor keyed on src_ts_ns.
3. **Same-book isolation.** Momentum/staleness only from the same
   (slug, us_side) book. No cross-slug or cross-side contamination (a LONG
   book's mid must not inform its SHORT counterpart's features).
4. **Label separation.** Drift-head targets (oriented_move) are never used as
   features anywhere. Flow features for round-head rows are computed at the
   mapentry eval moment, which by construction precedes the round's
   resolution — but the builder additionally asserts the joined round is still
   **unresolved at t** (same guard the instruments use), and drops rows where
   the assertion cannot be evaluated.
5. **CORPUS-F1 inherited.** Pool A rows pass `rounds_played + 1 ==
   round_number` against both feature fields and top-level round_number, and
   the scoreboard/segment coherence guard, exactly as `load_dataset` does.
6. **Deterministic replay test.** For a random 5% sample of built rows, an
   independent re-derivation from the raw capture files must reproduce every
   feature value bit-exact; a CI test freezes one golden row set so schema or
   join drift fails loudly.
7. **No pmus_bbo backfill.** If a watch file is replayed/re-ingested, imb rows
   are keyed to their original capture ts; a re-ingest never makes historical
   rows retroactively "have" depth.

## 3. Capture-side prerequisite (licensed action from the flow study, restated)

v2 is signal-starved on depth. The flow study's action item stands: the
mapentry recorder already fetches the BBO at eval time — **persist the
pmus_bbo snapshot (bidDepth/askDepth/bestBid/bestAsk) into the mapentry row**
(or a sibling stream). Until then `imb` is NaN almost everywhere and v2
runs on the 7-feature flow block. This is a capture change (registry row +
user sign-off), out of scope for this doc's implementation.

## 4. Training protocol

### 4.1 Splits
- **By series, never by row** (v1 rule): rows of one series never straddle a
  split; pool-B splits key on `series_id` (postquote rows carry it).
- **Temporal holdout, re-pinned for v2.** MODEL.md is explicit: re-pinning the
  holdout = new version, own directory, fresh baseline. v2 gets its own
  `data/models/roundwin-v2/holdout_series.json`: the last **20% of series by
  earliest capture** among all series with ≥1 flow-joined sample, frozen
  before any v2 hyperparameter is touched. v1's pinned list is untouched; v1
  remains the served model and its benchmark stays comparable only to itself.
- Within non-holdout series: seeded (42) random 15% = validation (early
  stopping + calibration), remainder = train.
- **Day stratification check:** report holdout day distribution; if the
  holdout spans <2 calendar days, flag it — flow momentum's sign was only
  demonstrated per-day.

### 4.2 Model & targets — two heads, one gradient
Single LightGBM (v1 hyperparameters as starting point: num_leaves 31, lr
0.05, early stopping ≤2000 trees, patience 50) with a **multi-label binary
objective**:
- Head R: round outcome (CT-framed), Pool A.
- Head D60 / D120: sign of oriented_move at 60s/120s, Pool B (three binary
  heads: R, D60, D120).
Rationale: the round outcome remains the contract q serves; the drift heads
inject the flow block's gradient. Since p_map has ~zero short-horizon signal,
the flow features get their training gradient almost entirely through the
drift heads — which is exactly the mechanism claim. If multi-label sharing
hurts head R (measured in ablation §4.4), fall back to **v2-alt: two separate
models** with identical feature spaces and merge at serve time
(q_final = round head; drift head = gating/confirmation input only).
Decision between shared and separate is made on validation, recorded in the
metrics artifact, and pre-registered.

### 4.3 Calibration
Per-head Platt scaling fitted on validation raw margins (v1 method), reported
as `(a, b)` per head in `calibration.json`. Holdout reports Brier + 10-bin ECE
per head, calibration fitted on validation only — holdout influences nothing.

### 4.4 Ablations (required, not optional)
Before any promotion claim:
1. v1-only features retrained on the v2 split → new baseline for the v2 world
   (v1's pinned-holdout numbers are not comparable to v2's new pin).
2. v2 without flow block (= 1, same model family) — quantifies flow lift.
3. v2 without drift heads — checks whether drift heads help or hurt head R.
4. v2 with `imb` forced to NaN always — isolates depth contribution.

### 4.5 Acceptance bar for v2 replacing v1 (fail-closed)
ALL must hold on v2's pinned holdout:
1. **Head R calibrated Brier strictly better than ablation 1** (same split,
   same filter) — this is the MODEL.md promotion rule restated for the new
   pin; ties go to keep-old.
2. **Drift head D120: AUC ≥ 0.56 on holdout** (study measured 0.578–0.579
   pooled; 0.56 is ~0.02 below pooled, still far above p_map's 0.42) **AND
   per-day oriented-drift lift positive on every holdout day** (mom120-style
   tercile test inside the holdout). One negative day = fail.
3. **Head R ECE (10-bin) ≤ 0.04** — calibration must not regress as flow
   features enter.
4. Anti-leakage replay test (§2.3.6) green, with zero bit-mismatch.
5. Registry pre-registration row H-ROUNDWINV2 exists with these bars frozen
   BEFORE training runs (iron rail 2).

## 5. Risk analysis

**Overfitting vectors, ranked:**
1. **Tiny drift pool, day effects.** ~3.3k flow-joined samples across 2 days;
   momentum's t-stat is 1.7 on one day and 3.6 on the other — the signal may
   be regime-dependent (adverse-day flow is exactly where we were losing).
   Mitigation: day-stratified holdout check (§4.5.2), no hyperparameter search
   on holdout, num_leaves capped at v1's 31.
2. **Depth sparsity.** 333 pmus_bbo records, one day, pre-market. If imb
   trains on that, it will memorize 08-27 pre-market microstructure.
   Mitigation: NaN-encoding + §3 capture prerequisite; `imb` is explicitly
   not trusted until persistence coverage ≥50%.
3. **Spread as a leaky state.** Wide spread correlates with drift (+1.22pt
   tercile) but is market *state*, confounded with unquoted/illiquid markets;
   it can silently re-introduce the "buying discounted model error" failure
   mode. Mitigation: carried but flagged; ablation 4 variant without it
   reported; entry policy unchanged regardless.
4. **Two-head interference.** Drift-head gradient could degrade head R.
   Mitigation: ablation 3; fallback to separate models.
5. **Join-time leakage.** As-of join bugs are the classic failure (the repo
   has one leak post-mortem). Mitigation: §2.3 protocol + golden-row CI test.

**n accounting (actual):**
- Round corpus: 22,273 rows → 19,326 post-filter, 159 series; holdout 4,276.
- Flow pool: ≈3,315 joined samples (08-27+28, per flow study; files show 855 /
  2,663 / 747 postquote rows per day, ≤960 per horizon per day pair).
- Depth pool: 333 records (08-27 only).
- Mapentry rows with matched books: 4,076 across 3 days.

**Minimum data before v2 training is licensed:**
- ≥ **5 capture days** with ≥ **10,000 flow-joined postquote samples** and the
  holdout spanning ≥ **2 full days** that are not both same-direction markets
  (the 08-27 up-day / 08-28 down-day pair is currently the only such pair).
- Round head (Pool A) activates only after §3 BBO persistence has accrued
  **≥ 2,000 mapentry rows with persisted books** spanning ≥ 2 days.
- Until then: keep serving roundwin-v1 with the drift-confirmation gate; v2
  work is limited to building the join builder + golden-row tests.

## 6. Out of scope (explicit non-goals)
- No engine changes, no registry edits, no re-pinning of v1's holdout.
- imb is not an entry gate (flow study conclusion 3).
- Flow momentum is a model input, not a license to widen bands (priced-in
  verdict stands).
