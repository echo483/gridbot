# gridbot deep-analysis — DRAFT report

**Status: DRAFT analysis for the human and the registry process. No orders, no registry edits, no
credential handling. Nothing below is a verdict.** Every candidate is labelled DRAFT and none is
proposed for arming. Where evidence is absent I say "no new evidence" rather than inferring.

**Evidence mounted at** `/mnt/session/uploads/workspace/evidence/` (the `/workspace/evidence/` path
was not populated in this sandbox; files are byte-identical to the described schemas).
`training_rounds.jsonl` (22,273 rows), `registry.jsonl` (27 entries), `verdict.json`,
`model_metrics.json`, `capture_qa.txt`.

**Method note.** All aggregates below are computed under the binding CORPUS-F1-LEAKFILTER. All
intervals labelled "clustered" resample SERIES, not rows, matching registry practice. Surprising
findings were each recomputed a second way; one of them (§6.3) **failed** its second check and is
reported as retracted-in-session.

---

## 1. Data integrity: the binding filter reproduces row-exact

Applied `rounds_played + 1 == round_number` against **both** `features.round_number` and the
top-level `round_number`, dropping the union of failures.

| quantity | this session | registry / model_metrics | match |
|---|---|---|---|
| total rows | 22,273 | 22,273 | exact |
| union-dropped (leak) | **2,898 (13.011%)** | 2,898 (13.0%) | exact |
| bin-3 dropped share | **2,581 / 5,429 = 47.54%** | 47.54% (CORPUS-F1 correction, IDX 7) | exact |
| kept | 19,375 | — | — |
| kept ∧ all-20-features present | **19,326** | `kept_rows` 19,326 | exact |
| missing-key rows / dropped | 67 / 49 | 67 / 49 | exact |

Sub-counts: 2,796 rows fail on `features.round_number`, 2,891 on the top-level field; the union is
2,898, so the two checks are **not** redundant — 102 rows are caught only by the feature-side check
and 7 only by the top-level check. The registry's insistence on BOTH checks is load-bearing and
empirically justified.

Post-filter corpus: 19,375 rows, **159 series, 334 maps, 5,647 distinct rounds**, base rate
P(CT wins) = 0.5267. Format is MR12 (race to 13): regulation = rounds 1–24, pistols = rounds 1 and
13, OT = rounds 25+ (212 OT round-rows observed).

**No drift in the integrity layer.** The filter is exactly as frozen and the corpus is the same
vintage the registry describes.

---

## 2. Headline corpus edges recomputed under the binding filter (focus a)

### 2.1 H-MANADV1 — frozen anchor still unreproduced; the CLOCKSTRAT amendment reproduces exactly

Literal predicate: map-score-trailing side (`score_lead` sign opposite the man advantage),
`|man_advantage| >= 2`, `time_remaining >= 20`.

| reading | this session | frozen text | note |
|---|---|---|---|
| trailing +2, clock ≥20 (rows) | **0.9405, n=1,311** (1,009 rounds, 156 series) | 0.931, N=606 | anchor NOT reproduced |
| first-qualifying obs per round | 0.9366, n=1,009 | — | |
| `ma >= +2` any score (rows) | 0.9587, n=2,299 | 0.950, N=1,680 | anchor NOT reproduced |

This independently confirms the provenance annotation already frozen in **H-MANADV1-CLOCKSTRAT**
(IDX 6): the 0.931/N=606 and 0.950/N=1,680 anchors do not reproduce, and my literal-predicate figure
lands on **0.941 / 1,311 rows / 1,009 rounds — the exact triple the amendment recorded**. The
anchors remain correctly flagged UNAUDITED. The bar itself is not threatened: every reading I
computed sits in 0.92–0.96, far above the 0.85 falsification line.

The amendment's own numbers reproduce cleanly:

| CLOCKSTRAT claim | frozen | recomputed | verdict |
|---|---|---|---|
| first-qual rate by bin b0/b1/b2/b3 | .800 / .881 / .955 / .982 | **.800 / .881 / .955 / .982** | exact (n = 45/236/508/220) |
| monotone clock decay | 0.961 → 0.741 | 0.966 (n=596) → 0.940 → 0.925 → **0.809** (n=89) | direction + monotonicity exact; endpoint differs |
| clustered logit clock coef | −0.0247/s, z=−4.79, p=1.7e−6 | −0.0237/s, **z=−4.79, p=1.68e−6** | z and p exact; coefficient differs by 0.0010 |

**Anomaly (annotation-level, not a bar issue): the frozen coefficient appears to be a transcription
slip.** The z-statistic and p-value match to full displayed precision while the coefficient does
not; a genuine specification difference would move z and p too. Suggested registry action: a
one-line annotation correcting −0.0247 → −0.0237, exactly as CORPUS-F1's 47.2%→47.54% slip was
handled in IDX 7. The amendment's substance is unaffected.

