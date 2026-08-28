#!/usr/bin/env python3
"""Is the number we TRADE calibrated? p_map vs GRID's own map winners.

quant_eval measures q_round — the ROUND model — at ECE 0.014 on ~5k
instances. But the executor does not trade q_round: it trades ``p_map``,
the frozen race aggregation of q_round into a map-win probability, and
prices every order at ``p_map - ENTRY_MARGIN``. A well-calibrated round
model can still aggregate into a badly-calibrated map number, and that
failure would be invisible to every check we run.

GROUND TRUTH: scripts/grid_winners.py — the first-observation map-winner
oracle over GRID series-state capture (MR12 terminal-score validated,
ambiguous finals excluded). Team names come from the same GRID feed as
the mapentry rows, so the join is exact; rows whose team matches NEITHER
the winner nor the loser are EXCLUDED, never guessed.

Two populations, and the difference matters:
  ALL          every mapentry evaluation with a resolved map
  QUALIFYING   rows the entry rule accepted (what we would have traded)
  DEDUPED      one entry per (series, map, side) — what the engine's
               dedup key actually permits. This is the honest n: the
               qualifying population counts the same map book dozens of
               times and inflates any edge estimate accordingly.

Run: uv run python scripts/pmap_calibration.py [--out data/analysis/pmap_calibration-YYYYMMDD.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
US_TICK = 0.01
CEILING = 0.90


def load_winners(state_glob: str) -> dict:
    """Map winners from the oracle script (run as a subprocess so this stays
    a pure consumer of the frozen resolution rules)."""
    files = sorted(glob.glob(state_glob))
    if not files:
        return {}
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "grid_winners.py"), *files],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"grid_winners failed: {res.stderr[-400:]}")
    return json.loads(res.stdout)["winners"]


def load_rows(mapentry_glob: str) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(mapentry_glob)):
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "mapentry":
                    rows.append(r)
    return rows


def outcome(row: dict, winners: dict) -> float | None:
    """1.0 if the row's team won its map, 0.0 if it lost, None if unresolved
    or the name matches neither side (never guess an outcome)."""
    w = winners.get(f"{row.get('series_id')}:{row.get('map_number')}")
    if not w:
        return None
    team = row.get("team")
    if team == w.get("winner"):
        return 1.0
    if team == w.get("loser"):
        return 0.0
    return None


def posted_price(row: dict) -> float | None:
    """The price build_us_intent would post, or None if the row can't price."""
    ask, cap = row.get("side_ask"), row.get("entry_price_max")
    if not isinstance(ask, (int, float)) or not isinstance(cap, (int, float)):
        return None
    return round(min(float(ask) - US_TICK, float(cap), CEILING), 4)


def calibrate(pairs: list[tuple[float, float]]) -> dict:
    """Brier, skill vs the base rate, and a 10-bin reliability table."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    base = sum(y for _, y in pairs) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ref = base * (1 - base)
    bins: dict[int, list] = defaultdict(lambda: [0, 0.0])
    for p, y in pairs:
        i = min(int(p * 10), 9)
        bins[i][0] += 1
        bins[i][1] += y
    table, ece = [], 0.0
    for i in sorted(bins):
        cnt, wins = bins[i]
        pred, act = (i + 0.5) / 10, wins / cnt
        ece += cnt / n * abs(pred - act)
        table.append({"lo": i / 10, "hi": (i + 1) / 10, "n": cnt,
                      "predicted": round(pred, 4), "actual": round(act, 4),
                      "gap": round(act - pred, 4)})
    return {
        "n": n, "base_rate": round(base, 4), "brier": round(brier, 4),
        "brier_base_rate": round(ref, 4),
        "skill_vs_base_rate": round(1 - brier / ref, 4) if ref else None,
        "ece_10bin": round(ece, 4), "reliability": table,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapentry", default=str(ROOT / "data/shadow/mapentry-*.jsonl"))
    ap.add_argument("--state", default=str(ROOT / "data/grid/series_state-*.jsonl"))
    ap.add_argument("--out")
    a = ap.parse_args()

    winners = load_winners(a.state)
    rows = load_rows(a.mapentry)
    resolved = [(r, outcome(r, winners)) for r in rows]
    unjoinable = sum(
        1 for r, y in resolved
        if y is None and f"{r.get('series_id')}:{r.get('map_number')}" in winners
    )

    all_pairs = [(r["p_map"], y) for r, y in resolved
                 if y is not None and isinstance(r.get("p_map"), (int, float))]
    qual = [(r, y) for r, y in resolved
            if y is not None and r.get("qualifies")
            and isinstance(r.get("p_map"), (int, float))
            and posted_price(r) is not None]

    # DEDUPED: the engine enters each (series, map, side) at most once, so
    # every edge number computed on the raw qualifying rows is inflated by
    # however many rounds re-qualified the same book.
    seen: dict[tuple, tuple[float, float, float]] = {}
    for r, y in qual:
        key = (r.get("series_id"), r.get("map_number"), r.get("us_side"))
        if key not in seen:
            seen[key] = (r["p_map"], y, posted_price(r))

    def edge(items) -> dict:
        if not items:
            return {"n": 0}
        n = len(items)
        return {
            "n": n,
            "win_rate": round(sum(y for _, y, _ in items) / n, 4),
            "mean_posted_price": round(sum(px for _, _, px in items) / n, 4),
            "ev_per_contract": round(sum(y - px for _, y, px in items) / n, 4),
        }

    qual_items = [(r["p_map"], y, posted_price(r)) for r, y in qual]
    report = {
        "generated": __import__("datetime").datetime.now(
            __import__("datetime").UTC).isoformat(timespec="seconds"),
        "maps_resolved": len(winners),
        "rows_read": len(rows),
        "rows_unjoinable_excluded": unjoinable,
        "all_evaluations": calibrate(all_pairs),
        "qualifying": calibrate([(p, y) for p, y, _ in qual_items]),
        "edge_qualifying_rows": edge(qual_items),
        "edge_deduped_per_book": edge(list(seen.values())),
        "notes": [
            "Ground truth is scripts/grid_winners.py (MR12-validated, "
            "ambiguous finals excluded). Rows whose team matches neither "
            "the winner nor the loser are excluded, never guessed.",
            "edge_deduped_per_book is the honest n: one entry per map book, "
            "matching the engine's dedup key. edge_qualifying_rows counts "
            "the same book once per re-qualifying round and overstates n.",
            "Paper edge assumes a fill AT the posted price. A resting maker "
            "order fills only when flow crosses INTO it, so realized edge is "
            "this number minus fill selection — which is why a positive "
            "number here is consistent with negative realized PnL.",
        ],
    }
    out = json.dumps(report, indent=2)
    if a.out:
        Path(a.out).write_text(out + "\n")
        print(f"wrote {a.out}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
