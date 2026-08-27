"""Shared tolerant JSONL reading for analysis scripts (slow loop).

Every scripts/*.py that reads capture or ledger day files re-implemented
the same three lines (open, json.loads, skip bad lines) — and each copy
is one filename convention away from silently reading nothing. This
module owns both: the tolerant read and day-file resolution.

Day-file gotcha this module exists to absorb (2026-08-27): ledger and
shadow files are named ``<prefix>-YYYYMMDD.jsonl`` (NO dashes in the
date). Passing a dashed date built a path that didn't exist and the
caller saw an empty/failed read, not a naming error.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


def read_rows(path: str | Path) -> Iterator[dict]:
    """Yield dict rows from a JSONL file, skipping unparseable lines.

    Tolerant on purpose: capture files can carry a torn final line from
    an in-flight write; one bad line must never sink a day's analysis.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def day_file(directory: str | Path, prefix: str, day: str) -> Path:
    """Path of ``<prefix>-YYYYMMDD.jsonl``, accepting dashed or bare days.

    Raises FileNotFoundError naming BOTH spellings it tried, so a wrong
    date/prefix fails loudly instead of reading as an empty day.
    """
    bare = day.replace("-", "")
    path = Path(directory) / f"{prefix}-{bare}.jsonl"
    if path.exists():
        return path
    raise FileNotFoundError(
        f"no {prefix} file for day {day!r}: tried {path} "
        f"(day files use the bare YYYYMMDD spelling)"
    )
