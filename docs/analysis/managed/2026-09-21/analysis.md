# gridbot deep-analysis — DRAFT for the human and the registry process

**Run date context:** evidence mounted at `/mnt/session/uploads/workspace/evidence/`
(the `/workspace/evidence/` path was empty in this sandbox; files resolved under the
uploads mirror). Latest registry row is `H-CROSSVENUE1-PRENOTE1`, 2026-09-08T13:30Z.

**Status of this document:** DRAFT analysis only. No orders, no registry edits, no
credentials. Every number below was computed this session from the mounted files;
where I could not compute something I say "no new evidence" or name it as missing.
Nothing here is a verdict.

---

## 0. Headline reads

1. **All three open round lanes sit comfortably above their clause-(i) bars, and none of
   them can render a verdict.** There is no pre-registered held-out window for
   `H-MANADV1` / `H-ECONBUY1` / `H-PISTOLCARRY1` anywhere in the registry. Their bars
   say "FALSIFIED on >=80 / >=120 / >=100 **held-out** instances"; the 9,429 instances in
   `verdict.json` are captured rows. This is the same structural fault
   `H-ROUNDLANES-RESTRUCTURE1` fixed for clauses (ii)/(iii) — it still binds on clause (i).
2. **The served model under-prices the pistol-carry state by +5.9 points** (bin-0,
   n=453, series-clustered CI [+0.026, +0.091]), the two other round-lane states are
   null controls, and the gap closes to +0.019 when one binary feature is added. This is
   a mechanism-confirmed model defect, not a dredged cell. Ranked DRAFT-1 below.
3. **Every confidence interval in `verdict.json`'s `cells` block is a row-level Wilson
   interval**, verified exactly on all 8 cells. The `mapentry` block pins
   "All intervals resample SERIES, not rows"; the cells block does not follow it.
   This does not currently flip any read, but it understates `H-MANADV1`'s interval ~1.7x.
4. **Two briefing/evidence contradictions** on `mapentry` (section 5.4). `holdout_declared`
   is `true` in the file, not false; and the `FALSIFIED_ON_CAPTURED_DATA` headline is
   contradicted by the registry's own holdout render.

---

## 1. Data integrity — the binding filter reproduces row-exact

`CORPUS-F1-LEAKFILTER` requires dropping rows failing `rounds_played + 1 == round_number`
against **both** `features.round_number` and the top-level `round_number`.

| quantity | registry / `model_metrics.json` | recomputed | match |
|---|---|---|---|
| total rows | 22,273 | 22,273 | exact |
| leak-dropped | 2,898 (13.0%) | 2,898 (13.01%) | exact |
| bin-3 leak share | 2,581 / 5,429 = 47.54% | 2,581 / 5,429 = 47.54% | exact |
| missing-key dropped | 49 (of 67 rows) | 49 (of 67 rows) | exact |
| kept rows | 19,326 | 19,326 | exact |

Kept corpus: 19,326 rows / 158 series / 331 maps / 5,598 rounds; label mean 0.5267.

**The union ("BOTH") wording is load-bearing.** 2,789 rows fail both checks, but **102
fail only the top-level check and 7 only the feature check**. A single-sided filter would
leak 109 self-labelling rows. Worth keeping explicit in any re-implementation.

### 1.1 Structural feature audit — only 13 of 20 features carry information