Dose-response (all rows, advantaged side): |ma|=1 → 0.7715 (n=4,573); =2 → 0.9452 (n=2,557);
=3 → 0.9873 (n=1,180); =4 → 0.9952 (n=413). Clean and monotone.

### 2.2 H-PISTOLCARRY1 — reproduces closely, and hides a significant half asymmetry

Round-level pair construction (round r winner → does the same team win r+1), teams matched by name
across the side switch; 5,228 pairs total, 0 unmatched.

| frozen claim | frozen | recomputed | verdict |
|---|---|---|---|
| pistol → next conversion | 0.859 (402/468) | **0.8543, n=453** (149 series) | reproduces (−0.5 pt, n −15) |
| loser stays below full buy | 0.874, N=388 | **0.8729, n=409** | reproduces |
| loser force/full-buys | 0.702, N=47 | **0.7021, n=47** | **exact, n and rate** |
| conditional gap | 17 pts | **17.1 pts** | exact |
| loser full-buy rate | 10.8% | 10.3% | reproduces |
| winner full-buy rate | 92.2% | 91.2% | reproduces |
| generic non-pistol carryover | 0.611, N=4,880 | **0.6115, n=4,772** | reproduces |

The exact N=47/0.702 recovery confirms the predicate is the intended one. **This is the best-audited
of the three corpus hypotheses.**

**New, not in the frozen text — a significant half asymmetry:**

| arm | n | series | conversion | Wilson | series-clustered |
|---|---|---|---|---|---|
| r1 → r2 (first-half pistol) | 186 | 135 | **0.8925** | (0.840, 0.929) | (0.849, 0.934) |
| r13 → r14 (second-half pistol) | 267 | 148 | **0.8277** | (0.778, 0.868) | (0.780, 0.873) |
| gap (r1 − r13) | | | **+0.0648** | | clustered CI **[+0.004, +0.123] — excludes zero** |

This matters directly for the frozen bar, see §5.3.

### 2.3 H-ECONBUY1 — anchors do NOT reproduce; population definition is not recoverable from features

Predicate: exactly one side's full-buy flag set (`ct_full_buy XOR t_full_buy`), regulation
non-pistol, read at the freeze-end snapshot (bin 0, median `time_remaining` = 96s).

| frozen claim | frozen | recomputed | verdict |
|---|---|---|---|
| pooled buyer win | 0.832, N=1,393 (CI .811–.851) | **0.8211, n=1,889** | rate −1.1 pt; **n off by +36%** |
| ≥10k equip gap | 0.849, N=1,234 | best variant `|equip_value_diff| ≥ 0.8`: **0.8507, n=1,226** | rate and n both near-exact |
| vs force-buys | 0.710, N=155 | no band reproduces this n | **not recoverable** |
| scoreline invariance (behind/tied/ahead) | .825 / .820 / .837 | **.818 / .802 / .826** | reproduces |

The ≥10k arm is recovered almost perfectly at a threshold of `|equip_value_diff| ≥ 0.8` (n=1,226 vs
1,234; 0.8507 vs 0.849), which pins the feature's scale at roughly $12.5k per unit rather than the
$10k a naive reading assumes. But the **pooled** and **force-buy** arms do not reproduce at any
threshold: the frozen split is 1,234 + 155 = 1,389 of 1,393 (88.6% in the ≥10k bucket), whereas the
feature-level XOR population is 1,889 with only 65% at gap ≥ 0.8. The frozen force-buy bucket is
almost certainly defined on the **non-buyer's absolute loadout** (a force buy is "spent nearly
everything but under $13.4k"), which is not identifiable from a signed *difference* alone.

**Recommendation (DRAFT, annotation-level):** treat the H-ECONBUY1 corpus anchors as **UNAUDITED**
under the same convention already applied to H-MANADV1 by CLOCKSTRAT — the exact predicate is not
recoverable from `training_rounds.jsonl`. The frozen bar is **not** threatened by this: every
variant I computed for the ≥10k arm lands 0.85–0.89, far above the 0.81 bar, and the pooled variant
lands 0.821, above the 0.79 bar. The live cells (§5.2) corroborate.

The live engine's bucket shares (2,012 / 280 / 6 → 87.6% at ≥10k) match the *frozen anchor's*
shares (88.6%) and not my feature-derived shares (65%), which is further evidence the engine reads
raw GRID loadouts and the corpus feature is a different, coarser quantity.

---

## 3. The run's principal anomaly: residual within-round contamination in the equipment features

CORPUS-F1-LEAKFILTER removes snapshots whose *scoreboard* encodes the labelled round's outcome. It
does **not** remove a second, distinct contamination channel that I found this session.

`ct_full_buy`, `t_full_buy` and `equip_value_diff` are **not** frozen freeze-end attributes — they
are live in-round quantities that decay as players die and drop equipment:

