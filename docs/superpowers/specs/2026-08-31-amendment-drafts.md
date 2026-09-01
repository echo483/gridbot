# Amendment drafts + mechanism memo — 2026-08-31

Author: gridbot-reasoner (hard-reasoning lane). Status: **DRAFTS ONLY.**
Nothing here is appended to `docs/experiments/registry.jsonl`; the user signs
off, then ops appends. No src/, no scripts/, no commits. Sibling build lanes
tonight: H-CROSSIN1 → `scripts/crossin_backtest.py`, H-PCAL1 →
`scripts/pcal_fit.py`; the pins below were written to match their frozen
method specs exactly.

Holdout all bars score on (H-MAPENTRY1-BOOKPIN1, registry 2026-08-31):
series whose FIRST GRID↔PMUS mapping row has `ts_ns >= 1788206026000000000`
(= 2026-08-31T19:53:46Z), accruing until >=150 resolved instances
(docs/analysis/2026-08-31-bookpin1-holdout-accrual.md: 0/40 series at
20:47Z; ETA 40 series ~2026-09-01 06:00–13:00Z).

Evidence base cited throughout:
- `docs/analysis/2026-08-31-queue16-bandskip.md` (phase 1: 4,282 band-skips, 39% cross-in @120s)
- `docs/analysis/2026-08-31-queue16-phase2.md` (phase 2: above-band +0.162 / below-band −0.128)
- `docs/analysis/2026-08-31-queue15-twosided.md` (deadness episodic; 94.4% of strict-gated samples futile)
- `docs/analysis/2026-08-30-pmap-slate-variance.md` (armed-band gap −0.2129; skill −0.0007)
- `docs/analysis/2026-08-28-priced-in-test.md`, `2026-08-28-pmap-calibration.md`
- `docs/analysis/2026-08-31-roundwin2-poolb.md` (drift-gate replay)
- `docs/STATE.md` ("Where the money actually goes", retraction row, item 11d shocks)

---

## Part 1 — registry-row drafts (verbatim-appendable after sign-off)

Each block is ONE JSON object, house style. `<REGISTRATION_TS_UTC>` is filled
at append time. Row 2 carries an artifact-hash placeholder that MUST be
filled before append (two-key: builder computes, user witnesses).

### Row 1 — H-CROSSIN1 (above-band cross-in entry)