| class | features | count |
|---|---|---|
| zero variance on every kept row | `bomb_planted`, `bomb_time_remaining`, `map_rating_diff`, `prior_round_p`, `rating_diff` | 5 |
| exactly redundant **post-filter** | `rounds_played` (= `round_number - 1` by the filter's own definition) | 1 |
| exactly collinear | `man_advantage` (= `5 x alive_ratio`) | 1 |
| informative | remainder | **13** |

Two consequences, both verified:

- `bomb_planted` is 0 on all 22,273 rows **pre-filter as well**, so the served model has
  no representation of post-plant state at all. That is a capture gap, not a filter effect.
- The leak filter makes `rounds_played` information-free *by construction*. LightGBM gain
  confirms: `rounds_played` and `man_advantage` both receive **exactly zero gain**.
- `awp_diff` and `awp_alive_diff` are identical on 99.98% of rows (corr 0.9998).

Gain concentration on the reproduced model: `equip_value_diff` 55.4%, `alive_ratio` 12.0%,
`hp_alive_diff` 11.8%, `econ_ratio` 6.3%. Four features carry 85% of gain.

### 1.2 Unit-scale correction: `equip_value_diff` is in **$20,000**, not $10,000

This matters because the registry text speaks in dollars ("loadout>=13400", ">=10k equip
gap") while the feature is normalised, and the natural reading (unit = $10k, so
">=10k" means `>= 1.0`) is **wrong**. Three independent confirmations:

1. **Hard ceiling.** Among the 4,215 rows where *neither* team full-buys, both loadouts
   are `< 13400` by definition, so `|gap| < 13400/unit`. Observed max is **0.6625**, with
   the 99.9th percentile at 0.652 pressing against it and **zero** violations. The $20k
   ceiling is 0.670 (observed/ceiling = 0.989); the $10k ceiling would be 1.340, leaving
   the observed maximum at exactly half the bound for no reason.
2. **Bucket shares.** Cutting at `gap >= 0.5` ($10k under a $20k unit) puts **87.7%** of
   bin-0 one-team-full-buy rounds in the `>=10k` bucket. The live cells in `verdict.json`
   have `>=10k` at **88.0%** (4,865/5,527) and `4-10k` at 11.7% vs corpus 11.5%. Under a
   $10k unit the corpus share would be 37.6% — irreconcilable with the live instrument.
3. **Frozen anchor share.** The frozen text has 1,234/1,393 = 88.6% in the `>=10k` bucket,
   matching (2) and not (the $10k reading).

All ECONBUY numbers in section 2 use the corrected $20k scale. *Recommendation: pin the
unit in a registry note — it is currently implicit and one misreading silently re-cuts the
lane's dose-response.*

---

## 2. Headline corpus edges recomputed, and drift vs the frozen anchors

Series-clustered bootstrap (resample series, 4,000 draws, seed 17) alongside Wilson.

### 2.1 H-PISTOLCARRY1 — anchors reproduce essentially exactly

| anchor | frozen | recomputed | delta |
|---|---|---|---|
| pooled conversion | 0.859 (402/468) | **0.8543** (387/453), Wilson [0.8188,0.8838], seriesBoot [0.8216,0.8863] | -0.005 |
| loser stays below full buy | 0.874, N=388 | **0.8716**, N=405 | -0.002 |
| loser force/full-buys | 0.702, N=47 | **0.7083**, N=48 | +0.006 |
| loser full-buy share | 10.8% | **10.6%** | -0.2pt |
| winner full-buy share | 92.2% | **91.6%** | -0.6pt |
| conditional gap | 17 pts | **16.3 pts** | -0.7pt |
| generic carryover | 0.611, N=4,880 | **0.6097**, N=4,735 | -0.001 |

This lane's corpus basis is sound. Design effect **1.00-1.03** (pair-level population, ~3
pairs/series), so its Wilson interval is approximately valid.

### 2.2 H-ECONBUY1 — structure reproduces, level runs ~1.1pt low

Population: regulation (r<=24), non-pistol, exactly one team full-buys, read at bin 0
(freeze-end, as the hypothesis specifies).

| anchor | frozen | recomputed (bin 0) |
|---|---|---|
| pooled | 0.832, N=1,393 | **0.8213**, N=1,880, seriesBoot [0.8025,0.8400] |
| `>=10k` gap | 0.849, N=1,234 | **0.8368**, N=1,648, seriesBoot [0.8191,0.8549] |
| force-buy (gap `<10k`) | 0.710, N=155 | **0.7112**, N=232 |
| behind / tied / ahead | .825 / .820 / .837 | **.8174 / .8000 / .8265** |

Dose-response is monotone and intact: `<4k` 0.600 (n=15) -> `4-10k` 0.7189 (n=217) ->
`>=10k` 0.8368 (n=1,648). Scoreline invariance holds (spread 2.7pt across strata, all
overlapping). Levels sit ~1.1-1.2pt below the frozen anchors on a 35% larger N — small,
and not a threat to bars at 0.79/0.81.

**Caution on reading style:** the all-bin row-level figure is 0.8697, 4.8 points above the
bin-0 figure, because later time-bins condition on in-round state. Design effect rises
from **1.02** (bin 0) to **3.31** (all bins). The bin-0 reading is the one that matches the
hypothesis text ("at freeze-end") and the live instrument's per-round cadence.

### 2.3 H-MANADV1 — the frozen anchor still does not reproduce; CLOCKSTRAT does

Literal predicate (map-score-trailing side, `>=2`-man alive advantage, clock `>=20s`):

- **0.9405, N=1,310 rows / 1,008 rounds**, Wilson [0.9263,0.9520], seriesBoot [0.9057,0.9638].
- `H-MANADV1-CLOCKSTRAT` recorded 0.941 / N=1,311 rows / 1,009 rounds. **I reproduce it to
  within one row** — independent confirmation of that amendment's recompute.
- The frozen anchor (0.931, N=606) is still not reproduced: my N is 2.16x larger.

I searched a **756-variant** predicate grid (dose, clock floor, clock ceiling, lead
threshold, dedup mode) for anything landing at N~606 with rate ~0.931. **13 variants
(1.7%) match**, but none is the literal registered predicate — all require a stricter clock
floor (>=40s), a stricter lead (>=3), or round-level dedup. So the anchor's exact predicate
**remains unrecovered**; this sharpens CLOCKSTRAT's UNAUDITED flag rather than resolving it.
As CLOCKSTRAT concluded, the bar is not threatened: every variant lands 0.92-0.95 against a
0.85 bar.

