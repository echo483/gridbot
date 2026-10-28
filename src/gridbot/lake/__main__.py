"""`python -m gridbot.lake <command>` — the ETL control plane.

    inventory   read-only scan of the capture trees + built-lake coverage
    rebuild     raw JSONL → Parquet (idempotent; --force re-copies)
    events      raw Parquet → typed event tape + resolution oracle
    gates       quality checks over the lake
    all         rebuild → events → gates, in that order

Point it at the T7 with `GRIDBOT_RAW_DIR=/mnt/gridbot/data` and keep the derived
Parquet local with `GRIDBOT_LAKE_DIR=~/lake/gridbot`. Nothing here needs a GRID
key, a network, or the OS keyring: it reads files and writes files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gridbot.lake.catalog import format_coverage, format_raw_scan, lake_coverage, scan_raw
from gridbot.lake.events import build_events, load_events
from gridbot.lake.gates import run_gates
from gridbot.lake.ingest import rebuild_raw


def _cmd_inventory(args: argparse.Namespace) -> int:
    root = Path(args.raw_dir) if args.raw_dir else None
    base = Path(args.lake_dir) if args.lake_dir else None
    print("== raw footprint (read-only) ==")
    print(format_raw_scan(scan_raw(root)))
    print()
    print("== lake coverage ==")
    print(format_coverage(lake_coverage(base)))
    return 0


def _cmd_rebuild(args: argparse.Namespace) -> int:
    root = Path(args.raw_dir) if args.raw_dir else None
    base = Path(args.lake_dir) if args.lake_dir else None
    results = rebuild_raw(root=root, base=base, force=args.force)
    if not results:
        print("no capture day files found — check GRIDBOT_RAW_DIR", file=sys.stderr)
        return 1
    new = [r for r in results if not r.skipped]
    for r in new:
        print(
            f"  {r.raw.stream:<14} {r.raw.day}  rows={r.rows:<9} "
            f"dropped={r.dropped}  {r.bytes:,}B"
        )
    print(f"{len(new)} file(s) ingested, {len(results) - len(new)} unchanged")
    return 0


def _cmd_events(args: argparse.Namespace) -> int:
    base = Path(args.lake_dir) if args.lake_dir else None
    result = build_events(base=base, force=args.force)
    if result.skipped:
        print("event tape up to date (use --force to rebuild)")
        return 0
    print(
        f"event tape rebuilt from {result.partitions_read} partition(s): "
        f"{result.events:,} event rows, {result.results:,} resolutions"
    )
    return 0


def _cmd_gates(args: argparse.Namespace) -> int:
    base = Path(args.lake_dir) if args.lake_dir else None
    report = run_gates(base=base, max_gap_s=args.max_gap_s)
    print(report.render())
    if not report.ok:
        failed = [g.name for g in report.gates if g.passed is not True]
        print(f"\nNOT CLEAN: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nAll gates pass.")
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    for name, fn in (("rebuild", _cmd_rebuild), ("events", _cmd_events), ("gates", _cmd_gates)):
        print(f"== {name} ==")
        code = fn(args)
        if code != 0:
            return code
        print()
    return 0


def _cmd_head(args: argparse.Namespace) -> int:
    """Peek at the event tape without writing anything."""
    base = Path(args.lake_dir) if args.lake_dir else None
    con = load_events(base)
    try:
        total = con.execute("SELECT count(*) FROM events").fetchone()[0]
        print(f"events: {total:,}")
        for row in con.execute(
            "SELECT series_id, title, map_number, round_number, a_side, a_score, b_side, b_score "
            "FROM events LIMIT ?",
            [args.n],
        ).fetchall():
            print("  ", row)
    finally:
        con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gridbot.lake", description=__doc__)
    ap.add_argument("--raw-dir", help="capture tree root (default $GRIDBOT_RAW_DIR or data/)")
    ap.add_argument("--lake-dir", help="derived lake root (default $GRIDBOT_LAKE_DIR)")
    ap.add_argument("--force", action="store_true", help="re-copy / rebuild even if unchanged")
    ap.add_argument("--max-gap-s", type=float, default=300.0, help="snapshot gap threshold (s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    helps = {
        "inventory": "read-only scan of the capture trees + built-lake coverage",
        "rebuild": "raw JSONL -> Parquet (idempotent; --force re-copies)",
        "events": "raw Parquet -> typed event tape + resolution oracle",
        "gates": "quality checks over the lake",
        "all": "rebuild -> events -> gates, in that order",
        "head": "peek at the event tape without writing anything",
    }
    for name, fn in (
        ("inventory", _cmd_inventory),
        ("rebuild", _cmd_rebuild),
        ("events", _cmd_events),
        ("gates", _cmd_gates),
        ("all", _cmd_all),
        ("head", _cmd_head),
    ):
        p = sub.add_parser(name, help=helps[name])
        if name == "head":
            p.add_argument("-n", type=int, default=5)
        p.set_defaults(func=fn)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