```json
{"run_ts": "<REGISTRATION_TS_UTC>", "hypothesis_id": "H-CROSSIN1", "status": "REGISTERED_NO_RUN", "hypothesis_text": "Above-band cross-in entry. MECHANISM CLAIM: fallen-favorite mispricing (favorite-longshot bias): after a visible round the favorite loses, retail flow over-discounts the favorite and the book's roughly 2-minute reprice into the armed band OVERSHOOTS fair; the cross-in is an overshoot, not a fair reprice, so buying the fallen favorite inside the band carries positive settled edge. The pre-registered below-band control arm exists to kill this claim if the asymmetry is not side-specific. CANDIDATE RULE, frozen exactly as scripts/crossin_backtest.py pins it: population = H-MAPENTRY1 band-skip evaluations on BOOKPIN1 holdout series (numeric p_map strictly outside [0.50,0.80], mapped market_slug, us_side in {long,short}, valid own-row book top; SLATE rows EXCLUDED per H-MAPENTRY1-NOTE4), deduped to the first evaluation per (series_id, map_number, round_number, market_slug, us_side); skip time = t0. First same-slug quote row within 300s of t0 whose our-side EXECUTABLE price enters [0.50,0.80] (executable = ask for long, 1-bid for short) is the crossing row. PERSISTENCE = the NEXT same-slug quote row within 180s of the crossing row also has our-side executable price inside [0.50,0.80]; only persistent crosses are entries. ENTRY PRICE = the first crossing row's executable price (taker mark). Quote rows with ts_ns after the grid_winners.py oracle's map-terminal observation for that (series_id, map_number) are excluded as settlement-informed. Dedup one entry per (series_id, map_number, market_slug); persistence-failed crosses do NOT consume the slot (the entry is the first PERSISTENT cross). ARMS: above-band skips (p_map > 0.80 at t0) = candidate; below-band skips (p_map < 0.50 at t0) = pre-registered negative control. Resolution = scripts/grid_winners.py first-observation map-winner oracle (MR12-validated, ambiguous finals excluded); won iff our team won the map (short leg via opponent-named book, matching phase 2). Edge = win_rate - entry_price; CI = series-clustered bootstrap, 1000 resamples, 95% percentile, seed 16. FROZEN BAR, all on the BOOKPIN1 holdout (series first-mapped at/after 2026-08-31T19:53:46Z, first-mapping ts_ns >= 1788206026000000000): (a) PASS requires n >= 60 deduped persistent above-band entries spread across >= 20 distinct series, AND the series-clustered 95% CI of settled edge excluding zero on the positive side (lower bound > 0); (b) the below-band control must NOT show settled edge whose CI excludes zero on the positive side - if it does, the mechanism claim is wrong (the asymmetry is not side-specific reprice overshoot) and the lane CLOSES regardless of clause (a); (c) clause (a) missed (CI includes zero) = FALSIFIED; (d) STARVATION: fewer than 60 entries or fewer than 20 series within 30 days of registration = INCONCLUSIVE_STARVED - no edge licensed, the lane does not PASS. PINNED CONSTANTS: cross window 300s; persistence = next same-slug quote within 180s; band [0.50,0.80] closed; n floor 60; series floor 20; bootstrap seed 16 with 1000 resamples; dedup key (series_id, map_number, market_slug); oracle scripts/grid_winners.py; instrument scripts/crossin_backtest.py. WHAT THE IN-SAMPLE +0.162 DOES NOT LICENSE: the motivating numbers (above-band +0.162 CI[+0.089,+0.227] n=593; below-band -0.128 CI[-0.219,-0.039] n=474; docs/analysis/2026-08-31-queue16-phase2.md) were measured on PRE-holdout data at the +120s our-side MID, on a SINGLE nearest-quote sample, with NO persistence filter. They do not license: mid-priced edge (this bar scores executable ask/1-bid, which is strictly worse in thin tops), single-sample crossings (persistence is required here and short-lived crossings drop out), any extrapolation to live fills (the engine is post-only maker; a PASS measures TAKER economics and adoption into the maker engine would need its own amendment citing this verdict), and any sub-stratum read (distance-outside, day, tournament) - arms only enter the verdict. Reversible: the lane is additive (a new entry path on holdout data); closure costs nothing.", "evidence": "docs/analysis/2026-08-31-queue16-bandskip.md; docs/analysis/2026-08-31-queue16-phase2.md; H-MAPENTRY1-BOOKPIN1 registry row", "registered_by": "gridbot-reasoner draft / user sign-off pending"}
```

### Row 2 — H-PCAL1 (recalibration overlay)

```json
{"run_ts": "<REGISTRATION_TS_UTC>", "hypothesis_id": "H-PCAL1", "status": "REGISTERED_NO_RUN", "hypothesis_text": "Recalibration overlay: monotone isotonic (PAV) map p_cal = g(p_map). METHOD PIN (scripts/pcal_fit.py): fit ONLY on pre-holdout resolved observations - series first-mapped BEFORE 2026-08-31T19:53:46Z (first-mapping ts_ns < 1788206026000000000), deduped to the FIRST evaluation per (series_id, map_number, round_number), resolved against the scripts/grid_winners.py first-observation map-winner oracle; unresolved observations excluded, never guessed. PAV enforces g monotone non-decreasing (deliberate anti-overfit choice at this n; plateaus are expected and reported). The fitted artifact is frozen at data/models/pcal-v1.json and sha256-pinned in this row at registration. FROZEN BAR, evaluated on the BOOKPIN1 holdout (series first-mapped at/after 2026-08-31T19:53:46Z, first-eval-per-(series,map,round), >= 150 resolved observations required; series-clustered bootstrap, 1000 resamples, 95% percentile, seed 16): (i) quintile ECE(p_cal) <= 0.08, with quintile edges computed ONCE on the point-estimate holdout predictions and held frozen across bootstrap resamples; (ii) Brier(p_cal) < Brier(p_map), with the series-clustered 95% CI of Brier(p_cal) - Brier(p_map) excluding zero on the negative side (upper bound < 0). PASS requires BOTH clauses; either missed = FALSIFIED; fewer than 150 resolved holdout observations within 30 days of registration = INCONCLUSIVE_STARVED. REPORTED BUT NOT IN THE BAR (describe-only, never a verdict input): armed-band gap (realized - predicted for 0.50 <= p_cal <= 0.80), the plateau structure of g (distinct p_cal values and their p_map spans), and an all-rows (not-first-eval) sensitivity read. SCOPE RAIL - TWO-KEY DISCIPLINE: a PASS licenses NOTHING in the trade path by itself. p_cal replaces p_map in gating, pricing, or sizing ONLY via a separate pre-registered amendment that cites this verdict by run_ts and artifact hash; until then the engine trades p_map unchanged and this row changes no live behavior. Coupling note for that future amendment: the H-CROSSIN1 skip population is p_map-defined, so adopting p_cal would redefine it; the two verdicts are mechanically independent but mechanistically coupled (see Part 3 finding F3).", "evidence": "docs/analysis/2026-08-28-pmap-calibration.md; docs/analysis/2026-08-30-pmap-slate-variance.md; H-MAPENTRY1-BOOKPIN1 registry row", "artifact": {"path": "data/models/pcal-v1.json", "sha256": "<TO-BE-PINNED-AT-REGISTRATION: compute over the frozen fit artifact after fit freeze and BEFORE the first holdout row is scored; append is blocked while this placeholder remains; builder computes, user witnesses>"}, "registered_by": "gridbot-reasoner draft / user sign-off pending"}
```

