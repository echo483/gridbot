# gridbot — DRAFT deep analysis (slow loop)

**Run scope:** offline evidence only (`/workspace/evidence`, mounted at
`/mnt/session/uploads/workspace/evidence`). No orders, no registry edits, no credentials.
Everything below is **DRAFT** input to the human + registry process.

**Evidence inventory**

| file | size | state |
|---|---|---|
| `training_rounds.jsonl` | 22,273 rows / 159 series / 5,746 rounds | full corpus |
| `registry.jsonl` | 6 entries | 1 lineage, 1 binding filter, 4 hypothesis/cell entries |
| `verdict.json` | `no instances recorded yet` | **empty — zero instances** |
| `model_metrics.json` | roundwin-v1 pinned holdout | Brier 0.1499 / ECE 0.0296 / AUC 0.864 |
| `capture_qa.txt` | grid OK / pm OK / shadow OK | 5 `grid_stale_alarms_today`, 0 errors |

---

## 0. Headline for the human

1. **H-MANADV1's 0.93 anchor is clock-confounded.** The literal predicate recomputes to
   **0.941** pooled, but that number is an average over states that are nearly resolved.
   Holding man-advantage dose fixed at exactly +2, resolution falls monotonically
   **0.961 → 0.917 → 0.885 → 0.741** as clock-remaining rises 20-45s → ≥90s. The
   frozen bar (i) falsifies below **0.85**. So whether this hypothesis passes or fails its own
   bar is decided by the *entry-clock mix* the live engine happens to sample — which the
   registration does not pin (it sets a floor, ≥20s, and no ceiling). This is a live risk of
   both a false PASS and a false FAIL.
2. **The frozen `1-q` symmetry in H-MODELENTRY1-CELLS is violated by 4–7 points** on the
   pinned holdout, and the pinned Platt layer makes it worse, not better.
3. **Zero instances recorded.** Every one of the four open bars is 100% short of its
   book-paired evidence requirement. Nothing is falsified, nothing is confirmed.
4. One candidate DRAFT survived a second-method check (HP-at-equal-men); one apparently
   large finding (**AWP-at-equal-men, +29 pts marginal**) **reversed sign** under an
   equipment control and is reported as killed, not as a candidate.

---

## 1. Binding filter (CORPUS-F1-LEAKFILTER) — reproduced exactly

Applied as written: drop rows failing `rounds_played + 1 == round_number` against
**both** `features.round_number` and top-level `round_number`.

| quantity | recomputed | `model_metrics.json` | match |
|---|---|---|---|
| total rows | 22,273 | 22,273 | ✓ |
| leak-dropped | **2,898** | 2,898 | ✓ |
| missing-key rows / dropped | 67 / 49 | 67 / 49 | ✓ |
| kept rows | **19,326** | 19,326 | ✓ |

Drop decomposition: 2,789 fail both checks, 102 fail only the top-level check, 7 fail only
the feature-side check. **The dual check is load-bearing: 109 rows (3.8% of all drops) are
caught by exactly one of the two comparisons.** Keeping only one check would leak them.

Leak concentration by within-round time-bin:

| bin | rows | leak rate |
|---|---|---|
| 0 | 5,746 | 1.78% |
| 1 | 5,528 | 0.76% |
| 2 | 5,570 | 3.11% |
| 3 | 5,429 | **47.54%** |

Registry states "47.2% of bin-3"; I get **47.54%** (2,581/5,429). A 0.34-point difference —
immaterial, but it means the registry figure was computed on a marginally different row set
or rounding. Flagged for the record, no action.

**Structural caveat used throughout:** rows are *not* independent. 5,746 rounds produce
22,273 rows (≤4 snapshots/round; 5,429 rounds have all 4, 317 have fewer). Any row-level
N overstates independent evidence. All headline CIs below are reported **both** as Wilson
(row-level, optimistic) **and** as a series-clustered bootstrap (4,000 resamples over series),
which is the honest interval.

---

## 2. Anchor drift vs the frozen registry values

### 2.1 H-ECONBUY1 — reproduces cleanly ✓

Predicate: bin-0 (freeze-end) snapshot, regulation (`round ≤ 30`), non-pistol
(`round ∉ {1,13,25}`), exactly one side with `full_buy = 1`.

`equip_value_diff` is **normalised, not dollars**. I recovered the scale as **≈1/20,000**
(so a 10k gap ↔ `|evd| ≥ 0.5`) by triangulating on two registry-published quantities
simultaneously — this is a derived assumption, flagged as such:

| check | registry | recomputed at scale 1/20k |
|---|---|---|
| share of mismatch rounds at ≥10k gap | 0.886 (1234/1393) | **0.873** |
| force-buy (sub-10k) subset win rate | 0.710 (N=155) | **0.711** (N=232) |