**Clock stratification (the CLOCKSTRAT amendment), recomputed:**

| clock bucket | n | rate | Wilson | seriesBoot |
|---|---|---|---|---|
| [20,45) | 596 | 0.9664 | [0.9487,0.9782] | [0.9399,0.9851] |
| [45,75) | 413 | 0.9395 | [0.9122,0.9587] | [0.9086,0.9655] |
| [75,90) | 212 | 0.9245 | [0.8809,0.9530] | [0.8818,0.9621] |
| **[90,inf)** | **89** | **0.8090** | **[0.7152,0.8772]** | **[0.6875,0.9091]** |

Monotone decline confirmed. Note a discrepancy inside the registry itself: CLOCKSTRAT's
text says the `>=90s` bucket is **0.741**, while `H-ROUNDLANES-RESTRUCTURE1` says **0.809**.
My row-level recompute gives **0.8090**, matching RESTRUCTURE1 exactly. CLOCKSTRAT's 0.741
does not reproduce under any cut I tried; RESTRUCTURE1 appears to have silently corrected it.
*Recommendation: a one-line registry note reconciling 0.741 vs 0.809.*

This matters operationally: the `>=90s` bucket is **below the 0.85 bar** with n=89 (>=30),
so under CLOCKSTRAT's pre-committed reading a pooled PASS would be **PROVISIONAL**, not
confirmed, if the live detection-clock mix carries meaningful `>=90s` weight.
**`verdict.json` does not expose `clock_seconds`, so that reading cannot be rendered from
the evidence provided.** See section 5.1.

Per-bin first-qualifying rates also reproduce CLOCKSTRAT (mine vs theirs): b0 0.7955/0.800,
b1 0.8760/0.881, b2 0.9543/0.955, b3 0.9818/0.982.

---

## 3. Calibration and drift vs the pinned benchmark

### 3.1 Benchmark reproduction

I recovered the pinned temporal split by ordering series on first `captured_at`:
the **holdout is exact** (last 32 series = 4,276 rows). The train/valid boundary is not
(mine 12,968/2,082 vs pinned 12,684/2,366 — 284 rows differ; no single series explains it,
so their 107/19 cut uses a tie-break or randomisation I could not recover).

Retraining the stated config (LightGBM, 31 leaves, lr 0.05, seed 42, 96 trees) plus the
pinned Platt map `sigmoid(0.96828*margin - 0.12790)`:

| metric | pinned (calibrated) | my surrogate | delta |
|---|---|---|---|
| Brier | 0.1501 | 0.1517 | +0.0016 |
| ECE-10 | 0.0296 | 0.0314 | +0.0018 |
| AUC | 0.8640 | 0.8615 | -0.0025 |

Close enough to use as a **labelled surrogate**; it is not the served artifact, and every
segment number below inherits that caveat.

### 3.2 The pinned holdout cannot support segment-level calibration claims

This is the main answer to focus area (c), and it is a **negative** result that I nearly
got wrong.

The surrogate shows a large miscalibration in rounds 12-17: realized CT rate 0.594 vs mean
q 0.516, **bias +0.0778 with a series-clustered CI of [+0.0495, +0.1068] that excludes
zero**. Taken at face value that is a 7.8-point segment defect.

**It does not survive verification.** Three independent checks:

1. **Full corpus, round-level.** r12-17 CT win rate is **0.5376** [0.5053, 0.5681] across
   1,611 rounds / 158 series — unremarkable. Train+valid is 0.5218; the holdout is 0.5948.
   The effect lives entirely in the 32 holdout series.
2. **Paired within-map, half vs half.** Across 250 maps, 2nd-half minus 1st-half CT win
   rate is **-0.0050** [-0.0458, +0.0348]. No side/half effect.
3. **Paired within-map, r13-17 vs r18-24.** Across 165 maps, **-0.0072** [-0.0647, +0.0533].
   No early-second-half effect.

So the holdout's r12-17 base rate is a **sampling artifact of 31 series**, and a
series-clustered bootstrap over 31 clusters did not protect against it (bootstrap
under-coverage at low cluster counts).

**Fragility, quantified.** Leave-one-series-out over the 32 holdout series:

| metric | pooled | LOO range | spread |
|---|---|---|---|
| Brier | 0.1517 | [0.1501, 0.1530] | 1.9% of pooled |
| AUC | 0.8615 | [0.8590, 0.8646] | small |
| **ECE-10** | 0.0314 | [0.0278, 0.0351] | **0.0074 = 23% of pooled; one series moves it 13% of the pinned 0.0296 headline** |

