"""The questions the graph exists to answer.

Three, in order of how much they cost when unanswerable:

`why`      — is this number traceable? hypothesis -> docs -> scripts -> tapes
`orphans`  — where is the chain broken?
`status`   — what is already settled, and what is already dead?

Everything else is `kg sql`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .schema import Edge, Graph

# Statuses that close a lane. A closed lane re-derived is the most expensive
# mistake available here, so `status` reports these separately and loudly
# rather than as one row among twenty.
CLOSED_MARKERS = ("FALSIFIED", "CLOSE", "NOT_FALSIFIED", "VERDICT")


def resolve(graph: Graph, term: str) -> list[str]:
    """Accept a full node id, a bare key, or a unique case-insensitive suffix."""
    if term in graph.nodes:
        return [term]
    exact = [n.id for n in graph.nodes.values() if n.key == term]
    if exact:
        return exact
    low = term.lower()
    return sorted(n.id for n in graph.nodes.values()
                  if low in n.key.lower() or low in n.label.lower())


def _by_src(graph: Graph) -> dict[str, list[Edge]]:
    out: dict[str, list[Edge]] = defaultdict(list)
    for e in graph.edges:
        out[e.src].append(e)
    return out


def _by_dst(graph: Graph) -> dict[str, list[Edge]]:
    out: dict[str, list[Edge]] = defaultdict(list)
    for e in graph.edges:
        out[e.dst].append(e)
    return out


def why(graph: Graph, node_id: str) -> dict[str, Any]:
    """The provenance chain behind one hypothesis.

    Walks hypothesis <- cites <- doc -> generated_by -> script -> touches ->
    tape -> tape_day, and reports the row counts at the bottom. A chain that
    bottoms out before the tape days is a number that cannot be re-derived,
    and that is the finding.
    """
    src, dst = _by_src(graph), _by_dst(graph)
    node = graph.nodes.get(node_id)
    out: dict[str, Any] = {
        "id": node_id,
        "label": node.label if node else None,
        "attrs": dict(node.attrs) if node else {},
        "lineage": [], "constrained_by": [], "supersedes": [], "superseded_by": [],
        "docs": [], "scripts": {}, "tapes": {}, "traceable": False,
    }
    if node is None:
        out["error"] = "no such node"
        return out

    cur = node_id
    seen = {cur}
    while True:
        parents = [e.dst for e in src.get(cur, []) if e.kind == "derives_from"]
        if not parents or parents[0] in seen:
            break
        cur = parents[0]
        seen.add(cur)
        out["lineage"].append(cur)

    out["constrained_by"] = sorted(
        e.src for e in dst.get(node_id, [])
        if e.kind in ("derives_from", "references") and e.src.startswith("note:"))
    out["supersedes"] = sorted(e.dst for e in src.get(node_id, []) if e.kind == "supersedes")
    out["superseded_by"] = sorted(e.src for e in dst.get(node_id, []) if e.kind == "supersedes")

    docs = sorted({e.src for e in dst.get(node_id, []) if e.kind == "cites"})
    out["docs"] = docs

    scripts: dict[str, list[str]] = {}
    for d in docs:
        for e in src.get(d, []):
            if e.kind == "generated_by":
                scripts.setdefault(e.dst, []).append(d)
    out["scripts"] = {k: sorted(v) for k, v in sorted(scripts.items())}

    tapes: dict[str, dict[str, Any]] = {}
    for s in scripts:
        for e in src.get(s, []):
            if e.kind != "touches":
                continue
            t = graph.nodes.get(e.dst)
            if t is None:
                continue
            entry = tapes.setdefault(e.dst, {
                "days": t.attrs.get("days"), "rows_counted": t.attrs.get("rows_counted"),
                "days_uncounted": t.attrs.get("days_uncounted"),
                "first_day": t.attrs.get("first_day"), "last_day": t.attrs.get("last_day"),
                "via": [], "ambiguous": False,
            })
            entry["via"].append(s)
            entry["ambiguous"] = entry["ambiguous"] or bool(e.attrs.get("ambiguous"))
    for entry in tapes.values():
        entry["via"] = sorted(set(entry["via"]))
    out["tapes"] = dict(sorted(tapes.items()))
    out["traceable"] = bool(docs and scripts and tapes)
    out["chain_breaks"] = [
        label for label, ok in (
            ("no citing doc", bool(docs)),
            ("no doc names a script", bool(scripts)),
            ("no script names a tape", bool(tapes)),
        ) if not ok
    ]
    return out


def status(graph: Graph) -> dict[str, Any]:
    by_status: dict[str, list[str]] = defaultdict(list)
    closed: list[dict[str, Any]] = []
    for n in graph.nodes.values():
        if n.kind != "hypothesis":
            continue
        st = n.attrs.get("status") or "(no status)"
        by_status[str(st)].append(n.key)
        if any(m in str(st) for m in CLOSED_MARKERS):
            closed.append({"id": n.key, "status": st, "label": n.label})
    return {
        "hypotheses": sum(1 for n in graph.nodes.values() if n.kind == "hypothesis"),
        "notes": sum(1 for n in graph.nodes.values() if n.kind == "note"),
        "by_status": {k: sorted(v) for k, v in sorted(by_status.items())},
        "closed": sorted(closed, key=lambda r: r["id"]),
    }


def orphans(graph: Graph) -> dict[str, list[Any]]:
    """Every break in the evidence chain, in one place.

    These are findings, not bugs. A hypothesis with no citing doc may be
    freshly registered; a script no doc names may be pure ops. The value is
    that the list is short enough to read and each entry is a question worth
    asking once.
    """
    src, dst = _by_src(graph), _by_dst(graph)
    hyp = [n for n in graph.nodes.values() if n.kind == "hypothesis"]

    uncited = sorted(n.key for n in hyp
                     if not any(e.kind == "cites" for e in dst.get(n.id, [])))
    docs_citing_nothing = sorted(
        n.key for n in graph.nodes.values() if n.kind == "doc"
        and not any(e.kind == "cites" for e in src.get(n.id, [])))
    scripts_unused = sorted(
        n.key for n in graph.nodes.values() if n.kind == "script"
        and not any(e.kind in ("generated_by", "runs") for e in dst.get(n.id, [])))
    tapes_untouched = sorted(
        n.key for n in graph.nodes.values() if n.kind == "tape"
        and not any(e.kind == "touches" for e in dst.get(n.id, [])))
    unoriented = sorted(
        n.key for n in graph.nodes.values() if n.kind == "market"
        and not n.attrs.get("orientable"))
    dangling = sorted({f"{e.kind}: {e.src} -> {e.dst} ({e.source})"
                       for e in graph.dangling()})

    # THE HEADLINE QUESTION (schema docstring, #1): is this number traceable?
    # A registry row cites a doc; the doc names the script that produced it. A
    # row whose docs name NO script states a number nobody can re-derive, which
    # is NOTE8's rule ("no backtest number without a depth/fill model") applied
    # to the record itself. Rows with no citing doc at all are excluded here --
    # they are already reported above and would otherwise be counted twice.
    no_repro = sorted(
        n.key for n in graph.nodes.values()
        if n.kind in ("hypothesis", "note")
        # Only rows that CLAIM a number. A row declaring no instrument is
        # pinning a definition or recording a rule change (statuses like
        # EXECUTION_RULE_CLARIFIED, BAR_RESTRUCTURE_PRE_REGISTERED); it has
        # nothing to reproduce and listing it here would be noise that trains
        # a reader to ignore the list.
        and n.attrs.get("declares_instrument")
        and any(e.kind == "cites" for e in dst.get(n.id, []))
        and not any(
            any(x.kind == "generated_by" for x in src.get(e.src, []))
            for e in dst.get(n.id, []) if e.kind == "cites"
        )
    )
    return {
        "hypotheses_with_no_citing_doc": uncited,
        "rows_whose_docs_name_no_script": no_repro,
        "docs_citing_no_hypothesis": docs_citing_nothing,
        "scripts_no_doc_or_unit_names": scripts_unused,
        "tapes_no_code_names": tapes_untouched,
        "markets_without_orientation": unoriented,
        "dangling_edges": dangling,
    }


def tape_coverage(graph: Graph, tape_key: str) -> dict[str, Any]:
    """Day-by-day coverage with the gaps named.

    A gap is a hole in the moat. The flat directory listing hides them —
    28 files look like 28 days until you diff the dates.
    """
    node = graph.nodes.get(f"tape:{tape_key}")
    days = sorted(
        (n.attrs for n in graph.nodes.values()
         if n.kind == "tape_day" and n.attrs.get("tape") == tape_key),
        key=lambda a: str(a.get("day")),
    )
    gaps: list[str] = []
    if len(days) > 1:
        # Day-file stems are UTC by construction (capture writes YYYYMMDD in
        # UTC); .date() drops the tz either way, and only differences are used.
        parsed = [datetime.strptime(str(d["day"]), "%Y%m%d")
                  .replace(tzinfo=UTC).date() for d in days]
        cur: date = parsed[0]
        for nxt in parsed[1:]:
            cur += timedelta(days=1)
            while cur < nxt:
                gaps.append(cur.strftime("%Y%m%d"))
                cur += timedelta(days=1)
            cur = nxt
    return {
        "tape": tape_key,
        "exists": node is not None,
        "attrs": dict(node.attrs) if node else {},
        "days": [{"day": d.get("day"), "rows": d.get("rows"), "bytes": d.get("bytes")}
                 for d in days],
        "gap_days": gaps,
    }