Two independent quantities landing within 1.3 pts and 0.1 pts respectively is strong
evidence the scale is right.

| statistic | registry | recomputed | N (mine) | Wilson | drift |
|---|---|---|---|---|---|
| pooled buyer win | 0.832 | **0.821** | 1,889 | [0.803, 0.838] | −1.1 pt |
| ≥10k-equiv gap | 0.849 | **0.837** | 1,649 | [0.818, 0.854] | −1.2 pt |
| force-buy (<10k) | 0.710 | **0.711** | 232 | [0.650, 0.766] | +0.1 pt |
| buyer behind | 0.825 | **0.818** | 566 | — | −0.7 pt |
| buyer tied | 0.820 | **0.802** | 177 | — | −1.8 pt |
| buyer ahead | 0.837 | **0.825** | 1,146 | — | −1.2 pt |

**Dose-response is intact and monotone** (this is the part frozen into the design):

| signed equip gap (buyer frame) | ≈dollars | win rate | N |
|---|---|---|---|
| 0.00–0.25 | 0–5k | 0.562 | 32 |
| 0.25–0.50 | 5–10k | 0.735 | 200 |
| 0.50–0.75 | 10–15k | 0.782 | 316 |
| 0.75–1.00 | 15–20k | 0.825 | 627 |
| ≥1.00 | ≥20k | 0.873 | 706 |

Scoreline invariance also holds (0.818 / 0.802 / 0.825 vs registry 0.825 / 0.820 / 0.837).

**Two things the human needs from this.** (a) Every recomputed value sits **1–2 points
below** its frozen anchor, consistently signed — corpus growth (N is 36% larger) revising the
prior slightly downward, not a break. (b) **Headroom to the bar is thin.** Bar (i) falsifies
at <0.81 in the ≥10k bucket; the recomputed corpus prior is 0.837 with Wilson lower bound
**0.818 — only 0.8 pt above the bar.** And the scope-freeze trigger (force-buy subset <0.66)
sits inside the corpus CI for that subset ([0.650, 0.766]). The corpus cannot currently
rule out either the ≥10k bar or the scope freeze.

### 2.2 H-PISTOLCARRY1 — reproduces near-exactly ✓

| statistic | registry | recomputed | N (mine) | Wilson |
|---|---|---|---|---|
| pooled conversion | 0.859 (402/468) | **0.857** (390/455) | 455 | [0.822, 0.886] |
| loser not full-buy | 0.874 (N=388) | **0.875** | 408 | [0.839, 0.904] |
| loser force/full-buys | 0.702 (N=47) | **0.702** (33/47) | 47 | [0.560, 0.813] |
| conditional gap | 17 pts | **17.3 pts** | — | bar (ii) needs ≥5 |
| loser full-buy rate | 0.108 | **0.103** | — | — |
| winner full-buy rate | 0.922 | **0.914** | — | — |
| generic carryover | 0.611 (N=4,880) | **0.619** (N=4,439) | — | [0.605, 0.633] |

This is the cleanest lane in the registry. The economy mechanism (not momentum) is
confirmed: 0.857 after a pistol vs 0.619 generic carryover — a **23.8-point** separation.
Bar (ii) has ~12 points of headroom (17.3 vs the ≥5 requirement). **No drift; no action.**

### 2.3 H-MANADV1 — does NOT reproduce; provenance gap ⚠

Literal predicate ("map-score-trailing team, ≥2-man alive advantage, ≥20s on the clock"):

| | registry | recomputed |
|---|---|---|
| trailing-side resolution | 0.931 | **0.941** |
| N | 606 | **1,311** rows / 1,009 rounds / 156 series |
| Wilson | [0.908, 0.948] | [0.926, 0.952] |
| series-clustered CI | — | **[0.907, 0.964]** |
| companion: `|ma|≥2` any score | 0.950, N=1,680 | **0.962**, N=4,165 |

The **N is 2.2× and 2.5× the registry values, and the two registry Ns are not mutually
consistent with any single slicing I could construct.** I searched:

- bin subsets (bin0/1/2/3 and all-bins), clock thresholds {10,15,20,25,30,40}s,
  score-lead thresholds {<0, ≤−1, ≤−2, ≤−3}, dose {≥2, ≥3}, side-pooled vs CT-only,
  and first-qualifying-observation-per-round dedup — 96 combinations;
- a temporal-prefix scan (corpus growth hypothesis): `|ma|≥2` hits N=1,680 at the first
  ~65 series, where the trailing predicate is only N=525; the trailing predicate hits
  N=606 at ~73 series, where `|ma|≥2` is ~1,950. **The two anchors do not co-occur at
  any prefix.**

