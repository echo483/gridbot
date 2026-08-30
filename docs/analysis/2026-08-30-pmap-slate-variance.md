# p_map has no demonstrated skill on the traded population — and over-forecasts 21pts inside the armed band

Run 2026-08-30 ~20:20Z. `scripts/pmap_calibration.py` (unchanged code, frozen
oracle `scripts/grid_winners.py`), **434 resolved maps / 21,786 mapentry rows** —
up from 277 maps / 11,692 at the 08-28 baseline. Reporting only; the registry
bar and `gridbot.instruments.verdict` remain the only PASS/FAIL rule.

## The headline number, correctly clustered

Rows are **not independent** — 2,668 qualifying rows come from 80 series, and
rows within a series share a match outcome. Every previous p_map number in this
repo, including the 08-28 "+0.198 skill" headline, was pooled at ROW level,
which treats correlated rows as independent and badly understates variance.
By-series bootstrap (2,000 reps, resampling series not rows):

| statistic | point | 95% CI | reads |
|---|---:|---|---|
| skill vs base rate | **−0.0007** | (−0.1639, +0.1389) | **includes zero — no demonstrated skill** |
| ECE (10-bin) | **0.1788** | (0.1177, 0.2515) | **entire CI above the 0.08 bar** |
| armed-band gap | **−0.2129** | (−0.3093, −0.1142) | **excludes zero — robust over-forecast** |

Armed-band gap = realized − predicted for `0.50 ≤ p_map ≤ 0.80`, n=610 in-band
rows. Negative means we over-forecast.

## What this corrects

An earlier draft of this document attributed the day-to-day swing in skill
(−0.161 on 08-26 to +0.142 on 08-27) to **slate quality**. That causal claim was
wrong, and the check that kills it is simple: grouped by tournament, **every one
of the 2,668 qualifying detector rows belongs to a single tournament (`astatc`)**.
There is no cross-tournament contrast in this data to carry a slate effect.

The day range (−0.161, +0.142) instead matches the by-series bootstrap CI
(−0.164, +0.139) almost exactly. The per-day swings are **sampling noise at the
series level**, not drift, not slate quality, not anything mechanistic. A daily
calibration read on this lane carries no signal and should not be produced.

The practical conclusion is unchanged and now stronger: it is not that *tonight*
is a bad night. It is that **no positive edge has been demonstrated on any night**.

## What survives, and is actionable

**1. Clause (ii) of the frozen bar is breached robustly.** ECE 0.1788 with the
lower CI bound at 0.1177 — 1.5× the 0.08 bar at the *optimistic* end. This is not
a marginal miss.

**2. The armed band buys above fair value.** Inside `0.50 ≤ p_map ≤ 0.80` the
model over-forecasts by **21.3 points [11.4, 30.9]**, series-clustered. Entry
qualification forces `side_ask ≤ p_map − 0.04` and the engine posts at
`side_ask − 0.01 ≈ p_map − 0.05`. So it pays ~5pts under a number that is ~21pts
too high — **≈16pts above fair, systematically**, with the CI excluding zero.

Row-level detail (detector rows, qualifying), armed band bolded:

| p_map bucket | n | predicted | realized | gap |
|---|---:|---:|---:|---:|
| [0.4,0.5) | 54 | 0.45 | 0.2222 | −0.2278 |
| **[0.5,0.6)** | **248** | **0.55** | **0.3992** | **−0.1508** |
| **[0.6,0.7)** | **40** | **0.65** | **0.3000** | **−0.3500** |
| **[0.7,0.8)** | **313** | **0.75** | **0.5399** | **−0.2101** |
| [0.8,0.9) | 431 | 0.85 | 0.5870 | −0.2630 |
| [0.9,1.0) | 1434 | 0.95 | 0.8033 | −0.1467 |

**3. The band gate does not buy the protection it was registered to buy.**
H-MAPENTRY1-BANDGATE1 was frozen as harm reduction on the belief (cs2agent
ADR-0038) that over-forecasting lives above P_map≈0.8. In-band buckets
over-forecast by 15–35pts; `0.6–0.7` is the worst bucket in the table. Keeping
the gate is still defensible (out-of-band is no better) but describing it as
protective is not supported.

**4. This explains the paper-vs-realized gap without fill selection carrying it
all.** Paper edge is scored against p_map, which is the biased number. Deduped
paper edge fell +2.83pt (n=117, 08-28) → **+1.05pt (n=216)** as evidence nearly
doubled, against a measured −6.5pt fill-selection cost.

**5. Queue-16 SLATE rows are worse than detector rows** and must be split out of
every calibration read, not pooled:

| population | n | skill (row-pooled) | ECE |
|---|---:|---:|---:|
| DETECTOR | 2641 | +0.0060 | 0.1733 |
| SLATE (commit a4d469f) | 463 | −0.1286 | 0.2483 |

Coverage rows are recording-only, so this costs nothing live — but pooled they
drag every future number. This is the first report to separate them.