**Reading I recommend:** the pinned holdout's **pooled Brier and AUC are trustworthy; its
ECE is noisy; and no segment-level calibration claim should be made on it at all.**
Segment cell sizes are 231-375 rounds across only 31 series. Any future calibration-by-
segment instrument needs a materially larger holdout, or it will manufacture findings like
the one above.

### 3.3 Where the model's skill actually lives

| segment | n | Brier | AUC | note |
|---|---|---|---|---|
| bin 0 (freeze-end) | 1,234 | 0.2072 | 0.730 | base-rate Brier is 0.2488 — skill only +0.167 |
| bin 1 | 1,219 | 0.1821 | 0.799 | |
| bin 2 | 1,201 | 0.1139 | 0.924 | |
| bin 3 | 622 | 0.0551 | 0.984 | |
| `\|ma\|>=2` | 936 | 0.0183 | 0.993 | man advantage fully absorbed |
| `\|ma\|=0` | 2,330 | 0.2073 | 0.728 | |

Two implications. (i) The model is **nearly uninformative at freeze-end**, which is exactly
where `H-ECONBUY1` and `H-PISTOLCARRY1` operate — so those lanes are not redundant with
`H-MODELENTRY1`, they cover the model's weakest regime. (ii) The model fully absorbs man
advantage (`|ma|>=2` Brier 0.018), so `H-MANADV1`'s claim must be a **book** mispricing
claim, never a model-edge claim. Confirmed directly: the model's residual on the MANADV
state is **-0.0049** [-0.0396, +0.0183] — dead null.

### 3.4 Under-confidence at high q — independent corroboration of BANDGATE1

Side-oriented reliability on the pinned holdout:

| cut | n | E[q] | realized | seriesBoot | gap |
|---|---|---|---|---|---|
| q >= 0.65 | 2,761 | 0.8528 | 0.8707 | [0.8574,0.8853] | **+0.0179** |
| q >= 0.75 | 2,212 | 0.8904 | 0.9150 | [0.9018,0.9285] | **+0.0246** |
| q >= 0.85 | 1,529 | 0.9304 | 0.9595 | [0.9444,0.9746] | **+0.0291** |

`H-MAPENTRY1-BANDGATE1` recorded "q at crossings transfers calibrated, **+0.030**
under-confident at tau=0.85, CI excludes 0". I get **+0.0291** at tau=0.85 on an
independently retrained surrogate. That registry number replicates.

### 3.5 A false drift alarm I am explicitly *not* raising

The naive comparison — live `H-MODELENTRY1` cell win rates (0.7861 / 0.8492 / 0.9093)
against holdout "all rows with q >= thr" (0.8707 / 0.9150 / 0.9595) — looks like a 5-8
point live shortfall. **That comparator is wrong.** The cells are defined as *first upward
crossings*, and I replicated that geometry on the holdout: 0.8515 / 0.9012 / 0.9578.

Still different, but for a structural reason: the corpus has only **4 time-bins per
round**, so a "first crossing" overshoots the threshold badly (at thr 0.65 the median q at
crossing is **0.849**, not ~0.67). The live engine polls inside a 13-27s GRID lead and its
crossings land far closer to the threshold. **The corpus therefore cannot benchmark the
live MODELENTRY cells at all**, and no drift conclusion should be drawn from that
comparison in either direction.

### 3.6 A real drift I *am* raising

The `H-MAPENTRY1` registration cites `H-MODELENTRY1` as "live-monotone **0.80/0.86/0.93**
across thresholds at n~500 crossings". At ~30x the sample:

| cell | anchor | now | resolved n | Wilson | anchor inside CI |
|---|---|---|---|---|---|
| thr 0.65 | 0.80 | 0.7861 | 15,370 | [0.7795,0.7925] | **NO** |
| thr 0.75 | 0.86 | 0.8492 | 15,070 | [0.8434,0.8548] | **NO** |
| thr 0.85 | 0.93 | 0.9093 | 13,486 | [0.9043,0.9140] | **NO** |

All three moved **down**, by 1.1-2.1 points, consistently. I checked whether clustering
could explain it: the design effect of a *crossing* population is **0.68-0.79** (sub-unity,
because crossings are spread thinly across many series), so the Wilson interval is if
anything conservative here. The drift is real but modest; monotonicity across thresholds —
the property the anchors were cited for — **holds**.

---

## 4. Candidate DRAFTs (ranked)

### Multiplicity discipline first — the honest negative result

I ran a systematic two-way interaction scan over the 13 informative features (bucketed),
using **out-of-fold** predictions (5-fold GroupKFold by series, same config, re-Platted so
residuals are globally centred; OOF Brier 0.1575 / ECE 0.0215 / AUC 0.8485).

**789 cells tested at n>=200. 12 survived |mean residual| >= 0.035 with a series-clustered
CI excluding zero. Approximately 39 false positives are expected by chance at alpha=0.05.**

