"""H-CROSSIN1: executable-price band cross-in backtest (analysis only).

Operationalizes the next measurement proposed by
docs/analysis/2026-08-31-queue16-phase2.md (item 10): reprice the band-skip
cross-in signal with EXECUTABLE our-side prices at FIRST cross plus a
persistence filter, instead of phase-2's single +120s MID sample.

POPULATION (engine-faithful, pinned by the dispatch; NOT phase-2's):
- data/shadow/mapentry-*.jsonl (all UTC day files, joined across day
  boundaries; a torn final line is skipped).
- rows: type=="mapentry", hypothesis_id=="H-MAPENTRY1", EXCLUDE slate rows
  (src_hypothesis_id=="H-MAPENTRY1-SLATE" — recording-only coverage rows the
  engine never trades; the phase-2 crosser delta this exclusion removes is
  reported).  numeric p_map strictly outside [0.50, 0.80]; market_slug
  present; us_side in {long, short}; own-row book alive (bid and ask present
  and > 0; zero/None = dead book).
- First evaluation per (series_id, map_number, round_number, market_slug,
  us_side) is the skip event t0 (phase-1/2 convention).

ENTRY RULE (the amendment candidate — frozen here, no tuning):
- Quote stream: later mapentry rows for the same market_slug (any round,
  ANY provenance — slate rows carry venue book tops too and phase-2's
  timeline included them), ordered by ts_ns, each carrying a valid two-sided
  long-side book top (0 < bid <= ask <= 1).
- Executable our-side price per quote row: long -> ask; short -> 1 - bid
  (mapentry.side_ask's orientation; a short on an opponent-named book buys
  the No leg).
- First crossing: first same-slug quote row in (t0, t0+300s] whose
  executable price is inside [0.50, 0.80].
- Persistence: the NEXT same-slug quote row within 180s after the crossing
  row must ALSO show an in-band executable price; no in-window next row or
  out-of-band => no entry (failed-persistence).
- Entry price = the FIRST crossing row's executable price (conservative:
  pay the ask at first cross, not the later confirmation price).
- Dedup: ONE entry per (series_id, map_number, market_slug) — engine-faithful
  one-entry-per-map-book; earliest qualifying entry (by crossing time) wins.
- Arms: above-band (p_map > 0.80) = the candidate; below-band (p_map < 0.50)
  = pre-declared control, computed identically, expected <= 0. Never pooled.

RESOLUTION: map-winner truth via the frozen oracle scripts/grid_winners.py
over data/grid/series_state-*.jsonl (subprocess, pure consumer — the exact
pattern of scripts/queue16_phase2_outcomes.py).  WON iff OUR team won the map
(both legs; us_side short = No leg on an opponent-named book).  Unresolvable
maps are excluded and counted, never guessed.

HOLDOUT (BOOKPIN1, registry 2026-08-31T19:53:39Z): series first mapped at or
after 2026-08-31T19:53:46Z (min ts_ns per grid_series_id across
data/pmus/mappings-*.jsonl; cutoff 1788206026000000000 ns) are HOLDOUT.
--design (default) = pre-holdout series only, output labeled design evidence;
--holdout = holdout series only and refuses to print metrics below 60
deduped entries (prints accrual count instead).

STRICTLY DESCRIBE-ONLY: no rule-change claim, no registry edit, no src/.
Run: uv run python scripts/crossin_backtest.py [--design|--holdout]
"""

from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

BAND_LO, BAND_HI = 0.50, 0.80
CROSS_WINDOW_S = 300.0     # first-crossing window after t0
PERSIST_WINDOW_S = 180.0   # confirmation window after the crossing row
BOOTSTRAP_N = 1000
BOOT_SEED = 17             # distinct from phase-1/2's 16 on purpose
HOLDOUT_CUTOFF_NS = 1788206026000000000  # 2026-08-31T19:53:46Z (BOOKPIN1)

SLATE_SRC = "H-MAPENTRY1-SLATE"
NS = 1_000_000_000

