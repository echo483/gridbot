"""Inventory and coverage: what data exists, before anything is written.

Two views, deliberately separate:

``scan_raw``
    Read-only walk of the capture trees. Runs against the T7 mount with no key,
    no network and no writes, so the shape of the corpus is known before a
    single Parquet byte is produced. This is the command to run first on any new
    disk.

``lake_coverage``
    Query over the built lake: per stream and day, rows, first/last timestamp,
    and the calendar days that are *missing* between the first and last observed
    day. A missing day is reported as a day, not inferred from row counts —
    the corpus's three historical outages were hours of silence that row counts
    alone would have hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from gridbot.lake.ingest import RawFile, discover
from gridbot.lake.paths import lake_dir as _lake_dir


@dataclass(frozen=True)
class RawStream:
    """One stream's on-disk footprint."""

    stream: str
    files: int
    days: tuple[str, ...]
    bytes: int

    @property
    def first_day(self) -> str | None:
        return self.days[0] if self.days else None

    @property
    def last_day(self) -> str | None:
        return self.days[-1] if self.days else None


def scan_raw(root: Path | None = None) -> list[RawStream]:
    """Group discovered raw files into a per-stream footprint. Read-only."""
    by_stream: dict[str, list[RawFile]] = {}
    for raw in discover(root):
        by_stream.setdefault(raw.stream, []).append(raw)
    out: list[RawStream] = []
    for stream, files in sorted(by_stream.items()):
        days = sorted({f.day for f in files})
        total = sum(f.path.stat().st_size for f in files)
        out.append(RawStream(stream=stream, files=len(files), days=tuple(days), bytes=total))
    return out


def format_raw_scan(streams: list[RawStream]) -> str:
    if not streams:
        return "no capture day files found"
    lines = [f"{'stream':<16}{'files':>6}{'first':>12}{'last':>12}{'bytes':>14}"]
    for s in streams:
        lines.append(
            f"{s.stream:<16}{s.files:>6}{s.first_day or '-':>12}"
            f"{s.last_day or '-':>12}{s.bytes:>14,}"
        )
    total = sum(s.bytes for s in streams)
    lines.append(f"{'TOTAL':<16}{sum(s.files for s in streams):>6}{'':>24}{total:>14,}")
    return "\n".join(lines)


@dataclass
class DayCoverage:
    stream: str
    day: str
    rows: int


@dataclass
class StreamCoverage:
    stream: str
    rows: int
    days: int
    first_day: str | None
    last_day: str | None
    missing_days: list[str] = field(default_factory=list)


def _all_days(first: str, last: str) -> list[str]:
    start = date.fromisoformat(first)
    end = date.fromisoformat(last)
    span = (end - start).days
    return [
        (start + timedelta(days=i)).isoformat() for i in range(span + 1)
    ]


def lake_coverage(base: Path | None = None) -> list[StreamCoverage]:
    """Per-stream coverage from the built raw lake, with missing calendar days."""
    root = base or _lake_dir()
    if not _has_parts(root):
        return []
    pattern = str(root / "raw" / "stream=*" / "dt=*" / "part.parquet")
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT _stream AS stream, _day AS day, count(*) AS rows "
            f"FROM read_parquet({_sql_str(pattern)}) "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
    finally:
        con.close()
    by_stream: dict[str, list[DayCoverage]] = {}
    for stream, day, count in rows:
        by_stream.setdefault(str(stream), []).append(
            DayCoverage(stream=str(stream), day=str(day), rows=int(count))
        )
    out: list[StreamCoverage] = []
    for stream, days in sorted(by_stream.items()):
        present = sorted({d.day for d in days})
        missing = (
            [d for d in _all_days(present[0], present[-1]) if d not in set(present)]
            if present
            else []
        )
        out.append(
            StreamCoverage(
                stream=stream,
                rows=sum(d.rows for d in days),
                days=len(present),
                first_day=present[0] if present else None,
                last_day=present[-1] if present else None,
                missing_days=missing,
            )
        )
    return out


def _has_parts(root: Path) -> bool:
    return any((root / "raw").glob("stream=*/dt=*/part.parquet"))


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def format_coverage(cov: list[StreamCoverage]) -> str:
    if not cov:
        return "lake is empty — run `python -m gridbot.lake rebuild` first"
    lines = [f"{'stream':<16}{'rows':>12}{'days':>6}{'first':>12}{'last':>12}{'gap days':>10}"]
    for c in cov:
        lines.append(
            f"{c.stream:<16}{c.rows:>12,}{c.days:>6}{c.first_day or '-':>12}"
            f"{c.last_day or '-':>12}{len(c.missing_days):>10}"
        )
        if c.missing_days:
            shown = ", ".join(c.missing_days[:12])
            more = f" (+{len(c.missing_days) - 12} more)" if len(c.missing_days) > 12 else ""
            lines.append(f"    missing: {shown}{more}")
    return "\n".join(lines)


def inventory_markdown(root: Path | None = None, base: Path | None = None) -> str:
    """The corpus inventory document: raw footprint + lake coverage."""
    stamp = datetime.now(UTC).isoformat()
    lines = [
        "# Corpus inventory",
        "",
        f"Generated {stamp}",
        "",
        "## Raw footprint (read-only scan of the capture trees)",
        "",
        "```",
        format_raw_scan(scan_raw(root)),
        "```",
        "",
        "## Lake coverage (built Parquet)",
        "",
        "```",
        format_coverage(lake_coverage(base)),
        "```",
        "",
    ]
    return "\n".join(lines)