- `equip_value_diff` takes more than one distinct value within **5,502 of 5,647 rounds (97.4%)**.
- `ct_full_buy` varies within-round in 2,144 rounds; `t_full_buy` in 2,488.
- bin0 → bin3 mean absolute change in `equip_value_diff` = 0.376; correlation only 0.618.
- Of 2,846 rounds observed at both bin 0 and bin 3, the "exactly one team full-buys" state **flips
  in 1,197 (42%)**: 802 rounds acquire it and 395 lose it.

Consequence — the H-ECONBUY1 edge is a monotone function of *how late you read it*:

| time bin | n | buyer win rate | mean clock |
|---|---|---|---|
| 0 (freeze-end) | 1,888 | **0.8210** | 57.4s |
| 1 | 2,162 | 0.8363 | 77.0s |
| 2 | 2,618 | 0.8862 | 54.2s |
| 3 | 1,299 | **0.9623** | 37.9s |
| pooled all bins | 7,967 | 0.8696 | |

By bin 3 the "buyer" is winning 96.2% — that is not an economy signal, it is a survivorship readout
("the side still holding a full loadout is the side whose players are still alive"). **Pooling bins
inflates the H-ECONBUY1 edge by +4.9 pts (0.870 vs 0.821).** Any backtest of the economy lane that
does not restrict to the freeze-end snapshot is measuring the outcome.

This is amplified by the served model's structure: `equip_value_diff` carries **57.1% of total
LightGBM split gain** — by far the dominant feature (next: `alive_ratio` 13.7%, `hp_alive_diff`
9.8%). The pinned model's headline skill therefore rests largely on a within-round survivorship
proxy. That is legitimate live information for an in-round forecaster, but it means the benchmark's
AUC is not transferable to pre-round or freeze-end decisions (see §4.3).

**Proposed registry action (DRAFT, data-integrity class, sibling of CORPUS-F1):** record a
non-binding annotation that equipment features are time-varying, and pin *snapshot discipline*
("economy-lane corpus statistics are read at bin 0 only") the way CORPUS-F1 pinned row eligibility.
This changes no bar and no instrument; it prevents a future backtest from citing 0.87.

---

## 4. Calibration and drift against the pinned benchmark (focus c)

### 4.1 The pinned holdout was reconstructed exactly

Ordering the 158 complete-feature series by first `captured_at` and taking the last 32 gives
**4,276 rows — exactly `split.holdout.rows`**. (The train/valid boundary does not reproduce:
12,968/2,082 vs the pinned 12,684/2,366, so their train/valid cut differs; the holdout, which is
what the benchmark is measured on, is recovered.)

Training a surrogate on the 126 non-holdout series with the pinned params
(`num_leaves` 31, `lr` 0.05, seed 42, `deterministic`, 96 boosting rounds) reproduces the benchmark:

| metric | surrogate | pinned (raw) | Δ |
|---|---|---|---|
| AUC | 0.8635 | 0.8640 | −0.0005 |
| Brier | 0.1500 | 0.14989 | +0.0001 |
| ECE (10-bin) | 0.0296 | 0.03075 | −0.0012 |
| log loss | 0.4487 | 0.44883 | −0.0001 |

**The pinned benchmark is verified, not merely accepted.** All calibration statements below are
computed on this faithful surrogate over the exact pinned holdout. (The pinned Platt map
`sigmoid(0.96828·margin − 0.12790)` was fitted on their train/valid split, which I could not
reproduce; I therefore segment the *raw* model, whose pooled metrics match to 4 decimals.)

### 4.2 Pooled ECE is at the low end of its own sampling distribution

| statistic | value | series-clustered 95% CI |
|---|---|---|
| pooled ECE-10 | 0.0296 | **[0.0230, 0.0489]** |
| conditional ECE (round-half × pred-decile) | 0.0334 | [0.0313, 0.0577] |

The pinned single number 0.0296/0.0307 is real but **fragile**: its own clustered upper bound is
0.049, and conditioning on round-half raises the point estimate by 13%. The headline ECE benefits
from error cancellation across segments with opposite-signed biases (rounds 13–18 bias **+0.033**
against rounds 19–24 **−0.028**). No segment is better calibrated than the pooled figure — every
segment's ECE-10 lies in 0.030–0.068 while pooled is 0.0296.

### 4.3 Segment table (pinned holdout, 4,276 rows / 32 series)

By round segment:

| segment | n | base | mean p | bias | Brier | ECE10 | AUC |
|---|---|---|---|---|---|---|---|
| r1–6 | 1,093 | .506 | .519 | −.013 | .131 | .047 | .902 |
| r7–12 | 1,268 | .532 | .526 | +.006 | .159 | .030 | .847 |
| r13–18 | 1,182 | .567 | .534 | **+.033** | .148 | .053 | .865 |
| r19–24 | 642 | .520 | .548 | **−.028** | .166 | .058 | .835 |
| OT 25+ | 91 | .571 | .565 | +.007 | .171 | .068 | .817 |

By time bin — **the benchmark's skill is concentrated in late-round snapshots**:

| bin | n | Brier | ECE10 | AUC |
|---|---|---|---|---|
| 0 (freeze-end) | 1,234 | 0.2051 | .044 | **0.736** |
| 1 | 1,219 | 0.1807 | .040 | 0.799 |
| 2 | 1,201 | 0.1121 | .047 | 0.924 |
| 3 | 622 | **0.0540** | .060 | **0.984** |

By clock: AUC 0.938 at 20–45s remaining vs **0.789 at 90s+**. By man-advantage: the
`man_advantage == 0` cell is 54% of the holdout (n=2,330) and is where the model is weakest —
AUC 0.733, Brier 0.2050.

**Read:** the pinned AUC 0.864 is a blend of a near-omniscient late-round regime (bin 3, AUC 0.984)
and a genuinely hard early-round regime (bin 0, AUC 0.736). Any instrument whose detection clock
skews late inherits the 0.98 regime and any instrument that fires early inherits the 0.74 regime.
This is the same structural risk H-MANADV1-CLOCKSTRAT pre-registered for the man-advantage lane,
and it applies to the **served model itself**.

### 4.4 The model is systematically UNDER-confident in the tails — and this replicates

Bias = realised − predicted, series-clustered CIs, measured on both the discovery split (126 series)
and the pinned holdout (32 series):

| pred bin | DISCOVERY bias [CI] | HOLDOUT bias [CI] | replicates? |
|---|---|---|---|
| 0.0–0.1 | −0.033 [−0.040, −0.025] | −0.019 [−0.034, −0.001] | yes |
| 0.1–0.5 | −0.039 … −0.062 (all significant) | +0.006 … +0.051 (none significant) | **no — sign flips** |
| 0.5–0.6 | −0.010 [−0.036, +0.018] | −0.052 [−0.100, +0.002] | no |
| 0.6–0.8 | +0.058/+0.059 (significant) | −0.007/−0.014 (n.s.) | no |
| **0.8–0.9** | **+0.051 [+0.029, +0.070]** | **+0.048 [+0.021, +0.074]** | **yes** |
| **0.9–1.0** | **+0.034 [+0.027, +0.039]** | **+0.040 [+0.030, +0.048]** | **yes** |

Aggregated tail:

| tail | split | n | series | mean p | realised | bias | clustered CI |
|---|---|---|---|---|---|---|---|
| q ≥ 0.85 | DISC | 2,949 | 126 | .9328 | .9719 | **+0.0390** | [+0.0302, +0.0465] |
| q ≥ 0.85 | HOLD | 839 | 31 | .9323 | .9678 | **+0.0355** | [+0.0224, +0.0470] |
| q ≤ 0.15 | DISC | 2,745 | 126 | .0678 | .0328 | −0.0350 | [−0.0439, −0.0253] |
| q ≤ 0.15 | HOLD | 789 | 32 | .0671 | .0494 | −0.0176 | [−0.0386, +0.0048] |

**This independently reproduces the cs2agent ADR-0038 diagnostic frozen in H-MAPENTRY1-BANDGATE1**
("q at crossings transfers calibrated, **+0.030 UNDER-confident at tau=0.85**, CI excludes 0"). My
holdout figure is +0.0355 [+0.022, +0.047]. Different reconstruction, same number.

Note the served model applies a Platt slope of 0.96828 (< 1, i.e. shrinking toward 0.5) with a
negative intercept. On a model that is already under-confident at the top, that calibration step
moves the wrong way in the tail. Not actionable on its own; recorded as a measurement.

The mid-range miscalibration (0.1–0.8) is significant on the discovery split and **fails to
replicate on the holdout with signs reversed** — that is overfitting noise, not structure. Only the
tails are real.

### 4.5 Drift in the live model-entry cells

`verdict.json` H-MODELENTRY1 cells vs the anchor cited in H-MAPENTRY1's registration text
("live-monotone 0.80/0.86/0.93 across thresholds at n~500 crossings"):

| threshold | now | n | cited anchor | drift | CI excludes anchor? |
|---|---|---|---|---|---|
| 0.65 | 0.7828 | 6,140 | 0.80 | −0.0172 | **yes** |
| 0.75 | 0.8522 | 6,018 | 0.86 | −0.0078 | no |
| 0.85 | 0.9094 | 5,337 | 0.93 | −0.0206 | **yes** |

Monotonicity across thresholds still holds. The drift is uniformly negative and small (≈ −1 to −2
pts) and is what you expect when an n~500 reading matures to n~6,000. **Flagged, not alarming.**
There is no frozen bar on these cells (`bar_n = 0`), so no bar is approached.

One structural observation: 5,337/6,140 = **86.9%** of round-sides that cross 0.65 go on to cross
0.85 in the same round. The crossing detector is not selecting a rare confident state; it is mostly
picking up rounds that resolve decisively, consistent with §4.3's late-bin concentration.

### 4.6 Feature-vector health