### Row 3 — Q15 futility window-gate: DRAFT (not reject) — argued below

My call: **draft it, narrowly.** The honest case on the numbers
(docs/analysis/2026-08-31-queue15-twosided.md):

- The strict window gate (pause after 3 consecutive dead samples) is the
  cleanest gate simulation this repo has measured: it gates 974 samples =
  6.8% of all in-play samples, and 919/974 = **94.4% of gated samples were
  futile** (55 = 5.6% live two-sided). Every other gate idea in this repo
  has had a live-book casualty rate an order of magnitude worse.
- The structure matches the data: deadness is **episodic** (74% of dead
  samples sit in >=3-run streaks; 139/219 slugs hit at least one streak;
  only 8/219 persistently dead), so a *window* gate (pause, resume at round
  boundary) is the right shape — a *market-level* pre-gate would mislabel
  139/219 slugs and is not drafted.
- Spread-gating is **rejected with the numbers**: treating wide-spread books
  as untradeable blocks 22.4–32.4% of genuinely live books (<=0.10 variant
  32.4%, <=0.25 31.6%, <=0.50 22.4%). Wide spreads come and go (median
  0.08) and a hindsight gate cannot separate them from quiet real books.
  No spread term appears in the draft.
- Why not REJECT outright: the counter-case is "6.8% is too small to carry
  a gate." That has real force — expected savings are ~160 dead fetches/day
  at late-August volume, plus ledger `skipped` noise. But the gate is one
  counter plus one state variable, its failure surface is observation-only
  (it can never place differently — it can only *fail to observe*), and the
  frozen bar below gives that failure surface a zero-tolerance tripwire.
  The 08-31 keyring outage (STATE.md gotchas) showed fetch-capacity and
  capture continuity are the fragile assets here; not hammering dead books
  for 6 rounds at a time is cheap insurance. A REJECT would leave a
  measured-94.4%-futile fetch pattern running forever to save zero code.

So: draft, with a do-no-harm bar and a pointlessness floor. It is an
**efficiency** amendment — anyone reading it as an edge claim is wrong.

