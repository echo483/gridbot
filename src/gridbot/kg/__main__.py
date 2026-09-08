"""CLI: build the graph, then ask it the three questions.

    build            rebuild data/kg from the repo (add --count-rows for exact
                     row counts on the multi-GB tapes; slow, off by default)
    why <id>         the provenance chain behind a hypothesis
    status           what is registered, and what is closed
    orphans          every break in the evidence chain
    tape <key>       day coverage and gaps for one stream
    sql "<query>"    escape hatch over the SQLite view
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .build import build_and_write
from .query import orphans, resolve, status, tape_coverage, why
from .store import DB_FILE, load_graph

DEFAULT_OUT = "data/kg"


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def _out(args: argparse.Namespace) -> Path:
    return _root(args) / args.out


def _load(args: argparse.Namespace):
    out = _out(args)
    if not (out / "nodes.jsonl").exists():
        sys.exit(f"no graph at {out} — run: uv run python -m gridbot.kg build")
    return load_graph(out)


def _emit(payload, as_json: bool) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str) if as_json
          else _render(payload))


def _render(payload, indent: int = 0) -> str:
    """Terminal-first rendering: the point is to be read, not parsed."""
    pad = "  " * indent
    if isinstance(payload, dict):
        lines = []
        for k, v in payload.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_render(v, indent + 1))
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}: -")
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(payload, list):
        return "\n".join(_render(v, indent) if isinstance(v, (dict, list))
                         else f"{pad}- {v}" for v in payload)
    return f"{pad}{payload}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gridbot.kg", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"graph dir (default: {DEFAULT_OUT})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="rebuild the graph from the repo")
    b.add_argument("--count-rows", action="store_true",
                   help="count rows in every day file, including the multi-GB tapes")
    w = sub.add_parser("why", help="provenance chain behind a hypothesis")
    w.add_argument("target")
    sub.add_parser("status", help="registered vs closed lanes")
    sub.add_parser("orphans", help="breaks in the evidence chain")
    t = sub.add_parser("tape", help="day coverage and gaps for one stream")
    t.add_argument("key")
    s = sub.add_parser("sql", help="ad-hoc query over the SQLite view")
    s.add_argument("query")

    args = ap.parse_args(argv)

    if args.cmd == "build":
        g, paths = build_and_write(_root(args), _out(args), count_rows=args.count_rows)
        _emit({"wrote": {k: str(v) for k, v in paths.items()},
               "counts": g.counts(), "dangling_edges": len(g.dangling())}, args.json)
        return 0

    if args.cmd == "sql":
        db = _out(args) / DB_FILE
        if not db.exists():
            sys.exit(f"no database at {db} — run: uv run python -m gridbot.kg build")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cur = con.execute(args.query)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchall()
        finally:
            con.close()
        if args.json:
            print(json.dumps([dict(zip(cols, r, strict=False)) for r in rows],
                             indent=2, default=str))
        else:
            print(" | ".join(cols))
            for r in rows:
                print(" | ".join("" if v is None else str(v) for v in r))
        return 0

    g = _load(args)

    if args.cmd == "why":
        matches = resolve(g, args.target)
        if not matches:
            sys.exit(f"no node matching {args.target!r}")
        if len(matches) > 1:
            _emit({"ambiguous": matches}, args.json)
            return 1
        _emit(why(g, matches[0]), args.json)
        return 0
    if args.cmd == "status":
        _emit(status(g), args.json)
        return 0
    if args.cmd == "orphans":
        _emit(orphans(g), args.json)
        return 0
    if args.cmd == "tape":
        _emit(tape_coverage(g, args.key), args.json)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
