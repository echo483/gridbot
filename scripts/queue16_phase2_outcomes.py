"""Queue 16 Phase 2: outcome join for band-skip crossers (analysis only, read-only).

Follow-up to scripts/queue16_bandskip_crossin.py (docs/analysis/
2026-08-31-queue16-bandskip.md, item 10): for each band-skipped H-MAPENTRY1
evaluation whose market CROSSED INTO the accepted band within +120s, join the
crosser to settlement truth to split "crossed and won" from "crossed and lost".

POPULATION: identical to phase 1 — the deduped band-skip set (p_map outside
[0.50, 0.80], mapped market_slug, us_side in {long,short}, valid t0 book),
crossers = rows whose our-side quote at +120s is inside the band. We reuse
phase-1's sampling/quote/stratum helpers verbatim (imported from
queue16_bandskip_crossin) and re-implement its row loader locally ONLY to
carry map_number/round_number/team through the dedup (phase-1's loader drops
them, and they are the join key here). The dedup key, population rules and
+120s sample are byte-identical, so the crosser set equals phase-1's.

RESOLUTION TRUTH (in priority order, all read-only):
1. Captured map resolution: scripts/grid_winners.py over data/grid/
   series_state-*.jsonl (the frozen first-observation map-winner oracle,
   MR12-validated). This is how src/gridbot/instruments/mapentry_verdict.py
   resolves H-MAPENTRY1 rows — H-MAPENTRY1 books are MAP-winner books (Yes side
   = the named team), so the map winner, not the round winner, is the market's
   settlement. Reused as a subprocess so this stays a pure consumer of the
   frozen oracle (never modified).
2. data/shadow/instances-*.jsonl: checked and REJECTED as an independent
   source — instance rows carry no resolution field (verdict.py derives
   resolved/won from the same grid capture), so they add no coverage beyond
   source 1.
3. data/grid/ capture: the raw source of source 1 (not a separate oracle).

WON for a crosser: the map-winner oracle names (winner, loser), and the
position settles in our favor iff OUR TEAM won the map — for BOTH legs. The
venue names one team as the Yes side of a map book; `us_side` is long when our
team is that named team and short when the OPPONENT is named (our team's win is
then the No leg). Either way we bet our team wins (phase-1's our-side quote is
P(our team wins): mid for long, 1-mid for short), so won == {our team won the
map} with no leg inversion. team matching NEITHER winner/loser name is
unresolved, never guessed. A crosser whose (series, map) never resolves in
capture is unresolved (resolution-coverage rate reported).

SETTLED EDGE per crossed side = win_rate - crossed_price, where crossed_price is
the mean our-side quote at the +120s crossing (the price the band would have
admitted). CI: series-cluster bootstrap (1000 resamples, 95% percentile).

STRICTLY DESCRIBE-ONLY: no rule-change claim, no registry edit, no src/ change.
This is pre-registration evidence for a possible band-widening amendment.

Run: uv run python scripts/queue16_phase2_outcomes.py
"""

from __future__ import annotations

import collections
import glob
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

from queue16_bandskip_crossin import (  # reuse phase-1 semantics verbatim
    BAND_HI,
    BAND_LO,
    H2_HI,
    H2_LO,
    distance_outside,
    in_band,
    our_side_quote,
    sample_at,
    stratum,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BOOTSTRAP_N = 1000
BOOT_SEED = 16  # matches phase-1's seed for continuity


# --------------------------------------------------------------------------
# loading — mirrors queue16_bandskip_crossin.load_rows + analyse EXACTLY, but
# carries the fields the outcome join needs. Same population, same dedup key,
# same +120s sample => same crosser set as phase 1.


def load_rows_rich() -> tuple[list[dict], dict[str, list[tuple[int, float, float]]]]:
    skips: dict[tuple, dict] = {}
    timeline: dict[str, list[tuple[int, float, float]]] = collections.defaultdict(list)
    for path in sorted(glob.glob(str(DATA / "shadow" / "mapentry-*.jsonl"))):
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
                quote = our_side_quote(us_side, bid, ask)
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
                    "map_number": r.get("map_number"),
                    "round_number": r.get("round_number"),
                    "team": r.get("team"),
                    "opponent": r.get("opponent"),
                    "side": r.get("side"),
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


# --------------------------------------------------------------------------
# resolution join


def load_map_winners(state_pattern: str) -> tuple[dict, list[dict]]:
    """(winners, excluded) from the frozen script (pure consumer, MR12-validated).

    Same approach as gridbot.instruments.mapentry_verdict.load_map_winners:
    subprocess so this module never re-implements the frozen resolution rules.
    `excluded` carries maps that finished in capture but could not be resolved
    (ambiguous_winner / impossible_score) — used to attribute unresolved
    crossers precisely.
    """
    files = sorted(glob.glob(state_pattern))
    if not files:
        return {}, []
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "grid_winners.py"), *files],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"grid_winners failed: {res.stderr[-400:]}")
    payload = json.loads(res.stdout)
    return payload.get("winners", {}), payload.get("excluded", [])


