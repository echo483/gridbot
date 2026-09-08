"""A lightweight, file-backed knowledge graph over the gridbot evidence chain.

Truth is two JSONL files under ``data/kg``; SQLite is a rebuilt-on-demand
view for ad-hoc SQL. Zero new dependencies, no service to keep alive.

    uv run python -m gridbot.kg build
    uv run python -m gridbot.kg why H-MAPENTRY1-EXEC1
    uv run python -m gridbot.kg orphans

See ``schema.py`` for why there are no inferred edges.
"""

from __future__ import annotations

from .build import build_and_write, build_graph
from .schema import Edge, Graph, Node
from .store import load_graph, write_graph

__all__ = ["Edge", "Graph", "Node", "build_and_write", "build_graph",
           "load_graph", "write_graph"]