Closest single fits are e.g. `score_lead ≤ −2, |ma| ≥ 2, clock ≥ 40s` (N=604, p=0.925) or
`score_lead ≤ −3, clock ≥ 25s` (N=633, p=0.929) — i.e. **the published anchor is
consistent with a materially stricter unstated predicate.** Contrast this with §2.1/§2.2,
which reproduced to ~1 point: the H-MANADV1 anchor is the outlier, so this is a
reproducibility gap in that entry specifically, not a corpus-wide problem.

**Direction of the risk is benign for the bar but not for the mechanism:** every variant I
tested lands 0.92–0.94, comfortably above the 0.85 bar. So bar (i) is not threatened by the
provenance gap itself. It *is* threatened by §3.1.

**DRAFT ask (human, not me):** recover the exact predicate + corpus snapshot for the 606/1,680
figures into the experiment doc. Until then, treat 0.931/0.950 as unaudited.

---

## 3. Anomalies, with computed evidence

### 3.1 The H-MANADV1 clock confound — verified three ways ⚠ **highest priority**

The registration sets a clock **floor** (≥20s) and asserts man count is "a near-sufficient
statistic." Both are contradicted by the leak-filtered corpus. Resolution *falls* as clock
remaining *rises*:

**Method 1 — stratified means, man-advantage dose held fixed at exactly +2:**

| clock remaining | resolution | N | series |
|---|---|---|---|
| 20–45s | 0.961 | 382 | 130 |
| 45–75s | 0.917 | 277 | 123 |
| 75–90s | 0.885 | 131 | 84 |
| **≥90s** | **0.741** | 54 | 44 |

**Method 2 — cluster-robust logit** (`win ~ clock + man_adv`, clustered on series, N=1,311):
clock coefficient **−0.0247 per second, z = −4.79, p = 1.7e-6**, i.e. **odds ratio 0.477
per additional +30s** — every extra half-minute on the clock roughly halves the odds. The
dose term is positive and significant (+1.059, p=0.031), so this is not dose leakage.

**Method 3 — within-bin cross-tab** (rules out the time-bin index proxying for clock):
the decline holds inside *every* bin, e.g. bin1: 0.944 (20–45s) → 0.891 → 0.885 → 0.765 (≥90s);
bin2: 0.960 → 0.948 → 0.950 → 0.920.

Equivalently, by within-round snapshot index — which is what the live engine's *first*
detection will look like:

| observation | resolution | N | series | Wilson | clustered CI |
|---|---|---|---|---|---|
| pooled (registry style) | 0.941 | 1,311 | 156 | [0.926, 0.952] | [0.907, 0.964] |
| first qualifying obs/round | 0.937 | 1,009 | 156 | [0.920, 0.950] | [0.908, 0.958] |
| — first obs @ bin0 | **0.800** | 45 | 37 | [0.662, 0.891] | [0.659, 0.919] |
| — first obs @ bin1 | **0.881** | 236 | 108 | [0.834, 0.917] | [0.828, 0.932] |
| — first obs @ bin2 | 0.955 | 508 | 145 | [0.933, 0.970] | [0.931, 0.974] |
| — first obs @ bin3 | 0.982 | 220 | 117 | [0.954, 0.993] | [0.956, 1.000] |

**Mechanism (DRAFT):** man count is a sufficient statistic only *net of time-to-resolution*.
A 2-man advantage with 100s left leaves the disadvantaged side enough clock to play a
retake/save-and-rotate; the same advantage with 30s left is close to arithmetically settled.
The registry's own framing — "market flow anchors on scoreboard inside GRID's 13–27s lead" —
implicitly assumes we are early in the round, which is exactly the regime where the edge is
weakest.

**Why this is decision-relevant right now:** the frozen bar is a single pooled number
(<0.85 ⇒ falsified). The realized pooled figure is a weighted average whose weights are set
by *when the engine happens to detect*. High-clock/early states resolve at 0.741–0.809 —
**below the bar**; late states at 0.955–0.982 — far above. With zero instances recorded, the
sampled mix is entirely unknown. Good news: the frozen CELLS spec already records `clock`
per instance, so the stratification is recoverable at verdict time *if* it is done.

### 3.2 "Man count is near-sufficient" is false — economy interacts ⚠

Within `|ma| ≥ 2`, cluster-robust logit (`win ~ dose + advantaged-side equip gap + clock`,
N=4,165 rows / 158 series): **equip-gap coefficient +1.270, z = 2.98, p = 0.0029**, alongside
dose (+1.136, p=0.003) and clock (−0.0134, p=1.0e-4). Economy carries independent signal
*conditional on* a ≥2-man advantage.

Stratified, dose fixed at `|ma| = 2`:

| advantaged side's equipment | resolution | N | series | clustered CI |
|---|---|---|---|---|
| poorer than opponent | **0.803** | 61 | 41 | [0.667, 0.922] |
| +0…8k richer | 0.937 | 853 | 148 | [0.912, 0.957] |
| >+8k richer | 0.955 | 1,640 | 155 | [0.937, 0.969] |

The **danger cell** for bar (i) — `|ma| ≥ 2`, advantaged side poorer, clock ≥ 20s:
**0.786** (N=56 rows / 49 rounds / 41 series, Wilson [0.662, 0.873]) — point estimate below
the 0.85 bar. Tightening to clock ≥ 60s: **0.697** (N=33, 27 series).

Group sizes here are small (41–49 independent rounds) and the clustered CI is wide; I am
**not** claiming this cell is established. I am claiming it is a **plausible, mechanistically
sensible route to a spurious FAIL** on a bar that pools it in, and it is cheap to record.

### 3.3 Pinned-benchmark calibration is side-asymmetric; the Platt layer hurts ⚠

**Reconstruction, and its honest label.** The model artifact is not mounted, so I refit a
**surrogate** from the pinned metadata (LightGBM, `num_leaves=31`, `lr=0.05`, `seed=42`,
`force_row_wise`, `deterministic`, forced to the pinned `best_iteration = 96`, no early
stopping) plus the pinned Platt map `sigmoid(0.96828·margin − 0.12790)`.

The **32-series / 4,276-row holdout reproduces exactly** under any capture-time series
ordering — so the *evaluation set* is certainly the right one. The train/valid boundary does
**not** reproduce (I get 12,968/2,082 vs pinned 12,684/2,366 over the same 126 series and the
same 15,050 rows, under four orderings and a seed-42 shuffle), so my train set carries ~2.2%
extra rows. Resulting agreement:

| metric | pinned | surrogate | delta |
|---|---|---|---|
| Brier (raw) | 0.1499 | 0.1513 | +0.0014 |
| Brier (calibrated) | 0.1501 | 0.1517 | +0.0016 |
| AUC | 0.8640 | 0.8615 | −0.0025 |
| ECE-10 (calibrated) | 0.0296 | 0.0314 | +0.0018 |

Close enough to read **segment-level** structure; **all segment numbers below are surrogate,
not the served artifact, and must be re-run on the real artifact before anything is frozen.**

**Finding: the frozen `1-q` symmetry does not hold.** Cells are defined as the entry side's
calibrated probability crossing up through {0.65, 0.75, 0.85}, with the t-side derived via
`1-q`. On the pinned holdout:

| threshold | side | n | mean predicted | observed | gap | gap 95% CI (series-clustered) |
|---|---|---|---|---|---|---|
| 0.65 | ct | 1,460 | 0.842 | 0.893 | **+0.052** | [+0.031, +0.071] |
| 0.65 | t | 1,301 | 0.865 | 0.846 | −0.020 | [−0.047, +0.005] |
| 0.75 | ct | 1,140 | 0.881 | 0.937 | **+0.055** | [+0.041, +0.068] |
| 0.75 | t | 1,072 | 0.900 | 0.892 | −0.008 | [−0.036, +0.018] |
| 0.85 | ct | 731 | 0.926 | 0.977 | **+0.051** | [+0.040, +0.061] |
| 0.85 | t | 798 | 0.935 | 0.944 | +0.009 | [−0.018, +0.034] |

CT-side gaps are positive with clustered CIs **excluding zero at all three thresholds**;
t-side CIs **include zero at all three**. Net **ct-minus-t asymmetry: +7.1 / +6.4 / +4.2
points**.

**Verified a second way (uncalibrated raw scores, same cells):** same sign and ordering —
ct +0.022 / +0.035 / +0.040 vs t −0.006 / +0.004 / +0.011. The effect is in the model, and
the Platt layer **amplifies** it (ct gaps grow from +0.022→+0.052, +0.035→+0.055,
+0.040→+0.051).

**Corollary — the pinned Platt layer is a net negative, by the registry's own numbers:**
raw Brier 0.14989 vs calibrated 0.15012, raw log-loss 0.4488 vs calibrated 0.4508. It is
worse on 3 of 4 published metrics and only improves ECE by 0.0011 (0.0307→0.0296). Its slope
0.968 (<1) plus intercept −0.128 shrinks toward and shifts below 0.5 — which is precisely
what over-shrinks the tails and pushes the CT side down.

