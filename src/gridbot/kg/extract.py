"""Extractors: flat files in, nodes and edges out.

Each function reads exactly one kind of source and returns a `Graph`
fragment. They are ordered by dependency in `build.py`: tapes and the
registry first, because docs and code are matched against their keys.

Precision over coverage. Every rule here is a literal match or a
structured field read; where a literal is genuinely ambiguous the edge is
emitted to every candidate and marked, never resolved by a guess.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from gridbot.jsonl import iter_jsonl

from .schema import Edge, Graph, Node

# A day file is <stem>-YYYYMMDD.jsonl. scripts/_jsonl.py owns this convention
# repo-wide (bare YYYYMMDD — dashed dates do not exist on disk).
DAY_FILE_RE = re.compile(r"^(?P<stem>[a-z_0-9]+)-(?P<day>\d{8})\.jsonl$")

# Counting rows means reading the file. data/pm/books is ~16 GB and 10.8M
# rows/day; counting it on every build would make the build unusable. Above
# this cap rows stay null with the reason recorded, and `--count-rows`
# overrides.
ROW_COUNT_CAP_BYTES = 64 * 1024 * 1024

SCRIPT_REF_RE = re.compile(r"\bscripts/([a-z0-9_]+)\.py\b")
# Docs name an instrument three ways and the first build proved all three are
# load-bearing: `scripts/quant_eval.py`, the bare stem `latency_report`, and
# for the src-side analysis instruments `gridbot.analysis.exec_fills` or
# `src/gridbot/analysis/exec_fills.py`. Matching only the first form made the
# whole EXEC1 evidence chain look untraceable, because EXEC1's instruments
# live in src/, not scripts/.
MODULE_REF_RE = re.compile(
    r"\bsrc/(gridbot/[a-z0-9_/]+)\.py\b|\b(gridbot\.[a-z0-9_.]+)\b")

# Every markdown under docs/, not a hand-listed subset. The old list was
# ("docs/analysis", "docs/learnings", "docs/superpowers/specs") and missed 23 of
# 95 files (24%) -- including STATE.md, the orientation baseline and the single
# most-cited document in the repo, plus MAP.md, DECISIONS.md, LIVE_FIRE.md and
# ALL of docs/experiments/, which is where pre-registration drafts live. A graph
# built to answer "what is already settled" cannot omit the file that says so.
#
# Nothing is excluded, archive included: knowing a lane is archived is exactly
# the kind of thing this graph exists to surface. The directory rides on each
# node as an attribute so a consumer can filter rather than being filtered for.
DOC_ROOT = "docs"

# Prose says "NOTE9", the registry says "H-MAPENTRY1-NOTE9". 158 such bare
# references across 31 files were invisible to exact matching. Resolved ONLY
# when exactly one registered id ends in that suffix -- 10 of 13 suffixes are
# unique, and NOTE1/NOTE2/NOTE3 are shared across lanes, so a bare one of those
# is genuinely ambiguous and gets no edge rather than a guessed one. Edges from
# this path carry method="bare_note" so a consumer can weigh them separately
# from method="exact".
BARE_NOTE_RE = re.compile(r"(?<![\w-])(NOTE\d+)(?![\w])")

# A registry row whose id ends in -NOTEn constrains an existing lane rather
# than proposing a new one. Modelled as its own kind because "what
# constrains this lane" is a question asked far more often than "what note
# rows exist".
NOTE_ID_RE = re.compile(r"-NOTE\d+$")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- registry ---------------------------------------------------------------

def extract_registry(registry_path: Path) -> Graph:
    """Registry rows -> hypothesis/note nodes plus their lineage.

    An id appears on more than one row (H-MAPENTRY1 has two, a
    registration and a verdict). The NODE is the id; each row is an event
    folded into it, so the node carries the full status history in
    chronological order rather than whichever row happened to be last.
    """
    g = Graph()
    rows_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    declared_supersedes: list[tuple[str, str, int]] = []

    for i, row in enumerate(iter_jsonl(registry_path), start=1):
        hid = str(row.get("hypothesis_id") or "").strip()
        if not hid:
            continue
        rows_by_id[hid].append(row)
        sup = row.get("supersedes")
        if isinstance(sup, str) and sup.strip():
            declared_supersedes.append((hid, sup.strip(), i))

    for hid, rows in rows_by_id.items():
        rows.sort(key=lambda r: str(r.get("run_ts") or ""))
        statuses = [str(r["status"]) for r in rows if r.get("status")]
        text = str(rows[0].get("hypothesis_text") or "")
        g.add_node(Node(
            kind="note" if NOTE_ID_RE.search(hid) else "hypothesis",
            key=hid,
            label=text[:120].replace("\n", " "),
            attrs={
                "rows": len(rows),
                "first_run_ts": rows[0].get("run_ts"),
                "last_run_ts": rows[-1].get("run_ts"),
                "statuses": statuses,
                "status": statuses[-1] if statuses else None,
                "has_evidence_field": any("evidence" in r for r in rows),
            },
        ))

    known = set(rows_by_id)

    # Structural lineage: the id IS the edge. H-MAPENTRY1-EXEC1-AMEND1-NOTE1
    # derives from H-MAPENTRY1-EXEC1-AMEND1 — the longest registered proper
    # prefix, not merely the first one that matches.
    for hid in known:
        parts = hid.split("-")
        for cut in range(len(parts) - 1, 0, -1):
            parent = "-".join(parts[:cut])
            if parent in known:
                kind = "note" if NOTE_ID_RE.search(hid) else "hypothesis"
                g.add_edge(Edge(
                    kind="derives_from",
                    src=f"{kind}:{hid}",
                    dst=f"{'note' if NOTE_ID_RE.search(parent) else 'hypothesis'}:{parent}",
                    method="declared",
                    source=f"{registry_path.name}#id-structure",
                ))
                break

    for hid, sup, line in declared_supersedes:
        if sup in known:
            g.add_edge(Edge(
                kind="supersedes",
                src=_hyp_id(hid), dst=_hyp_id(sup),
                method="declared", source=f"{registry_path.name}:{line}",
            ))

    # Cross-references in the prose: a row naming another lane. Exact token
    # match against registered ids, longest first so H-MAPENTRY1-NOTE8 is not
    # scored as a mention of H-MAPENTRY1.
    id_re = _id_alternation(known)
    if id_re is not None:
        for hid, rows in rows_by_id.items():
            seen: set[str] = set()
            for r in rows:
                for m in id_re.finditer(str(r.get("hypothesis_text") or "")):
                    other = m.group(0)
                    if other != hid and other not in seen:
                        seen.add(other)
                        g.add_edge(Edge(
                            kind="references", src=_hyp_id(hid), dst=_hyp_id(other),
                            method="exact", source=f"{registry_path.name}#text",
                        ))
    return g


def _hyp_id(hid: str) -> str:
    return f"{'note' if NOTE_ID_RE.search(hid) else 'hypothesis'}:{hid}"


def _bare_note_index(known: set[str]) -> dict[str, str]:
    """``{"NOTE9": "H-MAPENTRY1-NOTE9"}`` for suffixes owned by exactly one row.

    Fails closed on collision: NOTE1, NOTE2 and NOTE3 are each used by two or
    three lanes, so a bare reference to one of them names nothing in particular
    and is dropped. A wrong edge is worse than a missing one in a graph whose
    whole value is that its edges can be trusted.
    """
    by_suffix: dict[str, set[str]] = {}
    for hid in known:
        m = re.search(r"-(NOTE\d+)$", hid)
        if m:
            by_suffix.setdefault(m.group(1), set()).add(hid)
    return {k: next(iter(v)) for k, v in by_suffix.items() if len(v) == 1}


def _id_alternation(ids: set[str]) -> re.Pattern[str] | None:
    """Longest-first alternation so nested ids match at full length."""
    if not ids:
        return None
    ordered = sorted(ids, key=len, reverse=True)
    return re.compile(r"(?<![A-Z0-9-])(?:" + "|".join(re.escape(i) for i in ordered) + r")(?![A-Z0-9-])")


# --- tapes ------------------------------------------------------------------

def extract_tapes(data_dir: Path, *, count_rows: bool = False) -> Graph:
    """The data directory is ground truth for what streams exist.

    A tape is `<dir>.<stem>`; a tape_day is one file. Row counts are the
    provenance atom — "this verdict rests on 5,765 series-state rows" is
    the sentence the graph has to be able to produce.
    """
    g = Graph()
    if not data_dir.is_dir():
        return g
    per_tape: dict[str, list[tuple[str, int, int | None]]] = defaultdict(list)

    for sub in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        for f in sorted(sub.iterdir()):
            m = DAY_FILE_RE.match(f.name)
            if not m:
                continue
            stem, day = m.group("stem"), m.group("day")
            tape_key = f"{sub.name}.{stem}"
            try:
                size = f.stat().st_size
            except OSError:
                continue
            rows = _count_rows(f) if (count_rows or size <= ROW_COUNT_CAP_BYTES) else None
            per_tape[tape_key].append((day, size, rows))
            g.add_node(Node(
                kind="tape_day", key=f"{tape_key}/{day}", label=f.name,
                attrs={"tape": tape_key, "day": day, "bytes": size, "rows": rows,
                       "rows_unknown": rows is None,
                       "path": str(f.relative_to(data_dir.parent))},
            ))
            g.add_edge(Edge(
                kind="covers", src=f"tape_day:{tape_key}/{day}", dst=f"tape:{tape_key}",
                method="declared", source=str(f.relative_to(data_dir.parent)),
            ))

    for tape_key, days in per_tape.items():
        days.sort()
        known = [r for _, _, r in days if r is not None]
        g.add_node(Node(
            kind="tape", key=tape_key, label=tape_key,
            attrs={
                "dir": tape_key.split(".", 1)[0],
                "stem": tape_key.split(".", 1)[1],
                "days": len(days),
                "first_day": days[0][0], "last_day": days[-1][0],
                "bytes": sum(s for _, s, _ in days),
                "rows_counted": sum(known) if known else None,
                "days_uncounted": len(days) - len(known),
            },
        ))
    return g


def _count_rows(path: Path) -> int | None:
    try:
        with open(path, "rb") as fh:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))
    except OSError:
        return None


# --- code -------------------------------------------------------------------

def extract_code(root: Path, tapes: Graph) -> Graph:
    """Scripts and modules, and which tape each one names.

    Direction (reads vs writes) is NOT modelled. Determining it from a
    literal would be a guess, and a wrong-direction provenance edge is
    exactly the kind of confident error this graph exists to prevent. The
    edge is `touches`; the direction question is answered by reading the
    file, which the edge tells you to do.
    """
    g = Graph()
    stems: dict[str, list[str]] = defaultdict(list)
    for n in tapes.nodes.values():
        if n.kind == "tape":
            stems[str(n.attrs["stem"])].append(n.key)

    files: list[tuple[str, Path]] = []
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        files += [("script", p) for p in sorted(scripts_dir.glob("*.py"))
                  if not p.name.startswith("_tmp_")]
    src_dir = root / "src" / "gridbot"
    if src_dir.is_dir():
        files += [("module", p) for p in sorted(src_dir.rglob("*.py"))
                  if "__pycache__" not in p.parts]

    for kind, path in files:
        rel = str(path.relative_to(root))
        text = _read_text(path)
        g.add_node(Node(kind=kind, key=rel, label=path.stem,
                        attrs={"lines": text.count("\n"), "bytes": len(text)}))
        for stem, candidates in stems.items():
            # Two anchors: the day-file literal `series_state-` used by readers,
            # and the bare quoted stem `"discovery"` used by DayWriter at the
            # WRITE sites. Matching only the first hid every capture writer.
            anchor = next((a for a in (f"{stem}-", f'"{stem}"') if a in text), None)
            if anchor is None:
                continue
            chosen = candidates
            if len(candidates) > 1:
                # Disambiguate only on a second literal the file actually
                # contains; otherwise keep every candidate and say so.
                named = [c for c in candidates
                         if f"data/{c.split('.', 1)[0]}" in text or f'"{c.split(".", 1)[0]}"' in text]
                if len(named) == 1:
                    chosen = named
            for tape_key in chosen:
                g.add_edge(Edge(
                    kind="touches", src=f"{kind}:{rel}", dst=f"tape:{tape_key}",
                    method="exact", source=rel,
                    attrs={"anchor": anchor,
                           "ambiguous": len(chosen) > 1,
                           "candidates": len(candidates)},
                ))
    return g


# --- docs -------------------------------------------------------------------

def extract_docs(root: Path, registry: Graph, code: Graph | None = None) -> Graph:
    """Docs cite hypotheses and name the scripts that produced them.

    These two edge kinds ARE the provenance chain. A registry verdict with
    no citing doc, or a doc naming no script, is a number nobody can
    re-derive — surfaced by `kg orphans`.
    """
    g = Graph()
    known = {n.key for n in registry.nodes.values() if n.kind in ("hypothesis", "note")}
    id_re = _id_alternation(known)
    # Bare-stem matching needs the real inventory, so a prose word can never be
    # mistaken for an instrument that does not exist.
    script_stems = {Path(n.key).stem: n.key for n in (code.nodes.values() if code else [])
                    if n.kind == "script"}
    module_keys = {n.key for n in (code.nodes.values() if code else []) if n.kind == "module"}

    bare = _bare_note_index(known)
    docroot = root / DOC_ROOT
    for path in sorted(docroot.rglob("*.md")) if docroot.is_dir() else []:
        rel = str(path.relative_to(root))
        text = _read_text(path)
        g.add_node(Node(kind="doc", key=rel, label=path.stem,
                        attrs={"dir": str(path.parent.relative_to(root)),
                               "lines": text.count("\n"), "bytes": len(text)}))
        cited: set[str] = set()
        if id_re is not None:
            for hid in sorted({m.group(0) for m in id_re.finditer(text)}):
                cited.add(hid)
                g.add_edge(Edge(kind="cites", src=f"doc:{rel}", dst=_hyp_id(hid),
                                method="exact", source=rel))
        # Bare "NOTEn" prose, only where the suffix names exactly one row and
        # the full id was not already matched above.
        for m in sorted({m.group(1) for m in BARE_NOTE_RE.finditer(text)}):
            hid = bare.get(m)
            if hid is None or hid in cited:
                continue
            g.add_edge(Edge(kind="cites", src=f"doc:{rel}", dst=_hyp_id(hid),
                            method="bare_note", source=rel, attrs={"anchor": m}))
        named: dict[str, str] = {}
        for m in SCRIPT_REF_RE.finditer(text):
            named[f"script:scripts/{m.group(1)}.py"] = f"scripts/{m.group(1)}.py"
        for stem, key in script_stems.items():
            if f"script:{key}" not in named and re.search(rf"(?<![\w/]){re.escape(stem)}(?![\w])", text):
                named[f"script:{key}"] = stem
        for m in MODULE_REF_RE.finditer(text):
            path_form, dotted = m.group(1), m.group(2)
            key = (f"src/{path_form}.py" if path_form
                   else "src/" + dotted.replace(".", "/") + ".py")
            if key in module_keys:
                named.setdefault(f"module:{key}", m.group(0))
        for dst_id, anchor in sorted(named.items()):
            g.add_edge(Edge(kind="generated_by", src=f"doc:{rel}", dst=dst_id,
                            method="exact", source=rel,
                            attrs={"anchor": anchor}))
    return g


# --- units ------------------------------------------------------------------

EXEC_MODULE_RE = re.compile(r"-m\s+(gridbot[\w.]*)")
EXEC_SCRIPT_RE = re.compile(r"(scripts/[a-z0-9_]+\.py)")


def extract_units(root: Path) -> Graph:
    """deploy/*.service -> what actually runs, straight from ExecStart."""
    g = Graph()
    ddir = root / "deploy"
    if not ddir.is_dir():
        return g
    for path in sorted(ddir.glob("*.service")):
        text = _read_text(path)
        name = path.name
        execs = [ln.split("=", 1)[1] for ln in text.splitlines()
                 if ln.strip().startswith("ExecStart=")]
        g.add_node(Node(kind="unit", key=name, label=path.stem,
                        attrs={"exec_start": execs[0] if execs else None,
                               "inhibited": "systemd-inhibit" in text}))
        for line in execs:
            for m in EXEC_MODULE_RE.finditer(line):
                rel = "src/" + m.group(1).replace(".", "/") + ".py"
                target = rel if (root / rel).exists() else rel[:-3] + "/__main__.py"
                g.add_edge(Edge(kind="runs", src=f"unit:{name}", dst=f"module:{target}",
                                method="declared", source=f"deploy/{name}#ExecStart"))
            for m in EXEC_SCRIPT_RE.finditer(line):
                g.add_edge(Edge(kind="runs", src=f"unit:{name}", dst=f"script:{m.group(1)}",
                                method="declared", source=f"deploy/{name}#ExecStart"))
    return g


# --- venue mapping ----------------------------------------------------------

def extract_mappings(data_dir: Path, *, days: int = 3) -> Graph:
    """GRID series <-> venue market, carrying the Yes-side orientation.

    Orientation is the one attribute here that can silently destroy a
    strategy: an inverted pair does not look broken, it looks like a stable
    tradeable signal. It is a declared field on the mapping row, never
    derived, and it is stored on the edge so a consumer cannot read the
    pair without reading the sign.
    """
    g = Graph()
    mdir = data_dir / "pmus"
    if not mdir.is_dir():
        return g
    files = sorted(mdir.glob("mappings-*.jsonl"))[-days:]
    newest: dict[str, dict[str, Any]] = {}
    for f in files:
        for row in iter_jsonl(f):
            sid = str(row.get("grid_series_id") or "").strip()
            if not sid:
                continue
            prev = newest.get(sid)
            if prev is None or int(row.get("ts_ns") or 0) >= int(prev.get("ts_ns") or 0):
                newest[sid] = row

    for sid, row in newest.items():
        teams = [str(t) for t in (row.get("grid_teams") or [])]
        g.add_node(Node(kind="series", key=sid, label=" vs ".join(teams) or sid,
                        attrs={"teams": teams, "ts_ns": row.get("ts_ns")}))
        for kind, mkt in (row.get("us_markets") or {}).items():
            if not isinstance(mkt, dict):
                continue
            slug = str(mkt.get("market_slug") or "").strip()
            if not slug:
                continue
            sides = mkt.get("sides") if isinstance(mkt.get("sides"), dict) else {}
            longs = [t for t, s in sides.items() if s == "long"]
            shorts = [t for t, s in sides.items() if s == "short"]
            # Exactly the rule in analysis/crossvenue_join.py: one long AND one
            # short, or there is no Yes team to orient against and the pair is
            # refused. Restated rather than imported because that function does
            # a full intl join; if the rule ever changes, change it there first.
            orientable = len(longs) == 1 and len(shorts) == 1
            yes_team = longs[0] if longs else None
            g.add_node(Node(kind="market", key=slug, label=slug,
                            attrs={"venue": "pmus", "kind": kind, "sides": sides,
                                   "yes_team": yes_team, "orientable": orientable,
                                   "refusal": None if orientable else "pmus_sides_incomplete"}))
            g.add_edge(Edge(
                kind="maps_to", src=f"series:{sid}", dst=f"market:{slug}",
                method="declared", source=f"data/pmus/mappings#{sid}",
                attrs={"market_kind": kind, "yes_team": yes_team,
                       "orientable": orientable},
            ))
    return g


def data_dir_default(root: Path) -> Path:
    return Path(os.environ.get("GRIDBOT_DATA_DIR", root / "data"))