**Conclusion: the scan found nothing. The served model has already absorbed the broad
two-way structure available in these features.** I report this as a negative result and
decline to promote any cell on scan evidence alone. The one candidate I do promote below
is promoted because it has a **pre-registered mechanism** (from 2026-08-25), not because it
topped a scan.

---

### DRAFT-1 — The served model systematically under-prices the pistol-carry state

**Label: DRAFT. Not registered, not proposed for arming, no PnL claim.**

**Claim.** `roundwin-v1` under-forecasts the pistol winner's round-2/14 win probability by
~5-6 points at freeze-end.

**Evidence (bin 0, oriented to the true round-1/13 winner, series-clustered CI):**

| population | n | model q | realized | gap | CI |
|---|---|---|---|---|---|
| r2 + r14 | 453 | 0.7955 | 0.8543 | **+0.0588** | [+0.0260, +0.0908] |
| round 2 only | 186 | 0.8180 | 0.8871 | +0.0691 | [+0.0217, +0.1152] |
| round 14 only | 267 | 0.7799 | 0.8315 | +0.0516 | [+0.0065, +0.0928] |
| loser does not full-buy | 405 | 0.8197 | 0.8716 | +0.0519 | [+0.0207, +0.0807] |
| loser full-buys | 48 | 0.5917 | 0.7083 | +0.1166 | [-0.0466, +0.2743] |

**Controls are null — this is the key discriminator against a generic "model is
under-confident" story:**

| control state | n | gap | CI |
|---|---|---|---|
| `H-ECONBUY1` state (buyer-oriented, bin 0) | 1,880 | **-0.0103** | [-0.0288, +0.0078] |
| `H-ECONBUY1` `>=10k` bucket | 1,648 | -0.0129 | [-0.0303, +0.0046] |
| `H-MANADV1` state | 1,310 | -0.0049 | [-0.0396, +0.0183] |

The model under-prices *only* the pistol-carry state, not the other two registered
round-lane states.

**Mechanism claim.** The feature set contains **no representation of "won the immediately
preceding pistol round."** The state reaches the model only through `equip_value_diff`,
`econ_ratio`, `momentum_3` and `score_lead`. At round 2/14 the *dollar* gap understates the
*tactical* gap: armour + SMG versus pistols is a far larger real advantage than the same
dollar difference in a mid-game round, where the poorer side can still trade and save.

**Mechanism verified by ablation (second method).** Adding a single ternary feature
(`+1` CT won the preceding pistol, `-1` T did, `0` otherwise) and re-running the identical
OOF pipeline:

| model | OOF Brier | OOF AUC | gap in the pistol-carry cell |
|---|---|---|---|
| 20 features (served) | 0.1575 | 0.8485 | +0.0588 [+0.0260, +0.0909] |
| + `pistol_carry` | **0.1572** | **0.8489** | **+0.0186 [-0.0134, +0.0491]** |

The gap closes by 68% and its CI crosses zero. The defect is a **missing feature**, not
noise. Global gain is small because the cell is 2.3% of rows — which is precisely why a
pooled-metric model selection would never have caught it.

**Consistency across partitions (honest limits):** era A (first 79 series) +0.0678
[+0.0262,+0.1080]; era B (last 79) +0.0495 [-0.0002,+0.0986]; pinned 32-series holdout
+0.0339 [-0.0335,+0.1021], n=99. **Sign is consistent in all three; significance is not.**
The effect may be attenuating, or the later partitions are simply underpowered. This is
in-corpus discovery and is **not established out-of-sample.**

**Why it is decision-relevant.** `H-MODELENTRY1` gates entry on q crossing thresholds. At
thr 0.85 the served model fires on only **48.1%** of these states, and the states it skips
realize **0.7532** (n=235). With the feature added, firing rises to 56.5%. The lane is
systematically under-entering exactly where `H-PISTOLCARRY1` — pre-registered 2026-08-25 on
the same economy mechanism — says the edge is.

**Falsification sketch (for the human to pre-register, not applied here).**
Freeze the feature definition and the served-artifact swap rule *before* reading data. On a
pre-registered temporal holdout of newly-mapped series, at n >= 250 resolved round-2/14
pistol-winner instances at bin 0, series-clustered bootstrap:
FALSIFIED if the served model's mean residual in that cell has a 95% CI containing zero, **or**
if the augmented model's OOF Brier fails to improve on the served model's on the same rows.
Note the artifact-swap constraint in `H-MODELENTRY1-CELLS`: any model change requires its own
registry amendment, and this would retire the pinned `roundwin-v1` benchmark.

---

### DRAFT-2 — Feature hygiene on the served artifact

**Label: DRAFT. Housekeeping, not an edge claim.**