**Is it just base-rate drift?** Partly, and not enough. Temporal drift is small: CT-side win
rate is 0.510 (Jun, 49 rounds, 1 series) and 0.530 (Jul, 5,595 rounds, 158 series); holdout
0.539 vs pre-holdout 0.526 (**+1.3 pt**). Global holdout bias is mean-p 0.5105 vs observed
0.5339 (+2.3 pt). A pure base-rate shift would bias the two sides in *opposite* directions —
that explains the 0.65/0.75 pattern, but at the 0.85 cell **both** sides under-forecast
(+5.1 and +0.9), which is tail over-shrinkage, not drift. Two distinct effects, both
disfavouring the frozen symmetry.

**Segment calibration (worst offenders):**

| segment | n | mean p | observed | gap | Brier | ECE | AUC |
|---|---|---|---|---|---|---|---|
| rounds 13–15 (2nd-half open) | 621 | 0.515 | 0.583 | **+0.068** | 0.1513 | **0.088** | 0.863 |
| CT ahead ≥+5 | 490 | 0.595 | 0.655 | **+0.060** | 0.1564 | 0.064 | 0.833 |
| clock <20s | 871 | 0.479 | 0.528 | +0.049 | 0.1545 | 0.068 | 0.856 |
| rounds 23–30 | 211 | 0.554 | 0.592 | +0.039 | 0.1753 | 0.061 | 0.811 |
| bin0 (round start) | 1,234 | 0.521 | 0.537 | +0.017 | **0.2072** | 0.038 | **0.730** |
| bin3 (near-resolved) | 622 | 0.515 | 0.523 | +0.008 | 0.0551 | 0.068 | 0.984 |

Two collisions with the open lanes:
- **Rounds 13–15 is the worst-calibrated segment in the whole holdout (ECE 0.088).** That is
  exactly where H-PISTOLCARRY1's round-14 leg lives.
- **bin0 is the weakest discrimination regime (AUC 0.730, Brier 0.2072).** That is exactly
  the freeze-end state H-ECONBUY1 and H-PISTOLCARRY1 both enter on.

Neither hypothesis *uses* the model, so this is not a defect in them — but it does mean the
served model must not be used as a sanity filter or a sizing input for those two lanes
without re-measuring it in-segment.

### 3.4 Corpus hygiene / feature-vector defects

Confirmed by exact identity checks on all 22,273 rows:

- **`man_advantage` is exactly `5 × alive_ratio`** (`np.allclose` → True). A perfectly
  collinear duplicate; LightGBM assigns `man_advantage` **0.0000 gain**. Note the irony:
  the feature H-MANADV1 is built on contributes nothing to the served model *under that name*.
- **Five features are constant** across the entire corpus and carry zero information:
  `bomb_planted`, `bomb_time_remaining`, `map_rating_diff`, `prior_round_p` (≡0.5),
  `rating_diff`. That is **25% of the 20-float vector**, including every
  team-strength/rating input — the model has **no team-quality signal at all**.
- `rounds_played` also lands at 0.0000 gain (collinear with `round_number` post-filter).
  Effective feature count is **≈13, not 20**.
- Gain concentration: `equip_value_diff` **55.4%**, `alive_ratio` 12.0%, `hp_alive_diff`
  11.8%, `econ_ratio` 6.3%. The served model is **mostly an economy model**.
- `awp_diff` ≠ `awp_alive_diff` (distinct features, both present); `awp_diff` gain 0.0013.
- **One impossible row**: `man_advantage = 6.0` / `alive_ratio = 1.2` in 5v5. Single row,
  harmless to aggregates, but it means the builder does not bound this field.
- Provenance: 0 duplicate `row_key`, 0 duplicate `(series, map, round, bin)`, 239 source
  files, but **74 series appear in more than one source file** — worth confirming that is
  intentional multi-file capture and not double-ingest under different keys (row keys are
  unique, so no duplication reached the corpus).
- 317 of 5,746 rounds have <4 bins (176 have exactly 1). Uneven snapshot depth is a mild
  weighting hazard for any row-level aggregate.
- Capture QA: grid/pm/shadow all OK, 0 errors, but **5 `grid_stale_alarms_today`**. Stale
  GRID polls are the one failure mode that directly corrupts a clock-conditioned entry —
  see §3.1, where 30s of clock error moves the odds by ~2×. Worth confirming the shadow
  engine's `grid_ts_ns` staleness guard rejects those.

---

## 4. Per-hypothesis read — distance to each frozen bar

**`verdict.json` = `no instances recorded yet`. Zero instances, zero series, no books, no
paper/maker PnL. Therefore: for every bar below, the honest verdict is "no new evidence" on
every clause that requires live books.** What follows is corpus-prior distance only.