def team_won_map(row: dict, w: dict) -> bool | None:
    """True/False if the row's team matched winner/loser, None otherwise."""
    team = row.get("team")
    if w.get("winner") == team:
        return True
    if w.get("loser") == team:
        return False
    return None


def resolve(row: dict, winners: dict, excluded: list[dict]) -> tuple[bool | None, str]:
    """(won for our us_side, status). None won => unresolved (with reason).

    WON = our team won the map, for BOTH legs. The venue names ONE team as the
    Yes side of a map book; `us_side` is long when our team is that named team
    and short when the OPPONENT is named (our team's win is then the No leg).
    Either way the position settles in our favor iff OUR team wins the map —
    the same "p_map side" phase 1 quotes (long=mid, short=1-mid). Inverting
    short would double-count the leg for opponent-named books.
    """
    if row.get("map_number") is None:
        return None, "no_map_number"
    mkey = f"{row['series_id']}:{row['map_number']}"
    w = winners.get(mkey)
    if not w:
        for ex in excluded:
            if f"{ex.get('series_id')}:{ex.get('map_number')}" == mkey:
                return None, f"excluded:{ex.get('reason')}"
        return None, "no_map_winner_in_capture"
    tw = team_won_map(row, w)
    if tw is None:
        return None, "team_unmatched_to_winner_or_loser"
    return tw, "resolved"


def enrich(rows: list[dict], timeline: dict, winners: dict,
           excluded: list[dict]) -> list[dict]:
    """Attach +120s crossing quote, resolution and won to the skip rows."""
    out = []
    for r in rows:
        q120 = sample_at(timeline.get(r["slug"], []), r["us_side"], r["t0"],
                         H2_LO, H2_HI)
        cross120 = q120 is not None and in_band(q120)
        won, status = (None, "not_crossed") if not cross120 else resolve(
            r, winners, excluded)
        out.append({
            **r,
            "q120": q120,
            "cross120": cross120,
            "won": won,
            "res_status": status,
        })
    return out


def edge_rows(rows: list[dict]) -> dict[str, list[float]]:
    """Resolved crossers: per-series list of settled edge = won - crossed price."""
    by_series: dict[str, list[float]] = collections.defaultdict(list)
    for r in rows:
        if r["cross120"] and r["won"] is not None and r["q120"] is not None:
            by_series[r["series_id"]].append((1.0 if r["won"] else 0.0) - r["q120"])
    return by_series


