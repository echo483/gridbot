# Queue 16: band-skip cross-in measurement (2026-08-31)

Script: `scripts/queue16_bandskip_crossin.py` (analysis only, read-only over
capture data). Question from
`docs/superpowers/specs/2026-08-27-entry-gate-loosening.md` (Queue 16): when
the executor band gate (US_BAND_GATE 0.50-0.80) skips an evaluation on P_map,
does the market cross INTO the band within 60-120s? Non-trivial cross-in =
the gate's own price-cadence read lags the edge (timing problem); near-zero =
skips were honest no-edge.

## Method (and its one forced substitution)

- Population: H-MAPENTRY1 rows in `data/shadow/mapentry-20260826..31.jsonl`
  with numeric p_map strictly outside [0.50, 0.80], a mapped
  `market_slug` + `us_side`, and a valid own-row book top (dead books with
  zero/absent BBO excluded — they are not prices). Deduped to the FIRST
  evaluation per (series, map, round, slug, side): 4,282 band-skip
  evaluations.
- Book history: the postquote sampler recorded only QUALIFYING rows, and
  `data/pmus/watch-*.jsonl` BBO exists for 08-27 only. The only per-skip book
  cadence covering all six days is the mapentry stream itself: rows for the
  same slug observe the venue book every ~23s median (96% of inter-quote gaps
  ≤130s). +60s and +120s states are read from the nearest quote row for the
  same slug in [t0+45s, t0+90s] / [t0+105s, t0+150s]; skips without a sample
  in-window are excluded from that horizon's denominator (samples: +60s
  2,500/4,282; +120s 2,730/4,282).
- Cross-in: our-side quote (long = venue mid; short = 1 − mid) inside
  [0.50, 0.80]. CI: series-cluster bootstrap, 1000 resamples, 95% percentile.

## Results

| stratum (pts outside band) | n | cross@60s | cross@120s |
|---|---|---|---|
| ≤5 | 452 | 51.1% (139/272) [43.1–59.1] | 55.0% (165/300) [47.1–62.0] |
| 5–10 | 363 | 53.0% (116/219) [44.5–61.2] | 49.6% (113/228) [41.3–58.3] |
| >10 | 3,467 | 35.9% (721/2,009) [30.7–41.5] | 35.7% (787/2,202) [30.6–40.7] |
| ALL | 4,282 | 39.0% (976/2,500) [33.8–44.7] | 39.0% (1,065/2,730) [34.1–43.9] |

By side of band: above-band skips (p_map > 0.80) cross in at 44.8%/45.8%
(60s/120s); below-band (p_map < 0.50) at 33.5%/32.9%.

## Read (describe-only, no rule-change claim)

1. Cross-in is NOT near-zero: ~39% of band-skipped evaluations see the
   market inside the accepted band a minute later. The gate is rejecting a
   population that often re-prices into licensable territory within its own
   2-minute horizon.
2. But cross-in ≠ no-edge: for a skip to have been a missed entry, the
   market must cross in AND our discount must survive; these numbers bound
   the timing-loss ceiling, not the fill-quality question.
3. Edge-locality is weak: median distance-outside is nearly the same for
   crossers (18pts) vs non-crossers (20pts), and the >10pts stratum alone
   still crosses at ~36%. Deep out-of-band skips are not obviously honest
   no-edge by this measure.
4. Distance gradient exists at the boundary only: ≤10pts strata cross at
   ~50-55%, >10pts at ~36% — most of the gradient is that flat-ish step,
   not a monotone decay.
5. Asymmetry: above-band skips (long side expensive) re-price into band
   ~12pts more often than below-band. Market moves downward past 0.80 more
   readily than upward past 0.50 — consistent with priced-in favorites
   drifting back rather than longshots waking up.
6. Caveat A: book history is the mapentry stream itself, so horizons sample
   only when later evaluations exist for the slug (58-64% coverage); the
   sampled subset skews toward actively-evaluated rounds.
7. Caveat B: nearest-row sampling (±15-22s windows) admits short-lived
   crossings; a persistence check (crossed and stayed) would lower these
   rates and is the natural follow-up before any gate-timing claim.
8. Caveat C: venue books are quoted on the 0.01 grid with wide tops in thin
   rounds; a mid landing inside [0.50, 0.80] is not an executable 0.50-0.80
   entry on our side.
9. No claim here against the H-PRICEDIN1 verdict or the frozen band: this
   is a describe-only measurement of skip population dynamics, not a
   falsification test, and per-norule numbers are observational.
10. Suggested next measurement (not run): join crossers at +120s to
   round-settlement truth to split "crossed and won" from "crossed and
   lost" — that, not the cross-in rate, is what a widening decision needs.