### H-MANADV1 (REGISTERED_NO_RUN)
| bar clause | requirement | status |
|---|---|---|
| (i) trailing resolution <0.85 | ≥80 held-out instances | **no evidence.** Corpus prior 0.941 (clustered [0.907, 0.964]) — 9 pts of headroom pooled, but §3.1/§3.2 identify sub-states at 0.697–0.809, i.e. *below* the bar |
| (ii) <20% of instances ever quote ask ≤0.88 | live book stream | **no evidence.** Corpus contains no book data at all |
| (iii) mean settled edge ≤+2 pts at ask ≤0.85 | live book stream | **no evidence** |
Missing: ~**12 series** of book-paired capture at corpus instance rates (6.5 qualifying
rounds/series). **Do not read (i) as a pooled number** — pre-commit to reporting it stratified
by entry clock and by advantaged-side equip sign, both of which the CELLS spec already records.

### H-ECONBUY1 (REGISTERED_NO_RUN)
| bar clause | requirement | status |
|---|---|---|
| (i) buyer <0.79 pooled / <0.81 at ≥10k | ≥120 mismatch rounds (≥60 at ≥10k) | **no evidence.** Corpus prior 0.821 / 0.837; the ≥10k Wilson lower bound is **0.818, only 0.8 pt above its bar** |
| (ii) <10% of rounds ever quote buyer <0.80 | live book stream | **no evidence** |
| (iii) mean settled edge ≤+2 pts after fees | live book stream | **no evidence** |
| scope-freeze trigger: force-buy subset <0.66 | held-out force-buy subset | **cannot be excluded.** Corpus force-buy subset 0.711, Wilson [0.650, 0.766] — the trigger sits *inside* the interval |
Missing: ~**10 series** for the pooled bar, ~**6 series** for the ≥10k sub-bar. This is the
cheapest bar to reach. It is also the one whose corpus prior sits closest to falsification —
expect a genuinely informative result either way.

### H-PISTOLCARRY1 (REGISTERED_NO_RUN)
| bar clause | requirement | status |
|---|---|---|
| (i) pooled conversion <0.80 | ≥100 pairs | **no evidence.** Corpus prior 0.857, Wilson [0.822, 0.886] — lower bound 2.2 pts above the bar |
| (ii) loser-buy conditional gap <5 pts | ≥100 pairs | corpus gap **17.3 pts** — largest margin of any clause in the registry |
| (iii) edge ≤+2 pts, or no ask ≤0.80 in ≥85% of pairs | live book stream | **no evidence** |
Missing: ~**33 series** — the slowest lane, at 3.0 pairs/series (2 pistol rounds per map).
Caveat from §3.3: round 13–15 is the worst-calibrated model segment, so if any model output
is used to gate or size this lane, it will misbehave exactly here.

### H-MODELENTRY1 / -CELLS (frozen, artifact pinned)
| bar clause | status |
|---|---|
| taker entry loses; only maker fills clear | **no evidence.** Requires captured book stream + fill inference; none exists |
| per-cell instance accumulation | **not the bottleneck** — see below |
Crossing simulation on the reproduced holdout (bin sequence as a coarse poll proxy;
guards applied: `clock ≥ 10s`, first crossing only per series/map/round/side/threshold):
**2,204 instances across 32 series / 919 rounds ≈ 21–25 per series per threshold (both
sides), ~69/series across all 6 cells.** ~4 series would fill an 80-instance cell. Since the
corpus holds ≤4 snapshots/round against dense live polling, this **undercounts** — the true
rate is higher.

**Conclusion: instance count is not the constraint for any lane. Book capture is the sole
binding constraint on all four bars.**

Two further reads on the cells:
- The side asymmetry survives into the crossing frame: observed-minus-quoted is
  ct **+0.023 / +0.034 / +0.043** vs t **−0.004 / −0.001 / +0.013** at 0.65/0.75/0.85.
  Pooling ct and t instances will produce a number that describes neither side.
- **The cells are structurally near-disjoint from H-ECONBUY1** — a welcome result I expected
  to go the other way. Because a crossing requires `q_prev < thr`, rounds that *open* above
  threshold are excluded by construction. Only **17.3%** of crossing rounds are freeze-end
  full-buy-mismatch rounds, against a **33.1%** holdout base rate — crossings are
  *under*-represented in econ-mismatch rounds by nearly half. So the two lanes are
  independent evidence (good for the registry), but H-MODELENTRY1 will systematically **miss**
  the highest-conviction pre-round economy states. Crossings never occur at bin0 and cluster
  at 43–78s remaining.

---

## 5. Ranked candidate ideas — all DRAFT, none pre-registered

Ranked by (evidence strength × independence from existing lanes × cheapness to falsify).
I am not proposing any of these be opened; they are drafts for the human and the registry
process.

