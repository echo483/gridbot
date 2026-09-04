"""Queue 16 band-skip cross-in measurement (analysis only, read-only).

Question (docs/superpowers/specs/2026-08-27-entry-gate-loosening.md, Queue 16):
67% of raw H-MAPENTRY1 intents die at the executor band gate (P_map outside
0.50-0.80, US_BAND_GATE). For each band-skipped evaluation, does the market
CROSS INTO the accepted band within 60-120s of the skip? A non-trivial cross-in
rate means the price-cadence read lags the edge (a timing problem the gate
inflicts on itself); near-zero means the skips were honest no-edge.

DATA
- data/shadow/mapentry-*.jsonl: every H-MAPENTRY1 evaluation (qualifying or
  not). Rows carry market_slug ('market_slug' here; 'slug' upstream in the
  venue payloads), us_side, p_map, and the venue LONG-side book top (bid/ask)
  captured at evaluation time.
- Book history for band-skipped rows: the postquote sampler recorded only
  QUALIFYING rows, and data/pmus/watch-*.jsonl BBO exists for 08-27 only.
  The only per-skip book cadence that covers all days is the mapentry stream
  itself: consecutive rows for the same slug observe the venue book every
  ~23s median (96% of gaps <= 130s over the corpus). We therefore read the
  +60s/+120s state from the NEXT quote-bearing rows for the same slug.

METHOD
- Population: rows with numeric p_map strictly outside [0.50, 0.80], a
  market_slug, a us_side in {long, short}, and a valid t0 book top (own row's
  bid/ask; both present, both in (0, 1] — the code's half-open check).
- Dedup: many rows re-evaluate the same round (slate + per-hypothesis triggers
  + dedup_extra variants land within the same second). Keep the FIRST row per
  (series_id, map_number, round_number, slug, us_side).
- Our-side quote: us_side long -> long-side mid (bid+ask)/2; short -> 1 - mid.
  Cross-in at a horizon = our-side quote inside [0.50, 0.80].
- Horizons: +60s = nearest quote row for the slug in [t0+45s, t0+90s];
  +120s = nearest in [t0+105s, t0+150s]. Missing sample = not observed
  (excluded from that horizon's denominator, reported).
- Strata: distance of p_map outside the band edge: <=5pts, 5-10pts, >10pts.
- CI: nonparametric bootstrap over series_ids (1000 resamples, 95% percentile).

Output: stdout tables (also consumed by docs/analysis/2026-08-31-queue16-bandskip.md).
"""
from __future__ import annotations

import collections
import glob
import json
import random
import statistics
import sys
from pathlib import Path

BAND_LO, BAND_HI = 0.50, 0.80
H1_LO, H1_HI = 45.0, 90.0      # +60s acceptance window (s)
H2_LO, H2_HI = 105.0, 150.0    # +120s acceptance window (s)
DATA = Path("/home/ops/gridbot/data")
BOOTSTRAP_N = 1000


def our_side_quote(us_side: str, bid: float, ask: float) -> float | None:
    if bid is None or ask is None:
        return None
    if not (0.0 < bid <= 1.0 and 0.0 < ask <= 1.0):
        return None  # zero prices are dead-book placeholders, not prices
    if bid > ask:
        return None
    mid = (bid + ask) / 2.0
    return mid if us_side == "long" else round(1.0 - mid, 6)


def in_band(p: float) -> bool:
    return BAND_LO <= p <= BAND_HI


def distance_outside(p: float) -> float:
    return (BAND_LO - p) if p < BAND_LO else (p - BAND_HI)


def stratum(d: float) -> str:
    if d <= 0.05:
        return "<=5pts"
    if d <= 0.10:
        return "5-10pts"
    return ">10pts"


def load_rows() -> tuple[list[dict], dict[str, list[tuple[int, float, float]]]]:
    """(deduped band-skip rows with valid t0 book, per-slug quote timeline)."""
    skips: dict[tuple, dict] = {}
    timeline: dict[str, list[tuple[int, float, float]]] = collections.defaultdict(list)
    files = sorted(glob.glob(str(DATA / "shadow" / "mapentry-*.jsonl")))
    for path in files:
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "mapentry":
                    continue
                slug = r.get("market_slug")
                us_side = r.get("us_side")
                bid, ask = r.get("bid"), r.get("ask")
                ts = r.get("ts_ns")
                if not slug or us_side not in ("long", "short") or not ts:
                    continue
                quote = our_side_quote(us_side, bid, ask) if us_side else None
                # long-side book top feeds the timeline in venue terms
                l_bid, l_ask = bid, ask
                if l_bid is not None and l_ask is not None and not (
                    0.0 < l_bid <= 1.0 and 0.0 < l_ask <= 1.0 and l_bid <= l_ask
                ):
                    l_bid = l_ask = None
                if l_bid is not None:
                    timeline[slug].append((ts, l_bid, l_ask))
                p = r.get("p_map")
                if not isinstance(p, (int, float)) or isinstance(p, bool):
                    continue
                p = float(p)
                if in_band(p) or quote is None:
                    continue
                key = (str(r.get("series_id")), r.get("map_number"),
                       r.get("round_number"), slug, us_side)
                row = {
                    "key": key,
                    "series_id": str(r.get("series_id")),
                    "slug": slug,
                    "us_side": us_side,
                    "p_map": p,
                    "quote0": quote,
                    "t0": ts,
                }
                prev = skips.get(key)
                if prev is None or ts < prev["t0"]:
                    skips[key] = row
    for _rows in timeline.values():
        _rows.sort()
    return sorted(skips.values(), key=lambda r: r["t0"]), timeline


