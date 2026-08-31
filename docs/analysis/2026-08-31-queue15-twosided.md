# Queue 15 — book two-sidedness of mapped PMUS map markets (analysis)

Date: 2026-08-31 · Lane: bulk data wrangler (analysis only) · Spec:
`docs/superpowers/specs/2026-08-27-entry-gate-loosening.md` §Queue 15.
No rule-change claims, no registry edits, no engine change. Scripts:
`scripts/_tmp_q15_*.py` (tmp-owned, not committed).

## What was measured

Per mapped PMUS **map** market (kind=map, "astatc-*" slugs): does the book ever
show two-sided quotes (long-side bid>0 AND ask>0), at what spread, pre-match vs
in-play, and does deadness correlate with match class? Universe = 473 distinct
map markets across 239 mapped series (mappings-20260826..31). BBO source =
shadow/mapentry + shadow/postquote rows (live gateway `markets.bbo`, long-side
bestBid/bestAsk.value; zero-price placeholders are dead on this venue and record
as None). Samples deduped on (slug, ts_ns). 14,296 in-play-era samples across
219 observed slugs; full-depth (`book`) side-channel present on 196 rows / 3 slugs only.

**Coverage caveat (read first).** `data/pm/books-*.jsonl` does NOT cover the
mapped astatc markets — that feed subscribes the gamma esports/LOL contest
catalog (~50-63 condition markets/day, zero astatc overlap). The engine's own
per-round gateway BBO reads (shadow/mapentry) are the only on-disk BBO for
mapped markets, and mapentry fires on series-state rounds: **pre-match capture
is effectively absent** (172 samples on just 3 slugs) and 254 mapped markets
(137 series) were never evaluated at all (Queue-16 day-rollover/start seam, not
a tradability signal). Also: no-BBO rows conflate gateway fetch failure with a
genuinely empty book — indistinguishable from disk data.

## Headline table

| metric | value |
|---|---|
| mapped map-kind markets (universe) | 473 (239 series) |
| markets with >=1 observed BBO sample | 219 (46%) |
| markets never evaluated (coverage seam) | 254 (137 series) |
| ever two-sided at ANY observation | 217/219 (99.1%) |
| in-play only: ever two-sided | 214/216 (99.1%) |
| pre-match: markets with any sample | 3 (172 samples, 91.9% two-sided) |
| never two-sided at any observation | 2 slugs |
| persistently dead (>=50% dead of >=5 samples) | 8 slugs (3.7% of observed) |
| of those, never two-sided | 2 |
| sample-level dead-book rate (one-sided + no-BBO) | 1,226/14,296 = 8.6% (one 7.0%, no-BBO 1.6%) |
| sample-level literally two-sided | 13,070/14,296 = 91.4% |

## Spread structure (two-sided samples, n=13,070)

| spread | share | note |
|---|---|---|
| <=0.04 (L3-quoteable) | 27.7% | |
| 0.04-0.10 | 27.8% | ~55.5% of samples within a 10c book |
| 0.10-0.25 | 17.2% | |
| 0.25-0.50 | 10.7% | |
| >0.50 ("ghost" book) | 16.5% | bid~0.01/ask~0.99 style — literally two-sided, economically dead |

Median spread 0.08, mean 0.24 (long wide tail), no crossed (ask<bid) samples.
Per-slug median-spread quantiles: p25 0.04 / p50 0.08 / p75 0.22 / p90 0.88.
130/219 slugs trade a median spread <=0.10; 32 slugs NEVER show a <=0.25 spread
(30 of them median >0.50 — the untradeable tail).
Spread by round phase: early(1-5) median 0.100, mid(6-12) 0.080, late(13+) 0.090 —
flat, no in-match tightening. Postquote drift horizons: median spread 0.060 at
+30s, +60s, +120s — stable within a placement window.

## Per-class breakdown (slug-level; class fields from grid discovery tournament names + observed mid)

Favorite-heaviness (|first observed mid - 0.5| >= 0.2): heavy 59 slugs → 90.7%
two-sided samples; balanced 156 slugs → 92.4%. **No favorite-heavy effect.**
4 slugs unclassifiable (no two-sided first sample) carry 2 of the 2 never-two-
sided markets.

