"""`python -m gridbot.lab <command>` — the research control plane's front door.

    registry            what is registered, and how much of it is runnable
    scan                run every runnable hypothesis over the lake, write a report

``scan`` is the breadth path: it builds the join once, evaluates every runnable
registry row through the same engine, and writes one HTML page plus its JSON.
Adding a hypothesis to the search costs a registry row, not a new script — which
is the entire point of the exercise.

Nothing here places an order or reads a secret. It needs the lake and nothing
else: no GRID key, no network, no keyring.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from gridbot.lab.fills import DEFAULT_TTL_S, apply_touch_fill_model, fill_summary
from gridbot.lab.joins import JoinSpec, build_scan_input
from gridbot.lab.registry import (
    import_legacy,
    load_registry,
    registry_path,
    summary,
)
from gridbot.lab.report import ReportMeta, write_report
from gridbot.lab.scan import run_scans


def _cmd_registry(args: argparse.Namespace) -> int:
    base = Path(args.registry_dir) if args.registry_dir else None
    if args.import_legacy:
        v1 = Path(args.import_legacy)
        written = import_legacy(v1, base=base)
        print(f"imported {written} legacy row(s) into {registry_path(base)}")
    print(summary(base))
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    base = Path(args.registry_dir) if args.registry_dir else None
    hypotheses = [h for h in load_registry(base) if h.runnable]
    if not hypotheses:
        print(
            "no runnable hypotheses: every registered row is prose-only. "
            "Register a structured row before scanning.",
            file=sys.stderr,
        )
        return 1

    spec = JoinSpec(side=args.side, staleness_s=args.staleness_s)
    con = duckdb.connect()
    try:
        if args.markets_sql:
            con.execute(Path(args.markets_sql).read_text())
        else:
            print(
                "no --markets-sql: building an empty markets relation, so scan_input "
                "will be empty (the slot mapping is venue semantics and must be supplied)",
                file=sys.stderr,
            )
            con.execute(
                "CREATE OR REPLACE TABLE markets(slug VARCHAR, series_id VARCHAR, "
                "map_number INTEGER, slot VARCHAR)"
            )
        rows = build_scan_input(con, spec)
        print(f"scan_input: {rows:,} rows  (side={spec.side}, staleness={spec.staleness_s}s)")
        if args.fill_model:
            filled = apply_touch_fill_model(con, ttl_s=args.ttl_s)
            stats = fill_summary(con)
            print(
                f"fill model (touch, ttl={args.ttl_s:.0f}s): {filled:,} filled "
                f"({stats['fill_rate']:.1%}); "
                f"avg px filled={stats['avg_price_filled']}, unfilled={stats['avg_price_unfilled']}"
            )
        results = run_scans(
            con,
            hypotheses,
            reps=args.reps,
            variance_of_trial_sr=args.trial_sr_variance,
        )
    finally:
        con.close()

    for result in results:
        print(result.render())
        print()

    meta = ReportMeta(
        title="gridbot research scan",
        source=str(registry_path(base)),
        universe=f"side={spec.side} staleness<={spec.staleness_s}s rows={rows}",
        n_trials=max((h.n_trials for h in hypotheses), default=0),
    )
    out = Path(args.out)
    write_report(results, out, meta)
    print(f"report: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gridbot.lab", description=__doc__)
    ap.add_argument("--registry-dir", help="directory holding registry_v2.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("registry", help="list registered hypotheses")
    p_reg.add_argument("--import-legacy", help="import a v1 registry.jsonl as prose rows")
    p_reg.set_defaults(func=_cmd_registry)

    p_scan = sub.add_parser("scan", help="run runnable hypotheses and write a report")
    p_scan.add_argument("--side", choices=["long", "short"], default="long")
    p_scan.add_argument("--staleness-s", type=float, default=30.0)
    p_scan.add_argument("--markets-sql", help="SQL file creating the markets() relation")
    p_scan.add_argument(
        "--fill-model",
        action="store_true",
        help="apply the touch fill model (fills only when the tape comes to our price)",
    )
    p_scan.add_argument("--ttl-s", type=float, default=DEFAULT_TTL_S)
    p_scan.add_argument("--reps", type=int, default=2000)
    p_scan.add_argument(
        "--trial-sr-variance",
        type=float,
        default=0.0,
        help="cross-sectional variance of trial Sharpes; 0 disables deflation",
    )
    p_scan.add_argument("--out", default="data/analysis/scan-report.html")
    p_scan.set_defaults(func=_cmd_scan)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
