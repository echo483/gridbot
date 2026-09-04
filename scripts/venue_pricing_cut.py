"""Venue pricing cut: does the PMUS book we could hit reprice on GRID round ends?

Reads the timing chain's day JSON (``data/analysis/timing_chain-YYYYMMDD.json``,
the book-joined round-end events), re-scores every event on the MAP book (the
timing chain's own join) or the MATCH book (via the mapping's ``us_markets.match``
sides), buckets by the round's LEVERAGE (the race-model swing of P(map) between
winning and losing that round, p_fut=0.5, 12-12 -> 0.5 as in the frozen
aggregation), and reports, series-clustered:

* oriented mid move at +30/60/120s (coverage-aware, timing_chain rules), and
* the TAKER mark: buy the winner's side at the last quote at/before our
  observation (ask if long, 1-bid if short) and mark to that side's mid at +h.

``--spreads`` prints the spread distribution by book kind over the whole tape.
Measurement instrument (H-MAPENTRY1-NOTE9); no registry bar, no trading.

Run: uv run python scripts/venue_pricing_cut.py YYYYMMDD [--book map|match] [--spreads]
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from timing_chain import COVERAGE_SLACK_S, bootstrap_ci, load_mappings, load_rows

from gridbot import config

HORIZONS = (30, 60, 120, 300)
LEVERAGE_BUCKETS = (("swing<0.05", 0.0, 0.05), ("0.05-0.10", 0.05, 0.10),
                    ("0.10-0.20", 0.10, 0.20), (">=0.20", 0.20, 9.0))


@lru_cache(None)
def race(a: int, b: int, p: float = 0.5) -> float:
    """P(first to 13) from a-b with iid rounds at p; 12-12 -> 0.5."""
    if a >= 13:
        return 1.0
    if b >= 13:
        return 0.0
    if a == 12 and b == 12:
        return 0.5
    return p * race(a + 1, b, p) + (1 - p) * race(a, b + 1, p)


def swing(a: int, b: int) -> float:
    """Leverage of the round just played: winner now at a-b vs (a-1)-(b+1) had it lost."""
    return race(a, b) - race(a - 1, b + 1)


def scores_after(ss_rows: list[dict]) -> dict[tuple[str, int, int], dict[str, int | None]]:
    """(series_id, map_number, req_ns) -> {team: rounds won} from the ss polls."""
    out: dict[tuple[str, int, int], dict[str, int | None]] = {}
    for row in ss_rows:
        if row.get("type") != "ss" or not isinstance(row.get("req_ns"), int):
            continue
        state = (row.get("data") or {}).get("seriesState") or {}
        for g in state.get("games") or []:
            key = (str(row.get("series_id")), int(g.get("sequenceNumber") or 0), row["req_ns"])
            out[key] = {str(t.get("name")): t.get("score") for t in g.get("teams") or []}
    return out


def tape(bbo_rows: list[dict]) -> dict[str, list[tuple[int, float | None, float | None, float | None]]]:
    """slug -> sorted (req_ns, mid, bid, ask); one-sided/dead rows keep their time, no mid."""
    per: dict[str, list] = defaultdict(list)
    for rec in bbo_rows:
        if rec.get("type") != "pmus_bbo" or not isinstance(rec.get("req_ns"), int):
            continue
        b, a = rec.get("bid"), rec.get("ask")
        two = isinstance(b, (int, float)) and isinstance(a, (int, float)) and a > 0 and b > 0
        per[rec["slug"]].append((rec["req_ns"], (b + a) / 2 if two else None,
                                 b if two else None, a if two else None))
    for series in per.values():
        series.sort()
    return per


def quote_at(series: list, t_ns: int, slack_s: float = COVERAGE_SLACK_S):
    """(last two-sided row at/before t, covered?) — covered = a row within slack of t."""
    times = [x[0] for x in series]
    hi = bisect_right(times, t_ns)
    covered = hi > 0 and t_ns - times[hi - 1] <= int(slack_s * 1e9)
    i = hi - 1
    while i >= 0 and series[i][1] is None:
        i -= 1
    return (series[i] if i >= 0 else None), covered


def match_side(mapping: dict | None, winner: str, loser: str) -> tuple[str | None, str | None]:
    mk = ((mapping or {}).get("us_markets") or {}).get("match")
    if not mk:
        return None, None
    sides = {k.lower(): v for k, v in (mk.get("sides") or {}).items()}
    side = sides.get(winner.lower())
    if side is None and loser.lower() in sides:
        side = "short" if sides[loser.lower()] == "long" else "long"
    return mk.get("market_slug"), side


def score_events(events: list[dict], scores: dict, books: dict, mappings: dict,
                 book: str) -> list[dict]:
    out: list[dict] = []
    for ev in events:
        if ev.get("outcome") not in ("moved", "no_move_300s"):
            continue
        s = scores.get((ev["series_id"], ev["map_number"], ev["t_obs_ns"]))
        if not s or s.get(ev["winner_team"]) is None or s.get(ev["loser_team"]) is None:
            continue
        a, b = int(s[ev["winner_team"]]), int(s[ev["loser_team"]])
        e = dict(ev, a=a, b=b, swing=round(swing(a, b), 3))
        if book == "match":
            slug, side = match_side(mappings.get(e["series_id"]), e["winner_team"], e["loser_team"])
            if not slug or side is None:
                continue
            e["slug"], e["us_side"] = slug, side
        ser = books.get(e["slug"])
        if not ser:
            continue
        sign = 1.0 if e["us_side"] == "long" else -1.0
        q, _ = quote_at(ser, e["t_obs_ns"])
        before, _ = quote_at(ser, e["t_prev_ns"])
        if q is None or before is None:
            continue
        _, _mid, bid, ask = q
        e["entry_cost"] = round(ask if sign > 0 else 1 - bid, 4)
        e["spread"] = round(ask - bid, 4)
        e["book_first"] = e.get("lead_vs_obs_s") is not None and e["lead_vs_obs_s"] < 0
        for h in HORIZONS:
            m, covered = quote_at(ser, e["t_obs_ns"] + int(h * 1e9))
            if not covered or m is None:
                e[f"mid_{h}s"] = e[f"taker_{h}s"] = None
                continue
            e[f"mid_{h}s"] = round(sign * (m[1] - before[1]), 4)
            e[f"taker_{h}s"] = round((m[1] if sign > 0 else 1 - m[1]) - e["entry_cost"], 4)
        out.append(e)
    return out


def _blk(evs: list[dict], key: str) -> str:
    pairs = [(e["series_id"], e[key]) for e in evs if e.get(key) is not None]
    if len(pairs) < 3:
        return f"n={len(pairs)}"
    ci = bootstrap_ci(pairs)
    pos = sum(1 for _, v in pairs if v > 0) / len(pairs)
    s = f"{ci['point']:+.4f}"
    if ci["ci95"] is not None:
        s += f" [{ci['ci95'][0]:+.3f},{ci['ci95'][1]:+.3f}]"
    return s + f" n={len(pairs)} ser={ci['n_series']} pos={pos:.0%}"


def render(day: str, book: str, rows: list[dict], n_events: int) -> str:
    lines = [f"# venue pricing cut — {day} — {book.upper()} book",
             f"{n_events} book-joined round ends; {len(rows)} scored with score+quote", ""]
    for name, lo, hi in LEVERAGE_BUCKETS:
        evs = [e for e in rows if lo <= e["swing"] < hi]
        if not evs:
            continue
        spreads = sorted(e["spread"] for e in evs)
        lines.append(f"== {name}: n={len(evs)} series={len({e['series_id'] for e in evs})} "
                     f"book_first={sum(1 for e in evs if e['book_first'])} "
                     f"median_spread={spreads[len(spreads) // 2]:.2f}")
        for h in (30, 60, 120):
            lines.append(f"   oriented mid +{h}s   {_blk(evs, f'mid_{h}s')}")
        for h in HORIZONS:
            lines.append(f"   TAKER@quote +{h}s   {_blk(evs, f'taker_{h}s')}")
        lines.append("")
    return "\n".join(lines)


def spread_lines(bbo_rows: list[dict]) -> list[str]:
    sp: dict[str, list[float]] = defaultdict(list)
    dead: dict[str, int] = defaultdict(int)
    tot: dict[str, int] = defaultdict(int)
    for r in bbo_rows:
        if r.get("type") != "pmus_bbo":
            continue
        k = str(r.get("kind"))
        tot[k] += 1
        b, a = r.get("bid"), r.get("ask")
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and a > 0 and b > 0:
            sp[k].append(round(a - b, 2))
        else:
            dead[k] += 1
    out = ["# spread by book kind (whole tape)"]
    for k in sorted(sp):
        v = sorted(sp[k])
        n = len(v)
        out.append(f"{k}: rows={tot[k]} dead/one-sided={dead[k]} ({dead[k] / tot[k]:.0%}) "
                   f"spread p25={v[n // 4]:.2f} p50={v[n // 2]:.2f} p75={v[3 * n // 4]:.2f} "
                   f"p90={v[int(n * 0.9)]:.2f} share<=0.02={sum(1 for x in v if x <= 0.02) / n:.0%}")
    return out


def main(argv: list[str] | None = None, data: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("day")
    ap.add_argument("--book", choices=["map", "match"], default="map")
    ap.add_argument("--spreads", action="store_true")
    args = ap.parse_args(argv)
    data = data or config.DATA_DIR
    tc_path = data / "analysis" / f"timing_chain-{args.day}.json"
    if not tc_path.exists():
        print(f"no timing chain JSON for {args.day}: {tc_path}", file=sys.stderr)
        return 1
    events = json.loads(tc_path.read_text())["events"]
    bbo = load_rows(data / "pmus" / f"books-{args.day}.jsonl")
    scores = scores_after(load_rows(data / "grid" / f"series_state-{args.day}.jsonl"))
    mappings = load_mappings(data / "pmus", args.day)
    rows = score_events(events, scores, tape(bbo), mappings, args.book)
    n_events = sum(1 for e in events if e.get("outcome") in ("moved", "no_move_300s"))
    print(render(args.day, args.book, rows, n_events))
    if args.spreads:
        print("\n".join(spread_lines(bbo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
