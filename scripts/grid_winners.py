#!/usr/bin/env python3
"""First-observation map-winner oracle over GRID series-state capture files.

For each (series_id, game sequenceNumber) the verdict comes from the FIRST
observation (in file order across the given time-ordered files) where that game
shows finished == true and exactly one team has won == true.  Later
contradicting observations never change the verdict; they are counted and
reported.  Finals are validated against CS2 MR12 terminal-score rules (see
VALIDATION) and games whose terminal state is ambiguous are excluded.

The oracle itself lives in ``gridbot.corpus.winners``; this file is its CLI.

Since the game is indexed by (series_id, map_number) rather than by a numeric
game id, the map of a "best-of-3" contest never spills into the next series --
keys are namespaced per series.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gridbot.corpus.winners import process_lines

__all__ = ["main", "process_lines"]

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="First-observation map-winner oracle over GRID series-state JSONL."
    )
    ap.add_argument("files", nargs="+", help="time-ordered JSONL capture files")
    ap.add_argument("--out", help="also write the JSON result to this file")
    args = ap.parse_args(argv)

    lines = []
    for path in args.files:
        try:
            with open(path) as fh:
                lines.extend(fh)
        except OSError as exc:
            print(f"grid_winners: cannot read {path}: {exc}", file=sys.stderr)
            return 1

    result = process_lines(lines)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        try:
            Path(args.out).write_text(text)
        except OSError as exc:
            print(f"grid_winners: cannot write {args.out}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())