7 of 20 features carry zero information (section 1.1): 5 constant, `rounds_played`
redundant by the filter's own definition, `man_advantage` exactly collinear with
`alive_ratio`. Both zero-information features that reach the tree receive exactly zero
gain, so **removing them changes nothing numerically** — the value is that the feature
vector stops advertising capabilities the model does not have. `bomb_planted` /
`bomb_time_remaining` being all-zero pre-filter is a **capture gap**, and post-plant state
is plausibly the single largest missing in-round signal. Recording it is a capture
requirement, not a strategy (`H-MAPENTRY1-NOTE10` corollary).

**Falsification sketch:** capture post-plant state; on a pre-registered holdout, FALSIFIED
if adding it does not improve OOF Brier on post-plant rows with a series-clustered CI
excluding zero.

---

### DRAFT-3 — AWP-holder with a small HP deficit appears over-priced

**Label: DRAFT, WEAK — offered only so it is on the record, with its multiplicity caveat
attached.**

Cell `hp_alive_diff in [-0.15,-0.03)` and `awp_alive_diff > 0`: n=379 / 123 series, model
0.3677, realized 0.3008, residual **-0.0669** [-0.1146, -0.0197]; same sign in both eras.
Candidate mechanism: the model over-values a surviving AWP when its holder's side is losing
the HP trade, because AWP presence and HP are entered additively and an AWP in a losing
trade is worth much less than one in a winning trade.

**This is one of 12 survivors in a 789-cell scan where ~39 false positives are expected.
It has no pre-registered mechanism. I do not recommend registering it** without an
independent, pre-registered replication on data not used here. Listed below DRAFT-1 and
DRAFT-2 deliberately.

---

## 5. Verdict progress — exactly what is still missing per frozen bar

### 5.0 The fact that dominates every lane

Across **all 8 cells: n = 53,414, resolved = 53,346, `quoted` = 0, `paper_entries` = 0,
`maker_quotes` = 0, `maker_fills` = 0.** There is **no PnL evidence of any kind anywhere in
this evidence set.** Every edge/fill clause in every lane is at exactly zero progress.

For the three round lanes specifically: 9,429 instances / up to 454 series, `quoted` = 0.
`H-ROUNDLANES-RESTRUCTURE1` recorded this at 3,871 instances / up to 198 series. The
venue-gating evidence base has grown **2.4x in instances and 2.3x in series and is still
exactly zero** — that amendment's structural claim (PMUS lists no round books) is now
confirmed at scale rather than merely asserted.

### 5.1 H-MANADV1 — clause (i) clears; verdict unreachable

- Live: 0.9105 (2,003/2,200), reported Wilson [0.8978, 0.9217]. Bar: `<0.85` falsifies. Margin **+0.0605**.
- Reported `bar_n` = 80, have 2,200 — **n met**.
- **Clustering correction:** corpus DEFF for this row-level population is **5.2**
  (analytic 5.14, bootstrap 5.22 — two methods agreeing). The live cell has 5.2
  instances/series vs the corpus's 8.45, so DEFF ~3 is the defensible estimate.
  Adjusted interval **[0.8898, 0.9312]** — still clears 0.85. **The CI defect does not flip
  this read.**

**Missing:**
1. **A declared pre-registered holdout.** The bar reads "on >=80 **held-out** state
   instances". No registry row declares one for this lane. `H-MAPENTRY1-BOOKPIN1` did this
   for mapentry; the round lanes never got the equivalent. Until one exists, 0.9105 is a
   captured-data measurement, exactly as `H-ROUNDLANES-RESTRUCTURE1` states
   ("NO captured (i) measurement is a confirmed PASS").
2. **Clock stratification of live instances.** `H-MANADV1-CLOCKSTRAT` pre-committed that
   bar (i) "will ALSO be read stratified by the instance's recorded `clock_seconds`" in
   [20,45)/[45,75)/[75,90)/[90,inf), and that a pooled PASS with any n>=30 bucket below
   0.85 is **PROVISIONAL**. `verdict.json` reports no clock breakdown, so **the
   pre-committed reading cannot be rendered from this evidence.** The corpus says the
   `>=90s` bucket runs **0.8090** — below the bar. This is the single cheapest missing
   field in the whole evidence set: `clock_seconds` is already a frozen recorded field.
3. Fill-PnL clause: VENUE_GATED / DEFERRED_OPEN. Needs a round-book venue.

### 5.2 H-ECONBUY1 — clause (i) clears on both sub-bars; verdict unreachable

- Pooled (derived from the three cells): 0.8265 (4,564/5,522), Wilson [0.8163, 0.8363]. Bar `<0.79`. Margin **+0.0365**.
- `>=10k`: 0.8432 (4,099/4,861), [0.8328, 0.8532]. Bar `<0.81`. Margin **+0.0332**.
- `bar_n` 120 pooled / 60 at `>=10k` — both met many times over.
- **Scope-freeze trigger** ("held-out force-buy subset < 0.66 -> scope freezes to `>=10k`
  only"): force-buy = gap `<$10k` = the `<4k` + `4-10k` cells = **0.7035** (465/661),
  Wilson [0.6676, 0.7370]. Margin **+0.0435** above 0.66 — **not triggered**, and the CI
  lower bound clears it.
- DEFF for this population is **1.02** at the corpus's bin-0 cadence (10.5 instances/series,
  matching the live 10.8), so the Wilson intervals here are approximately valid.

