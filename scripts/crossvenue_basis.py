#!/usr/bin/env python3
"""First look at the intl-vs-PMUS basis, on TRAINING series only.

H-CROSSVENUE1 is NOT pre-registered (iron rail 2), so this script may not touch
the holdout. It calls ``require_training_only``, which RAISES rather than
filtering when a holdout series is handed to it, and which fails closed on a
series whose first-mapped time is unknown. Nothing here is an edge claim, a
verdict, or a PnL: it reports the distribution of a price difference.

WHAT IT DOES. Joins the intl CS2 catalog to the PMUS mappings (crossvenue_join,
which settles orientation or refuses), keeps the training side of the
pre-registered boundary, then for each market builds

  intl  -- event-driven: every WS ``price_change`` carries best_bid/best_ask
  PMUS  -- sampled: our own polled BBO tape

and joins them AS-OF ON THE PMUS CLOCK (crossvenue_basis.basis_series): for each
PMUS observation, what intl believed at that instant, never after it.

READ THE COVERAGE COLUMN FIRST. The binding constraint on this lane is not the
basis, it is that we mostly cannot see the venue we would trade -- see
docs/analysis/2026-09-08-crossvenue1-readiness.md. A market with thousands of
intl events and zero PMUS observations contributes nothing, and that is the
finding, not a bug in this script.

Intl books are ~12GB/day, so the intl side is read through a token filter. Pass
``--intl-jsonl`` with pre-filtered rows to skip the scan:

    grep -F -f tokens.txt data/pm/books-20260907.jsonl > intl.jsonl

Run: uv run python scripts/crossvenue_basis.py [--days 20260907 20260908]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot.analysis.crossvenue_basis import (
    Quote,
    basis_series,
    require_training_only,
    split_by_boundary,
    summarise,
)
from gridbot.analysis.crossvenue_join import join_markets


def load_mappings(data_dir: Path) -> tuple[dict[str, int], list[dict]]:
    """(first-mapped ns per series, newest mapping row per series) over the corpus."""
    first: dict[str, int] = {}
    latest: dict[str, dict] = {}
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
                latest[sid] = r
    return first, list(latest.values())


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--intl-jsonl", nargs="*", default=None,
                    help="pre-filtered intl rows, to skip the ~12GB/day scan")
    ap.add_argument("--max-intl-age-s", type=float, default=None,
                    help="drop points whose intl quote is staler than this")
    a = ap.parse_args(argv)
    data_dir = Path(a.data_dir)

    first_mapped, mappings = load_mappings(data_dir)
    joined, _refusals = join_markets(load_intl_catalog(a.days, data_dir), mappings)
    train_ids, holdout_ids = split_by_boundary([j.grid_series_id for j in joined], first_mapped)
    train = [j for j in joined if j.grid_series_id in set(train_ids)]
    # The gate, on the set we are about to read. Raises if anything holdout slipped in.
    require_training_only([j.grid_series_id for j in train], first_mapped)
    print(f"joined markets {len(joined)} -> TRAINING {len(train)} "
          f"(holdout {len(set(holdout_ids))} series NOT read)\n")

    intl = load_intl_quotes({j.intl.yes_token for j in train}, a.days, data_dir,
                            [Path(p) for p in a.intl_jsonl] if a.intl_jsonl else None)
    pmus = load_pmus_quotes({j.pmus_market_slug for j in train}, a.days, data_dir)
    max_age = int(a.max_intl_age_s * 1e9) if a.max_intl_age_s else None

    print(f"{'market':<32} {'intl evts':>9} {'pmus obs':>8} {'n':>5} "
          f"{'|basis|t':>9} {'>=2t':>5} {'age s':>7}")
    pooled, covered = [], 0
    for j in train:
        iq = intl.get(j.intl.yes_token, [])
        pq = pmus.get(j.pmus_market_slug, [])
        pts = basis_series(pq, iq, j.orientation, max_age)
        pooled += pts
        s = summarise(pts)
        if s["n"]:
            covered += 1
            print(f"{j.pmus_market_slug[:32]:<32} {len(iq):>9,} {len(pq):>8,} {s['n']:>5,} "
                  f"{s['median_abs_basis_ticks']:>9.2f} {s['frac_abs_ge_2_ticks']:>5.2f} "
                  f"{s['median_intl_age_s']:>7.1f}")
        else:
            print(f"{j.pmus_market_slug[:32]:<32} {len(iq):>9,} {len(pq):>8,} {0:>5}"
                  f"   (no PMUS coverage)")
    print(f"\ncoverage: {covered} of {len(train)} training markets had ANY overlap")
    print("POOLED (training only; descriptive -- no edge claim, no verdict):")
    for k, v in summarise(pooled).items():
        print(f"   {k:<26} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
