"""Raw capture day files → bronze Parquet, losslessly and idempotently.

Three rules make the raw layer safe to build on:

**Lossless.** Each line is stored as its own JSON text (``raw``), next to the
few envelope fields every stream has (``type``, ``ts_ns``). Nothing is
schema-inferred at ingest. That matters: DuckDB's struct inference over a
heterogeneous file *silently* drops keys it did not sample, casts ``1.5`` into
a ``BIGINT`` column, and turns a string in a numeric field into NULL — and a
row-count reconciliation cannot see any of it, because the row survives.
Typing happens in :mod:`gridbot.lake.events` (silver), where a mistake is a
rebuild of a derived table, never a silently corrupted copy of the corpus.

**A dropped row is a reported row.** Torn lines (the tail of an append that
was cut off) are counted per file in the manifest and never become rows.

**A stream is ``<directory>.<stem>``.** ``pm/books-D.jsonl`` (Polymarket
international CLOB) and ``pmus/books-D.jsonl`` (Polymarket US BBO) share a
stem, so the stem alone is not an identity: ``pm.books`` and ``pmus.books``.

Idempotency is by content hash in ``<lake>/manifest.json``. A file whose sha256
and size are unchanged is skipped unless ``force`` is set; a file still being
appended changes size and is re-ingested (capture v2 closes hourly files so
only finished files are ingested — docs/specs/2026-10-28-capture-v2-homelab.md).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from gridbot.lake.paths import lake_dir as _lake_dir
from gridbot.lake.paths import raw_root as _raw_root

# ``<dir>.<stem>`` of what the capture processes write today (v1 layout).
SERIES_STATE = "grid.series_state"
GRID_DISCOVERY = "grid.discovery"
PM_BOOKS = "pm.books"
PM_CATALOG = "pm.catalog"
PMUS_BOOKS = "pmus.books"
KNOWN_STREAMS = (SERIES_STATE, GRID_DISCOVERY, PM_BOOKS, PM_CATALOG, PMUS_BOOKS)

# Bronze schema: the lake's own columns, then the lossless line.
BRONZE_COLUMNS = ("type", "ts_ns", "raw", "_stream", "_day", "_src_file", "_ingested_at")


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


def _stem_of(path: Path) -> str:
    """``series_state-20261028.jsonl`` → ``series_state`` (date suffix removed)."""
    stem = path.stem
    head, _, tail = stem.rpartition("-")
    if head and len(tail) == 8 and tail.isdigit():
        return head
    return stem


def stream_of(path: Path, root: Path) -> str:
    """``<root>/grid/series_state-20261028.jsonl`` → ``grid.series_state``.

    A file directly under ``root`` has no source directory and keeps its bare
    stem; capture never writes there, so in practice every stream is qualified.
    """
    stem = _stem_of(path)
    parent = path.parent
    if parent == root:
        return stem
    return f"{parent.name}.{stem}"


def _day_of(path: Path) -> str:
    """``…-20261028.jsonl`` → ``2026-10-28``; falls back to the file's mtime."""
    _, _, tail = path.stem.rpartition("-")
    if len(tail) == 8 and tail.isdigit():
        return f"{tail[0:4]}-{tail[4:6]}-{tail[6:8]}"
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d")


def discover(
    root: Path | None = None,
    *,
    streams: tuple[str, ...] | None = None,
) -> list[RawFile]:
    """Every ``<stem>-YYYYMMDD.jsonl`` under ``root``, sorted by stream then day.

    Walks one level into the per-source directories (``grid/``, ``pm/``,
    ``pmus/``). Unknown streams are kept when ``streams`` is None — a new
    capture stream must not need a code change to reach the lake, only a
    typing decision later.
    """
    base = root or _raw_root()
    if not base.exists():
        return []
    out: list[RawFile] = []
    for path in sorted(base.glob("*.jsonl")) + sorted(base.glob("*/*.jsonl")):
        stream = stream_of(path, base)
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


def manifest_path(base: Path | None = None) -> Path:
    return (base or _lake_dir()) / "manifest.json"


def load_manifest(base: Path | None = None) -> dict:
    """The ingest ledger. An unreadable manifest means "re-ingest everything",
    which is slow but never wrong."""
    path = manifest_path(base)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"files": {}}
    return {"files": {}}


def save_manifest(manifest: dict, base: Path | None = None) -> None:
    """Atomic: write a sibling temp file, then rename over the old one."""
    path = manifest_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(UTC).isoformat()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    os.replace(tmp, path)


def parquet_path_for(raw: RawFile, base: Path | None = None) -> Path:
    root = base or _lake_dir()
    return root / "raw" / raw.partition / "part.parquet"


def _parse_counts(path: Path) -> tuple[int, int]:
    """``(non_blank_lines, parseable_object_lines)`` — the reconciliation truth."""
    lines = parsed = 0
    with open(path, "rb") as fh:
        for line in fh:
            if not line.strip():
                continue
            lines += 1
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(obj, dict):
                parsed += 1
    return lines, parsed


def ingest_file(
    con: duckdb.DuckDBPyConnection,
    raw: RawFile,
    *,
    base: Path | None = None,
    force: bool = False,
) -> IngestResult:
    """Copy one day file into its bronze partition. Returns the reconciliation.

    The Parquet write is atomic-by-rename: a crash mid-copy leaves a ``.tmp``
    file that the next run clears, never a half-written ``part.parquet`` that
    reads as a short day.
    """
    out = parquet_path_for(raw, base)
    stat = raw.path.stat()
    sha = _sha256(raw.path)
    manifest = load_manifest(base)
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

    # read_ndjson_objects returns each line as JSON text; a torn line becomes a
    # NULL row (dropped here, counted below) and blank lines are skipped. A line
    # that parses but is not an object is not a capture record either.
    con.execute(
        f"""
        COPY (
            SELECT
                json_extract_string(json, '$.type')            AS type,
                TRY_CAST(json_extract(json, '$.ts_ns') AS BIGINT) AS ts_ns,
                json                                           AS raw,
                {_sql_str(raw.stream)} AS _stream,
                {_sql_str(raw.day)}    AS _day,
                {_sql_str(key)}        AS _src_file,
                now()                  AS _ingested_at
            FROM read_ndjson_objects({_sql_str(str(raw.path))}, ignore_errors=true)
            WHERE json IS NOT NULL AND json_type(json) = 'OBJECT'
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
        # rows must equal the object lines that parsed; anything else means the
        # loader and the counter disagree and the day is not trustworthy.
        "reconciled": result.rows == parsed,
        "parquet": str(out),
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    save_manifest(manifest, base)
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


def bronze_glob(stream: str, base: Path | None = None) -> str:
    """The Parquet glob for one bronze stream (every day)."""
    root = base or _lake_dir()
    return str(root / "raw" / f"stream={stream}" / "dt=*" / "part.parquet")
