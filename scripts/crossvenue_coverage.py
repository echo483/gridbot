"""Daily join-coverage report for the intl<->PMUS market join.

Prerequisite for H-CROSSVENUE1. This script reports COVERAGE ONLY: how many
intl CS2 winner markets pair up with a Polymarket US book each day, and why
the rest do not. It computes NO price basis, lead, edge, PnL or correlation —
the hypothesis is not pre-registered, and measuring before the freeze would
invalidate the held-out window. If a future edit adds price arithmetic here it
has gone out of scope: pull it out into its own instrument.

The refusal breakout is the health signal. Each ``reason`` maps to a distinct
operational failure:
  no_pmus_match         -> no PMUS series matched the teams at all
  no_pmus_book_of_kind  -> a series matched, but no kind-matching book
  pmus_sides_incomplete -> the book exists but has no resolvable Yes side
  yes_team_unresolved   -> both sides present but neither is the intl Yes team

A non-zero INVERTED count is a sign-convention event — the most dangerous thing
this pipeline can hit — and is surfaced loudly in every output mode, never
buried in a table.

Run: uv run python scripts/crossvenue_coverage.py [--day YYYYMMDD] [--json]

Reads the tapes with scripts/_jsonl.read_rows (gridbot/jsonl.py is the src-side
reader and its docstring excludes scripts) and joins with the already-existing
gridbot.analysis.crossvenue_join.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gridbot import config
from gridbot.analysis import crossvenue_join as cj

try:
    from _jsonl import read_rows  # run as `python scripts/crossvenue_coverage.py`
except ImportError:  # imported as scripts.crossvenue_coverage (tests)
    from scripts._jsonl import read_rows

REASONS = (
    "no_pmus_match",
    "no_pmus_book_of_kind",
    "pmus_sides_incomplete",
    "yes_team_unresolved",
)
KINDS = ("match", "map")
DAYS_DEFAULT = 5


def _catalog_path(data_dir: Path, day: str) -> Path:
    return data_dir / "pm" / f"catalog-{day}.jsonl"


def _mapping_path(data_dir: Path, day: str) -> Path:
    return data_dir / "pmus" / f"mappings-{day}.jsonl"


def _dedup_intl(rows: list[dict]) -> list[dict]:
    """Keep one catalog row per market identity (``condition_id``).

    The catalog re-emits the same market on every ~5min discovery sweep, so
    dedupe before counting or feeding the join — otherwise a single market is
    counted (and joined) repeatedly.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        cid = row.get("condition_id")
        if cid:
            seen[cid] = row
    return list(seen.values())


def _dedup_mappings(rows: list[dict]) -> list[dict]:
    """Keep the NEWEST mapping snapshot per ``grid_series_id``.

    The mappings file holds a fresh snapshot every ~15min; later snapshots
    supersede earlier ones, so only the newest is authoritative per series.
    """
    newest: dict[Any, tuple[int, dict]] = {}
    for row in rows:
        if row.get("type") != "grid_pmus_mapping":
            continue
        sid = row.get("grid_series_id")
        if not sid:
            continue
        ts = row.get("ts_ns") or 0
        cur = newest.get(sid)
        if cur is None or ts > cur[0]:
            newest[sid] = (ts, row)
    return [r for _, r in newest.values()]


