"""Timing profile of the signal→quote→outcome path (STATE queue 11).

Read-only over local day files. For every placed (and dry_run) intent:
- signal latency: GRID observation (grid_ts_ns on the mapentry row) →
  intent ledger write (ts_ns) — how fast the model reacts;
- quote lifetime: placed → cancel row age_s (TTL calibration truth);
- post-quote drift: oriented mid moves at +30/60/120s from the postquote
  sampler for the same book/side — did the book come TOWARD our quote
  (we were early = good) or run away (we were late/adverse)?

Run: uv run python scripts/timing_profile.py [YYYYMMDD]
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def rows_both_days(directory: Path, stem: str, day: str) -> list[dict]:
    """Day plus day-1 rows, oldest first (day files partition at UTC midnight)."""
    cur = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:8]}")
    prev = f"{cur - timedelta(days=1):%Y%m%d}"
    out = []
    for d in (prev, day):
        out.extend(rows(directory / f"{stem}-{d}.jsonl"))
    return out


def main() -> None:
    explicit = len(sys.argv) > 1
    day = sys.argv[1] if explicit else f"{datetime.now(UTC):%Y%m%d}"
    # Cross-midnight sessions split rows across day files (mapentry row
    # pre-midnight, its intent post-midnight), so an unattended no-arg run
    # must join across the boundary; an explicit day stays single-file.
    if explicit:
        intents = rows(DATA / "executor" / f"intents-{day}.jsonl")
        mapentry = rows(DATA / "shadow" / f"mapentry-{day}.jsonl")
        postquote = rows(DATA / "shadow" / f"postquote-{day}.jsonl")
    else:
        intents = rows_both_days(DATA / "executor", "intents", day)
        mapentry = rows_both_days(DATA / "shadow", "mapentry", day)
        postquote = rows_both_days(DATA / "shadow", "postquote", day)

    me_by_key: dict[tuple, dict] = {}
    for r in mapentry:
        k = (r.get("series_id"), r.get("map_number"), r.get("round_number"),
             r.get("us_side"), r.get("market_slug"))
        me_by_key.setdefault(k, r)

    cancels = {str(r.get("order_id")): r for r in intents if r.get("type") == "cancel"}

    print(f"== timing profile {day} ==")
    sig_lat, ages = [], []
    for r in intents:
        if r.get("type") != "order_intent" or r.get("status") not in ("placed", "dry_run"):
            continue
        k = (r.get("series_id"), r.get("map_number"), r.get("round_number"),
             r.get("us_side"), r.get("market_slug"))
        me = me_by_key.get(k)
        lat_ms = None
        if me and isinstance(me.get("grid_ts_ns"), int) and isinstance(r.get("ts_ns"), int):
            lat_ms = (r["ts_ns"] - me["grid_ts_ns"]) / 1e6
            if r.get("status") == "placed":
                sig_lat.append(lat_ms)
        cxl = cancels.get(str(r.get("order_id")))
        if cxl and isinstance(cxl.get("age_s"), (int, float)):
            ages.append(cxl["age_s"])
        if r.get("status") == "placed":
            print(f"  {str(r.get('market_slug'))[-32:]:>32} {r.get('us_side'):>5} "
                  f"@{r.get('price')} sig→ledger {lat_ms and round(lat_ms) or '?'}ms "
                  f"ttl {cxl and cxl.get('age_s') or '—'}s cancel={cxl and cxl.get('status')}")

    if sig_lat:
        print(f"\nsignal→ledger latency ms (placed, n={len(sig_lat)}): "
              f"min {min(sig_lat):.0f} | median {statistics.median(sig_lat):.0f} | "
              f"max {max(sig_lat):.0f}")
    if ages:
        print(f"quote lifetime s (n={len(ages)}): all {sorted({round(a) for a in ages})}")

    # postquote drift, split by side, oriented (+ = book moved in our favor)
    moves: dict[tuple[str, int], list[float]] = {}
    for r in postquote:
        m = r.get("oriented_move")
        off = r.get("dt_s")
        side = r.get("us_side")
        if isinstance(m, (int, float)) and isinstance(off, (int, float)):
            moves.setdefault((str(side), int(off)), []).append(float(m))
    if moves:
        print("\npost-quote oriented drift (mean, + = toward our position):")
        for side, off in sorted(moves):
            vals = moves[(side, off)]
            print(f"  {side:>5} +{off:>3}s: {statistics.mean(vals):+.4f} (n={len(vals)})")


if __name__ == "__main__":
    main()
