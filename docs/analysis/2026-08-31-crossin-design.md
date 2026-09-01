# H-CROSSIN1: executable-price band cross-in backtest (design evidence, 2026-09-01)

Script: `scripts/crossin_backtest.py` (analysis only, read-only; no src/,
no registry edit, no rule claim). Tests: `tests/test_crossin_backtest.py`.

Operationalizes queue-16 phase-2's item-10: reprice the band-skip cross-in
signal with **executable our-side prices at FIRST cross plus a persistence
filter**, instead of phase-2's single +120s nearest-quote MID sample.

## Method (frozen, no tuning)

- **Population** (engine-faithful, deliberately NOT phase-2's): all
  `data/shadow/mapentry-*.jsonl` (UTC day files joined across day
  boundaries, torn lines skipped). Rows with `type=="mapentry"`,
  `hypothesis_id=="H-MAPENTRY1"`, **slate rows excluded**
  (`src_hypothesis_id=="H-MAPENTRY1-SLATE"` — recording-only queue-16
  coverage rows the engine never trades), numeric `p_map` strictly outside
  [0.50, 0.80], `market_slug` present, `us_side` in {long, short}, and the
  own-row book alive (bid and ask present, > 0, uncrossed). First
  evaluation per (series, map, round, slug, us_side) is the skip event t0
  (phase-1/2 convention).
- **Quote stream**: later mapentry rows for the same `market_slug` (any
  round, any provenance — slate rows carry venue book tops and phase-2's
  timeline included them), ordered by `ts_ns`.
- **Executable our-side price**: long ⇒ ask; short ⇒ 1 − bid. Orientation
  copied from `src/gridbot/instruments/mapentry.py::side_ask` (a short on
  an opponent-named book buys the No leg).
- **First crossing**: first same-slug quote in (t0, t0+300s] whose
  executable price is inside [0.50, 0.80].
- **Persistence**: the NEXT same-slug quote within 180s after the crossing
  row must ALSO be in-band; otherwise no entry (failed-persistence).
- **Entry price** = the FIRST crossing row's executable price
  (conservative: pay the ask at first cross, not the confirmation price).
- **Dedup**: ONE entry per (series_id, map_number, market_slug) —
  engine-faithful one-entry-per-map-book; earliest qualifying crossing wins
  the slot.
- **Arms**: above-band (p_map > 0.80) = the candidate; below-band =
  pre-declared control, computed identically, expected ≤ 0. Never pooled.
- **Resolution**: map-winner truth via the frozen oracle
  `scripts/grid_winners.py` over `data/grid/series_state-*.jsonl`
  (subprocess, pure consumer — phase-2's exact pattern). WON iff OUR team
  won the map, for BOTH legs (short = No leg on an opponent-named book).
  Unresolvable maps excluded and counted, never guessed.
- **Statistics**: per-arm win rate, mean settled edge (= win − entry_price),
  series-clustered bootstrap CI (resample series_id, 1000 reps, seed 17 —
  never row-pooled), median entry price, failed-persistence count, per-day
  table.
- **Holdout discipline** (BOOKPIN1): series first-mapped at/after
  2026-08-31T19:53:46Z (min `ts_ns` per `grid_series_id` across
  `data/pmus/mappings-*.jsonl`; cutoff 1788206026000000000 ns) are HOLDOUT.
  `--design` (default) = pre-holdout only; `--holdout` refuses to print
  metrics below 60 deduped entries. **This report is --design only.**

## Anchor reconciliation

Reproduced `uv run python scripts/queue16_phase2_outcomes.py` (committed
loader, byte-identical to the phase-2 commit b5dd78b) before building:

| population | doc (2026-08-31 ~21:35Z) | live run 06:06Z 09-01 | corpus capped @21:40Z 08-31 |
|---|---|---|---|
| deduped band-skips | ~4,294 | 4,405 | **4,294** |
| +120s crossers | 1,067 | 1,091 | **1,067** |
| resolved split | 667W / 388L | 686W / 397L | **670W / 389L** |

The live-run counts exceed the doc beyond ~1% drift **because capture
appended after the doc was written**: capping the same committed loader at
21:40Z 2026-08-31 (doc run time) reproduces the doc's population and
crosser counts exactly, and the resolved split to within 3W/1L (the doc
notes 667W/388L was "stable across repeated runs" as rows landed 21:35→21:40).
No loader drift — the delta is post-doc capture append only.

## Slate-exclusion delta (a finding)

Excluding slate rows from the phase-2 population removes **966 deduped
band-skip keys** (4,405 → 3,439 on the live corpus; ~8% of the phase-2
population), nearly all from 08-30/08-31. Phase-2 counted those
recording-only coverage rows as tradeable band-skips: 967 of its skip
events existed ONLY as slate rows (the same (series, map, round, slug,
side) never appeared from the detector path). Phase-2's per-day day-level
counts for 08-30/08-31 are therefore inflated relative to what the engine
could ever have traded; its band-side split conclusion is unaffected here
(the same rows were re-priced out of the executable measurement).

## Design-mode arms (IN-SAMPLE DESIGN EVIDENCE — not a verdict)

Live corpus, pre-holdout series only (2026-09-01 06:32Z run;
`data/analysis/crossin-design-20260901T063237Z.json`):

Population: 27,971 mapentry rows → 2,755 slate rows excluded → 3,439
deduped band-skip skip events (all design; holdout pool 0) → 2,313 no
cross within 300s → 1,126 first crosses → 94 failed persistence → 1,032
entries before dedup → 875 dedup slots lost → **157 entries**.

```
above-band (p_map > 0.80) — CANDIDATE
  entries 104 (series 75)  resolved 101  won 62  lost 39  unresolved 3
  win% 61.4%  median entry px 0.665  mean settled edge -0.0466 [-0.146, 0.048]
below-band (p_map < 0.50) — control
  entries 53 (series 45)  resolved 52  won 28  lost 24  unresolved 1
  win% 53.8%  median entry px 0.680  mean settled edge -0.1094 [-0.242, 0.024]
```

Per-day (candidate arm): 08-26 9e 4W-5L, 08-27 20e 13W-7L, 08-28 16e
11W-4L, 08-29 6e 2W-2L, 08-30 34e 18W-16L, 08-31 19e 14W-5L.

## Read (describe-only)

1. **The persistence + executable-ask repricing does not preserve the
   phase-2 mid-priced headline.** Phase-2's above-band crossers won 77.5%
   at a mean MID of 0.617 (edge +0.159, CI excluding zero). After
   executable pricing at first cross and requiring the very next quote
   (≤180s) to confirm in-band, the surviving above-band entries win 61.4%
   at a median first-cross ask of 0.665 — settled edge **−0.047**,
   CI [−0.146, +0.048]. The point estimate flipped sign.
2. **Where the edge went**: (a) the band-crossing ask is higher than the
   +120s mid (median 0.665 vs 0.617 — you pay the offer the moment price
   enters the band), and (b) persistence selects quotes that had already
   stabilized inside the band, i.e. the fastest repricers whose remaining
   upside at first cross is smallest. Both were the biases phase-2's
   caveats A/B predicted; they were material.
3. **The control behaves as pre-declared**: below-band edge −0.109,
   CI [−0.242, +0.024] — the arm computed identically, expected ≤ 0,
   delivered ≤ 0. The candidate's −0.047 is NOT evidence the mechanism
   works (both arms price negative); it is only less bad than the control.
4. **Survivorship is severe**: 157 entries from 3,439 skip events (4.6%).
   875 candidates were dropped by the one-entry-per-map-book dedup alone —
   the same map book crossing repeatedly is one tradeable event, not many.
   Per-series clustering is high (75 series behind 104 candidate entries).
5. **Caveats**: (a) in-sample design evidence on the same corpus that
   motivated the rule — the BOOKPIN1 holdout is the venue for any verdict
   and currently holds 0 usable entries (holdout series only began mapping
   08-31 23:02Z; no holdout-series band-skip has accumulated yet —
   `--holdout` prints the accrual count and withholds metrics below 60);
   (b) the quote stream is the mapentry evaluation cadence (~23s median),
   not a tick feed — first-cross "ask" is the ask at the nearest evaluation
   after the true cross, an unavoidable coarse-quantization of this
   instrument; (c) resolution coverage 99% (oracle, MR12-validated); 4
   unresolved entries excluded, never guessed; (d) phase-2's slate-row
   contamination means its per-day tables, though not its band-side
   conclusion, should be re-read with the delta above in mind.
6. **No rule-change claim, no registry edit, nothing in src/.** This
   measurement does not show an above-band widening amendment would have
   profited on executable prices; the phase-2 mid-priced edge does not
   survive conservative repricing on the design sample.

## Ops addendum (2026-09-01) — F1 terminal cutoff wired and verified empty

The registry draft's F1 pin (exclude quote rows at/after the oracle's
map-terminal observation as settlement-informed) was added to the instrument
post-build (`simulate_entries(..., terminal_by_map)`, first_seen_ts_ns from
the winners payload, both crossing and persistence windows capped; regression
test test_terminal_cutoff_excludes_settlement_informed_quotes). Empirical
check on the full design corpus: ZERO post-terminal entries in either arm —
the quote stream dies with the rounds — so the negative candidate-arm result
stands unconfounded. Re-run with the cutoff active (2026-09-01T10:10Z,
corpus grown): above-band 108 entries, 66W-39L, win 62.9%, median ask 0.660,
edge −0.031 CI [−0.124, +0.061]; below-band control −0.109 CI [−0.242,
+0.024]. Suite 726 green.