Region (keyword heuristic on tournament name — describe-only):

| region | slugs | two-sided samples | ever two-sided |
|---|---|---|---|
| OTHER | 66 | 93.9% | 66/66 |
| EU | 61 | 93.8% | 59/61 (the 2 never-two-sided) |
| SA | 41 | 90.5% | 41/41 |
| NA | 40 | 90.9% | 40/40 |
| ASIA | 11 | 68.9% | 11/11 |

Asia's low sample rate is a single-event effect (ESL Challenger League S52 Cup
#1 Asia Playoffs: jsu-ae map1 1/118 two-sided, nexv-ra map1 1/68 — both nearly
dead) plus thin books elsewhere. Tournament view: every event except those two
series sits 86-97% two-sided; the wide-tail ghosts cluster in FRAG Midwest
St. Louis (6 map books at ~0.98 median spread + the dead playoff map3) and
CCT EU Play-Ins perm-misa (median 0.80-0.88 on both maps).

## Would a tradability pre-gate have blocked futility? (retro-sim, describe-only)

Strict gate — skip a slug after >=3 consecutive one-sided/no-BBO samples
(~6 min at per-round cadence):

- fires on 139/219 slugs; dead samples are streak-clustered (903 in >=3-run
  streaks vs 323 isolated);
- of 974 samples it would have gated, only 55 (5.6%) were live two-sided books;
- but it gates only 6.8% of all samples — strict deadness is rare and episodic,
  not a per-market property (only 2-8 persistently dead markets exist).

Gate variants treating wide-spread books as untradeable (same 3-consecutive
rule): spread<=0.10 → 32.4% of gated samples live; <=0.25 → 31.6%; <=0.50 →
22.4%. Wide-spread gating catches 3-6x more dead samples but blocks 22-32% of
books that were genuinely two-sided — not a free filter.

## Ten-line read

1. Mapped PMUS books are overwhelmingly two-sided in-play: 99.1% of observed
   markets, 91.4% of samples. The user's "only some mapped games are tradeable"
   is not literal one-sidedness. 2. The "untradable" part is the spread: 27% of
   two-sided samples quote >0.25 apart and 16.5% quote >0.50 apart (0.01/0.99-
   style ghosts) — books that are two-sided in name only. 3. Dead markets
   (never two-sided) are vanishingly rare (2/219); persistently dead markets
   are 8/219 — 2 absolutely dead (CCT EU nemi-bpl map2, bw-unity map1), the
   ESL Challenger Asia playoffs pair (~98-99% dead), and 4 partially dead
   late/odd maps (FRAG Midwest map3, nt-ot map2, mouz-9z map1, omg-td map2). 4. Deadness is episodic, not market-level: dead samples cluster in >=3-round
   streaks and 74% of all dead samples sit in streaks, but 139/219 slugs hit at
   least one streak — a market pre-gate would mislabel most markets. 5. A
   strict *window* gate (pause after 3 consecutive dead rounds) retro-cleans:
   94.4% of gated samples were futile, only 5.6% live, no engines touched. 6.
   Favorite-heaviness does not predict deadness (90.7% vs 92.4% two-sided) —
   the "heavily-favored favorites" hypothesis is not supported. 7. Region
   effects reduce to one Asia event — no structural region signal. 8. Spread
   gating (skip wide books) is not free: 22-32% of gated samples were live
   books; wide spreads come and go (median 0.08) and are not a stable per-market
   property. 9. Pre-match tradability is unmeasurable from disk: capture starts
   at first round; the 3 slugs with pre-match samples were two-sided. 10.
   Verdict (describe-only): a pre-gate keyed on persistent strict one-sidedness
   would have blocked futility almost cleanly but touches little; the untradable
   texture lives in spreads, which a hindsight gate cannot separate from quiet
   real books — no gate change is licensed by this data alone.

## Reproduction

```
uv run python scripts/_tmp_q15_measure.py   # headline, classes, strict retro-gate
uv run python scripts/_tmp_q15_pass2.py     # spread buckets, dead-slug roll call, full-depth
uv run python scripts/_tmp_q15_final.py     # per-slug median spread, never-evaluated series
uv run python scripts/_tmp_q15_gatevar.py   # spread-threshold gate variants
```