Of the 20 features, **5 are constant on the entire leak-filtered corpus**: `bomb_planted` (0),
`bomb_time_remaining` (0), `map_rating_diff` (0), `rating_diff` (0), `prior_round_p` (0.5).
A further 3 are redundant: `alive_ratio ≡ man_advantage / 5` (exact),
`rounds_played ≡ round_number − 1` (forced by the binding filter itself), and `awp_diff` vs
`awp_alive_diff` (ρ = 0.9998, identical on 19,323 of 19,326 rows). **Effective dimensionality is 12,
not 20.** `man_advantage` receives 0 split gain because `alive_ratio` absorbs it.

Two of the dead features (`rating_diff`, `map_rating_diff`) are team-strength priors that are
never populated — the model has **no team-quality input at all**. That is relevant to §6.1, where I
show map score does carry real team-quality information the round model cannot see.

---

## 5. Verdict progress: distance to each frozen bar, and exactly what is missing (focus d)

### 5.0 The dominant fact: every book-dependent clause has zero evidence

| cell | n | series | resolved | win rate | quoted | paper entries | maker quotes |
|---|---|---|---|---|---|---|---|
| H-ECONBUY1:≥10k | 2,012 | 195 | 2,011 | 0.8434 | **0** | 0 | 0 |
| H-ECONBUY1:4-10k | 280 | 141 | 279 | 0.6918 | **0** | 0 | 0 |
| H-ECONBUY1:<4k | 6 | 6 | 6 | 0.8333 | **0** | 0 | 0 |
| H-MANADV1 | 861 | 184 | 859 | 0.9139 | **0** | 0 | 0 |
| H-PISTOLCARRY1 | 712 | 198 | 711 | 0.8031 | **0** | 0 | 0 |
| H-MODELENTRY1 (3 cells) | 17,495 | 199 | 17,470 | — | **0** | 0 | 0 |
| **TOTAL** | **21,366** | | | | **0** | **0** | **0** |

**3,871 instances across up to 198 series sit under a book-dependent bar and not one has ever
carried a quote.** This is not a capture failure — H-MAPENTRY1's registration (IDX 8) states the
cause plainly: *"Polymarket US … lists esports_match_winner and esports_map_winner_N books but NO
round books, so all four round-settling registrations are unquotable there."* The shadow engine has
now confirmed that empirically at scale.

Therefore, for **all three** open round-settling hypotheses, clause (i) is the only clause that can
accumulate evidence, and clauses (ii) and (iii) are **structurally unreachable on the only
executable venue**. As written, none of these lanes can reach a pre-registered PASS. That is a
registry-process problem, not an analysis problem, and it is the single most decision-relevant fact
in this run.

`capture_qa.txt` is otherwise clean and does **not** explain the zero: grid/pm/shadow all OK,
`grid_errors_today` 0, `pm_ws_errors_today` 4, `grid_stale_alarms_today` 8 (both low and
non-blocking), disk 685 GiB.

### 5.1 H-MANADV1 (bar_n = 80; n = 861 across 184 series — instance bar met 10.8×)

| clause | bar | status | what is missing |
|---|---|---|---|
| (i) trailing-team resolution < 0.85 falsifies | 0.85 | **0.9139**, CI [0.8932, 0.9308]; margin **+0.064**, CI lower bound 0.8932 clears the bar | **the CLOCKSTRAT stratification.** `verdict.json` reports one pooled number; the frozen amendment requires bar (i) to *also* be read in clock buckets [20,45)/[45,75)/[75,90)/[90,∞) and declares a pooled PASS "PROVISIONAL, not confirmed" while any N≥30 bucket sits below 0.85. `clock_seconds` is a frozen recorded field, so this is a reporting-layer gap, not an instrumentation gap. **Corpus says this matters:** the ≥90s bucket reads 0.809 (n=89), below the bar. |
| (ii) <20% of instances ever show ask ≤ 0.88 | — | **no evidence** (quoted = 0/861) | round books on PMUS |
| (iii) mean settled edge of ask ≤0.85 entries ≤ +2 pts | — | **no evidence** (paper entries = 0) | round books on PMUS |

### 5.2 H-ECONBUY1 (bar_n = 120; pooled n = 2,298, ≥10k n = 2,012 — both bars met ≫)

| clause | bar | status | what is missing |
|---|---|---|---|
| (i) pooled < 0.79, or ≥10k bucket < 0.81 | 0.79 / 0.81 | pooled **0.8249** (n=2,298), margin **+0.035**; ≥10k **0.8434**, CI [0.8268, 0.8586], margin **+0.033**, CI lower bound clears 0.81 | nothing — this clause is measurable and currently passing |
| (ii) <10% of mismatch rounds ever quote the buyer below 0.80 | — | **no evidence** (quoted = 0/2,298) | round books on PMUS |
| (iii) mean settled edge ≤ +2 pts after fees | — | **no evidence** (paper entries = 0) | round books on PMUS |
| scope-freeze: force-buy subset < 0.66 → scope to ≥10k only | 0.66 | 4-10k cell (the force-buy proxy) reads **0.6918**, CI **[0.6353, 0.743]** — **the CI lower bound 0.635 sits BELOW the 0.66 trigger** | more force-buy instances; at n=280 this clause is genuinely undecided |

