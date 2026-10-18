# gridbot deep-analysis — DRAFT for the human and the registry process

**Run type:** slow-loop deep analysis. **Posture:** dry_run, no orders, no registry edits, no credentials.
**Nothing in this document is a verdict.** Every candidate below is labelled DRAFT and requires its own
pre-registration before any evidence is read against it.

**Evidence mounted:** `/mnt/session/uploads/workspace/evidence/` (the `/workspace/evidence/` path was not
populated in this sandbox; the upload mirror was used).
`training_rounds.jsonl` (22,273 rows) · `registry.jsonl` (46 rows) · `verdict.json` · `model_metrics.json` · `capture_qa.txt`

---

## 0. Two corrections to the kickoff framing, before anything else

**(0a) The kickoff says `holdout_declared` is false for H-MAPENTRY1. The evidence file says it is `true`.**
`verdict.json → mapentry.holdout` reads `holdout_declared: true`, `n: 1084`, `bar: 150`, `series: 40`,
`verdict_reachable: true`. The instruction to "treat `FALSIFIED_ON_CAPTURED_DATA` as a measurement over
captured rows and say so explicitly" is still **correct**, but for a different reason than the one given,
and the difference matters:

- The `mapentry` block's clause numbers (`clause_i.n = 22895`) are the **in-sample captured** population,
  not the holdout. The holdout-restricted render is carried in `mapentry_holdout`, and that key is **`null`**
  in this snapshot. So this file contains **no holdout reading at all** — it reports in-sample clauses next to a
  `holdout_declared: true` flag, which is exactly the reporting defect the registry already caught once
  (`H-MAPENTRY1-HOLDOUT1`: *"the previous render path only COUNTED holdout instances while printing in-sample
  clauses; no verdict was ever declared from that output"*). **That defect appears to have regressed, or this
  snapshot was produced by the un-fixed path.** Flagging it as a reporting-layer bug, not a lane result.
- On the actual pre-registered holdout the lane was **NOT falsified** (`H-MAPENTRY1-HOLDOUT1`, 2026-09-04:
  Brier(P_map) 0.1213 vs book mid 0.1490; quintile ECE 0.0535 vs the 0.08 bar; clause (iii) unpowered at 39/60).
  So quoting `FALSIFIED_ON_CAPTURED_DATA` as the lane's state would be wrong in the *opposite* direction from
  the kickoff's framing.

**(0b) H-MAPENTRY1 is CLOSED, and this run adds no new evidence about it.**
`H-MAPENTRY1-CLOSE1` (2026-09-07) closed the entry rule as a live candidate on three grounds (no path to power,
no qualifying instrument, structural illiquidity of the map book) and states explicitly that this **is not a
falsification**. `H-MAPENTRY1-NOTE11` then voided the EXEC1 renders as numbers. I have **no new evidence** on
this lane: the mounted evidence contains no book tape, no trades tape, no mapentry instance rows — only the
already-superseded aggregate in `verdict.json`. I did not re-cut it and make no claim about it.

**Correct one-line status of the mapentry block:** *a stale in-sample measurement over 22,895 captured rows,
rendered by a path whose holdout output is missing; superseded by HOLDOUT1 (not falsified on held-out data)
and then by CLOSE1 (lane closed, not falsified).*

---

## 1. Data integrity: the binding filter reproduces exactly

`CORPUS-F1-LEAKFILTER` applied as written — drop rows failing `rounds_played + 1 == round_number` against
**both** `features.round_number` and the top-level `round_number`.

| quantity | registry / model_metrics | recomputed this run | match |
|---|---|---|---|
| total rows | 22,273 | 22,273 | ✓ |
| leak-dropped | 2,898 (13.0%) | 2,898 (13.01%) | ✓ |
| bin-3 leak share | 2,581 / 5,429 = 47.54% | 2,581 / 5,429 = 47.54% | ✓ |
| missing-key rows | 67 | 67 | ✓ |
| missing-key dropped | 49 | 49 | ✓ |
| kept rows | 19,326 | 19,326 | ✓ |

Per-bin drop rate: b0 1.78%, b1 0.76%, b2 3.11%, b3 47.54%. The filter is doing exactly what its entry claims.
Label mean is near-identical in kept (0.5267) and dropped (0.5338) rows, so the filter is not label-selecting
in aggregate — it is removing rows whose *features* encode the label.

Corpus shape after filtering: **19,326 rows / 158 series / 335 maps / 5,598 rounds** (159 series pre-filter;
one series is removed entirely). The file is byte-identical in size to the one the anchors were frozen against
(`model_metrics.dataset.total_rows = 22273`), so **no corpus drift is possible within this file** — every
anchor mismatch below is a *predicate* mismatch, not new data.

`capture_qa.txt`: grid OK / pm OK / shadow OK, 0 grid errors, 0 stale alarms, 1 pm_ws error, 575 GiB bucket.
Nothing here gates the analysis.

---

## 2. Headline corpus edges recomputed under the binding filter

All intervals below are **Wilson (row-level)** plus a **series-clustered bootstrap** (10,000 resamples, seed 17),
reported side by side because the two differ materially for some lanes (§5).

### 2.1 H-MANADV1 — anchors remain UNAUDITED, bar not threatened, one bucket below bar

Literal predicate (map-score-trailing side holds ≥2-man alive advantage, clock ≥20s):

| cut | n | rate | Wilson | series-boot | series |
|---|---|---|---|---|---|
| rows, no clock gate | 1,609 | 0.9459 | [0.9338, 0.9560] | [0.9113, 0.9691] | 156 |
| **rows, clock ≥20s** | **1,311** | **0.9405** | [0.9264, 0.9521] | [0.9077, 0.9640] | 156 |
| rounds (first bin), clock ≥20s | 1,009 | 0.9366 | [0.9198, 0.9500] | [0.9071, 0.9590] | 156 |

This **reproduces `H-MANADV1-CLOCKSTRAT` exactly** — that entry recorded "recomputed literal predicate:
0.941/N=1,311 rows, 1,009 rounds". Independent confirmation that my predicate matches the prior session's.
The frozen anchors (0.931/N=606 and 0.950/N=1,680) still do **not** reproduce (I get 0.9405/N=1,311 and
0.9623/N=4,165 rows / 0.9551/N=2,941 rounds). **No change to the registry's existing UNAUDITED annotation;
the 0.85 bar remains un-threatened at every variant tested (all land 0.92–0.96).**

Clock stratification (CLOCKSTRAT's frozen reading of bar (i)):

| bucket | n | rate | Wilson | series-boot |
|---|---|---|---|---|
| [20,45) | 596 | 0.9664 | [0.9487, 0.9782] | [0.9404, 0.9849] |
| [45,75) | 413 | 0.9395 | [0.9122, 0.9587] | [0.9086, 0.9654] |
| [75,90) | 213 | 0.9249 | [0.8815, 0.9532] | [0.8804, 0.9623] |
| **[90,∞)** | **89** | **0.8090** | [0.7152, 0.8772] | [0.6907, 0.9091] |

Monotone decline confirmed. The ≥90s bucket at **0.8090 (n=89 ≥ 30)** sits **below the 0.85 bar** — this exactly
matches the 0.809 already recorded in `H-ROUNDLANES-RESTRUCTURE1`. **Per CLOCKSTRAT this makes any pooled
clause-(i) PASS PROVISIONAL, not confirmed.** That is the operative constraint on this lane and it has not moved.

Dose–response (advantaged side, rows): |ma|=0 → 0.5426 (n=10,637); 1 → 0.7715 (4,573); 2 → 0.9452 (2,557);
3 → 0.9873 (1,180); 4 → 0.9952 (413); 5 → 1.0000 (15). Clean and monotone.

### 2.2 H-ECONBUY1 — anchors do NOT reproduce (new finding); bucket structure does

The corpus stores `equip_value_diff` **normalised**, not in dollars (range −1.380 … +1.545). Calibrating against
the live instrument's own bucket edges, the implied divisor is ≈20,000 (i.e. `≥10k gap ⟺ gap_norm ≥ 0.50`).
Under that reading the corpus reproduces the **live shadow cells** closely — a genuine two-source agreement:

| bucket | corpus (round-level) | live shadow (verdict.json) | Δ |
|---|---|---|---|
| <4k | 0.600 (n=15) | 0.5882 (n=17) | +1.2 pt |
| 4–10k | 0.7189 (n=217) | 0.7106 (n=585) | +0.8 pt |
| ≥10k | 0.8371 (n=1,651) | 0.8421 (n=4,315) | −0.5 pt |
| pooled | 0.8211 (n=1,889) | 0.8256 (n=4,917) | −0.5 pt |
| share in ≥10k | 87.3% | 87.8% | −0.5 pt |

**But the frozen corpus anchors themselves do not reproduce.** Frozen: 0.832/N=1,393 pooled, 0.849/N=1,234 at
≥10k, 0.710/N=155 force-buy. I swept **108 predicate variants** (population ∈ {bin-0, first-bin, all rows,
bins 0–1} × round filters {excl. pistols, excl. pistols+r2/r14, none} × regulation cut {≤24, ≤30, all} ×
gap floor {none, 0.0, 0.2}). The closest N landed at 1,475 (rate 0.8114); **no variant reached both N≈1,393 and
rate ≈0.832 simultaneously**, and the frozen 1,234/155 split (88.6%/11.4%) is only reachable at a gap cut of
0.475, not at any round-number threshold.

**→ DRAFT registry note recommended:** H-ECONBUY1's corpus anchors warrant the same **UNAUDITED** provenance
annotation that CLOCKSTRAT applied to H-MANADV1's. As with MANADV, **the bar is not threatened** — every
variant tested lands 0.81–0.88, above the 0.79 pooled / 0.81 ≥10k bars. This is a provenance gap, not an
edge failure. I am not proposing a bar change.

Scoreline invariance holds (buyer behind 0.8177 n=565 / tied 0.8023 n=177 / ahead 0.8256 n=1,147) — consistent
with the frozen 0.825/0.820/0.837 in direction and level.

### 2.3 H-PISTOLCARRY1 — anchors audit CLEAN

This lane's anchors **do** reproduce, which is worth stating precisely because the other two do not:

| quantity | frozen | recomputed | match |
|---|---|---|---|
| loser force/full-buys | 0.702, N=47 | **0.7021, N=47** | exact |
| loser stays below full buy | 0.874, N=388 | 0.8729, N=409 | rate ✓ |
| pooled conversion | 0.859, 402/468 | 0.8553, 390/456 | ✓ (12 pairs short) |
| loser full-buy share | 10.8% | 10.3% | ✓ |
| winner full-buy share | 92.2% | 91.2% | ✓ |
| generic carryover control | 0.611, N=4,880 | **0.6115, N=4,772** | ✓ |
| conditional gap (bar (ii): <5 pts falsifies) | 17 pts | **17.1 pts** | ✓ |

Split by half: pistol r1 → 0.8936 (n=188), pistol r13 → 0.8284 (n=268). The **second-half pistol converts
6.5 points worse than the first-half pistol**, and it supplies 59% of the pairs. Not a registered qualifier;
noted for §6.

---

## 3. The pinned benchmark: reproduced, then audited

### 3.1 Reproduction

The pinned split is recoverable. Ordering series by first `captured_at`, the last 32 series contain **exactly
4,276 rows** and the first 126 contain **exactly 15,050** = 12,684 + 2,366 — both matching `model_metrics`
to the row. (The train/valid boundary inside the first 126 is not temporal; a seeded random 19-series draw
reproduces the totals but not the membership, so my surrogate differs slightly there.)

Retraining with the pinned params (`num_leaves 31`, `lr 0.05`, `seed 42`, 96 iterations):

| metric | pinned | surrogate refit | Δ |
|---|---|---|---|
| holdout Brier (raw) | 0.149888 | 0.150437 | +0.00055 |
| holdout AUC | 0.864024 | 0.863309 | −0.0007 |
| holdout ECE-10 (raw) | 0.030746 | 0.031538 | +0.0008 |
| Platt-calibrated Brier | 0.150120 | 0.150921 | +0.0008 |

A faithful surrogate (0.4% off on Brier), so segment-level reads below are trustworthy in *shape*.

### 3.2 Finding: the pinned Platt step makes the benchmark worse on its own holdout

Read straight off `model_metrics.json`, no recomputation needed:

- raw: Brier 0.1498879, log-loss 0.4488326
- calibrated: Brier 0.1501199 (**+0.000232 worse**), log-loss 0.4507651 (**+0.0019 worse**)
- only ECE improves: 0.0307 → 0.0296 (−0.0012)

My surrogate reproduces the sign on both. The mechanism is the intercept: `sigmoid(0.96828·margin − 0.12790)`
shifts every probability **down**. Measured bias (predicted mean − observed mean):

| | raw | Platt-calibrated |
|---|---|---|
| OOF over full corpus | **−0.0002** | **−0.0211** |
| pinned holdout | −0.0034 | −0.0247 |

**The raw model is essentially unbiased; the calibrator injects a systematic ~2.1–2.5 point downward bias.**

### 3.3 Why that is decision-relevant: it creates a phantom ct/t asymmetry in H-MODELENTRY1

H-MODELENTRY1-CELLS trades the **calibrated** q, with the t-side entered via **1−q symmetry**. A uniform
downward bias on q is therefore *conservative* for ct entries and *anti-conservative* for t entries.
Measured on out-of-fold predictions (5-fold GroupKFold by series, 19,326 rows):

| threshold | ct: observed − q | t: observed − q | asymmetry |
|---|---|---|---|
| 0.65 | +0.0195 (n=6,307) | −0.0132 (n=6,130) | **+0.0327** |
| 0.75 | +0.0188 (n=4,935) | −0.0135 (n=4,858) | **+0.0324** |
| 0.85 | +0.0147 (n=3,619) | +0.0005 (n=3,576) | +0.0142 |

**Verification (second method): repeat on the raw, uncalibrated score.** The asymmetry collapses to
+0.0027 / +0.0109 / +0.0001. **The ct/t gap is an artifact of the calibrator's intercept, not a structural
CT/T effect.** Replicating the exact cell definition (first up-crossing per series·map·round·side·threshold)
reproduces the same sign: ct +0.001/+0.004/+0.003 vs t −0.028/−0.030/−0.014.

Practical read: t-side H-MODELENTRY1 instances are entered at a true win probability roughly **1.3–3 points
below** their nominal threshold, while ct-side instances are entered above it. The cells are not
exchangeable, and `verdict.json` pools them.

### 3.4 Finding: the pinned holdout is the easiest block in the corpus

Out-of-fold (5-fold grouped by series) over the whole corpus: **Brier 0.1575, AUC 0.8485** — materially
*worse* than the pinned 0.1499 / 0.8640, and note CV has a look-ahead advantage the pinned temporal holdout
does not. Localising by temporal block of 32 series:

| block (series, first-captured order) | rows | OOF Brier | OOF AUC |
|---|---|---|---|
| 0–29 | 3,585 | 0.1693 | 0.8239 |
| 32–63 | 3,867 | 0.1578 | 0.8473 |
| 64–95 | 3,921 | 0.1562 | 0.8524 |
| 96–127 | 3,738 | 0.1559 | 0.8518 |
| **126–157 (the pinned holdout)** | **4,276** | **0.1484** | **0.8664** |
| rest of corpus (0–125) | 15,050 | 0.1601 | 0.8435 |

Only **2.9%** of 20,000 random 32-series blocks achieve OOF Brier ≤ 0.1484. **Verification:** reweighting each
block to the corpus-wide (bin × |man_advantage|) mix leaves the trend intact (0.1683 → 0.1584 → 0.1569 →
0.1535 → 0.1501), so it is **not** a composition artifact.

Two readings, and the evidence here cannot separate them: (a) capture quality genuinely improved over the
corpus timeline, in which case the benchmark generalises forward and is fine; (b) the last block is
favourable, in which case the pinned 0.1499 is ~0.008 Brier (≈5%) optimistic. Either way, **live-vs-benchmark
drift should be judged against the block-4 number, not against the corpus average** — a live Brier of 0.157
would look like drift against 0.1499 while being exactly corpus-typical.

### 3.5 Calibration by score / round segment

Reliability on the pinned holdout (Platt-calibrated, 10 equal-width bins) — `gap = predicted − observed`:

| bin | n | pred | obs | gap |
|---|---|---|---|---|
| 0.0–0.1 | 628 | 0.0472 | 0.0271 | **+0.0201** |
| 0.1–0.2 | 330 | 0.1446 | 0.1848 | −0.0402 |
| 0.2–0.3 | 231 | 0.2459 | 0.3506 | **−0.1047** |
| 0.3–0.4 | 246 | 0.3530 | 0.3577 | −0.0047 |
| 0.4–0.5 | 435 | 0.4541 | 0.4644 | −0.0103 |
| 0.5–0.6 | 762 | 0.5472 | 0.5486 | −0.0014 |
| 0.6–0.7 | 372 | 0.6451 | 0.6478 | −0.0027 |
| 0.7–0.8 | 339 | 0.7536 | 0.8201 | −0.0664 |
| 0.8–0.9 | 362 | 0.8501 | 0.9254 | **−0.0754** |
| 0.9–1.0 | 571 | 0.9438 | 0.9842 | −0.0404 |

Under-prediction concentrated in the 0.7–0.9 band — **exactly the H-MODELENTRY1 entry region** — but ~2.5 pts
of that is the global Platt bias (§3.2), leaving ~5 pts of genuine residual shape at 0.8–0.9.

By score and round segment (worst ECE first):

| segment | n | series | bias | Brier | ECE-10 |
|---|---|---|---|---|---|
| \|score_lead\| ≥ 7 | 382 | 20 | **−0.0963** | 0.1632 | **0.1044** |
| overtime r25+ | 91 | 5 | −0.0360 | 0.1799 | 0.1039 |
| r14 | 199 | 31 | −0.0848 | 0.1203 | 0.1009 |
| r2 (post-pistol) | 151 | 30 | +0.0103 | 0.0648 | 0.0894 |
| pistol r13 | 216 | 31 | −0.0265 | 0.1705 | 0.0820 |
| \|score_lead\| 5–6 | 635 | 29 | −0.0199 | 0.1489 | 0.0607 |
| r21–24 | 346 | 24 | −0.0486 | 0.1681 | 0.0585 |
| ALL | 4,276 | 32 | −0.0247 | 0.1509 | 0.0306 |

The blowout-scoreline segment (|lead| ≥ 7) carries **3.4× the pooled ECE**, on only 20 series. Rounds 2 and 14
— the H-PISTOLCARRY1 population — are the two highest-ECE non-extreme round segments.

By man advantage, oriented to the advantaged side: ma ≥ +2 predicts 0.9365 realises 0.9780 (n=500);
ma ≤ −2 predicts 0.9590 (T side) realises 0.9885 (n=436). **Both signs under-predict the advantaged side by
3–4 points** — opposite in CT-space, consistent when oriented, i.e. not the global bias. This is the
H-MANADV1 mechanism showing up inside the served model.

### 3.6 Feature hygiene: 8 of 20 features carry no information

| issue | features | evidence |
|---|---|---|
| zero variance on the leak-filtered corpus | `bomb_planted` (0), `bomb_time_remaining` (0), `map_rating_diff` (0), `rating_diff` (0), `prior_round_p` (0.5) | 5 features, 0 unique values ≠ const |
| exact duplicate | `alive_ratio` ≡ `man_advantage / 5` in **100.00%** of rows (ρ = 1.000000) | |
| near duplicate | `awp_diff` vs `awp_alive_diff`, ρ = 0.999803 | |
| deterministic function of another | `rounds_played` ≡ `round_number − 1` in 100% of rows — **forced by the binding leak filter itself** | |

Gain importance confirms: `man_advantage`, `rounds_played`, `bomb_planted`, `bomb_time_remaining`,
`map_rating_diff`, `prior_round_p`, `rating_diff` all score **0.00%**; `awp_diff` 0.02%.
`equip_value_diff` alone carries **56.3%** of gain, then `alive_ratio` 13.4%, `hp_alive_diff` 9.8%,
`econ_ratio` 6.9%.

**Note the trap:** `man_advantage` shows 0.00% gain *not* because it is uninformative — it is the single
strongest predictor in the corpus (§2.1) — but because `alive_ratio` is the same column divided by 5 and won
the split. Any future feature-importance-based pruning would delete the man-advantage signal. The
`bomb_planted`/`bomb_time_remaining` deadness is a **capture gap**: bomb state is never recorded, so no lane
can currently express a post-plant hypothesis.

---

## 4. Anomalies found this run

### 4.1 `time_remaining` is a mixed-semantics field (20.7% of the corpus is ambiguous)

The same numeric band 0–19s denotes two opposite states, distinguishable only by in-round position:

| bin | clock band | n | combat occurred (\|ma\|>0) | HP damage | reading |
|---|---|---|---|---|---|
| 0 | 0–19s | 2,151 | **3.2%** | 2.8% | pre-round (freeze countdown) |
| 0 | 20–94s | 550 | 38.4% | 55.8% | mid-round |
| 0 | 95–115s | 2,943 | 8.6% | 19.0% | pre-round (round clock) |
| 3 | 0–19s | 1,110 | **83.8%** | 91.9% | late round |
| 3 | 20–94s | 1,717 | 86.7% | 94.1% | late round |

bin-0 rows at 0–19s are 96.8% undamaged and 96.5% 5v5 — nothing has happened. bin-3 rows at the identical
clock value are 83.8% post-combat. The field is reading the **freeze countdown** (CS2 freeze time is 15–20s)
for pre-round snapshots and the **round clock** (115s) afterwards. Corroborating: 16.7% of consecutive
within-round observations show the clock *increasing*, median +92s, concentrated in bin0→bin1 (33.0%) — the
signature of a freeze countdown handing off to a round clock.

**Consequence, and it is mostly good news:** H-MANADV1's predicate requires clock ≥20s and CLOCKSTRAT's
buckets start at 20s, so **both are accidentally protected** from the ambiguous band. Nothing else is. Any
future lane gating on `clock < 20s`, and the served model's `time_remaining` feature (2.8% gain), read a field
whose meaning flips. This is orthogonal to CORPUS-F1 — these rows pass the binding filter cleanly.

### 4.2 ~9.7% of bin-0 "freeze-end" snapshots are actually mid-round

The 550 bin-0 rows at clock 20–94s show combat in 38.4% and HP damage in 55.8%. These are not freeze-end
states, yet they sit in the population H-ECONBUY1 reads its loadouts from.

**I tested whether this threatens the bar, and it does not.** Restricting to state-pure freeze-end rows
(5v5 **and** HP-flat — a clock-independent purity test, 84.5% of the population):

| population | pooled (bar <0.79) | ≥10k (bar <0.81) |
|---|---|---|
| all bin-0 | 0.8212 (n=1,890) | 0.8370 (n=1,650) |
| **state-pure** | **0.8284** (n=1,597) Wilson [0.8092, 0.8461] | **0.8381** (n=1,445) Wilson [0.8182, 0.8562] |
| impure | 0.7816 (n=293) | 0.8293 (n=205) |

Purifying the population *improves* both readings and both Wilson lower bounds clear their bars. **Recorded as
a population-definition defect worth a registry note, explicitly NOT as evidence against the lane.**

*Self-correction, disclosed:* my first cut of this used a naive clock ≥95s split and produced pooled 0.7979 /
≥10k 0.8130 — i.e. apparently sitting on both bars. That split was **wrong**: it classified the 2,151
state-pure freeze-countdown rows as "suspect". The state-based test above supersedes it. I am recording the
error because the wrong version would have read as a near-falsification.

### 4.3 The served model is miscalibrated by in-round observation index — and by nothing else

I scanned **899 two-way segments** (n≥300, series≥30) of OOF residuals with series-clustered bootstrap CIs.
**72 (8.0%) cleared 95%, against ~5% expected by chance** — that scan is essentially multiplicity noise and
I am not drawing candidates from it. Doing it properly, one-way, with Benjamini–Hochberg across 43 levels
spanning clock, man advantage, score lead, round, equip gap, AWP, momentum and match point:

**Zero of 43 levels survive FDR at 10%.** The served model is well calibrated conditional on every game-state
feature I tested. That is a strong positive result for the model and it should be said plainly.

The **one** exception is a factor that is *not* a model feature — the in-round capture index:

| bin | n | series | pred | obs | residual | series-boot 95% | p |
|---|---|---|---|---|---|---|---|
| 0 | 5,595 | 158 | 0.5440 | 0.5296 | **−0.0144** | [−0.0266, −0.0021] | 0.021 |
| 1 | 5,486 | 158 | 0.5325 | 0.5279 | −0.0046 | [−0.0166, +0.0075] | 0.457 |
| 2 | 5,397 | 158 | 0.5062 | 0.5277 | **+0.0215** | [+0.0124, +0.0305] | **0.0001** |
| 3 | 2,848 | 157 | 0.5192 | 0.5169 | −0.0024 | [−0.0120, +0.0069] | 0.612 |

**Verification by disjoint split:** the bin-2 effect replicates at nearly identical magnitude in the two
independent subpopulations defined by whether the round contains a clock reset — **+0.0221** (n=2,255, p=0.0015)
in reset rounds and **+0.0211** (n=3,142, p=0.0020) in clean rounds. It is not the clock artifact of §4.1.

### 4.4 `verdict.json` cell intervals are row-level Wilson, not series-clustered

I reproduced all eight reported `win_ci` values to within 0.0003 using a plain row-level Wilson interval —
every one matches. Yet the same file's `mapentry.notes` states *"All intervals resample SERIES, not rows."*
**The two blocks use different interval conventions and only one says so.** Rows within a series are
correlated, so the cell intervals are anti-conservative.

Estimating the design effect from corpus intra-cluster correlation (`deff = 1 + (m̄−1)·ICC`):

| lane | corpus ICC | live n | series | m̄ | deff | rate | reported Wilson lo | **clustered lo** | bar | margin |
|---|---|---|---|---|---|---|---|---|---|---|
| H-MANADV1 | 0.284 | 1,944 | 379 | 5.13 | 2.17 | 0.9109 | 0.8974 | **0.8910** | 0.85 | +0.0410 |
| H-ECONBUY1 ≥10k | 0.002 | 4,315 | 403 | 10.71 | 1.02 | 0.8421 | 0.8309 | **0.8308** | 0.81 | +0.0208 |
| H-PISTOLCARRY1 | 0.018 | 1,512 | 405 | 3.73 | 1.05 | 0.8220 | 0.8019 | **0.8014** | 0.80 | **+0.0014** |

**H-PISTOLCARRY1's clustered lower bound sits 0.14 points above its bar.** MANADV has the largest design
effect (2.17×) but the most headroom, so the correction changes no conclusion there.

---

## 5. Per-hypothesis read: distance to each frozen bar, and what evidence is still missing

Structure per `H-ROUNDLANES-RESTRUCTURE1`: clause (i) = resolution rate (unchanged); old (ii)+(iii) replaced by
a single **fill-PnL clause** that renders **VENUE-GATED / DEFERRED-OPEN** until a round-book venue exists.
`verdict.json → round_lane_status` confirms all three round lanes carry that status, `quoted = 0` everywhere.

### H-MANADV1 — clause (i) passing pooled, PROVISIONAL by its own amendment
- **Captured:** 1,944 instances / 379 series, rate 0.9109, reported CI [0.8974, 0.9227], clustered lo **0.8910**.
  Bar 0.85 → margin **+6.1 pts**, robust to the design-effect correction. `bar_n` 80, captured n is **24.3×** that.
- **Corpus vs live drift:** corpus 0.9366–0.9459 vs live 0.9109 — live runs **2.6–3.5 pts lower**. Direction is
  toward the bar but the gap is far from closing it.
- **Blocking:** CLOCKSTRAT's stratified reading. The corpus ≥90s bucket is **0.8090 (n=89 ≥ 30), below the 0.85 bar**.
- **Missing:** (1) the per-instance **clock_seconds** distribution of the 1,944 captured instances — CLOCKSTRAT
  froze clock stratification as part of bar (i) and `verdict.json` publishes **no clock breakdown at all**, so
  the pooled 0.9109 cannot currently be read as CLOCKSTRAT requires. This is the single cheapest unblock: the
  field is already recorded. (2) A declared held-out window (none exists for this lane; ≥80 instances is
  long satisfied on *captured* data). (3) The fill-PnL clause is venue-gated — needs a round-book venue.
- **Verdict-reachability:** clause (i) alone cannot pass the lane. **Not arming-relevant.**

### H-ECONBUY1 — clause (i) passing pooled and ≥10k; <4k cell is the watch item
- **Captured:** 4,917 instances / ~403 series, pooled **0.8256** (bar 0.79, margin **+3.6 pts**);
  ≥10k **0.8421** (bar 0.81, margin **+3.2 pts**, clustered lo 0.8308). `bar_n` 120 pooled / 60 at ≥10k;
  captured n is **41×** / **72×**.
- **Corpus vs live drift:** agreement within **0.5–1.2 pts in every bucket** (§2.2). This is the best-corroborated
  lane in the set.
- **Scope-freeze trigger** ("held-out force-buy subset buyer win <0.66 → scope freezes to ≥10k"): live <10k
  combined **0.7071** (n=602) — clear. But the **<4k cell reads 0.5882 (n=17, CI [0.3601, 0.7839])** live and
  0.600 (n=15) in the corpus. Both are below 0.66 in point estimate, both hopelessly underpowered. Two
  independent sources agreeing at ~0.59–0.60 is worth taking seriously even at n=32 combined.
- **Missing:** (1) a declared **held-out window** — none exists; all 4,917 instances are captured, in-sample.
  (2) **~100+ more <4k instances** to resolve the scope-freeze trigger; at the current arrival rate
  (17 in 4,917 ≈ 0.35%) that is a starvation problem in its own right and should be named as one.
  (3) An UNAUDITED provenance annotation on the frozen anchors (§2.2) — DRAFT, registry's call.
  (4) Fill-PnL clause venue-gated.

### H-PISTOLCARRY1 — the lane that can still flip
- **Captured:** 1,512 instances / 405 series, rate **0.8220**, reported CI [0.8019, 0.8404],
  **clustered lo 0.8014** vs bar 0.80 → margin **+0.14 pts**. `bar_n` 100, captured n is 15.1×.
- **Movement since RESTRUCTURE1:** that entry recorded 0.8031 with the CI straddling the bar and named this
  "the one that could still flip". It has improved to 0.8220, but **under series clustering the interval still
  effectively touches the bar.** The improvement is real but does not yet clear it.
- **Corpus vs live drift:** corpus 0.8553 (n=456) vs live 0.8220 — live runs **3.3 pts lower**, the largest
  corpus→live decay of the three lanes, and it is decaying *toward* the bar.
- **Anchor audit:** clean (§2.3) — the only lane of the three whose frozen anchors reproduce. Bar (ii)
  (conditional gap <5 pts falsifies) reads **17.1 pts** in the corpus, comfortable.
- **Unregistered structure found:** first-half pistol converts **0.8936** (n=188), second-half pistol
  **0.8284** (n=268) — a 6.5-point split, with r13 supplying most of the volume. If the live instance mix is
  r13-weighted, the pooled rate is mix-determined in exactly the way CLOCKSTRAT warned about for MANADV.
- **Missing:** (1) a declared **held-out window**. (2) The **r1 vs r13 breakdown** of the 1,512 captured
  instances — not published, and it decides whether the +0.14 pt margin is a mix artifact.
  (3) The **loser-buy conditional split** live (bar (ii)) — `verdict.json` publishes one pooled cell, no
  conditional. (4) Fill-PnL clause venue-gated.

### H-MODELENTRY1 (+CELLS) — no bar to be near
- `bar_n = 0` for all three cells; the confirmation bar and kill clause live in the 2026-08-24 cs2agent
  registration, which is **not in the mounted registry file**. I cannot compute distance to a bar I cannot read.
- **Captured:** thr0.65 n=13,764 / 0.7861; thr0.75 n=13,466 / 0.8495; thr0.85 n=12,024 / 0.9092 (410–411 series).
- **Issue raised this run:** the cells pool ct and t, and §3.3 shows the calibrator makes them non-exchangeable
  (t-side entered ~1.3–3 pts *below* nominal, ct-side above). The registration's own prior is that taker entry
  loses and only maker fills can clear — that prior is untouched by anything here.
- **Missing:** (1) the frozen bar text itself. (2) The recorded **`q_prev` / `q` per instance** — frozen as
  recorded fields in H-MODELENTRY1-CELLS but absent from `verdict.json`, so the realised-vs-q gap at crossing
  cannot be computed and my corpus replication (§3.3) is only a 4-bin proxy of a continuous-poll engine.
  (3) A **ct/t split** of each cell. (4) `quoted = 0`, `paper_entries = 0`, `maker_quotes = 0` — no execution
  evidence of any kind exists yet.

### H-MAPENTRY1 — closed; no new evidence
Covered in §0. **No new evidence this run.** The only defensible action is the reporting-layer fix in §0a.

---

## 6. Ranked candidate ideas — all DRAFT

Each carries a mechanism claim and a falsification sketch. **None is registered. None may be tested against
held-out data before it is frozen.** Ordered by expected decision value per unit of new capture required.

### DRAFT-1 — Split the H-MODELENTRY1 cells by side (ct vs t) before any verdict is read
- **Rank:** 1. Highest value, zero new capture, addresses an open lane.
- **Mechanism:** the pinned Platt intercept (−0.12790) shifts q down ~2.1 pts uniformly; the t-side is entered
  via 1−q, which flips the sign of the bias. Verified two ways: the ct−t gap is +0.033/+0.032/+0.014 on the
  calibrated score and collapses to +0.003/+0.011/+0.000 on the raw score (§3.3).
- **Falsification sketch:** on ≥400 held-out instances per side per threshold, the claim FAILS if the
  (observed − recorded q) difference between ct and t cells has a series-clustered 95% CI containing zero at
  every threshold. It is confirmed only if the ct−t gap is ≥ +0.02 with a CI excluding zero at ≥2 of 3 thresholds.
- **Why it matters:** pooling ct and t is currently averaging a conservative cell with an anti-conservative one.
  This is a *reading* amendment of the CLOCKSTRAT type, not a new instrument.
- **Cost:** the fields are already frozen as recorded. This is a reporting change.

### DRAFT-2 — Drop the Platt layer from the traded quantity, or re-fit its intercept
- **Rank:** 2. Zero new capture; the evidence is already in `model_metrics.json`.
- **Mechanism:** the calibrator worsens Brier (+0.000232) and log-loss (+0.0019) on the model's own pinned
  holdout and improves only ECE (−0.0012); the raw score is unbiased (−0.0002 OOF) while the calibrated score
  carries −0.0211. The intercept is doing the damage, not the slope (0.96828 ≈ 1).
- **Falsification sketch:** FAILS if, on a pre-registered held-out window of ≥1,000 resolved instances, the raw
  score does **not** beat the calibrated score on **both** Brier and log-loss with a series-clustered CI
  excluding zero. Note the ECE trade-off must be pre-committed: if ECE is the quantity the lane cares about,
  this candidate should not be run at all.
- **Caveat, stated:** `H-MODELENTRY1-CELLS` pins the served artifact and says an **artifact swap requires a new
  registry amendment**. This DRAFT is a request for that amendment, nothing more.

### DRAFT-3 — Publish and stratify H-PISTOLCARRY1 by pistol half (r1 vs r13)
- **Rank:** 3. Cheap, and it targets the only lane whose bar is genuinely close.
- **Mechanism:** economy asymmetry differs across the halftime side swap; corpus shows r1 → 0.8936 (n=188) vs
  r13 → 0.8284 (n=268), a 6.5-pt split, with r13 the majority of volume. The pooled rate is therefore
  mix-determined — structurally the same hazard CLOCKSTRAT pre-empted for H-MANADV1.
- **Falsification sketch:** the *stratification* claim FAILS if, on ≥150 held-out pairs per half, the r1−r13 gap
  has a series-clustered 95% CI containing zero. If it does not fail, a pooled clause-(i) PASS while either
  half with n ≥ 50 sits below 0.80 should read PROVISIONAL — a direct transplant of the CLOCKSTRAT rule.
- **Urgency:** the clustered lower bound is +0.0014 from the bar (§4.4). A mix shift toward r13 could carry
  this lane across without any change in the underlying edge.

### DRAFT-4 — CORPUS-F2: a state-purity predicate for pre-round populations
- **Rank:** 4. A data-integrity note, in the CORPUS-F1 tradition; binding on lane *populations*, not a strategy.
- **Mechanism:** two distinct defects, both passing CORPUS-F1. (a) `time_remaining` is mixed-semantics — the
  0–19s band means freeze countdown pre-round and round clock late-round; 20.7% of the corpus sits in it,
  and bin-0 vs bin-3 rows at identical clock values differ 3.2% vs 83.8% on combat-occurred (§4.1). (b) 9.7%
  of bin-0 "freeze-end" snapshots are mid-round (38.4% combat, 55.8% HP damage) (§4.2).
- **Proposed predicate:** a freeze-end row must satisfy `man_advantage == 0 AND |hp_alive_diff| <= 0.03`
  (state-based, clock-independent — 84.5% of the current ECONBUY population).
- **Falsification sketch:** the filter is *unnecessary* if applying it to a pre-registered held-out ECONBUY
  window moves the pooled buyer resolution rate by less than 1 point with a CI containing zero. Measured
  in-sample it moves it **+0.7 pts** (0.8212 → 0.8284), i.e. **currently the honest expectation is that this
  filter does not change any verdict** — file it as a definition tightening, not an edge.
- **Explicitly:** this does **not** threaten H-ECONBUY1. My first, wrong cut of it appeared to (§4.2
  self-correction); the corrected reading improves the lane.

### DRAFT-5 — In-round capture-index (bin) miscalibration
- **Rank:** 5. Statistically the most robust residual found, but the weakest mechanism claim.
- **Mechanism claim, and it is thin:** the served model is calibrated conditional on every game-state feature
  tested (0 of 43 one-way levels survive BH-FDR at 10%, §4.3), yet carries +0.0215 residual at bin 2
  (p = 0.0001) and −0.0144 at bin 0 (p = 0.021). Bin index is *not* a model feature. Replicated at equal
  magnitude in two disjoint subpopulations (+0.0221 / +0.0211). The candidate mechanism is that bin encodes
  time-since-round-start information that `time_remaining` cannot carry because of the DRAFT-4(a) semantics
  collision — i.e. **DRAFT-5 may simply be a symptom of DRAFT-4 and should be tested after it, not before.**
- **Falsification sketch:** FAILS if, after adding a clean elapsed-time feature (or the DRAFT-4 purity filter),
  the bin-2 residual on a pre-registered held-out window has a series-clustered 95% CI containing zero.
- **Honest caveat:** a ~2-point calibration residual on a capture-pipeline index is not obviously tradeable on
  any venue gridbot can reach, and `H-MAPENTRY1-NOTE10`'s rule applies — a strategy whose validation requires
  data we do not capture is a capture requirement, not a strategy.

### Explicitly NOT proposed
- Anything from the 899-segment two-way scan (§4.3): 8.0% significant vs 5.0% expected by chance. Reporting
  the top of that list as candidates would be exactly the "with enough thresholds some cut always clears"
  failure `H-CROSSVENUE1-PRENOTE1` names (Quantopian L22).
- Any bar change on any lane. Two lanes have unaudited anchors (§2.1, §2.2); in both cases the bar survives
  every variant tested, and moving a bar after seeing a measurement is the tuning the freeze exists to prevent.
- Any re-cut, re-render or re-verdict of H-MAPENTRY1 (§0b).
- Any post-plant hypothesis: `bomb_planted` and `bomb_time_remaining` are identically zero (§3.6). That is a
  capture requirement first.

---

## 7. What I did not find

- **No calibration drift** of the served model against any game-state segment that survives multiplicity
  correction (§4.3). This is a positive result and the main reason no §6 candidate is ranked above a
  reporting fix.
- **No corpus drift.** The mounted `training_rounds.jsonl` is the same 22,273-row file the anchors and the
  benchmark were frozen on; within-file drift is not possible.
- **No new evidence on H-MAPENTRY1, H-CROSSVENUE1, or any execution/fill question.** No book tape, no trades
  tape, no ledger, no per-instance rows are mounted. Every fill-selection and PnL question raised in NOTE5,
  NOTE6, NOTE11 and NOTE13 is untouched by this run.
- **No execution evidence for any open lane.** `quoted`, `paper_entries`, `maker_quotes` and `maker_fills` are
  **0 across all eight cells**. Under RESTRUCTURE1 the fill-PnL clause of all three round lanes is
  VENUE-GATED / DEFERRED-OPEN, so **no round lane can reach a PASS on currently reachable evidence**, however
  favourable clause (i) becomes.

## 8. Recommended next actions, in order (the human's call)

1. **Fix the `verdict.json` render path** so the `mapentry` block either carries the holdout clauses or is
   labelled in-sample; `mapentry_holdout: null` next to `holdout_declared: true` is the HOLDOUT1 defect
   pattern (§0a).
2. **Publish clock_seconds strata for H-MANADV1 and the r1/r13 split for H-PISTOLCARRY1.** Both fields are
   already recorded; both are required to read a frozen bar as its own amendment specifies (§5).
3. **Label the cell intervals** in `verdict.json` as row-level Wilson, or switch them to series-clustered to
   match the mapentry convention. H-PISTOLCARRY1's margin is +0.14 pts and depends on which is used (§4.4).
4. Consider DRAFT-1 and DRAFT-3 for pre-registration — both are reading amendments on already-frozen recorded
   fields, both are cheap, and both bear on open lanes.
5. Treat the <4k ECONBUY cell as a **starvation** question, not just an underpowered one (§5).
