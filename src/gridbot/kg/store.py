"""Persist the graph: append-only JSONL as truth, SQLite as a view.

Two representations, on purpose.

`nodes.jsonl` / `edges.jsonl` are the source of truth: greppable, diffable
in review, and readable with the same tolerant reader as every other tape
in this repo. No service, no daemon, no format nobody can open in five
years.

`kg.sqlite` is a **derived, disposable** convenience for ad-hoc SQL — it is
rebuilt from the JSONL every time and nothing may write to it. SQLite is in
the standard library, so the whole graph costs zero new dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from gridbot.jsonl import iter_jsonl

from .schema import Edge, Graph, Node, dumps

NODES_FILE = "nodes.jsonl"
EDGES_FILE = "edges.jsonl"
META_FILE = "meta.json"
DB_FILE = "kg.sqlite"


def write_graph(graph: Graph, out_dir: Path, *, meta: dict[str, Any] | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    npath, epath, mpath = out_dir / NODES_FILE, out_dir / EDGES_FILE, out_dir / META_FILE

    # Sorted so a rebuild that changed nothing produces a byte-identical file.
    with open(npath, "w", encoding="utf-8") as fh:
        fh.writelines(dumps(n.to_row()) + "\n"
                      for n in sorted(graph.nodes.values(), key=lambda n: n.id))
    with open(epath, "w", encoding="utf-8") as fh:
        rows = sorted((e.to_row() for e in graph.edges),
                      key=lambda r: (r["kind"], r["src"], r["dst"], r["source"]))
        fh.writelines(dumps(r) + "\n" for r in rows)

    payload = dict(meta or {})
    payload["counts"] = graph.counts()
    payload["dangling_edges"] = len(graph.dangling())
    mpath.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"nodes": npath, "edges": epath, "meta": mpath}


def load_graph(out_dir: Path) -> Graph:
    g = Graph()
    for row in iter_jsonl(out_dir / NODES_FILE):
        g.add_node(Node(kind=row["kind"], key=row["key"],
                        label=row.get("label", ""), attrs=row.get("attrs") or {}))
    for row in iter_jsonl(out_dir / EDGES_FILE):
        g.add_edge(Edge(kind=row["kind"], src=row["src"], dst=row["dst"],
                        method=row["method"], source=row.get("source", ""),
                        attrs=row.get("attrs") or {}))
    return g


SCHEMA_SQL = """
CREATE TABLE nodes (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
  label TEXT, attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE edges (
  kind TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  method TEXT NOT NULL, source TEXT, attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_edges_src ON edges(src, kind);
CREATE INDEX idx_edges_dst ON edges(dst, kind);
-- Convenience view: every edge with both endpoint labels resolved, which is
-- what an ad-hoc question almost always wants.
CREATE VIEW e AS
  SELECT edges.kind, edges.src, s.label AS src_label, edges.dst,
         d.label AS dst_label, edges.method, edges.source, edges.attrs
  FROM edges LEFT JOIN nodes s ON s.id = edges.src
             LEFT JOIN nodes d ON d.id = edges.dst;
"""


def materialize_sqlite(graph: Graph, db_path: Path) -> Path:
    """Rebuild the SQLite view from scratch. Never incremental: the JSONL is
    truth, and a stale half-updated database would be a lying oracle."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA_SQL)
        con.executemany(
            "INSERT INTO nodes(id,kind,key,label,attrs) VALUES (?,?,?,?,?)",
            [(n.id, n.kind, n.key, n.label, json.dumps(n.attrs, sort_keys=True))
             for n in graph.nodes.values()],
        )
        con.executemany(
            "INSERT INTO edges(kind,src,dst,method,source,attrs) VALUES (?,?,?,?,?,?)",
            [(e.kind, e.src, e.dst, e.method, e.source, json.dumps(e.attrs, sort_keys=True))
             for e in graph.edges],
        )
        con.commit()
    finally:
        con.close()
    return db_path
