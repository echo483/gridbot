"""CLI for H-MAPENTRY1-EXEC1: ``uv run python -m gridbot.analysis.exec_replay_cli``.

Two modes, both offline and read-only:

* ``--calibrate``  — the 48 venue-verified armed-night orders (2026-08-26/27) through
  the fill bracket: confusion matrix vs the real fills, recall by one-sidedness,
  fill-time error. Prints the numbers; the gate thresholds are PENDING USER
  CONFIRMATION (registry H-MAPENTRY1-EXEC1), so this never says PASS or FAIL.
* ``--heldout``    — the held-out population (series first-mapped at/after the
  BOOKPIN1 start) replayed and summarised. REFERENCE only: ``--terminal`` is
  refused until the user confirms the thresholds and the stop rule is met.

Writes ``data/analysis/exec_replay-<mode>-<YYYYMMDD>.json`` next to the markdown.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config
from gridbot.analysis import exec_fixture as ef
from gridbot.analysis import exec_population as ep
from gridbot.analysis import exec_replay as er
from gridbot.instruments import mapentry_verdict as mv
from gridbot.jsonl import iter_jsonl

FIXTURE_DAYS = ("20260826", "20260827")


def _rows(pattern: str, data: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(data.glob(pattern)):
        out.extend(iter_jsonl(path))
    return out


def _dump(data: Path, mode: str, payload: dict) -> Path:
    out_dir = data / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"exec_replay-{mode}-{datetime.now(UTC):%Y%m%d}.json"
    path.write_text(json.dumps(payload, indent=1, default=str))
    return path


def run_calibrate(data: Path) -> dict:
    intents, mapentry_rows, docs = ef.load_day_files(data, FIXTURE_DAYS)
    orders, actual, counts = ef.fixture_orders(intents, mapentry_rows, docs)
    postquote = ep.index_postquotes(_rows("shadow/postquote-2026082[67].jsonl", data))
    tape = ep.index_tape(_rows("pmus/books-2026082[67].jsonl", data))  # none exist: unsized
    winners = mv.load_map_winners(str(data / "grid" / "series_state-*.jsonl"))
    replayed = er.replay_orders(orders, postquote, tape, winners)
    summary = er.summarize(replayed)
    calib = er.calibration(replayed, ef.actual_dicts(actual))
    return {"mode": "calibrate", "days": FIXTURE_DAYS, "counts": counts,
            "calibration": calib, "summary": summary}


def run_heldout(data: Path) -> dict:
    rows = mv.load_mapentry_rows(str(data / "shadow" / "mapentry-*.jsonl"))
    first = mv.first_mapping_ts(str(data / "pmus" / "mappings-*.jsonl"))
    start_ns = mv.iso_to_ns(mv.holdout_start_iso())
    orders = ep.heldout_orders(rows, first, start_ns)
    postquote = ep.index_postquotes(_rows("shadow/postquote-*.jsonl", data))
    tape = ep.index_tape(_rows("pmus/books-*.jsonl", data))
    winners = mv.load_map_winners(str(data / "grid" / "series_state-*.jsonl"))
    replayed = er.replay_orders(orders, postquote, tape, winners)
    return {"mode": "heldout", "holdout_start": mv.holdout_start_iso(),
            "n_orders": len(orders), "n_series": len({o.series_id for o in orders}),
            "summary": er.summarize(replayed)}


def _calibration_lines(calib: dict, counts: dict) -> list[str]:
    lines = [
        (f"fixture: placed {counts['placed']} joined {counts['joined']} rested {counts['rested']} "
         f"filled {counts['filled']} rejected {counts['rejected']} unknown {counts['unknown_state']}"),
        f"scored (rested): n_rested={calib['n_rested']} actual_fills={calib['n_actual_fills']}",
    ]
    for b in ("lower", "upper"):
        c = calib[b]
        rb = c["recall_by_one_sidedness"]
        lines.append(
            f"  {b:>5}: tp={c['tp']} fp={c['fp']} fn={c['fn']} tn={c['tn']} "
            f"precision={er._none(c['precision'])} recall={er._none(c['recall'])} "
            f"fill_time_mae_s={er._none(c['fill_time_mae_s'])} "
            f"recall one-sided={er._none(rb['one_sided'])} two-sided={er._none(rb['two_sided'])} | "
            f"unobserved={c['n_unobserved']} (fills {c['fills_unobserved']}) "
            f"undetectable={c['n_undetectable']} (fills {c['fills_undetectable']}) "
            f"recall_detectable={er._none(c['recall_detectable'])}")
    lines.append("gate thresholds: PENDING USER CONFIRMATION (registry H-MAPENTRY1-EXEC1) — numbers only")
    return lines


def main(argv: list[str] | None = None, data: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--heldout", action="store_true")
    ap.add_argument("--terminal", action="store_true",
                    help="refused: thresholds pending user confirmation")
    args = ap.parse_args(argv)
    data = data or config.DATA_DIR
    if args.terminal:
        print("--terminal refused: H-MAPENTRY1-EXEC1 thresholds are PENDING USER CONFIRMATION; "
              "no verdict is rendered", file=sys.stderr)
        return 2
    if not (args.calibrate or args.heldout):
        ap.print_help()
        return 1
    if args.calibrate:
        res = run_calibrate(data)
        print("# H-MAPENTRY1-EXEC1 — calibration fixture (08-26/27 real orders)")
        print("\n".join(_calibration_lines(res["calibration"], res["counts"])))
        print()
        print(er.render(res["summary"], "fixture replay (REFERENCE)"))
        print(f"\nwritten: {_dump(data, 'calibrate', res)}")
    if args.heldout:
        res = run_heldout(data)
        print(f"# H-MAPENTRY1-EXEC1 — held-out replay, REFERENCE (start {res['holdout_start']}, "
              f"{res['n_orders']} orders / {res['n_series']} series)")
        print(er.render(res["summary"], "held-out replay (REFERENCE — no verdict)"))
        print(f"\nwritten: {_dump(data, 'heldout', res)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