Two notes. First, the frozen text ties clause (i) to *held-out* mismatch rounds; `verdict.json`
carries no `holdout_declared` flag for these cells, so I read these as **measurements over captured
rows**, not as clause satisfaction. Second, the `<4k` cell has n=6 and is inert — the three-way
grading in the frozen design is effectively a two-way split in practice.

### 5.3 H-PISTOLCARRY1 (bar_n = 100; n = 712 across 198 series — instance bar met 7.1×) — the closest bar in the registry

| clause | bar | status | what is missing |
|---|---|---|---|
| (i) pooled conversion < 0.80 falsifies | 0.80 | **0.8031**, CI **[0.7723, 0.8307]** — margin **+0.0031**, and the CI **straddles the bar** | more instances; this is the one clause in the registry that could plausibly flip |
| (ii) loser-buy conditional gap < 5 pts (corpus: 17) | 5 pts | **not reported at all** in `verdict.json` — no conditional split by the loser's freeze-end buy | a by-loser-buy breakdown of the live cell |
| (iii) mean settled edge of ask ≤0.80 entries ≤ +2 pts, or no such quote in ≥85% of pairs | — | **no evidence** (quoted = 0/712) | round books on PMUS |

**Drift flag.** Corpus 0.8543 (n=453, 149 series) vs live captured 0.8031 (n=712, 198 series):
**−5.1 pts**, unclustered two-proportion z = 2.30, p = 0.022. Clustering would widen this, so I do
not claim significance — but the point estimate has moved *toward* the bar, and it is now 0.3 pts
above it.

**And the mix problem is real.** §2.2 showed r1→r2 converts at 0.8925 while r13→r14 converts at
0.8277 (clustered gap CI [+0.004, +0.123], excludes zero). The pooled 0.80 bar is therefore decided
partly by the detector's first-half/second-half pistol mix — structurally the same defect
H-MANADV1-CLOCKSTRAT pre-registered for the clock. `verdict.json` does not report the mix.

---

## 6. Candidate DRAFTs, ranked

All are DRAFT. None is proposed for arming; each would need its own pre-registration with a frozen
bar and an untouched held-out window before any evidence counts.

### 6.1 DRAFT-1 (highest value): serial-dependence aggregation for the map lane

**Mechanism claim.** `mapwin-race-v1` models future rounds as **iid** at
`p_fut = (a+4)/(a+b+8)`. CS2 rounds are not iid — they are strongly positively autocorrelated
through the economy chain, and this survives a team-quality control.

Evidence computed this session (5,125 consecutive round pairs across 312 maps):

- Raw: P(win r+1 | won r) = **0.6368** vs P(win r+1 | lost r) = **0.3733** — a +0.264 lift.
- With a **leave-two-out map win share** as a team-quality control (removes the mechanical
  correlation): the lift is still **+0.208 to +0.304 in every quality quintile**, and a
  series-clustered logit gives `prev` coefficient **+1.0205, z = 18.13, p = 1.8e−73**; at
  quality = 0.5 the implied residual autocorrelation is **+0.2497**.
- Excluding the r12→r13 side-switch boundary the lift *rises* to +0.281, so it is not a halftime
  artifact.

**Verified a second way, with no future-length conditioning.** On the 86 fully-observed contiguous
maps (1,667 one-step-ahead observations, using only past information):

| next-round model | log loss | Brier |
|---|---|---|
| constant 0.5 (the B0 candidate) | 0.69315 | 0.25000 |
| `p_fut = (a+4)/(a+b+8)` — **v1's assumption** | 0.67472 | 0.24089 |
| last-round outcome only (Markov) | 0.65192 | 0.22966 |
| `p_fut` + last-round outcome | **0.64795** | **0.22780** |

Series-clustered dBrier for adding the last-round state: **+0.0131 [+0.0080, +0.0185]**, excludes
zero. Clustered logit: `p_fut` coef +1.807 (z=3.33, p=0.0009), `last` coef +1.024 (z=9.42,
p=4.5e−21). **The last-round state is a stronger next-round predictor than the entire
score-extrapolation term.** (This is an in-sample fit for the challenger; direction only.)

**Why this is not the closed λ-shrinkage family.** H-MAPENTRY2's verdict closed
"dispersion-repair claims … for this formula family (lambda-shrinkage `p_fut`)". λ-shrinkage moves
the *mean* of `p_fut` toward 0.5, which compresses `P_map` toward 0.5 **uniformly — including in the
0.50–0.80 band that live capture showed was already well calibrated (A's in-band ECE 0.0592, under
the 0.08 bar)**. That is exactly why B0/B10/B20 all lost. A Markov correction **preserves the mean
and changes the autocorrelation**, so its correction is anti-symmetric about 0.5 and concentrates
where the miscalibration actually is. Computed (race-to-13 DP, 12-12→0.5 checked before terminals,
persistence gap 0.2497):