def coverage_for_day(day: str, data_dir: Path = config.DATA_DIR) -> dict[str, Any]:
    """Coverage breakdown for one UTC ``day`` (YYYYMMDD).

    A missing day file counts as zero rather than crashing — a missing file is
    itself a coverage datum, and it lets a caller probe any day safely.
    """
    cat_path, map_path = _catalog_path(data_dir, day), _mapping_path(data_dir, day)

    intl_rows = list(read_rows(cat_path)) if cat_path.exists() else []
    mapping_rows = list(read_rows(map_path)) if map_path.exists() else []
    intl_rows = _dedup_intl(intl_rows)
    mapping_rows = _dedup_mappings(mapping_rows)

    # Distinct CS2 winner markets (the join universe) split by kind. Filter via
    # parse_intl_market, never a hand-rolled title regex.
    intl_by_kind: dict[str, int] = dict.fromkeys(KINDS, 0)
    for row in intl_rows:
        m = cj.parse_intl_market(row)
        if m is not None:
            intl_by_kind[m.kind] = intl_by_kind.get(m.kind, 0) + 1
    intl_total = sum(intl_by_kind.values())

    # join_markets re-parses (the deduped) raw rows internally and REFUSES
    # rather than silently drops — the refusals are what make coverage
    # measurable, never trust the silent path.
    joined, refusals = cj.join_markets(intl_rows, mapping_rows)

    joined_by_kind: dict[str, int] = dict.fromkeys(KINDS, 0)
    joined_by_orientation: dict[str, int] = {"aligned": 0, "inverted": 0}
    for jm in joined:
        joined_by_kind[jm.kind] = joined_by_kind.get(jm.kind, 0) + 1
        joined_by_orientation[jm.orientation] = joined_by_orientation.get(
            jm.orientation, 0
        ) + 1

    refusal_counts: dict[str, int] = dict.fromkeys(REASONS, 0)
    for r in refusals:
        reason = r.get("reason")
        if reason in refusal_counts:
            refusal_counts[reason] += 1

    return {
        "date": day,
        "files": {"catalog": cat_path.exists(), "mappings": map_path.exists()},
        "intl_cs2_winner_markets": intl_total,
        "intl_by_kind": intl_by_kind,
        "pmus_mapped_series": len(mapping_rows),
        "joined": len(joined),
        "joined_by_kind": joined_by_kind,
        "joined_by_orientation": joined_by_orientation,
        "inverted": joined_by_orientation["inverted"],
        "refusals": refusal_counts,
        # joined / intl-CS2-winner-markets; 0 when there is nothing to join.
        "join_rate": (len(joined) / intl_total) if intl_total else 0.0,
    }


def _available_days(data_dir: Path) -> list[str]:
    """UTC days where BOTH catalogue and mappings files exist, ascending."""
    cat_days = {
        p.stem[len("catalog-"):]
        for p in _catalog_path(data_dir, "x").parent.glob("catalog-*.jsonl")
    }
    map_days = {
        p.stem[len("mappings-"):]
        for p in _mapping_path(data_dir, "x").parent.glob("mappings-*.jsonl")
    }
    return sorted(cat_days & map_days)


def build_report(days: list[str], data_dir: Path) -> dict[str, Any]:
    """Coverage for ``days`` (ascending), plus a loud inverted-days alert.

    ``main`` feeds it only days with both files present; an explicitly
    requested day that lacks a file still reports (zeros), not crashes.
    """
    day_reports = [coverage_for_day(d, data_dir) for d in days]
    inverted_days = [d["date"] for d in day_reports if d["inverted"] > 0]
    return {"days": day_reports, "inverted_days": inverted_days}


def _print_table(report: dict[str, Any]) -> None:
    days = report["days"]
    header = (
        f"{'date':<9} {'intl(m,map)':<13} {'pmus':<5} {'join(m,map)':<11} "
        f"{'aligned':>7} {'inverted':>8} "
        f"{'ref[nm/nbk/ns/yu]':>22} {'rate':>5}"
    )
    print(header)
    for d in days:
        q = (d["intl_by_kind"]["match"], d["intl_by_kind"]["map"])
        j = (d["joined_by_kind"]["match"], d["joined_by_kind"]["map"])
        r = d["refusals"]
        refs = (
            f"{r['no_pmus_match']}/{r['no_pmus_book_of_kind']}/"
            f"{r['pmus_sides_incomplete']}/{r['yes_team_unresolved']}"
        )
        print(
            f"{d['date']:<9} {f'{q[0]},{q[1]}':<13} {d['pmus_mapped_series']:<5} "
            f"{f'{j[0]},{j[1]}':<11} {d['joined_by_orientation']['aligned']:>7} "
            f"{d['inverted']:>8} {refs:>22} {d['join_rate']:>5.2f}"
        )
    if report["inverted_days"]:
        print()
        print("*** INVERTED PAIR ALERT *** " + ", ".join(report["inverted_days"]))
        print("    A non-zero inverted count is a sign-convention event — do not trade.")
        print("    Suspect the orientation resolution (crossvenue_join) before anything else.")


def _print_json(report: dict[str, Any]) -> None:
    print(json.dumps(report, sort_keys=True))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="intl<->PMUS join coverage report")
    p.add_argument("--day", type=str, default=None,
                   help="single UTC day (YYYYMMDD) to report")
    p.add_argument("--days", type=int, default=DAYS_DEFAULT,
                   help="last N days present, when --day is not given")
    p.add_argument("--json", action="store_true",
                   help="emit a machine-readable dict instead of the human table")
    return p.parse_args(argv)


def _report_days(args: argparse.Namespace, data_dir: Path) -> list[str]:
    if args.day:
        return [args.day]
    return _available_days(data_dir)[-args.days:]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    report = build_report(_report_days(args, config.DATA_DIR), config.DATA_DIR)
    if args.json:
        _print_json(report)
    else:
        _print_table(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())