```json
{"run_ts": "<REGISTRATION_TS_UTC>", "hypothesis_id": "H-Q15FETCHPAUSE1", "status": "REGISTERED_NO_RUN", "hypothesis_text": "Futility window-gate: engine-efficiency amendment, ZERO entry-rule change. CONTEXT (docs/analysis/2026-08-31-queue15-twosided.md): 8.6% of in-play BBO samples on mapped map markets are dead (7.0% one-sided zero-placeholder, 1.6% no-BBO); deadness is EPISODIC, not market-level (139/219 observed slugs hit >=1 dead streak of length >=3; only 8/219 persistently dead; 74% of dead samples sit in >=3-run streaks). Retro-sim of this exact gate shape: fires on 139/219 slugs, gates 974 samples = 6.8% of all samples, of which 94.4% (919/974) were futile and 5.6% (55/974) live two-sided books. Spread-based gating is REJECTED by the same data and NOT frozen in any form: wide-spread-as-untradeable variants block 22.4-32.4% of genuinely live books. RULE: after 3 CONSECUTIVE dead samples on a market_slug (dead = one-sided long-side bestBid/bestAsk zero-placeholder OR no-BBO gateway response - exactly the retro-sim strict definition; wide spreads NEVER count as dead), the recorder pauses gateway BBO fetches for that slug and resumes at the next round boundary (round_number increment on that series in data/grid/series_state). Pause state is in-memory per slug; restart clears it (fail-open to fetching = pre-gate behavior, the safe direction). Paused rounds still ledger one skipped:fetch_paused row per round with NO gateway call, so the H-MAPENTRY1 starvation denominator stays reconstructable. FROZEN BAR (do-no-harm; engine-faithful replay over the BOOKPIN1 holdout window, pause arm vs no-pause baseline on identical rows, full gate stack band+drift+dedup): (i) ZERO placement rows lost under the pause gate relative to baseline - any lost placement FALSIFIES and reverts; (ii) the gate must skip >= 30 gateway BBO fetches across the holdout window, else INCONCLUSIVE_POINTLESS (the mechanism is too small to carry a gate); (iii) no entry-rule quantity may differ: identical qualifying-row population and identical intent prices, verified by full replay diff, not sampling. REVERT: one constant (streak threshold 0 disables). SCOPE, STATED PLAINLY: this is a cost/latency/ledger-noise amendment, not an edge claim; expected savings ~6.8% of BBO fetches (~160/day at late-August volume). It touches no qualification rule, band, drift gate, dedup, sizing, or registry verdict bar.", "evidence": "docs/analysis/2026-08-31-queue15-twosided.md", "registered_by": "gridbot-reasoner draft / user sign-off pending"}
```

---

## Part 2 — mechanism memo: what actually drives the inefficiency

**The measured asymmetries (all pre-holdout, describe-only):**

| # | asymmetry | source |
|---|---|---|
| 1 | above-band crossers +0.162 CI[+.089,+.227] vs below-band −0.128 CI[−.219,−.039] | queue16-phase2.md |
| 2 | DOWN-shocks leave book +15.1c above model, ~29s median convergence, only 44% converge; UP-shocks +3.2c (n=230 shocks/52 priced) | STATE.md 11d, scripts/postshock_lag.py |
| 3 | drift-gated replay placements 44/3538 at +10.5pt CI[−4.3,+23.9] vs ungated +6.9pt CI[+0.7,+12.6]; live exact-rule confirms 0/370 (mean visible move at decision −2.9pt) | 2026-08-31-roundwin2-poolb.md |
| 4 | armed band over-forecast −21.3pt CI[−30.9,−11.4]; skill vs base rate −0.0007 CI incl. zero | 2026-08-30-pmap-slate-variance.md |
| 5 | post-quote drift after discounts +0.5–0.94pt/120s vs 20pt+ discounts paid | 2026-08-28-priced-in-test.md |

**Mechanism A — favorite–longshot bias (retail over-discounts the favorite
after a bad round).** Consistent with #1, #2, #5: the reprice into the band
is an overshoot that decays slowly (minutes), which is why a 300s cross
window still sees +16pt of edge in-sample. Falsifiable prediction in OUR
data: after an above-band persistent cross, same-slug our-side mids in
`data/shadow/mapentry-*.jsonl` (later rows for the same `market_slug`,
ts_ns > crossing ts) must show continued positive oriented drift at
+60/+120s, while below-band crossers show continued negative drift; if
post-cross drift is flat, the +0.162 is a one-shot level shift, not a
decaying mispricing, and the entry window has no mechanism to fill inside.
Tested by: H-CROSSIN1 candidate/control asymmetry (tonight) IS the primary
test; the drift profile is a describe-only extension needing no amendment.

