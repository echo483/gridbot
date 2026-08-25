"""Measure gridbot's own observation-latency structure (the edge window).

The trading edge is a measured GRID-vs-market time lead; this report measures
OUR side of it: GRID poll round-trip latency, inter-poll cadence per live
series, capture errors, and instrument detect lag across shadow/replay data.

Deterministic: reads local day files only, no network, no writes.

Run: uv run python scripts/latency_report.py [--days N] [--include-test]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

from gridbot import config


def percentile(sorted_values: list[float], pct: float) -> float:
    """Return the ``pct`` percentile of pre-sorted values (linear interpolation)."""
    if not sorted_values:
        raise ValueError("percentile of empty list")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct / 100.0
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping malformed lines."""
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def is_test_series(rec: dict, include_test: bool) -> bool:
    """True when a record belongs to a permanent GRID test series."""
    return not include_test and str(rec.get("series_id")) in config.GRID_TEST_SERIES_IDS


def poll_latency_by_title(records: list[dict]) -> dict[str, list[float]]:
    """Group successful ss-poll latency_ms values by title."""
    out: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        if rec.get("type") != "ss":
            continue
        latency = rec.get("latency_ms")
        if isinstance(latency, (int, float)):
            out[str(rec.get("title", "unknown"))].append(float(latency))
    return dict(out)


def inter_poll_gaps(records: list[dict]) -> list[float]:
    """Gaps (s) between consecutive polls of a series while it was LIVE.

    Idle series poll at 60s by design; only a gap that FOLLOWS a live
    observation (started and not finished) measures the 12s live-cadence
    hold — mixing idle polls in would fake a breach.
    """
    by_series: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    for rec in records:
        if rec.get("type") != "ss":
            continue
        req_ns = rec.get("req_ns")
        if not isinstance(req_ns, int):
            continue
        state = (rec.get("data") or {}).get("seriesState") or {}
        live = bool(state.get("started")) and not state.get("finished")
        by_series[str(rec.get("series_id"))].append((req_ns, live))
    gaps: list[float] = []
    for stamps in by_series.values():
        stamps.sort()
        for (prev_ns, prev_live), (cur_ns, _) in pairwise(stamps):
            if prev_live:
                gaps.append((cur_ns - prev_ns) / 1e9)
    return gaps


def ss_errors_per_day(day_files: dict[Path, list[dict]]) -> dict[str, int]:
    """Count ss_error records per day file, keyed by YYYYMMDD stem suffix."""
    out: dict[str, int] = {}
    for path, records in day_files.items():
        count = sum(1 for r in records if r.get("type") == "ss_error")
        if count or path.name.startswith("series_state-"):
            out[path.stem.removeprefix("series_state-")] = count
    return dict(sorted(out.items()))


def detect_lags(instances: list[dict]) -> list[float]:
    """Detect lag in ms: instance write time minus source-poll receive time."""
    lags: list[float] = []
    for inst in instances:
        grid_ts = inst.get("grid_ts_ns")
        ts_ns = inst.get("ts_ns")
        if isinstance(grid_ts, int) and isinstance(ts_ns, int):
            lags.append((ts_ns - grid_ts) / 1e6)
    return lags


def _fmt(values: list[float], unit: str, stats: tuple[float, ...] = (50, 90, 99)) -> str:
    """Format a distribution line from unsorted values."""
    if not values:
        return "count=0"
    s = sorted(values)
    parts = [f"count={len(s)}"]
    for p in stats:
        parts.append(f"p{p}={percentile(s, p):.1f}{unit}")
    parts.append(f"max={s[-1]:.1f}{unit}")
    return " ".join(parts)


def collect(data_dir: Path, days: int, include_test: bool) -> None:
    """Gather day files and print the full latency report to stdout."""
    grid_files = sorted((data_dir / "grid").glob("series_state-*.jsonl"))[-days:]
    live_files = sorted((data_dir / "shadow").glob("instances-*.jsonl"))[-days:]
    replay_files = sorted((data_dir / "shadow").glob("replay-*.jsonl"))[-days:]

    day_files = {p: read_jsonl(p) for p in grid_files}
    ss_records = [
        r for recs in day_files.values() for r in recs if not is_test_series(r, include_test)
    ]
    # Only LIVE instances measure real detect lag; replay rows are written
    # hours after capture, so their write-time lag is meaningless.
    instances = [
        r for p in live_files for r in read_jsonl(p) if not is_test_series(r, include_test)
    ]
    n_replay = sum(len(read_jsonl(p)) for p in replay_files)

    print(f"gridbot latency report ({len(grid_files)} grid day files)")
    print("\n== GRID poll latency_ms per title ==")
    for title, latencies in sorted(poll_latency_by_title(ss_records).items()):
        print(f"  {title}: {_fmt(latencies, 'ms')}")

    print("\n== Inter-poll gap (s) per live series ==")
    print(f"  all: {_fmt(inter_poll_gaps(ss_records), 's', stats=(50, 90))}")

    print("\n== ss_error count per day ==")
    for day, count in ss_errors_per_day(day_files).items():
        print(f"  {day}: {count}")

    print("\n== Instance detect lag (ms), live shadow only ==")
    lags = detect_lags(instances)
    print(f"  {_fmt(lags, 'ms', stats=(50, 90))}" + (
        f"  (replay rows excluded: {n_replay})" if n_replay else ""
    ))

    gaps = inter_poll_gaps(ss_records)
    breaches: list[str] = []
    pending: list[str] = []
    if gaps:
        if percentile(sorted(gaps), 90) > 20.0:
            breaches.append("p90 live poll gap > 20s")
    else:
        pending.append("no live polls yet")
    if lags:
        if percentile(sorted(lags), 90) > 1500.0:
            breaches.append("p90 detect lag > 1500ms")
    else:
        pending.append("no live instances yet")
    if breaches:
        print(f"\nverdict: EDGE WINDOW BREACHED — {'; '.join(breaches)}")
    elif pending:
        print(f"\nverdict: no breach measured ({'; '.join(pending)})")
    else:
        print("\nverdict: edge window intact")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3, help="day files to scan")
    parser.add_argument("--include-test", action="store_true", help="keep test series")
    args = parser.parse_args()
    collect(config.DATA_DIR, max(args.days, 1), args.include_test)


if __name__ == "__main__":
    main()
