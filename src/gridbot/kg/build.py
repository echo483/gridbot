"""Run every extractor in dependency order and persist the result.

Order is load-bearing: the registry supplies the id set the docs are
matched against, and the tape inventory supplies the stems the code is
matched against. Extracting docs before the registry would silently yield
zero `cites` edges — the failure mode this ordering exists to prevent.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import extract
from .schema import Graph
from .store import DB_FILE, materialize_sqlite, write_graph

REGISTRY_REL = "docs/experiments/registry.jsonl"


def build_graph(root: Path, *, data_dir: Path | None = None, count_rows: bool = False) -> Graph:
    data = data_dir if data_dir is not None else extract.data_dir_default(root)
    g = Graph()
    registry = extract.extract_registry(root / REGISTRY_REL)
    tapes = extract.extract_tapes(data, count_rows=count_rows)
    code = extract.extract_code(root, tapes)
    for fragment in (
        registry,
        tapes,
        extract.extract_docs(root, registry, code),
        code,
        extract.extract_units(root),
        extract.extract_mappings(data),
    ):
        g.merge(fragment)
    return g


def build_and_write(root: Path, out_dir: Path, *, data_dir: Path | None = None,
                    count_rows: bool = False) -> tuple[Graph, dict[str, Path]]:
    t0 = time.perf_counter()
    g = build_graph(root, data_dir=data_dir, count_rows=count_rows)
    paths = write_graph(g, out_dir, meta={
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(root.resolve()),
        "count_rows": count_rows,
        "build_seconds": round(time.perf_counter() - t0, 2),
    })
    paths["db"] = materialize_sqlite(g, out_dir / DB_FILE)
    return g, paths