**Mechanism B — retail momentum-chasing after visible rounds.** Retail buys
whatever the just-visible round produced; stream delay (~60s+) puts that
flow BEHIND our GRID lead, which would make the mispricing systematically
late-arriving and slow to correct. Falsifiable prediction: crossing-row
ts_ns joined to the immediately preceding round transition in
`data/grid/series_state-*.jsonl` should mass within ~30–90s of the
transition, and cross DIRECTION should match the round winner (underdog
won the round → below-band cross-up; favorite lost → above-band
cross-down). If crosses are uniform in time since round end, momentum-chasing
is not the driver. Tested by: no instrument tonight — describe-only join,
no amendment needed; it discriminates A from B (both predict slow decay,
only B predicts round-locked timing).

**Mechanism C — market-maker withdrawal vs lagged repricing.** #2 is the
signature: a +15.1c gap that only 44% of episodes close in-window means
nobody is arbitraging the stale book, i.e. the MM withdrew and the top is
stale-wide, rather than a slow belief update. Falsifiable prediction:
DOWN-shock gap episodes must show contemporaneous spread widening —
`bestAsk − bestBid` on the shocked slug's `mapentry`/`postquote` rows
rising above that slug's own median (0.08 corpus-wide) — and the ~29s
convergence must coincide with spread re-tightening. If spreads stay flat
through the gap, it is belief-lag, not withdrawal. Tested by: no instrument
tonight; describe-only follow-up joining `scripts/postshock_lag.py` events
to BBO fields. **Critical timing implication:** if C is the true mechanism,
the exploitable window is ~29s — inside our latency race (13–27s GRID lead
minus 8.5s pipe) — and a 300s cross-in instrument arrives too late; the
holdout verdict will then show the edge converged away.

**Mechanism D — our model's overconfidence masquerading as market error.**
#4 is the standing indictment: p_map over-forecasts by 21pts inside the
armed band, and the skip population itself is defined by p_map. The
above-band arm is enriched with favorites the MODEL overrated; the market
crossing down may be correct repricing of a bad p_map, and the +0.162 may
be smaller after conditioning on true fair. What saves the mechanism claim
(for now): crossers won 77.8% at a 0.617 price — if the market were merely
correcting our error, realized should sit near the price, not 16pts above
it. But the 0.8–0.9 bucket's pooled realized 0.587 vs predicted 0.850
(2026-08-30 table) shows how badly the conditioning variable lies.
Falsifiable prediction: H-PCAL1. If p_cal PASSes and the armed-band gap
collapses toward zero, re-cut the cross-in population on p_cal (describe-only):
if the above-band edge survives redefinition, the market-side mechanism is
real; if it evaporates, mechanism D was carrying it.

**Ranked chase / don't-chase (tied to the low-latency thesis — 13–27s GRID
lead, 8.5s median pipe ⇒ ~5–18s effective window):**

1. **CHASE — above-band fallen-favorite cross-in (H-CROSSIN1, tonight).**
   Largest significant asymmetry on disk, and — decisively — it does NOT
   require winning the latency race: a mispricing still open at +300s is a
   slow behavioral decay (A/B), which our 8.5s pipe can eat with room to
   spare. The 300s window is itself the A/B-vs-C discriminator.
2. **CHASE, small — DOWN-shock fade (short side).** Same sign as #1 (fade
   the overreaction), +15.1c gap. But its window is the ~29s convergence —
   latency-race territory where our pipe consumes a third of the lead — and
   n=52 priced shocks is thin. Needs its own instrument; do not bolt it on.
3. **DON'T CHASE — below-band longshot wake-ups.** Demonstrated anti-edge,
   CI excludes zero negatively (−0.128). The band's lower bound is doing
   real work; leave it.
4. **DON'T CHASE — drift-confirmation as an edge source.** Replay says it
   selects +10.5pt subpopulations offline, but the live exact-rule path
   confirmed 0/370 decisions at mean visible move −2.9pt: it currently
   functions as a (correct) near-total block, i.e. harm reduction, not a
   monetizable signal.
5. **DON'T CHASE — trading p_cal directly.** The over-forecast is real, but
   a calibration overlay is a measurement until its own amendment cites a
   PASS verdict (two-key, Row 2).

What is NOT demonstrated: any mechanism on holdout data (everything above
is pre-holdout), executable-price edge (all cross-in numbers are mid-priced),
and whether the +0.162 survives persistence — tonight's instruments decide.

