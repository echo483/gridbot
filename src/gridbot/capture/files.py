"""Append-only, day-partitioned jsonl writers. The capture corpus is the moat."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


class DayWriter:
    """Appends one JSON object per line to ``<dir>/<stem>-YYYYMMDD.jsonl``."""

    def __init__(self, directory: Path, stem: str) -> None:
        self.dir = directory
        self.stem = stem
        self._day: str | None = None
        self._fh: TextIO | None = None
        self.written = 0
        self.dir.mkdir(parents=True, exist_ok=True)

    def _handle(self) -> TextIO:
        day = datetime.now(UTC).strftime("%Y%m%d")
        if self._fh is None or day != self._day:
            if self._fh:
                self._fh.close()
            self._day = day
            # Long-lived append handle by design (closed on day roll / shutdown).
            self._fh = open(self.dir / f"{self.stem}-{day}.jsonl", "a", buffering=1)  # noqa: SIM115
        return self._fh

    def append(self, obj: dict[str, Any]) -> None:
        obj.setdefault("ts_ns", time.time_ns())
        self._handle().write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")
        self.written += 1

    def flush(self) -> None:
        """Force buffered records to disk (rare-event writers, e.g. shadow instances)."""
        if self._fh:
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
