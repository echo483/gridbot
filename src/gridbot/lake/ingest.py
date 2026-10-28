"""Raw capture day files → Parquet, faithfully and idempotently.

Two rules make the raw layer safe to build on:

**No interpretation at ingest.** Rows land as-is with the source file and ingest
time attached. Typing happens in :mod:`gridbot.lake.events`, at query time. A
schema mistake is then a rebuild of the derived layer, never a re-ingest of the
corpus — and the corpus stays the thing every published number reproduces from.

**A dropped row is a reported row.** DuckDB's ``ignore_errors`` silently
discards malformed lines. This module counts the file's physical lines and
reconciles them against the rows actually written, so a silent drop cannot
quietly become a wrong denominator. Torn final lines are a known artefact of
append-only capture; ``dropped`` is how many there were, per file, forever.

Idempotency is by content hash in ``<lake>/manifest.json``. A file whose sha256
and size are unchanged is skipped unless ``force`` is set; a file whose size
changed (capture still appending) is re-ingested and the partition rewritten.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from gridbot.lake.paths import lake_dir as _lake_dir
from gridbot.lake.paths import manifest_path as _manifest_path
from gridbot.lake.paths import raw_root as _raw_root

# Stream = the day-file stem. These are the stems the capture supervisors write
# (capture/supervisor.py, capture/pm_books.py, capture/pmus_books.py).
KNOWN_STREAMS = ("series_state", "discovery", "books", "catalog", "pmus_bbo", "executor")


def _sql_str(value: str) -> str:
    """Quote a literal for DuckDB SQL. Paths come from the operator's disk."""
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class RawFile:
    """One capture day file on disk."""

    stream: str
    day: str
    path: Path

    @property
    def partition(self) -> str:
        return f"stream={self.stream}/dt={self.day}"


@dataclass(frozen=True)
class IngestResult:
    """Outcome for one raw file. ``dropped`` is lines the parser refused."""

    raw: RawFile
    rows: int
    lines: int
    dropped: int
    bytes: int
    sha256: str
    parquet: Path
    skipped: bool = False


def _stream_of(path: Path) -> str:
    """``series_state-20261028.jsonl`` → ``series_state``.

    The stem may itself contain hyphens in principle, so split on the LAST
    hyphen only when the suffix parses as 8 digits — otherwise keep the stem.
    """
    stem = path.stem
    head, _, tail = stem.rpartition("-")
    if head and len(tail) == 8 and tail.isdigit():
        return head
    return stem


def _day_of(path: Path) -> str:
    """``…-20261028.jsonl`` → ``2026-10-28``; falls back to the file's mtime."""
    stem = path.stem
    _, _, tail = stem.rpartition("-")
    if len(tail) == 8 and tail.isdigit():
        return f"{tail[0:4]}-{tail[4:6]}-{tail[6:8]}"
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d")


def discover(
    root: Path | None = None,
    *,
    streams: tuple[str, ...] | None = None,
) -> list[RawFile]:
    """Every ``<stream>-YYYYMMDD.jsonl`` under ``root``, sorted by stream then day.

    Walks one level into per-stream subdirectories (``grid/``, ``pm/``,
    ``pmus/``) and also accepts a flat directory. Unknown stems are kept when
    ``streams`` is None — a new capture stream must not require a code change to
    get into the lake, only a typing decision later.
    """
    base = root or _raw_root()
    if not base.exists():
        return []
    out: list[RawFile] = []
    for path in sorted(base.glob("*.jsonl")) + sorted(base.glob("*/*.jsonl")):
        stream = _stream_of(path)
        if streams is not None and stream not in streams:
            continue
        out.append(RawFile(stream=stream, day=_day_of(path), path=path))
    return sorted(out, key=lambda r: (r.stream, r.day, str(r.path)))


def _sha256(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    path = _manifest_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"files": {}}
    return {"files": {}}


def save_manifest(manifest: dict) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True))


def parquet_path_for(raw: RawFile, base: Path | None = None) -> Path:
    root = base or _lake_dir()
    return root / "raw" / raw.partition / "part.parquet"


