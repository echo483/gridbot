"""One tolerant NDJSON line iterator for the repo's jsonl readers.

Nine sites used to inline the same loop with inconsistent failure modes.
This module is the single implementation: skip blank lines, tolerate a
malformed line (torn day-file final lines are a fact of the append-only
format), never raise on file-level OSError, and (by default) skip
non-dict rows — the strictest existing variant, executor/ledger.py's
defensive semantics. Pass ``skipped`` (any mutable counter with an
``update`` method, or a 1-element list) to learn how many lines were
skipped instead of dropping them silently.

NOT migrated here (bespoke on purpose):
- executor/engine.py's tail-cap seek() reader — load-bearing, documented.
- scripts/_jsonl.py — scripts are not src.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class SkippedCounter:
    """Minimal mutable counter for the ``skipped`` argument."""

    def __init__(self) -> None:
        self.count = 0

    def update(self, n: int = 1) -> None:
        self.count += n


def iter_jsonl(
    path: str | Path,
    *,
    dicts_only: bool = True,
    skipped: Any = None,
) -> Iterator[dict | object]:
    """Yield parsed rows from an NDJSON file, tolerating damage.

    Blank lines and lines that fail ``json.loads`` (torn finals) are
    skipped; a file that cannot be opened yields nothing. With
    ``dicts_only`` (default) non-dict rows are skipped too. When
    ``skipped`` is given (a :class:`SkippedCounter` or any object with
    ``update(n)``) each skipped line increments it — loud-but-tolerant.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    if skipped is not None:
                        skipped.update(1)
                    continue
                if dicts_only and not isinstance(row, dict):
                    if skipped is not None:
                        skipped.update(1)
                    continue
                yield row
    except OSError:
        return
