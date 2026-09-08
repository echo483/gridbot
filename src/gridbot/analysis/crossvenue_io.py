"""Corpus loaders shared by the CROSSVENUE1 instruments.

Kept out of the scripts so two entry points cannot drift into two different
definitions of the same population -- which matters here more than usual,
because the population IS the measurement: `load_mappings` pins each series to
the mapping row that still carries `us_markets`, and getting that wrong made the
same command return 18 joined markets and then 8 an hour later.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from gridbot.analysis.crossvenue_basis import Quote


def load_mappings(data_dir: Path) -> tuple[dict[str, int], list[dict]]:
    """(first-mapped ns per series, best mapping row per series) over the corpus.

    "Best" is the newest row that still CARRIES us_markets, not simply the newest
    row. The mapper re-snapshots every 5 minutes and a settled market drops out of
    ``us_markets``, so keying on the newest row makes the join decay as matches
    finish: the same command produced 18 joined markets and then 8 an hour later.
    An instrument that answers differently each time it is run cannot support a
    holdout read, so the population is pinned to the mapping as it stood while the
    market was actually quoted.
    """
    first: dict[str, int] = {}
    best: dict[str, dict] = {}
    for p in sorted((data_dir / "pmus").glob("mappings-*.jsonl")):
        with p.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "grid_pmus_mapping":
                    continue
                sid, t = str(r["grid_series_id"]), int(r.get("ts_ns") or 0)
                if sid not in first or t < first[sid]:
                    first[sid] = t
                if r.get("us_markets") or sid not in best:
                    best[sid] = r
    return first, list(best.values())


def load_intl_catalog(days: list[str], data_dir: Path) -> list[dict]:
    """Unique intl CS2 markets, deduped by condition id."""
    out, seen = [], set()
    for day in days:
        p = data_dir / "pm" / f"catalog-{day}.jsonl"
        if not p.exists():
            continue
        with p.open() as fh:
            for line in fh:
                if "ounter-strike" not in line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = r.get("conditionId") or r.get("condition_id")
                if cid in seen:
                    continue
                seen.add(cid)
                out.append(r)
    return out


def load_intl_quotes(tokens: set[str], days: list[str], data_dir: Path,
                     prefiltered: list[Path] | None = None) -> dict[str, list[Quote]]:
    """Per-token intl touch series from WS price_change events."""
    out: dict[str, list[Quote]] = defaultdict(list)
    if prefiltered:
        sources: list[Path] = list(prefiltered)
    else:
        sources = [data_dir / "pm" / f"books-{d}.jsonl" for d in days]
    pat = re.compile("|".join(re.escape(t) for t in tokens)) if tokens else None
    for p in sources:
        if not p.exists() or pat is None:
            continue
        with p.open() as fh:
            for line in fh:
                if not pat.search(line):
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw = r.get("raw")
                ts = r.get("ts_ns")
                if not isinstance(raw, dict) or not ts:
                    continue
                if raw.get("event_type") != "price_change":
                    continue
                for pc in raw.get("price_changes") or []:
                    tok = pc.get("asset_id")
                    if tok not in tokens:
                        continue
                    try:
                        out[tok].append(Quote(int(ts), float(pc["best_bid"]),
                                              float(pc["best_ask"])))
                    except (KeyError, TypeError, ValueError):
                        continue
    for v in out.values():
        v.sort(key=lambda q: q.t_ns)
    return dict(out)


def load_pmus_quotes(slugs: set[str], days: list[str], data_dir: Path) -> dict[str, list[Quote]]:
    out: dict[str, list[Quote]] = defaultdict(list)
    for day in days:
        p = data_dir / "pmus" / f"books-{day}.jsonl"
        if not p.exists():
            continue
        with p.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "pmus_bbo" or r.get("slug") not in slugs:
                    continue
                out[str(r["slug"])].append(Quote(int(r["ts_ns"]), r.get("bid"), r.get("ask")))
    for v in out.values():
        v.sort(key=lambda q: q.t_ns)
    return dict(out)