**Missing:** (1) a declared pre-registered holdout, same as 5.1; (2) the fill-PnL clause,
venue-gated. Note the `<4k` cell is at n=17 and will likely never power up — it is 0.3% of
the population; the registry should expect that bucket to stay descriptive forever.

### 5.3 H-PISTOLCARRY1 — clause (i) clears, but this is the lane closest to its bar

- Live: 0.8227 (1,397/1,698), Wilson [0.8038, 0.8402]. Bar `<0.80`. Margin **+0.0227**.
- DEFF ~1.0-1.05 (pair-level); adjusted interval **[0.8041, 0.8413]** — lower bound clears
  0.80 by **0.004**. Genuinely marginal, but on the right side.

**Drift, and it is favourable.** `H-ROUNDLANES-RESTRUCTURE1` (2026-08-31) recorded this cell
at **0.8031** and called it out as "the one that could still flip" with a straddling CI.
It now reads **0.8227** and its interval excludes 0.80. Reconstructing the increment: the
ECONBUY pooled cell grew 2,298 -> 5,522 (2.40x) over the same period; **assuming**
PISTOLCARRY scaled similarly (n ~707 then), the incremental ~991 instances ran at ~0.837 —
close to the corpus anchor of 0.8543 and well above the bar. *That reconstruction is
assumption-laden and clearly labelled as such; `verdict.json` does not carry historical n,
so it cannot be computed directly.* The safe statement: **the lane has moved away from its
bar, not toward it.**

Note the live cell (0.8227) still runs ~3.2 points below the corpus anchor (0.8543), and
`H-MANADV1` likewise runs ~3.0 points below its corpus recompute (0.9105 vs 0.9405), while
ECONBUY matches. Both CIs overlap their corpus counterparts, so this is not established
drift — but two of three lanes reading below corpus in the same direction is worth watching.