| state (a,b) | q | P_map v1 (iid) | P_map Markov | shift |
|---|---|---|---|---|
| (6,6) | 0.85 | 0.6193 | 0.6151 | **−0.004** |
| (9,9) | 0.85 | 0.6293 | 0.6343 | **+0.005** |
| (6,3) | 0.50 | 0.8954 | 0.8396 | −0.056 |
| (10,5) | 0.50 | 0.9805 | 0.9477 | −0.033 |
| (12,9) | 0.50 | 0.9456 | 0.9100 | −0.036 |
| (8,10) | 0.85 | 0.2787 | 0.3503 | +0.072 |
| (5,10) | 0.85 | 0.0311 | 0.0807 | +0.050 |

Mean shift by `P_iid` band: **−0.017 above 0.9, +0.023 below 0.5, and |shift| < 0.007 through
0.50–0.80.** It leaves the band gate's protected region alone and repairs precisely the region
H-MAPENTRY1-NOTE5 measured as broken (`P_map` 0.9–1.0 predicts 0.950, realises 0.828).

**Honest magnitude.** The correction supplies ≈1.6–3.5 pts against NOTE5's measured 12.2 pt
over-confidence in the top bucket — it explains perhaps **15–25%** of the gap, not all of it. It is
directionally right and mechanistically grounded, not a fix.

**Implementation cost: zero new instrumentation.** Inputs are unchanged `(a, b, q)`. `q` already
resolves the chain's initial state probabilistically:
`P_map = q·R_markov(a+1, b | last=W) + (1−q)·R_markov(a, b+1 | last=L)`. It is computable
retrospectively from captured mapentry rows, exactly as H-MAPENTRY2's candidates were.

**Falsification sketch (for the human to freeze, if adopted):** register as a shadow/analysis
instrument only, on a window opening at registration, with map winners from
`scripts/grid_winners.py`. At n ≥ 60 resolved maps, series-clustered paired bootstrap on the
identical observation set: the challenger **WINS only if it improves BOTH quintile-ECE and Brier vs
A with the paired dECE CI excluding zero** — the same criterion H-MAPENTRY2 used, so the two
verdicts are comparable. Pre-declare the persistence gap δ = 0.2497 as **frozen from this corpus**
(it is a training-data constant, not a free parameter) so the challenger has zero fitted degrees of
freedom against A. If it fails, the serial-dependence repair closes alongside the λ family and the
`P_map` over-confidence must be sought in the winner oracle or the OT domain instead.

### 6.2 DRAFT-2: half-stratified reading amendment for H-PISTOLCARRY1 bar (i)

**Mechanism claim.** Second-half pistol carryover is materially weaker than first-half: 0.8277
(n=267) vs 0.8925 (n=186), clustered gap CI [+0.004, +0.123]. Plausible mechanism: after the side
switch a team's second-half pistol win is worth less because the opponent enters round 14 with
carried-over map-level economy discipline and a known opponent, whereas round 2 follows a genuinely
broken economy. **The pooled 0.80 bar is therefore partly decided by the detector's r1/r13 mix** —
and clause (i) currently sits at 0.8031 with a CI straddling the bar.

**Falsification sketch:** this is a *reading* amendment of the type CLOCKSTRAT already set precedent
for, and — critically — it must be frozen while it can still change how evidence is read.
Bar (i) would **also** be read stratified by the pistol's round number (r1 vs r13). A pooled PASS
while the r13 arm with N ≥ 100 sits below 0.80 is **PROVISIONAL**; a pooled FAIL while both N ≥ 100
arms sit above 0.80 is likewise PROVISIONAL pending mix analysis. Requires no new instrumentation —
`round_number` is already recorded. If the live r1/r13 arms come back within 2 pts of each other at
N ≥ 100 each, the heterogeneity claim dies and the pooled bar stands unqualified.

### 6.3 DRAFT-3 (retracted in-session — recorded so it is not re-run)

I first measured `p_fut` against realised **future round share** and found it *anti-predictive*:
WLS slope **−0.153**, 95% CI [−0.347, +0.040], with textbook regression to the mean (p_fut 0.352 →
realised 0.529; p_fut 0.658 → realised 0.482). Taken at face value this would have contradicted
H-MAPENTRY2's verdict that A beats B0.

**It is an artifact.** That test conditions on the map having ≥6 further rounds, which is
length-biased sampling: blowouts end early, so long horizons are selected to be close maps. The
tell is in the dispersion diagnostic — the observed/iid variance ratio falls monotonically from
1.564 at N_future=6 to **0.565 at N_future=19**, i.e. *under*-dispersed at long horizons, which is
impossible for a genuine over-dispersion effect and is the signature of the selection.