### DRAFT-1 — Clock-ceiling refinement of H-MANADV1 *(highest confidence)*
**Claim.** The man-advantage edge is a function of time-to-resolution, not man count alone;
resolution ≥0.95 requires a clock *ceiling*, not just the registered ≥20s floor.
**Mechanism.** Clock remaining is the disadvantaged side's optionality: time to retake, save,
rotate, or trade back. A 2-man deficit with ~100s left is recoverable; with ~30s it is
close to arithmetically settled. Books anchored on the scoreboard misprice both regimes, but
only the low-clock regime is actually worth 0.95.
**Evidence.** Dose-fixed monotone decline 0.961 → 0.917 → 0.885 → 0.741 (N=382/277/131/54,
84–130 series/cell); cluster-robust logit clock coefficient −0.0247/s (p=1.7e-6, OR 0.477
per +30s); holds within every time-bin.
**Falsification sketch.** Pre-register a clock-banded read on ≥80 held-out book-paired
instances: FALSIFIED if the 20–45s band resolves <0.92, **or** if the ≥90s band resolves
≥0.90 (i.e. no monotone clock gradient exists live), **or** if the band-to-band spread is
<5 points. Note this partly re-tests H-MANADV1 and should be framed as a refinement of a
frozen entry, not a new lane — sequencing is the human's call.
**Cost.** Zero new capture: `clock` is already a recorded field in the frozen CELLS spec.

### DRAFT-2 — HP-differential edge at equal man count *(only survivor of a second-method check)*
**Claim.** With man count level, the side ahead on aggregate alive-HP wins materially more
often; no registered hypothesis touches sub-lethal damage state.
**Mechanism.** Damage dealt without a kill is invisible to the scoreboard and to man-count
anchoring, but it determines who wins the next contact. This is a genuine information
asymmetry rather than a repricing lag.
**Evidence (equipment-controlled, which is the load-bearing part).** At `man_advantage = 0`
**and** near-equal equipment (`|evd| ≤ 0.15`; stratum N=3,725, 156 series):
hp>0 → **0.549** (N=997) vs hp<0 → **0.411** (N=698), a **+13.7-point** spread.
Cluster-robust logit at equal man count: `hp_alive_diff` coefficient **+2.359, z = 5.94,
p = 2.8e-9** (OR 10.6 per unit), net of equip gap, AWP, score lead and clock. The marginal
(uncontrolled) quintile spread was only +6.8 pts — controlling for economy makes it
*stronger*, the opposite of the DRAFT-5 failure mode.
**Falsification sketch.** ≥100 held-out equal-man / near-equal-equip states with books:
FALSIFIED if the hp>0 side resolves <0.52, or if the hp>0 minus hp<0 spread is <6 points,
or if <15% of instances ever quote the hp-favoured side ≤0.47.
**Caveat.** Requires GRID to expose alive-HP with low staleness; the 5 stale alarms in
`capture_qa.txt` matter more here than anywhere else, since HP is the fastest-moving field
in the vector. Verify staleness before this is worth pre-registering.

### DRAFT-3 — Economy carve-out for the man-advantage lane
**Claim.** A ≥2-man advantage held by the *poorer* side is worth far less than the registry's
0.93 prior and may sit below the 0.85 bar.
**Mechanism.** Advantages won while on an eco/force (trading up from a deficit) leave the
advantaged side without the utility or rifles to close out the round; the man count overstates
their control.
**Evidence.** Within `|ma| ≥ 2`, equip-gap logit coefficient +1.270 (p=0.0029) net of dose and
clock; `|ma| = 2` stratified 0.803 (poorer, N=61, 41 series) vs 0.937 / 0.955 (richer).
Danger cell `|ma| ≥ 2` + poorer + clock ≥ 20s: **0.786** (N=56 rows / 49 rounds / 41 series,
Wilson [0.662, 0.873]); at clock ≥ 60s **0.697** (N=33).
**Falsification sketch.** Cheapest as a *recorded covariate* rather than a lane: log the
advantaged side's equip sign on every H-MANADV1 instance and pre-commit to reporting bar (i)
split on it. As a standalone claim: FALSIFIED on ≥60 held-out poorer-advantaged instances if
they resolve ≥0.88 (no carve-out needed).
**Honest limit.** 41–49 independent rounds. Suggestive, not established. Its value now is
defensive — it identifies a route to a spurious FAIL on a frozen bar.