---

## Part 3 — method audit of the two sibling instruments

### H-CROSSIN1 (scripts/crossin_backtest.py)

F1. **MAJOR — settlement-window leak, closed by one loader line.**
Persistence-confirmation does not mechanically leak settlement info — a
quote within 180s of an in-play cross is pre-resolution, and post-map
prices snap to extremes, EXIT the band, and fail persistence (conservative
direction). But the quote stream is not guaranteed map-live: rows between
the GRID terminal observation and venue resolution are settlement-informed.
The draft therefore pins: exclude quote rows with ts_ns after the oracle's
map-terminal observation for that (series_id, map_number). Without this
line the pin has a hole; with it, sound.

F2. **MAJOR — observability selection (phase-1 caveat A, unfixable from
disk).** A crosser exists only if later same-slug quotes exist: +120s
coverage was 2,730/4,282 = 64%, and persistence demands a SECOND later
quote. The mapentry stream is the book history (GRID-driven cadence, median
23s, 96% gaps ≤130s), so entries condition on the slug staying actively
evaluated — i.e. on the series still producing rounds through our capture
(08-31's 100-min keyring outage deletes exactly such windows). If quote
cadence correlates with map competitiveness, edge is conditional on
observability, bias direction unknown. Mitigations pinned: verdict must
report the observable/unobservable split and series count per arm, plus the
>=20-series floor. Capture gaps bias toward starvation, not toward inflated
edge.

F3. **MAJOR — population endogeneity: the skip is defined by a falsified
forecaster.** p_map over-forecasts 21pts in-band (CI excl. zero), so the
above-band arm is enriched with favorites the model overrated. The verdict
remains mechanically valid (the rule is defined on realized p_map, which is
what the engine sees), but the MECHANISM attribution (market overshoots
fair) is not identifiable by this instrument alone — that is what the
H-PCAL1 re-cut in Part 2 mechanism D is for. Do not cite a CROSSIN1 PASS as
proof the market, rather than our model, was wrong.

F4. **MAJOR — first-cross executable price is a sampled mark, and the bias
is arm-asymmetric.** The crossing row's ask is observed at ~23s cadence,
not at the true band-crossing instant. When price falls through the band
(above-band arm), the late-sampled ask is CHEAPER than the crossing-instant
price → candidate-arm edge overstated. When price rises through the band
(below-band arm), the late-sampled ask is dearer → control-arm edge
understated. Both biases widen the measured asymmetry. Unfixable from disk;
the verdict must carry this caveat and the bar's margin must absorb it.
Also: entry at ask is a TAKER mark while the live engine is post-only maker
(ask − 1 tick, measured −6.5pt fill-selection cost); a PASS measures taker
economics and cannot be inherited by the maker path.

F5. **MINOR — dedup/persistence interaction pinned in the draft.** The
sibling spec says "entry at the FIRST crossing row's executable price" plus
dedup per (series_id, map_number, market_slug). The only self-consistent
reading — and the one the draft freezes — is that persistence-failed
crosses do not consume the slot; otherwise a first ephemeral cross would
burn the map's only entry opportunity and starve the arm artificially.
Builder must confirm the instrument implements this reading.

F6. **MINOR — cluster count at the n floor.** Series-clustered bootstrap on
a holdout accruing ~40–60 series is borderline; the >=20-distinct-series
floor in the bar guards against one series dominating the CI. Report the
series count next to every CI.

F7. **MINOR — multiple reads.** Candidate + control is a pre-registered
negative-control design, not a multiple-testing problem; but pin that the
verdict emits the two arm CIs and NOTHING else — distance/day/tournament
splits are describe-only and may never trigger a re-run (the +0.026
post-hoc selection in H-BAND1 is the precedent for why).

F8. Sound lines: oracle choice (grid_winners.py map winners, matching
mapentry_verdict.py), slate-row exclusion, dedup key, executable-price
convention, seed 16/1000-resample bootstrap continuity with phase 2 — all
match the frozen sibling pin and engine semantics. **Pin is sound subject
to F1's loader line and F5's reading.**

### H-PCAL1 (scripts/pcal_fit.py)

F9. **MAJOR — isotonic flattening exactly where the bias is worst.** The
08-30 bucket table is non-monotone in realized rates: [0.5,0.6) 0.399,
[0.6,0.7) 0.300, [0.7,0.8) 0.540. PAV pools any inversion into a flat
plateau, so p_cal is constant — zero local information — precisely across
the armed band where the −35pt worst bucket sits. This is the correct
anti-overfit choice at this n (no spurious curvature), but the overlay may
under-correct the traded region. The verdict must report g's plateau
structure (pinned in the row) so a future reader sees what PAV flattened.

F10. **MAJOR — first-eval dedup skews the fit's clock distribution.**
First eval per (series,map,round) is the earliest-clock observation; there
is a measured ~3x within-round Brier gradient (cs2agent cross-check on our
08-25 capture, STATE.md item 11). The engine's qualifying rows trade at a
different clock distribution than the fit sees. Direction of the induced
p_cal bias is not demonstrated. Fit stays pinned to first-eval (correct);
the all-rows sensitivity read in the row is the disclosure.

F11. **MINOR — bootstrap bin edges.** Pin: quintile edges computed once on
point-estimate holdout predictions, frozen across series-bootstrap
resamples; recomputing edges per resample injects bin-boundary noise into
the CI. (Same trap as pooled-vs-clustered was for the retracted +0.198.)

F12. **MINOR — hash ceremony is load-bearing.** The sha256 placeholder must
be filled BEFORE the first holdout row is scored and at append time; an
appended row with the placeholder still in it renders the bar
indeterminate. Two-key: builder computes over the frozen artifact, user
witnesses the append.

F13. **MINOR — denominator hygiene.** Holdout observations without oracle
resolution are excluded never guessed; the >=150 floor counts resolved
first-evals after dedup, and shortfall renders INCONCLUSIVE_STARVED, not
PASS. Holdout/fit separation itself is clean: the holdout is series-defined
by first-mapping ts, so no holdout-series row can exist pre-cutoff and the
fit cannot touch holdout data by construction — verified against the
BOOKPIN1 definition.

F14. Sound lines: monotone PAV + sha-pinned artifact + pre-holdout fit +
first-eval dedup + series-clustered eval is the right anti-overfit design
for this n; the two-key scope rail prevents a PASS from laundering itself
into the trade path. **Pin is sound subject to F9's reporting and F11's
edge-freeze.**

### Cross-instrument

C1. **MAJOR (design coupling, handled):** the two verdicts are
mechanically independent — CROSSIN1's population stays p_map-defined even
if p_cal later replaces p_map in the engine — so neither verdict's validity
depends on the other's outcome. The coupling is mechanistic only (F3 /
mechanism D), and the draft rows say so in text so a future reader cannot
cite one PASS as evidence for the other's mechanism claim.