Redoing it without any future conditioning (§6.1, 86 fully-observed maps) shows `p_fut` **is**
genuinely predictive (logit coef +1.807, z=3.33, p=0.0009; beats constant-0.5 on both metrics).
**H-MAPENTRY2's verdict is corroborated on independent corpus data, not contradicted.** Recorded
here so the retracted claim is not resurrected by a future session.

### 6.4 Explicit negatives — no new evidence

I probed for residual signal the served model has not already absorbed, using a discovery split
(126 series) with confirmation required on the pinned holdout (32 series), series-clustered CIs
throughout. Target population: `man_advantage == 0` (54% of the holdout, the model's weakest cell at
AUC 0.733).

| probe | discovery | holdout | read |
|---|---|---|---|
| residual vs `hp_alive_diff` (5 buckets) | all CIs span zero | all CIs span zero | **no signal** |
| residual vs `awp_alive_diff` (3 buckets) | all CIs span zero | all CIs span zero | **no signal** |
| residual vs `momentum_3` (3 buckets) | all CIs span zero | all CIs span zero | **no signal** |
| residual vs `econ_ratio` (5 buckets, all rows) | all CIs span zero | all CIs span zero | **no signal** |

**No new evidence** for any HP-, AWP-, momentum- or economy-ratio interaction beyond what the served
model already exploits. These directions are exhausted; the model has absorbed them. This is why
DRAFT-1 targets the **aggregation** rather than the round model — the residual is in `R(·)`, not
in `q`.

That conclusion is reinforced by the sign decomposition: `q` is **+3.5 pts UNDER-confident** at
q ≥ 0.85 on the pinned holdout (§4.4), while `P_map` was measured **−12.2 pts OVER-confident** at
0.9–1.0 (NOTE5). Since the aggregation takes an under-confident input and produces an
over-confident output, essentially all of the error is introduced by `R(·)`. **`q` is exonerated
again, on a fresh reconstruction** — corroborating BANDGATE1's "score-only null `R(a,b)` reproduces
the error" claim.

---

## 7. Summary of flags for the human

1. **Structural, lane-wide (highest priority).** 3,871 instances / 198 series under book-dependent
   bars, **0 quotes ever**. Clauses (ii) and (iii) of H-MANADV1, H-ECONBUY1 and H-PISTOLCARRY1
   cannot resolve on PMUS because it lists no round books. As written these lanes cannot reach a
   PASS. Needs a registry decision (venue amendment, or restructure the bars around the map-lane
   bridge), not more capture.
2. **Closest bar.** H-PISTOLCARRY1 clause (i) at 0.8031 vs 0.80, CI [0.7723, 0.8307] straddling,
   drifted −5.1 pts from corpus, with a significant unreported r1/r13 mix effect. → DRAFT-2.
3. **Undecided sub-clause.** H-ECONBUY1's force-buy scope-freeze: 4-10k cell 0.6918, CI lower bound
   0.6353 **below** the 0.66 trigger.
4. **Reporting gap.** H-MANADV1's frozen CLOCKSTRAT stratification is not present in
   `verdict.json`; the corpus ≥90s bucket (0.809) sits below the bar, so the stratification is not
   cosmetic.
5. **Data-integrity annotation.** Equipment features are time-varying within a round; pooling bins
   inflates the economy edge by +4.9 pts (0.870 vs 0.821 at freeze-end). §3.
6. **Anchor provenance.** H-ECONBUY1's corpus anchors do not reproduce (pooled n 1,889 vs 1,393;
   force-buy arm unrecoverable) — recommend UNAUDITED status, matching H-MANADV1. Bars unthreatened.
7. **Transcription slip.** CLOCKSTRAT's clock coefficient reads −0.0247; recomputes to −0.0237 with
   z and p matching exactly.
8. **Benchmark caveat.** The pinned holdout reproduces exactly and the benchmark is verified, but
   its ECE 0.0296 has a clustered upper bound of 0.049, no segment beats it, and its AUC blends a
   0.98-AUC late-round regime with a 0.74-AUC freeze-end regime.
9. **Model hygiene.** 5 of 20 features are constant and 3 more redundant; effective dimensionality
   12. The model has no team-quality input at all.
10. **Capture health.** Clean. No integrity concern from `capture_qa.txt`.

## 8. Reproduction

Every figure above was computed this session from the mounted files with pandas / numpy /
statsmodels / scikit-learn / lightgbm. Load `training_rounds.jsonl`, apply the union leak filter,
derive the time bin from the `:b<n>` row-key suffix, and reconstruct the holdout as the last 32
series ordered by first `captured_at`. Surrogate model: LightGBM with the pinned params at 96
boosting rounds on the 126 non-holdout series (reproduces pinned raw AUC/Brier/ECE/log-loss to
≤0.0012). Bootstraps resample series with 800–3,000 replicates.