### DRAFT-4 — Opening-duel trim for H-ECONBUY1
**Claim.** The full-buy edge is conditional on surviving the opening contact; the frozen
design grades only by equip gap and has no in-round exit.
**Mechanism.** A full buy converts into a win via map control; losing the first duel forfeits
that control while the buy is already sunk, and the anti-eco upside collapses.
**Evidence.** Regulation non-pistol mismatch rounds at bin-1 (first in-round snapshot),
buyer frame: buyer down 1 man → **0.646** (N=96, 66 series, clustered [0.548, 0.740]);
buyer even → 0.818 (N=1,260, 156 series); buyer up → 0.888 (N=810, 147 series). A
**24-point** collapse against the pooled 0.821 prior.
**Falsification sketch.** ≥80 held-out mismatch rounds where the buyer is down ≥1 at first
in-round poll: FALSIFIED if they resolve ≥0.75 (no trim justified), or if the book does not
quote the buyer ≥0.78 in ≥40% of those states (no exit liquidity to trim into).
**Note.** This is an exit rule on an open lane, so it interacts with a frozen design.
Sequencing is the human's call — a trim added mid-flight would contaminate bar (iii).

### DRAFT-5 — AWP-at-equal-man-count: **KILLED this session, do not open**
Recorded so nobody re-derives it. The marginal signal is large and tempting: at
`man_advantage = 0`, CT win rate is **0.348 / 0.529 / 0.642** for `awp_alive_diff`
−1 / 0 / +1 (N=865 / 4,661 / 2,315; 139/157/153 series) — a **29.4-point** spread at
identical man count, which looks like a clean untraded edge.

**It reverses under an equipment control.** Cluster-robust logit at equal man count gives
`awp_alive_diff` coefficient **−0.297 (z = −4.48, p = 7.4e-6, OR 0.74)** — *negative* — once
`equip_value_diff` (coefficient +1.926, OR 6.86) is included. And at equal man count **and**
near-equal equipment, the effect vanishes and is non-monotone: 0.456 / 0.528 / 0.478
(N=287 / 2,982 / 456). Textbook confounding: holding a live AWP is a **marker of being rich**,
and conditional on total equipment value the AWP is mildly *negative* — plausibly because
~4,750 of the team's equipment is concentrated in one player, leaving the other four weaker.
The marginal 29-point spread is an economy signal already captured by H-ECONBUY1 and already
worth 55.4% of the served model's gain. **No new lane here.**

---

## 6. What I did not and could not do

- **No book data exists in this evidence set.** Every clause of every frozen bar that
  mentions asks, quotes, spreads, fills, fees or settled edge is untestable offline. I have
  asserted nothing about any of them.
- **The served artifact is not mounted.** All segment- and cell-level calibration in §3.3 and
  §4 is from a **surrogate** that matches the pinned benchmark to +0.0016 Brier / −0.0025 AUC
  on an exactly-reproduced holdout. Directionally trustworthy; must be re-run on
  `data/models/roundwin-v1` before anything is frozen on it.
- **The `equip_value_diff` → dollars scale (≈1/20,000) is my inference**, triangulated on two
  registry-published quantities (§2.1). If the true scale differs, every "10k" bucket
  boundary in §2.1 shifts. Worth confirming against the builder.
- **H-MANADV1's registry anchors (0.931/N=606, 0.950/N=1,680) remain unreproduced** after a
  96-combination predicate search and a temporal-prefix scan.
- I did not touch the registry, place or simulate any order against a live venue, or handle
  credentials. `dry_run` discipline assumed throughout.

---

## 7. Suggested next actions (for the human to decide, in dependency order)

1. **Turn on book capture.** It is the single binding constraint on all four bars; instance
   supply is 4–33 series depending on lane and is not the bottleneck. Priority by cheapness:
   H-ECONBUY1 (~6–10 series) → H-MANADV1 (~12) → H-PISTOLCARRY1 (~33).
2. **Before instances accumulate, decide how bar (i) of H-MANADV1 will be read.** Pooled, it
   is a number whose value is set by the engine's detection-clock mix (0.741 → 0.982 across
   the range). The `clock` field is already recorded; pre-commit to the stratified read.
3. **Consider a registry amendment on H-MODELENTRY1-CELLS' `1-q` symmetry** — violated by
   4–7 points, clustered CIs excluding zero on the CT side and including zero on the t side.
   At minimum, pre-commit to reporting ct and t cells separately rather than pooled.
4. **Re-examine the pinned Platt layer.** It is worse than raw on Brier and log-loss in the
   registry's own published numbers, and §3.3 shows it actively widens the cell asymmetry.
   Artifact swap requires an amendment, so this is a registry decision, not a code change.
5. **File the corpus-hygiene items** (§3.4): the `man_advantage` ≡ `5 × alive_ratio`
   duplicate, five constant features including all team-rating inputs, the single
   `man_advantage = 6` row, and confirmation that 74 series spanning multiple source files
   is intentional.
6. **Recover the H-MANADV1 anchor provenance** into the experiment doc, or mark 0.931/0.950
   as unaudited.
7. **Check the GRID staleness guard** against the 5 `grid_stale_alarms_today`, specifically
   for clock- and HP-conditioned entries (DRAFT-1, DRAFT-2).