def sample_at(timeline: list[tuple[int, float, float]], us_side: str,
              t0: int, lo_s: float, hi_s: float) -> float | None:
    """Our-side quote from the nearest venue quote row in [t0+lo, t0+hi]."""
    lo, hi = t0 + lo_s * 1e9, t0 + hi_s * 1e9
    best, best_dt = None, None
    for ts, bid, ask in timeline:
        if ts < lo:
            continue
        if ts > hi:
            break
        dt = abs(ts - (t0 + (lo_s + hi_s) / 2.0 * 1e9))
        if best_dt is None or dt < best_dt:
            best_dt = dt
            best = our_side_quote(us_side, bid, ask)
    return best


def analyse(skips: list[dict], timeline: dict) -> dict:
    out_rows = []
    for r in skips:
        tl = timeline.get(r["slug"], [])
        q60 = sample_at(tl, r["us_side"], r["t0"], H1_LO, H1_HI)
        q120 = sample_at(tl, r["us_side"], r["t0"], H2_LO, H2_HI)
        out_rows.append({
            **r,
            "stratum": stratum(distance_outside(r["p_map"])),
            "below_band": r["p_map"] < BAND_LO,
            "cross60": None if q60 is None else in_band(q60),
            "cross120": None if q120 is None else in_band(q120),
        })
    return {"rows": out_rows}


def rate(rows: list[dict], field: str) -> tuple[float, int, int] | None:
    obs = [r[field] for r in rows if r[field] is not None]
    if not obs:
        return None
    return sum(obs) / len(obs), sum(obs), len(obs)


def fmt_rate(rows: list[dict], field: str) -> str:
    rr = rate(rows, field)
    if rr is None:
        return "  n/a (no samples)"
    return f"{rr[0]*100:5.1f}%  ({rr[1]}/{rr[2]})"


def bootstrap_ci(rows: list[dict], field: str,
                 rng: random.Random) -> tuple[float, float] | None:
    obs = [r for r in rows if r[field] is not None]
    if len(obs) < 20:
        return None
    series = collections.defaultdict(list)
    for r in obs:
        series[r["series_id"]].append(r)
    sids = list(series)
    stats = []
    for _ in range(BOOTSTRAP_N):
        sample = []
        for s in rng.choices(sids, k=len(sids)):
            sample.extend(series[s])
        k = sum(r[field] for r in sample)
        stats.append(k / len(sample))
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats))]


def main() -> int:
    skips, timeline = load_rows()
    if not skips:
        print("no band-skip rows with valid t0 book found", file=sys.stderr)
        return 1
    res = analyse(skips, timeline)
    rows = res["rows"]

    print(f"== Queue 16 band-skip cross-in (band {BAND_LO}-{BAND_HI}) ==")
    print(f"deduped band-skip evaluations with valid t0 book: {len(rows)}")
    obs60 = sum(1 for r in rows if r["cross60"] is not None)
    obs120 = sum(1 for r in rows if r["cross120"] is not None)
    print(f"with +60s sample: {obs60}/{len(rows)}   with +120s sample: {obs120}/{len(rows)}")
    print()
    print("by side of band:")
    for label, sel in [("p_map < 0.50 (below band)", lambda r: r["below_band"]),
                       ("p_map > 0.80 (above band)", lambda r: not r["below_band"])]:
        sub = [r for r in rows if sel(r)]
        if not sub:
            continue
        print(f"  {label}: n={len(sub)}  cross@60s {fmt_rate(sub,'cross60')}  "
              f"cross@120s {fmt_rate(sub,'cross120')}")
    print()
    print("cross-in rate by distance from band edge (pts outside):")
    hdr = f"  {'stratum':<9} {'n':>5}  {'cross@60s':>16}  {'cross@120s':>16}"
    print(hdr)
    for st in ("<=5pts", "5-10pts", ">10pts"):
        sub = [r for r in rows if r["stratum"] == st]
        if not sub:
            print(f"  {st:<9} {0:>5}  {'-':>16}  {'-':>16}")
            continue
        ci60 = bootstrap_ci(sub, "cross60", random.Random(16))
        ci120 = bootstrap_ci(sub, "cross120", random.Random(16))
        def f(rr, ci):
            if rr is None:
                return "n/a"
            base = f"{rr[0]*100:.1f}% ({rr[1]}/{rr[2]})"
            if ci:
                base += f" [{ci[0]*100:.1f},{ci[1]*100:.1f}]"
            return base
        print(f"  {st:<9} {len(sub):>5}  {f(rate(sub,'cross60'), ci60):>24}  "
              f"{f(rate(sub,'cross120'), ci120):>24}")
    sub_all = rows
    ci60 = bootstrap_ci(sub_all, "cross60", random.Random(16))
    ci120 = bootstrap_ci(sub_all, "cross120", random.Random(16))
    def f2(rr, ci):
        if rr is None:
            return "n/a"
        s = f"{rr[0]*100:.1f}% ({rr[1]}/{rr[2]})"
        if ci:
            s += f" [{ci[0]*100:.1f},{ci[1]*100:.1f}]"
        return s
    print(f"  {'ALL':<9} {len(sub_all):>5}  {f2(rate(sub_all,'cross60'), ci60):>24}  "
          f"{f2(rate(sub_all,'cross120'), ci120):>24}")
    print()
    # median distance among crossers vs non-crossers (is cross-in edge-local?)
    cross = [distance_outside(r["p_map"]) for r in rows if r["cross120"]]
    nocross = [distance_outside(r["p_map"]) for r in rows
               if r["cross120"] is False]
    if cross and nocross:
        print(f"median distance-outside: crossed {statistics.median(cross)*100:.1f}pts "
              f"(n={len(cross)}) vs not-crossed {statistics.median(nocross)*100:.1f}pts "
              f"(n={len(nocross)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