**Missing:** (1) a declared pre-registered holdout; (2) the loser-buy **conditional gap**
qualifier, which RESTRUCTURE1 retained as a clause-(i) reading qualifier ("PISTOLCARRY
drift/mix flags") but which `verdict.json` does not report at all — the live conditional
split is unavailable. Corpus says 16.3 points (0.8716 vs 0.7083) against the old 5-point
floor; (3) fill-PnL clause, venue-gated.

### 5.4 H-MAPENTRY1 — two contradictions to resolve on the record

The lane is **CLOSED as a live candidate** (`H-MAPENTRY1-CLOSE1`, 2026-09-07, user-directed),
explicitly **not** a falsification. Three things in the evidence need flagging:

1. **The kickoff briefing says `holdout_declared` is false. The file says `true`**
   (`mapentry.holdout_declared = true`, `holdout.n = 1084` against a bar of 150, 40 series,
   start 2026-08-31T19:53:46Z). The registry agrees with the file:
   `H-MAPENTRY1-HOLDOUT1` rendered that holdout on 2026-09-04. **I am treating the file and
   the registry as authoritative over the briefing.**
2. **The `FALSIFIED_ON_CAPTURED_DATA` status is an in-sample measurement over 23,383
   DETECTOR rows, and it is contradicted on the pre-registered holdout.** HOLDOUT1 on the
   1,084 held-out two-sided-book instances: clause (i) Brier model **0.1213** vs book mid
   **0.1490** (the model *beats* the book) -> not breached; clause (ii) quintile ECE
   **0.0535** vs the 0.08 bar -> not breached; clause (iii) 39 deduped qualifying entries
   against its own >=60 floor -> cannot bind. **The correct sentence is: H-MAPENTRY1 was
   NOT falsified on its held-out data, and it closed for lack of a path to power, lack of a
   qualifying instrument, and structural venue illiquidity — not on a verdict.**
3. **`mapentry_holdout` is `null`.** The holdout-scored clauses are not in this file at all;
   the only holdout numbers available to me are the ones quoted in the registry. So the
   `cells`/`mapentry` blocks in `verdict.json` are, as shipped, an **in-sample view that
   reads as more adverse than the pre-registered verdict.** Anyone reading `verdict.json`
   alone would draw the wrong conclusion. *Recommendation: populate `mapentry_holdout`, or
   have the `status` field name its population inline.*

For completeness, the in-sample clause readings in the file (n=23,383): clause (i) Brier
model 0.2251 vs book 0.1939, diff +0.0312 CI [0.012, 0.051]; clause (ii) quintile ECE
0.1245 vs 0.08; clause (iii) mean edge -0.0013, n_deduped 660; starvation 0.1548 vs the
0.05 bar (not starved). These are consistent with a lane whose in-sample and held-out
readings genuinely diverge — which is itself the strongest available argument for the
holdout discipline the registry already imposes.

### 5.5 Cross-cutting: the interval method in the `cells` block

Verified exactly on all 8 cells using the `resolved` denominator (which is the denominator
`win_rate` uses — confirmed by integer-numerator reconstruction on every cell): **every
reported `win_ci` is a row-level Wilson interval.** The `mapentry` block's note 2 pins
"All intervals resample SERIES, not rows"; the `cells` block does not follow it.

Measured design effects (bootstrap and analytic ratio-estimator variance, two methods
agreeing to within 0.08 on every population):

| population | DEFF | Wilson valid? |
|---|---|---|
| MANADV, row-level, series-clustered | 5.14 / 5.22 | **no — ~1.7x too narrow in width** |
| MANADV, round-level | 3.06 / 3.14 | no |
| ECONBUY bin-0 pooled | 1.15 / 1.17 | approximately |
| ECONBUY bin-0 `>=10k` | 1.02 / 1.02 | yes |
| ECONBUY all-bin rows | 3.31 / 3.28 | no |
| PISTOLCARRY pairs | 1.03 / 1.00 | yes |
| MODELENTRY crossings | 0.68 - 0.79 | yes (conservative) |

**No current read flips** under the correction — MANADV clears 0.85 even at DEFF 3. But the
defect is latent: it will matter the moment a lane's margin is thin, and PISTOLCARRY's is
(+0.0227). *Recommendation: switch the cells block to series-clustered intervals, matching
the mapentry block's own pinned rule.*

---

## 6. Capture health

`capture_qa.txt`: grid OK, pm OK, shadow OK; `grid_errors_today` 0; `grid_stale_alarms_today`
**1**; `pm_ws_errors_today` **4**; `disk_bucket_gib` 355.

All three feeds are up and error counts are low. Two observations, neither alarming:
the single stale alarm and 4 PM websocket errors are the kind of transient the registry has
repeatedly found to be load-bearing after the fact (`H-MAPENTRY1-NOTE12`: every missed
series on 2026-09-07 was a poller outage, and the *enabling defect* was that a tape which
writes only per-series rows cannot distinguish "no results" from "never ran"). Since that
note shipped a `discovery_sweep` row per sweep, **the uptime question is now directly
answerable from the tape** — but not from this evidence set, which carries no discovery
rows. I cannot verify capture continuity from what is mounted, only that the counters are
low. Disk at 355 GiB is recorded without a configured ceiling in any mounted file, so I
have no basis to call it a risk; flagging it only as a quantity that grows monotonically
and has no stated bound here.

---

## 7. What I did not find

Said plainly, because absence of evidence is evidence here:

- **No new PnL or fill evidence of any kind.** Every cell is `quoted = 0`. Nothing in this
  evidence set moves any edge clause on any lane.
- **No untapped broad feature interaction.** 789 cells tested; 12 survivors against ~39
  expected false positives. The served model has absorbed the available two-way structure.
- **No support for a segment-level calibration claim on the pinned holdout.** The one
  segment effect that looked significant was a 31-series sampling artifact, disproved three
  ways.
- **No recovery of the `H-MANADV1` 0.931/N=606 anchor's predicate.** 756 variants tested,
  13 in the neighbourhood, none matching the literal registered text. The CLOCKSTRAT
  UNAUDITED flag should stand.
- **No verdict is reachable on any open lane from this evidence**, and no lane is close to
  one, because no round-lane holdout has been declared and no round-book venue exists.

## 8. Suggested next actions (for the human; I register nothing)

1. **Pre-register a temporal holdout declaration for the three round lanes**, modelled
   directly on `H-MAPENTRY1-BOOKPIN1` (membership by first-mapping timestamp, instance
   floors matching each lane's existing `bar_n`). This is the single change that converts
   three lanes from "measured, unreachable" to "verdict-capable", and it costs one registry
   row. Their clause-(i) margins are currently +0.061 / +0.037 / +0.023.
2. **Add `clock_seconds` strata to the `H-MANADV1` cell output**, so CLOCKSTRAT's
   pre-committed provisional/confirmed reading can actually be rendered. Field already exists.
3. **Switch the `cells` block to series-clustered intervals** to match the rule the
   `mapentry` block already pins.
4. **Populate `mapentry_holdout`** (or inline the population in `status`) so `verdict.json`
   stops reading more adverse than the registry's own holdout render.
5. **Pin the `equip_value_diff` = $20,000 unit in a registry note.**
6. Consider DRAFT-1 for pre-registration — noting it implies a served-artifact swap and
   would retire the pinned `roundwin-v1` benchmark, so it needs its own amendment.