— gridbot-reasoner, 2026-08-31. Bad-news inventory: nothing in this
document is holdout evidence; every number above is pre-holdout and
describe-only until the two instruments run and the bars render.

---

## Ops addendum (2026-09-01, before sign-off) — Row 2 population is now stale

After this draft was written, the first real-data `pcal_fit.py fit` REFUSED
via its anchor guard: the Row-2 fit population (ALL first-evals) shows an
armed-band gap of only **+0.049** (n=7,677), while the QUALIFYING subset —
the traded population the −0.2129 was measured on — shows **+0.196** (n=399
in-band, 87 series). See docs/analysis/2026-08-31-pcal-population-finding.md.

Consequences for sign-off:
1. **Row 2 as drafted should NOT be registered.** A g(p_map) fit on all
   evaluations under-corrects ~15pt exactly inside the trade gate; its bar
   could even PASS while fixing nothing the engine trades. The fit
   population needs a user decision first: qualifying-only (thin: 399
   in-band first-evals) vs discount-conditional g(p_map, side_ask).
2. This finding STRENGTHENS mechanism D (F3, mechanism memo): conditioning
   on qualification alone moves the gap +0.049 → +0.196, i.e. the book
   disagreeing with p_map by ≥4pts is itself ~15pts of evidence against
   p_map. Read H-CROSSIN1's above-band arm with that in mind.
3. Rows 1 and 3 are unaffected (neither conditions on the broad-population
   calibration). Lane A's builder must still confirm F1 and F5.
