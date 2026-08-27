"""Live-night status panel. Read-only. Run:  watch -n 5 uv run python scripts/watch_night.py"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UNITS = ["gridbot-capture", "gridbot-shadow", "gridbot-mapentry",
         "gridbot-executor", "gridbot-postquote"]
WINDOW_NS = 24 * 3600 * 10**9  # rolling 24h


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def window_rows(subdir: str, stem: str) -> list[dict]:
    """Rows from the last 24h across yesterday's and today's day files,
    OLDEST FIRST so ``[-N:]`` panel tails show the freshest records.

    A night spans UTC midnight (17:00 PT = 00:00Z): placed rows sit in
    yesterday's file while their cancels land in today's, so reading one
    day file resets the panel mid-session and breaks resting arithmetic.
    Within one file appends are chronological and every row of the older
    day predates every row of the newer one, so reading older-file-first
    restores global time order across the seam.
    """
    lo_ns = time.time_ns() - WINDOW_NS
    now = datetime.now(UTC)
    out = []
    for age in (1, 0):
        day = f"{now - timedelta(days=age):%Y%m%d}"
        out.extend(r for r in rows(DATA / subdir / f"{stem}-{day}.jsonl")
                   if isinstance(r.get("ts_ns"), int) and r["ts_ns"] >= lo_ns)
    return out


def age_s(path: Path) -> int | None:
    return int(time.time() - path.stat().st_mtime) if path.exists() else None


def main() -> None:
    print(f"== gridbot night watch  {datetime.now(UTC):%H:%M:%S}Z ==")

    act = subprocess.run(["systemctl", "--user", "is-active", *UNITS],
                         capture_output=True, text=True).stdout.split()
    print("daemons:", " ".join(f"{u.split('-')[1]}:{a}" for u, a in zip(UNITS, act)))
    n_eng = subprocess.run(["pgrep", "-fc", "gridbot.executor.engine"],
                           capture_output=True, text=True).stdout.strip()
    daemon_up = act[3] == "active" if len(act) > 3 else False
    armed = int(n_eng or 0) > 0 and not daemon_up
    print(f"engine: {'🔴 ARMED session (daemon stopped)' if armed else 'dry-run daemon' if daemon_up else '⚠️ NO ENGINE RUNNING'}")

    # Freshest of today/yesterday: just after 00:00Z today's file is new
    # (a tiny age) while capture death pre-midnight must still show as an
    # aging yesterday file, not vanish.
    ages = []
    for age in (0, 1):
        d = f"{datetime.now(UTC) - timedelta(days=age):%Y%m%d}"
        a = age_s(DATA / "grid" / f"series_state-{d}.jsonl")
        if a is not None:
            ages.append(a)
    ss_age = min(ages) if ages else None
    print(f"grid feed: {'no file' if ss_age is None else f'{ss_age}s ago' + (' ⚠️ STALE' if ss_age > 120 else '')}")

    me = window_rows("shadow", "mapentry")
    booked = [r for r in me if r.get("market_slug")]
    qual = [r for r in booked if r.get("qualifies")]
    inband = [r for r in qual if isinstance(r.get("p_map"), (int, float)) and 0.50 <= r["p_map"] <= 0.80]
    print(f"mapentry: {len(me)} rows | with book {len(booked)} | QUALIFY {len(qual)} (in-band {len(inband)} / out {len(qual)-len(inband)})")
    for r in booked[-3:]:
        print(f"  {r.get('team','?')[:14]} v {r.get('opponent','?')[:14]} m{r.get('map_number')}r{r.get('round_number')} "
              f"pmap={r.get('p_map')} ask={r.get('side_ask')} Q={'Y' if r.get('qualifies') else 'n'}")

    it = window_rows("executor", "intents")
    orders = [r for r in it if r.get("type") == "order_intent"]
    by = {}
    for r in orders:
        by[r.get("status", "?")] = by.get(r.get("status", "?"), 0) + 1
    placed = by.get("placed", 0)
    cxl = [r for r in it if r.get("type") == "cancel"]
    # Count CANCELS per order (24h window admits duplicate cancel rows for
    # one order — e.g. an age_s-less cancel followed by a reconciled one);
    # counting rows against placed ORDERS made resting arithmetic go negative.
    freed = len({r.get("order_id") for r in cxl if r.get("status") == "cancelled"})
    gone = len({r.get("order_id") for r in cxl if r.get("status") == "gone_before_cancel"})
    print(f"ledger: {by if by else 'no orders yet'} | clean-cancel {freed} | likely-fill {gone} | resting≈{max(0, placed - freed - gone)}")
    for r in orders[-4:]:
        print(f"  {r.get('status'):>18} {str(r.get('market_slug'))[-28:]} {r.get('us_side')} @{r.get('price')} x{r.get('shares')} oid={str(r.get('order_id'))[:12]}")
    for r in it[-6:]:
        if r.get("type") in ("cancel", "reconcile"):
            print(f"  {r['type']}: " + json.dumps({k: v for k, v in r.items() if k not in ('type', 'ts_ns')})[:110])

    pq = window_rows("shadow", "postquote")
    moves = [r["oriented_move"] for r in pq if r.get("oriented_move") is not None]
    if pq:
        mean = sum(moves) / len(moves) if moves else None
        print(f"postquote: {len(pq)} samples | mean oriented {mean:+.4f}" if mean is not None else f"postquote: {len(pq)} samples (no mids)")
    else:
        print("postquote: none yet")


if __name__ == "__main__":
    main()