IN_SAMPLE_LABEL = "IN-SAMPLE DESIGN EVIDENCE — not a verdict"


# --------------------------------------------------------------------------
# loading


def _finite(v) -> float | None:
    """float(v) for a finite real number, else None (never raises)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def book_top(row: dict) -> tuple[float, float] | None:
    """Valid two-sided LONG-side book top (bid, ask) or None (dead book).

    Zero prices are dead-book placeholders on this venue; a crossed or
    one-sided top is not a readable book (phase-1/2 timeline convention).
    """
    bid, ask = _finite(row.get("bid")), _finite(row.get("ask"))
    if bid is None or ask is None:
        return None
    if not (0.0 < bid <= 1.0 and 0.0 < ask <= 1.0 and bid <= ask):
        return None
    return bid, ask


def exec_price(us_side: str, bid: float, ask: float) -> float | None:
    """Executable OUR-side price of the long-side book top.

    long buys the named team's Yes: pay the ask.  short buys the No leg:
    pay 1 - bid.  Orientation matches src/gridbot/instruments/mapentry.py
    side_ask exactly.
    """
    if us_side == "long":
        return ask
    if us_side == "short":
        return round(1.0 - bid, 6)
    return None


def in_band(p: float) -> bool:
    return BAND_LO <= p <= BAND_HI


def load_population(
    pattern: str | None = None,
) -> tuple[list[dict], dict[str, list[tuple[int, float, float]]], dict]:
    """(deduped skip rows, per-slug quote timeline, counters).

    Skip rows carry series_id, map_number, round_number, slug, us_side,
    p_map, team, opponent, t0 (ts_ns of the first evaluation).  The timeline
    is keyed by market_slug and holds every mapentry row's valid long-side
    book top — identical to phase-2's timeline construction (slate rows
    included: they are quotes of the same venue book).
    """
    pattern = pattern or str(DATA / "shadow" / "mapentry-*.jsonl")
    skips: dict[tuple, dict] = {}
    timeline: dict[str, list[tuple[int, float, float]]] = collections.defaultdict(list)
    c = collections.Counter({
        "mapentry_rows": 0, "no_slug_or_side": 0, "p_map_missing_or_in_band": 0,
        "dead_book_at_eval": 0, "slate_rows_excluded": 0, "skip_events_raw": 0,
    })
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn final line — skip
                if not isinstance(r, dict) or r.get("type") != "mapentry":
                    continue
                c["mapentry_rows"] += 1
                slug = r.get("market_slug")
                us_side = r.get("us_side")
                ts = r.get("ts_ns")
                if not slug or us_side not in ("long", "short") or not ts:
                    c["no_slug_or_side"] += 1
                    continue
                # timeline: every quote-bearing row for the slug (any round,
                # any provenance), phase-2 construction
                top = book_top(r)
                if top is not None:
                    timeline[str(slug)].append((int(ts), top[0], top[1]))
                if r.get("hypothesis_id") != "H-MAPENTRY1":
                    continue
                if str(r.get("src_hypothesis_id") or "") == SLATE_SRC:
                    c["slate_rows_excluded"] += 1
                    continue
                p = _finite(r.get("p_map"))
                if p is None or in_band(p):
                    c["p_map_missing_or_in_band"] += 1
                    continue
                if top is None:
                    # own-row book dead at evaluation time
                    c["dead_book_at_eval"] += 1
                    continue
                c["skip_events_raw"] += 1
                key = (str(r.get("series_id")), r.get("map_number"),
                       r.get("round_number"), str(slug), us_side)
                row = {
                    "key": key,
                    "series_id": str(r.get("series_id")),
                    "map_number": r.get("map_number"),
                    "round_number": r.get("round_number"),
                    "team": r.get("team"),
                    "opponent": r.get("opponent"),
                    "slug": str(slug),
                    "us_side": us_side,
                    "p_map": p,
                    "t0": int(ts),
                }
                prev = skips.get(key)
                if prev is None or ts < prev["t0"]:
                    skips[key] = row
    for rows in timeline.values():
        rows.sort()
    return sorted(skips.values(), key=lambda r: r["t0"]), dict(timeline), dict(c)


# --------------------------------------------------------------------------
# entry simulation


def find_first_crossing(
    tl: list[tuple[int, float, float]], us_side: str, t0: int,
    terminal_ns: int | None = None,
) -> tuple[int, float, int | None, float | None] | None:
    """First in-band executable quote in (t0, t0+CROSS_WINDOW].

    Returns (cross_ts, entry_price, confirm_ts, confirm_price) where the
    confirm fields are the NEXT quote row within PERSIST_WINDOW after the
    crossing row (None when no such row exists).

    ``terminal_ns`` is the oracle's first terminal observation for the map
    (registry-draft pin F1): quote rows at/after it are settlement-informed
    and are excluded from both the crossing and the persistence windows.
    (Empirically zero rows on the 08-26..09-01 corpus — the quote stream
    dies with the rounds — but the holdout bar requires the exclusion.)
    """
    lo, hi = t0 + 1, int(t0 + CROSS_WINDOW_S * NS)
    if terminal_ns is not None:
        hi = min(hi, terminal_ns - 1)
    cross_i = None
    for i, (ts, bid, ask) in enumerate(tl):
        if ts < lo:
            continue
        if ts > hi:
            break
        px = exec_price(us_side, bid, ask)
        if px is not None and in_band(px):
            cross_i = i
            break
    if cross_i is None:
        return None
    cts, cbid, cask = tl[cross_i]
    entry = exec_price(us_side, cbid, cask)
    if entry is None:  # unreachable given the crossing predicate; fail closed
        return None
    # persistence: the NEXT quote row within PERSIST_WINDOW after the crossing
    p_lo, p_hi = cts + 1, int(cts + PERSIST_WINDOW_S * NS)
    if terminal_ns is not None:
        p_hi = min(p_hi, terminal_ns - 1)
    confirm_ts = confirm_px = None
    for ts, bid, ask in tl[cross_i + 1:]:
        if ts < p_lo:
            continue
        if ts > p_hi:
            break
        confirm_ts, confirm_px = ts, exec_price(us_side, bid, ask)
        break
    return cts, entry, confirm_ts, confirm_px


def simulate_entries(
    skips: list[dict], timeline: dict[str, list[tuple[int, float, float]]],
    terminal_by_map: dict[tuple, int] | None = None,
) -> tuple[list[dict], dict]:
    """Entry simulation over the deduped skip events.

    Returns (entries, counters).  Entries carry entry_price (= first-cross
    executable price), cross_ts, arm, and pass the one-entry-per-map-book
    dedup: earliest crossing per (series_id, map_number, slug) wins.

    ``terminal_by_map`` maps (series_id, map_number) -> the oracle's first
    terminal observation ts_ns; crossings/confirmations at/after it are
    settlement-informed and excluded (registry-draft pin F1).
    """
    c = collections.Counter({
        "skips": 0, "no_cross_in_300s": 0, "crossed": 0,
        "failed_persistence": 0, "entries_before_dedup": 0,
        "dedup_slots_lost": 0, "entries": 0,
    })
    cands: list[dict] = []
    for r in skips:
        c["skips"] += 1
        term = (terminal_by_map or {}).get((r["series_id"], r["map_number"]))
        hit = find_first_crossing(
            timeline.get(r["slug"], []), r["us_side"], r["t0"], term)
        if hit is None:
            c["no_cross_in_300s"] += 1
            continue
        cts, entry, confirm_ts, confirm_px = hit
        c["crossed"] += 1
        if confirm_ts is None or confirm_px is None or not in_band(confirm_px):
            c["failed_persistence"] += 1
            continue
        arm = "above" if r["p_map"] > BAND_HI else "below"
        c["entries_before_dedup"] += 1
        cands.append({**r, "arm": arm, "cross_ts": cts, "entry_price": entry,
                      "confirm_ts": confirm_ts})
    # engine-faithful dedup: one entry per (series, map, book); earliest
    # qualifying crossing wins the slot
    seen: dict[tuple, dict] = {}
    for cand in sorted(cands, key=lambda x: (x["cross_ts"], x["t0"])):
        key = (cand["series_id"], cand["map_number"], cand["slug"])
        if key in seen:
            c["dedup_slots_lost"] += 1
            continue
        seen[key] = cand
    entries = sorted(seen.values(), key=lambda x: x["cross_ts"])
    c["entries"] = len(entries)
    return entries, dict(c)


# --------------------------------------------------------------------------
# holdout split


def series_first_mapped_ns(pattern: str | None = None) -> dict[str, int]:
    """min ts_ns per grid_series_id across data/pmus/mappings-*.jsonl."""
    pattern = pattern or str(DATA / "pmus" / "mappings-*.jsonl")
    first: dict[str, int] = {}
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid, ts = r.get("grid_series_id"), r.get("ts_ns")
                if sid is None or ts is None:
                    continue
                sid, ts = str(sid), int(ts)
                if sid not in first or ts < first[sid]:
                    first[sid] = ts
    return first


def split_holdout(
    rows: list[dict], first_mapped: dict[str, int]
) -> tuple[list[dict], list[dict]]:
    """(pre-holdout rows, holdout rows) on the BOOKPIN1 first-mapped cutoff."""
    design, holdout = [], []
    for r in rows:
        fm = first_mapped.get(r["series_id"])
        (holdout if (fm is not None and fm >= HOLDOUT_CUTOFF_NS) else design).append(r)
    return design, holdout


# --------------------------------------------------------------------------
# resolution (frozen oracle)


def load_map_winners(state_pattern: str | None = None) -> tuple[dict, list[dict]]:
    """(winners, excluded) from the frozen map-winner oracle (pure consumer)."""
    state_pattern = state_pattern or str(DATA / "grid" / "series_state-*.jsonl")
    files = sorted(glob.glob(state_pattern))
    if not files:
        return {}, []
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "grid_winners.py"), *files],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"grid_winners failed: {res.stderr[-400:]}")
    payload = json.loads(res.stdout)
    return payload.get("winners", {}), payload.get("excluded", [])


def resolve(row: dict, winners: dict, excluded: list[dict]) -> tuple[bool | None, str]:
    """(won for our side, status); None won => unresolved (never guessed).

    WON = OUR team won the map, for BOTH legs: us_side short = the No leg of
    an opponent-named book, which settles in our favor exactly when our team
    wins (phase-2's orientation, trace-verified there).
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
    team = row.get("team")
    if w.get("winner") == team:
        return True, "resolved"
    if w.get("loser") == team:
        return False, "resolved"
    return None, "team_unmatched_to_winner_or_loser"


# --------------------------------------------------------------------------
# statistics


def edge_rows(entries: list[dict]) -> dict[str, list[float]]:
    """Resolved entries: per-series settled edges (won - entry_price)."""
    by_series: dict[str, list[float]] = collections.defaultdict(list)
    for e in entries:
        if e["won"] is not None:
            by_series[e["series_id"]].append((1.0 if e["won"] else 0.0) - e["entry_price"])
    return by_series


def bootstrap_ci(by_series: dict[str, list[float]]) -> tuple[float, float] | None:
    """95% percentile CI for the mean settled edge, resampling SERIES (never
    row-pooled — the repo retracted a headline over row-pooling)."""
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


def day_of(ts_ns: int) -> str:
    return datetime.datetime.fromtimestamp(
        ts_ns / NS, datetime.UTC).strftime("%Y-%m-%d")


def summarize(entries: list[dict]) -> dict:
    resolved = [e for e in entries if e["won"] is not None]
    won = [e for e in resolved if e["won"]]
    by_series = edge_rows(entries)
    mean_edge = (
        sum(x for v in by_series.values() for x in v) /
        sum(len(v) for v in by_series.values())
    ) if by_series else None
    return {
        "n_entries": len(entries),
        "n_series": len({e["series_id"] for e in entries}),
        "n_resolved": len(resolved),
        "n_won": len(won),
        "n_lost": len(resolved) - len(won),
        "n_unresolved": len(entries) - len(resolved),
        "win_rate": (len(won) / len(resolved)) if resolved else None,
        "mean_settled_edge": mean_edge,
        "edge_ci": bootstrap_ci(by_series),
        "median_entry_price": (
            statistics.median(e["entry_price"] for e in entries)
            if entries else None),
        "unres_reasons": dict(collections.Counter(
            e["res_status"] for e in entries if e["won"] is None)),
        "per_day": {
            day: {
                "entries": len(ds),
                "won": sum(1 for e in ds if e["won"] is True),
                "lost": sum(1 for e in ds if e["won"] is False),
            }
            for day, ds in sorted(collections.defaultdict(
                list, {d: [e for e in entries if day_of(e["cross_ts"]) == d]
                       for d in {day_of(e["cross_ts"]) for e in entries}}
            ).items())
        },
    }


# --------------------------------------------------------------------------
# phase-2 crosser delta (slate-exclusion finding)


def phase2_crossers(
    exclude_slate: bool, pattern: str | None = None
) -> int:
    """Count phase-2 crossers (+120s nearest-quote MID inside band) with or
    without the slate exclusion — the exclusion's delta on the phase-2
    crosser set is itself a finding, reported verbatim."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from queue16_bandskip_crossin import our_side_quote

    pattern = pattern or str(DATA / "shadow" / "mapentry-*.jsonl")
    skips: dict[tuple, dict] = {}
    timeline: dict[str, list[tuple[int, float, float]]] = collections.defaultdict(list)
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or r.get("type") != "mapentry":
                    continue
                slug = r.get("market_slug")
                us_side = r.get("us_side")
                ts = r.get("ts_ns")
                if not slug or us_side not in ("long", "short") or not ts:
                    continue
                top = book_top(r)
                if top is not None:
                    timeline[str(slug)].append((int(ts), top[0], top[1]))
                if r.get("hypothesis_id") != "H-MAPENTRY1":
                    continue
                if exclude_slate and str(
                        r.get("src_hypothesis_id") or "") == SLATE_SRC:
                    continue
                p = _finite(r.get("p_map"))
                if p is None or in_band(p) or top is None:
                    continue
                key = (str(r.get("series_id")), r.get("map_number"),
                       r.get("round_number"), str(slug), us_side)
                prev = skips.get(key)
                if prev is None or ts < prev["t0"]:
                    skips[key] = {"slug": str(slug), "us_side": us_side,
                                  "t0": int(ts)}
    for rows in timeline.values():
        rows.sort()
    n = 0
    for r in skips.values():
        lo, hi = r["t0"] + int(105 * NS), r["t0"] + int(150 * NS)
        best, best_dt = None, None
        for ts, bid, ask in timeline.get(r["slug"], []):
            if ts < lo:
                continue
            if ts > hi:
                break
            dt = abs(ts - (r["t0"] + int(127.5 * NS)))
            if best_dt is None or dt < best_dt:
                best_dt = dt
                best = our_side_quote(r["us_side"], bid, ask)
        if best is not None and in_band(best):
            n += 1
    return n


# --------------------------------------------------------------------------
# output


def fmt(x, dp=4):
    return "n/a" if x is None else f"{x:.{dp}f}"


def arm_table(arm_summaries: dict) -> str:
    lines = []
    for name, label in (("above", "above-band (p_map > 0.80) — CANDIDATE"),
                        ("below", "below-band (p_map < 0.50) — control")):
        s = arm_summaries.get(name)
        if not s:
            lines.append(f"  {label}: n/a")
            continue
        ci = s["edge_ci"]
        cistr = "" if ci is None else f" [{fmt(ci[0], 3)}, {fmt(ci[1], 3)}]"
        wr = s["win_rate"]
        wrstr = "n/a" if wr is None else f"{wr * 100:.1f}%"
        lines.append(
            f"  {label}\n"
            f"    entries {s['n_entries']} (series {s['n_series']})  "
            f"resolved {s['n_resolved']}  won {s['n_won']}  lost {s['n_lost']}  "
            f"unresolved {s['n_unresolved']}\n"
            f"    win% {wrstr}  median entry px {fmt(s['median_entry_price'], 3)}  "
            f"mean settled edge {fmt(s['mean_settled_edge'])}{cistr}"
        )
    return "\n".join(lines)


def run(mode: str = "design") -> dict:
    skips, timeline, counters = load_population()
    first_mapped = series_first_mapped_ns()
    design_skips, holdout_skips = split_holdout(skips, first_mapped)
    pool = design_skips if mode == "design" else holdout_skips
    winners, excluded = load_map_winners()
    terminal_by_map = {
        (w["series_id"], w["map_number"]): w["first_seen_ts_ns"]
        for w in winners.values() if w.get("first_seen_ts_ns") is not None
    }
    entries, ec = simulate_entries(pool, timeline, terminal_by_map)
    for e in entries:
        e["won"], e["res_status"] = resolve(e, winners, excluded)
    arms = {a: summarize([e for e in entries if e["arm"] == a])
            for a in ("above", "below")}
    return {
        "mode": mode,
        "label": IN_SAMPLE_LABEL if mode == "design" else "HOLDOUT ACCRUAL",
        "counters": counters,
        "entry_counters": ec,
        "n_skips_pool": len(pool),
        "n_skips_design": len(design_skips),
        "n_skips_holdout": len(holdout_skips),
        "n_series_holdout": len({
            r["series_id"] for r in holdout_skips}),
        "arms": arms,
        "n_map_winners": len(winners),
    }


HOLDOUT_MIN_ENTRIES = 60


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--design", dest="mode", action="store_const", const="design",
                   default="design", help="pre-holdout series only (default)")
    g.add_argument("--holdout", dest="mode", action="store_const",
                   const="holdout", help="holdout series only (accrual)")
    args = ap.parse_args()

    res = run(args.mode)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_json = DATA / "analysis" / f"crossin-{args.mode}-{stamp}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, sort_keys=True)

    print(f"== H-CROSSIN1 executable-price band cross-in "
          f"[{res['label']}] ==")
    print(f"mode: {args.mode}")
    c, ec = res["counters"], res["entry_counters"]
    print(f"mapentry rows read: {c['mapentry_rows']}  "
          f"skip events (raw): {c['skip_events_raw']}  "
          f"slate rows excluded: {c['slate_rows_excluded']}")
    print(f"deduped band-skip population (pool): {res['n_skips_pool']}  "
          f"(design {res['n_skips_design']} / holdout {res['n_skips_holdout']} "
          f"series {res['n_series_holdout']})")
    print(f"no cross within 300s: {ec['no_cross_in_300s']}  "
          f"first crosses: {ec['crossed']}  failed persistence: "
          f"{ec['failed_persistence']}  entries before dedup: "
          f"{ec['entries_before_dedup']}  dedup slots lost: "
          f"{ec['dedup_slots_lost']}  ENTRIES: {ec['entries']}")
    print(f"map-winner oracle: {res['n_map_winners']} resolved (series,map) keys")
    print()
    if args.mode == "holdout" and ec["entries"] < HOLDOUT_MIN_ENTRIES:
        print(f"HOLDOUT ACCRUAL: {ec['entries']} deduped entries — below the "
              f"{HOLDOUT_MIN_ENTRIES}-entry floor; metrics withheld "
              f"(full JSON written to {out_json}).")
        return 0
    print(arm_table(res["arms"]))
    for a, s in res["arms"].items():
        if s and s["per_day"]:
            print(f"  per-day [{a}]: " + ", ".join(
                f"{d}: {v['entries']}e {v['won']}W-{v['lost']}L"
                for d, v in s["per_day"].items()))
    print(f"\nJSON: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