def _parse_counts(path: Path) -> tuple[int, int]:
    """``(non_blank_lines, parseable_lines)`` — the reconciliation truth.

    DuckDB's ``ignore_errors`` keeps a malformed line's slot and emits an
    all-NULL row for it, so the Parquet row count alone cannot tell a clean day
    from a day that lost its torn tail. This pass parses every line with the
    standard library and counts what is actually readable; the two numbers
    travel in the manifest so a drop is always a reported drop.
    """
    lines = parsed = 0
    with open(path, "rb") as fh:
        for line in fh:
            if not line.strip():
                continue
            lines += 1
            try:
                json.loads(line)
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                continue
            parsed += 1
    return lines, parsed


def _original_columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    """Column names the raw JSON produced, before the lake's ``_``-prefixed ones."""
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_json({_sql_str(str(path))}, "
        "format='newline_delimited', ignore_errors=true, union_by_name=true)"
    ).fetchall()
    return [str(r[0]) for r in rows]


def ingest_file(
    con: duckdb.DuckDBPyConnection,
    raw: RawFile,
    *,
    base: Path | None = None,
    force: bool = False,
) -> IngestResult:
    """Copy one day file into its Parquet partition. Returns the reconciliation.

    The Parquet write is atomic-by-rename: DuckDB writes a temp file in the same
    partition directory and the final name is only used after a successful
    ``COPY``. A crash mid-copy leaves a ``.tmp`` file that the next run clears —
    never a half-written ``part.parquet`` that reads as a short day.
    """
    out = parquet_path_for(raw, base)
    stat = raw.path.stat()
    sha = _sha256(raw.path)
    manifest = load_manifest()
    key = str(raw.path)
    prev = manifest["files"].get(key)
    if (
        not force
        and prev
        and prev.get("sha256") == sha
        and prev.get("bytes") == stat.st_size
        and out.exists()
    ):
        return IngestResult(
            raw=raw,
            rows=int(prev.get("rows", 0)),
            lines=int(prev.get("lines", 0)),
            dropped=int(prev.get("dropped", 0)),
            bytes=stat.st_size,
            sha256=sha,
            parquet=out,
            skipped=True,
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    if tmp.exists():
        tmp.unlink()

    # Drop the all-NULL rows a torn line produces, so the tape never carries a
    # row that has no content. The predicate is generated from the file's own
    # columns, so it holds for any stream without a per-stream rule.
    cols = _original_columns(con, raw.path)
    junk = " AND ".join(f"{c} IS NULL" for c in cols) if cols else "FALSE"
    con.execute(
        f"""
        COPY (
            SELECT
                *,
                {_sql_str(raw.stream)} AS _stream,
                {_sql_str(raw.day)}   AS _day,
                {_sql_str(key)}       AS _src_file,
                now()                 AS _ingested_at
            FROM read_json({_sql_str(str(raw.path))},
                           format='newline_delimited',
                           ignore_errors=true,
                           union_by_name=true)
            WHERE NOT ({junk})
        ) TO {_sql_str(str(tmp))} (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    rows = int(
        con.execute(f"SELECT count(*) FROM read_parquet({_sql_str(str(tmp))})").fetchone()[0]
    )
    tmp.replace(out)

    lines, parsed = _parse_counts(raw.path)
    result = IngestResult(
        raw=raw,
        rows=rows,
        lines=lines,
        dropped=max(0, lines - parsed),
        bytes=stat.st_size,
        sha256=sha,
        parquet=out,
    )
    manifest["files"][key] = {
        "stream": raw.stream,
        "day": raw.day,
        "bytes": stat.st_size,
        "sha256": sha,
        "rows": result.rows,
        "lines": result.lines,
        "dropped": result.dropped,
        # rows must equal the lines that actually parsed; anything else means
        # the loader and the counter disagree and the day is not trustworthy.
        "reconciled": result.rows == parsed,
        "parquet": str(out),
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    save_manifest(manifest)
    return result


def rebuild_raw(
    *,
    root: Path | None = None,
    base: Path | None = None,
    streams: tuple[str, ...] | None = None,
    force: bool = False,
) -> list[IngestResult]:
    """Ingest every discovered raw file. Idempotent; ``force`` re-copies all."""
    con = duckdb.connect()
    try:
        return [
            ingest_file(con, raw, base=base, force=force)
            for raw in discover(root, streams=streams)
        ]
    finally:
        con.close()
