"""Node and edge shapes for the gridbot knowledge graph.

The graph exists to answer three questions the flat files answer badly:

1. **Is this number traceable?** A registry verdict cites a doc; the doc
   names the script that produced it; the script touches a tape; the tape
   has day files with row counts. Any break in that chain is a number
   nobody can re-derive.
2. **What is already falsified?** A dead lane re-derived is the most
   expensive mistake available here — the post-shock "+19pt edge" was a
   dead-book artifact and cost real analysis time twice.
3. **What is the domain model?** The entity set below is what a rewrite,
   or a Nautilus custom ``Data`` type, has to reproduce. Naming it once
   here beats rediscovering it per consumer.

Every edge records HOW it was derived, and the method set is deliberately
small and closed:

``declared``
    Read out of a structured field (a registry ``supersedes``, a unit's
    ``ExecStart``, a mapping row's sides). The source asserted it.
``exact``
    A literal string match — a doc containing ``H-MAPENTRY1-NOTE8``, or
    the token ``scripts/quant_eval.py``.
``bare_note``
    Prose writes ``NOTE9`` where the registry writes ``H-MAPENTRY1-NOTE9``.
    Resolved **only** when exactly one registered row ends in that suffix;
    NOTE1, NOTE2 and NOTE3 are each shared across lanes, so a bare one of
    those resolves to nothing and produces no edge. 158 such references
    across 31 files were invisible before this method existed.

There is deliberately **no ``inferred`` method**. A graph that guesses is
worse than no graph, because a guessed provenance edge reads exactly like
a real one and there is no way to tell them apart later. ``bare_note`` is
not an exception to that rule: it is a total function on an unambiguous
suffix, and it fails closed on collision rather than choosing. It is kept
as its own method — instead of being folded into ``exact`` — precisely so
a consumer who does not want it can drop it in one predicate.

Where the evidence is genuinely ambiguous — the stem ``books-`` names a
tape under both ``data/pm`` and ``data/pmus`` — this schema emits an edge
to every candidate and marks it ambiguous, rather than picking one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# --- node kinds -------------------------------------------------------------
# Kept small on purpose. A kind earns its place by being the subject of a
# question someone actually asks, not by existing in the filesystem.
NODE_KINDS = (
    "hypothesis",  # a registry lane: H-MAPENTRY1, H-CROSSVENUE1
    "note",        # a registry row that CONSTRAINS rather than proposes (-NOTEn)
    "doc",         # every markdown under docs/, dir kept as an attribute
    "script",      # scripts/*.py — evidence instruments and ops tools
    "module",      # src/gridbot/**/*.py — the system itself
    "tape",        # a capture stream: pm.books, grid.series_state, pmus.trades
    "tape_day",    # one day file — the provenance atom, where rows are counted
    "unit",        # a systemd unit in deploy/
    "series",      # a GRID esports series
    "market",      # a venue market (PMUS slug / intl condition id)
)

# --- edge kinds -------------------------------------------------------------
EDGE_KINDS = (
    "derives_from",  # H-X-AMEND1 -> H-X          (structural, from the id)
    "supersedes",    # declared in the registry row
    "references",    # one registry row naming another lane in its text
    "cites",         # doc -> hypothesis
    "generated_by",  # doc -> script
    "touches",       # script|module -> tape
    "runs",          # unit -> script|module
    "covers",        # tape_day -> tape
    "maps_to",       # series -> market, carrying the Yes-side orientation
)

# How an edge was established, and therefore how much it can be trusted.
#   declared   the source states it structurally (an id's own lineage, a unit
#              file naming its script) — not an inference at all
#   exact      a full literal match of a known key in text
#   bare_note  prose wrote "NOTE9" where the registry says "H-MAPENTRY1-NOTE9",
#              and exactly ONE registered row ends in that suffix. Ambiguous
#              suffixes (NOTE1/2/3 are each shared across lanes) produce no edge.
#              Weaker than `exact` and kept separate so a consumer can drop it.
METHODS = ("declared", "exact", "bare_note")


@dataclass(frozen=True, slots=True)
class Node:
    kind: str
    key: str  # unique within kind; the node id is f"{kind}:{key}"
    label: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(f"unknown node kind {self.kind!r}")
        if not self.key:
            raise ValueError(f"empty key for node kind {self.kind!r}")

    def to_row(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "key": self.key,
                "label": self.label, "attrs": self.attrs}


@dataclass(frozen=True, slots=True)
class Edge:
    kind: str
    src: str  # a Node.id
    dst: str  # a Node.id
    method: str
    source: str  # the file (and where in it) this edge was read out of
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in EDGE_KINDS:
            raise ValueError(f"unknown edge kind {self.kind!r}")
        if self.method not in METHODS:
            # The guard that keeps the no-guessing rule true: an "inferred"
            # edge cannot be added without editing METHODS and confronting
            # why. Adding `bare_note` went through exactly this gate.
            raise ValueError(f"unknown method {self.method!r}; v1 admits {METHODS}")
        if ":" not in self.src or ":" not in self.dst:
            raise ValueError(f"edge endpoints must be node ids: {self.src!r} -> {self.dst!r}")

    def to_row(self) -> dict[str, Any]:
        return {"kind": self.kind, "src": self.src, "dst": self.dst,
                "method": self.method, "source": self.source, "attrs": self.attrs}


@dataclass
class Graph:
    """A build result. Nodes are deduped by id, last write winning."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)

    def merge(self, other: Graph) -> None:
        self.nodes.update(other.nodes)
        self.edges.extend(other.edges)

    def dangling(self) -> list[Edge]:
        """Edges pointing at a node no extractor produced.

        Not an error — a doc may cite a hypothesis id that was never
        registered, and that is a finding, not a bug. Reported by
        ``kg orphans``.
        """
        return [e for e in self.edges
                if e.src not in self.nodes or e.dst not in self.nodes]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes.values():
            out[f"node:{n.kind}"] = out.get(f"node:{n.kind}", 0) + 1
        for e in self.edges:
            out[f"edge:{e.kind}"] = out.get(f"edge:{e.kind}", 0) + 1
        return dict(sorted(out.items()))


def dumps(row: dict[str, Any]) -> str:
    """Stable one-line JSON so the built graph diffs cleanly in review."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"))
