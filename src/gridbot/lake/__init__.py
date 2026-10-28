"""The lake: raw capture → Parquet, typed event tape, quality gates, inventory.

Three layers, each a separate command so a failure in one cannot corrupt the
one below it:

1. ``raw`` (bronze) — each JSONL line stored losslessly as JSON text, plus the
   envelope fields; streams are ``<source>.<stem>`` (``grid.series_state``).
2. ``events`` (silver) — bronze → typed event tape, round results, and map
   results from the one map-winner oracle (``gridbot.corpus.winners``).
3. ``gates`` — SQL checks over both, emitting a machine-readable verdict.

The raw layer is immutable evidence. Everything above it is derived and can be
rebuilt from it. That ordering is what lets a schema mistake be a rebuild
instead of a data loss.
"""

from __future__ import annotations

from gridbot.lake.ingest import (
    IngestResult,
    RawFile,
    discover,
    ingest_file,
    load_manifest,
    manifest_path,
    rebuild_raw,
)
from gridbot.lake.paths import lake_dir, raw_root

__all__ = [
    "IngestResult",
    "RawFile",
    "discover",
    "ingest_file",
    "lake_dir",
    "load_manifest",
    "manifest_path",
    "raw_root",
    "rebuild_raw",
]