## Caveats — stated, not hidden

- **Measurement, not verdict.** All captured data, no held-out split. The
  registry bar decides, via `gridbot.instruments.verdict` (still unwired for
  H-MAPENTRY1 — that build is in flight).
- Clause (i) of the bar — Brier(p_map) vs Brier(book price as forecast) — is
  **not computed here**; this script's comparator is the base rate, not the book.
  Note Brier 0.2289 already exceeds the base-rate Brier 0.2287, and the book is
  near-certainly a better forecaster than the base rate, but clause (i) remains
  formally unmeasured.
- 72 qualifying rows were unjoinable to the oracle and excluded, never guessed.
- 08-30 is an open session; its rows will grow.
- One tournament only — nothing here generalizes to a different circuit or tier.

## Recommended next actions — NONE APPLIED

1. **Do not arm.** Not "not tonight" — there is no demonstrated positive edge on
   any night, and the armed band robustly buys above fair value.
2. **Series-cluster every verdict statistic.** Clause (iii) already requires a
   by-series bootstrap; clauses (i) and (ii) are specified pooled and pooled is
   the statistic this document shows is untrustworthy. Needs an amendment.
3. **Split SLATE from DETECTOR in all calibration reads.** Mechanical reporting
   correction, no registration needed.
4. **Stop producing daily calibration reads for this lane** — at 80 series the
   daily number is indistinguishable from noise.
5. **Amend the STATE.md PROVEN row** "the traded number has skill". At 434 maps,
   series-clustered, it is −0.0007 with a CI spanning zero.
6. The over-forecast is the mechanism lead worth pursuing (the iid `p_fut` race
   assumption is the prime suspect) — but it needs its own hypothesis, own bar,
   own held-out window. Do **not** tune on this data.

— orchestrator lane, 2026-08-30


## APPENDED 2026-08-30 ~20:40Z — clause (i) measured for the first time, and the full bar picture

Clause (i) of the frozen bar ("the model must beat the venue's own price as a
forecaster") had never been computed — `pmap_calibration.py` compares against the
base rate, not the book. Computed here on rows carrying a real two-sided PMUS
book at detection, series-clustered (2,000 reps, 90 series). "Book price" is
ambiguous in the registry text, so both readings are reported:

| reading | population | n | Brier(p_map) | Brier(book) | diff | 95% CI | favours |
|---|---|---:|---:|---:|---:|---|---|
| book = **mid** (fair reading) | all w/ book | 8169 | 0.2165 | 0.1896 | +0.0269 | (−0.008, +0.063) | BOOK |
| book = **mid** | **qualifying** | 2622 | 0.2322 | 0.2062 | +0.0260 | (−0.028, +0.083) | BOOK |
| book = **side_ask** (model-favourable) | all w/ book | 8169 | 0.2165 | 0.2185 | −0.0019 | (−0.040, +0.035) | model (tie) |
| book = **side_ask** | **qualifying** | 2622 | 0.2322 | 0.1919 | +0.0403 | (−0.006, +0.088) | BOOK |

**The point estimate favours the book in 3 of 4 cuts, including both cuts on the
qualifying population — the rows we would actually trade. No cut has the model
beating the book with a CI excluding zero.** Note the largest gap (+0.0403) is
under the interpretation chosen to *favour* the model, on the qualifying subset:
qualification selects rows where the model most disagrees with the book, and
those are exactly where the model is most wrong.

### Full bar picture — all clauses, first complete read

| clause | bar (falsifies if) | measured | reads |
|---|---|---|---|
| (i) | Brier(p_map) ≥ Brier(book) | 0.2322 vs 0.2062 (qual, mid) | **breached on point estimate**; CI includes 0 |
| (ii) | ECE > 0.08 | 0.1788, CI (0.118, 0.252) | **breached robustly — entire CI above bar** |
| (iii) | ≥60 qual entries AND edge ≤ +2pt | n=216 deduped, **+1.05pt** | **breached** |
| starvation | <5% qualifying → lane closes | **32.1%** (2622/8169) | not starved |

The registry text reads "FALSIFIED … if **ANY** of". On captured data, all three
falsification clauses read breached; clause (ii) robustly, clause (iii) plainly,
clause (i) on point estimate only.

### Why this is still not a verdict

- The bar requires **≥150 held-out resolved instances**. There is no
  pre-registered held-out split here — this is every row captured.
- Nuance worth stating: the model is **frozen** (`mapwin-race-v1`, roundwin-v1),
  so these rows are out-of-sample with respect to *fitting*. What is missing is
  the pre-declared holdout and the verdict code path, not freedom from leakage.
- Rail 2 is explicit: verdicts come from `gridbot.instruments.verdict` against
  the registry, which still has no H-MAPENTRY1 path. **That build remains the
  gate between this measurement and a verdict**, and it is the honest reason not
  to write FALSIFIED into the registry today.

The correct next step is to ship the verdict wiring, declare a holdout, and let
the registered code render the verdict — not to shortcut it from this document.