def bootstrap_ci(by_series: dict[str, list[float]]) -> tuple[float, float] | None:
    series = list(by_series)
    if len(series) < 3:
        return None
    rng = random.Random(BOOT_SEED)
    vals = []
    for _ in range(BOOTSTRAP_N):
        sample: list[float] = []
        for _ in series:
            sample.extend(by_series[rng.choice(series)])
        if sample:
            vals.append(sum(sample) / len(sample))
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def summarize(rows: list[dict]) -> dict:
    crossers = [r for r in rows if r["cross120"]]
    resolved = [r for r in crossers if r["won"] is not None]
    won = [r for r in resolved if r["won"]]
    lost = [r for r in resolved if not r["won"]]
    unresolved = [r for r in crossers if r["won"] is None]
    cov = len(resolved) / len(crossers) if crossers else 0.0
    win_rate = len(won) / len(resolved) if resolved else None
    cross_price = (
        sum(r["q120"] for r in resolved) / len(resolved) if resolved else None
    )
    edge = (
        None
        if win_rate is None or cross_price is None
        else win_rate - cross_price
    )
    by_series = edge_rows(crossers)
    ci = bootstrap_ci(by_series)
    return {
        "n_crossers": len(crossers),
        "n_resolved": len(resolved),
        "n_won": len(won),
        "n_lost": len(lost),
        "n_unresolved": len(unresolved),
        "coverage": cov,
        "win_rate": win_rate,
        "cross_price_mean": cross_price,
        "edge": edge,
        "edge_ci": ci,
        "unres_reasons": collections.Counter(r["res_status"] for r in unresolved),
        "n_series": len({r["series_id"] for r in crossers}),
    }


def fmt_s(s: dict) -> str:
    def f(x, dp=4):
        return "n/a" if x is None else f"{x:.{dp}f}"

    ci = s["edge_ci"]
    cistr = "" if ci is None else f" [{f(ci[0],3)},{f(ci[1],3)}]"
    wr = s["win_rate"]
    winstr = "n/a" if wr is None else f"{wr*100:.1f}%"
    return (
        f"  crossers={s['n_crossers']} (series {s['n_series']})  "
        f"won {s['n_won']} / lost {s['n_lost']} / unresolved {s['n_unresolved']}  "
        f"cov {s['coverage']*100:.1f}%  win% {winstr}  "
        f"crosspx {f(s['cross_price_mean'],3)}  edge {f(s['edge'],4)}{cistr}"
    )


def main() -> int:
    skips, timeline = load_rows_rich()
    if not skips:
        print("no band-skip rows found", file=sys.stderr)
        return 1
    winners, excluded = load_map_winners(
        str(DATA / "grid" / "series_state-*.jsonl"))
    rows = enrich(skips, timeline, winners, excluded)
    crossers = [r for r in rows if r["cross120"]]
    if not crossers:
        print("no +120s crossers", file=sys.stderr)
        return 1

    print("== Queue 16 PHASE 2: band-skip crosser outcomes (band "
          f"{BAND_LO}-{BAND_HI}) ==")
    print(f"band-skip evaluations (phase-1 population): {len(rows)}")
    print(f"+120s crossers joined to settlement truth: {len(crossers)}")
    print(f"map-winner oracle coverage of crosser series: "
          f"{len(winners)} resolved (series,map) keys\n")

    overall = summarize(crossers)
    print("overall (all resolved crossers):")
    print(fmt_s(overall))
    print("  unresolved reasons: "
          + ", ".join(f"{k}:{v}" for k, v in overall["unres_reasons"].items()))

    print("\nby stratum (pts outside band edge):")
    for st in ("<=5pts", "5-10pts", ">10pts"):
        sub = [r for r in crossers if stratum(distance_outside(r["p_map"])) == st]
        if not sub:
            print(f"  {st:<9}: n/a")
            continue
        print(f"  {st:<9}: {fmt_s(summarize(sub))}")

    print("\nby side of band:")
    for label, sel in [("below-band p_map<0.50", lambda r: r["p_map"] < BAND_LO),
                       ("above-band p_map>0.80", lambda r: r["p_map"] > BAND_HI)]:
        sub = [r for r in crossers if sel(r)]
        if not sub:
            print(f"  {label}: n/a")
            continue
        print(f"  {label}: {fmt_s(summarize(sub))}")

    # sanity: median crossed price by outcome
    won_q = [r["q120"] for r in crossers if r["won"] is True]
    lost_q = [r["q120"] for r in crossers if r["won"] is False]
    if won_q and lost_q:
        print(f"\nmedian crossed price: won {statistics.median(won_q)*100:.1f}pt "
              f"(n={len(won_q)}) vs lost {statistics.median(lost_q)*100:.1f}pt "
              f"(n={len(lost_q)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
