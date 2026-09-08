#!/usr/bin/env python3
"""Why did the GRID poller never discover a series the PMUS mapper knew about?

There are only two candidate answers, and they imply opposite fixes:

  (A) the poller was NOT RUNNING during the series' opportunity window
      -> fix uptime; widening any query window changes nothing
  (B) the poller WAS sweeping and the series still never came back
      -> the query window really is too narrow; widen it and pay the rate budget

The test is exact, because the opportunity window is exact. A sweep at time t
asks GRID for series scheduled in [t + DISCOVERY_WINDOW[0], t + DISCOVERY_WINDOW[1]].
Invert it: a series scheduled at S is discoverable by a sweep run at t iff

    t + BACK <= S <= t + FWD    <=>    t in [S - FWD, S - BACK]

which for the deployed (-4h, +1h) is [S-1h, S+4h] -- roughly twenty sweeps at the
15-minute cadence. If NO sweep timestamp falls in that interval, the poller was
down and cause (A) is proven. If sweeps did run in it, cause (B) is proven.

NOTE the sign. Reading the window in the same direction as the query -- asking
whether sweeps ran in [S-4h, S+1h] -- inverts the interval and reverses the
verdict. That error produced a wrong published cause on 2026-09-08.

Run: uv run python scripts/discovery_attribution.py [--days 20260906 20260907]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot import config

SWEEP_BUCKET_S = 300  # collapse a paginated sweep's rows into one observation


def _ts(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


def load_discovery(data_dir: Path) -> tuple[set[str], list[float]]:
    """Every series id the poller ever recorded, and every sweep instant."""
    seen: set[str] = set()
    buckets: set[int] = set()
    for path in sorted((data_dir / "grid").glob("discovery-*.jsonl")):
        with path.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = r.get("type")
                # discovery_sweep rows (added 2026-09-08) mark a sweep even when it
                # returned nothing; series rows are the only evidence before that.
                if kind not in ("series", "discovery_sweep"):
                    continue
                if kind == "series" and r.get("id") is not None:
                    seen.add(str(r["id"]))
                if r.get("ts_ns"):
                    buckets.add(int(r["ts_ns"] // 1e9) // SWEEP_BUCKET_S)
    return seen, sorted(b * SWEEP_BUCKET_S for b in buckets)


def load_mapped(day: str, data_dir: Path) -> dict[str, dict]:
    """Series the mapper matched to a PMUS event on ``day``."""
    out: dict[str, dict] = {}
    path = data_dir / "pmus" / f"mappings-{day}.jsonl"
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") != "grid_pmus_mapping" or not r.get("match"):
                continue
            out.setdefault(str(r["grid_series_id"]), {
                "sched": _ts(r.get("grid_start")),
                "slug": (r.get("match") or {}).get("pmus_event_slug"),
            })
    return out


def attribute(day: str, data_dir: Path, window: tuple[int, int]) -> dict:
    back, fwd = window
    seen, sweeps = load_discovery(data_dir)
    mapped = load_mapped(day, data_dir)
    day_end = (datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC).timestamp()) + 86400
    down, real, not_due = [], [], []
    for sid, d in sorted(mapped.items()):
        if sid in seen or d["sched"] is None:
            continue
        lo, hi = d["sched"] - fwd, d["sched"] - back   # [S-FWD, S-BACK]
        if lo > day_end:
            not_due.append((sid, d))
            continue
        n = sum(1 for t in sweeps if lo <= t <= hi)
        (real if n else down).append((sid, d, n))
    return {"day": day, "mapped": len(mapped),
            "discovered": sum(1 for s in mapped if s in seen),
            "capture_down": down, "real_window_gap": real, "not_due": not_due}


def render(rep: dict) -> str:
    out = [(f"\n=== {rep['day']}: {rep['mapped']} mapped series, "
            f"{rep['discovered']} discovered ===")]
    out.append(f"  (A) no sweep in the opportunity window -> CAPTURE DOWN : "
               f"{len(rep['capture_down'])}")
    for sid, d, _ in sorted(rep["capture_down"], key=lambda x: x[1]["sched"]):
        out.append(f"        sid={sid:<9} "
                   f"sched={datetime.fromtimestamp(d['sched'], UTC):%m-%d %H:%M}Z  {d['slug']}")
    out.append(f"  (B) sweeps ran, series never returned -> REAL WINDOW GAP: "
               f"{len(rep['real_window_gap'])}")
    for sid, d, n in sorted(rep["real_window_gap"], key=lambda x: x[1]["sched"]):
        out.append(f"        sid={sid:<9} "
                   f"sched={datetime.fromtimestamp(d['sched'], UTC):%m-%d %H:%M}Z  "
                   f"({n} sweeps)  {d['slug']}")
    out.append(f"  (C) window opens after the day ended (correctly untracked): "
               f"{len(rep['not_due'])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260906", "20260907"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    a = ap.parse_args(argv)
    for day in a.days:
        print(render(attribute(day, Path(a.data_dir), config.DISCOVERY_WINDOW)))
    print("\n(A) implies: fix uptime.  (B) implies: widen DISCOVERY_WINDOW.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
