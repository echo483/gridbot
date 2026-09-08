#!/usr/bin/env python3
"""How fast do we learn what intl did? And does our own slowness pick us off?

The cross-venue lane rests on acting inside the window between an intl repricing
and PMUS following it. PMUS follows at a median 35.8s (crossvenue_response), so
the question is how much of that window our own ingest spends.

Every intl WS ``price_change`` carries the venue's own millisecond timestamp
alongside our receive clock, so feed latency is directly measurable rather than
inferred.

TWO READINGS, and the second is the one that matters for going live:

  BULK      the distribution of venue-stamp -> our-receive latency
  BY LABEL  that latency split by what PMUS did (STALE / INFORMED / NO_FOLLOW).
            If INFORMED events -- the pick-offs, where PMUS moved first -- ran
            systematically late, the -6.5pt fill-selection cost would be OUR
            latency and could be engineered away. If they do not, it is genuine
            adverse selection and no amount of infrastructure fixes it.

CLOCK SKEW IS A REAL LIMIT ON THE ABSOLUTE NUMBER. We do not sync to the venue's
clock, and a small share of events arrive "before" their venue stamp, which
bounds the skew rather than explaining it away. Constant skew cancels out of the
BY LABEL comparison, which is why that comparison carries the conclusion and the
absolute median does not.

Training series only (iron rail 2). No edge claim.

Run: uv run python scripts/intl_feed_latency.py --intl-jsonl FILE [FILE ...] [--by-label]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot.analysis.book_liveness import classify
from gridbot.analysis.crossvenue_basis import Quote, require_training_only, split_by_boundary
from gridbot.analysis.crossvenue_io import load_intl_catalog, load_mappings, load_pmus_quotes
from gridbot.analysis.crossvenue_join import join_markets
from gridbot.analysis.crossvenue_response import responses
from gridbot.analysis.crossvenue_signal import intl_moves

QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90, 0.99, 0.999)


def pct(xs: list[float], q: float) -> float:
    return xs[min(len(xs) - 1, max(0, int(q * len(xs))))]


def read_latencies(paths: list[Path], tokens: set[str] | None
                   ) -> tuple[list[float], dict, dict]:
    """(all latencies, quotes per token, latency per (token, ts_ns))."""
    allv: list[float] = []
    quotes: dict[str, list[Quote]] = defaultdict(list)
    per: dict[str, dict[int, float]] = defaultdict(dict)
    for p in paths:
        if not p.exists():
            continue
        with p.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw, ts = r.get("raw"), r.get("ts_ns")
                if not isinstance(raw, dict) or not ts:
                    continue
                if raw.get("event_type") != "price_change":
                    continue
                try:
                    lat = ts / 1e6 - int(raw.get("timestamp"))
                except (TypeError, ValueError):
                    continue
                allv.append(lat)
                for pc in raw.get("price_changes") or []:
                    tok = pc.get("asset_id")
                    if tokens is not None and tok not in tokens:
                        continue
                    try:
                        quotes[tok].append(Quote(int(ts), float(pc["best_bid"]),
                                                 float(pc["best_ask"])))
                    except (KeyError, TypeError, ValueError):
                        continue
                    per[tok][int(ts)] = lat
    for v in quotes.values():
        v.sort(key=lambda q: q.t_ns)
    return allv, dict(quotes), dict(per)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--intl-jsonl", nargs="+", required=True)
    ap.add_argument("--by-label", action="store_true")
    a = ap.parse_args(argv)
    dd = Path(a.data_dir)

    first, maps = load_mappings(dd)
    joined, _ = join_markets(load_intl_catalog(a.days, dd), maps)
    train_ids, _h = split_by_boundary([j.grid_series_id for j in joined], first)
    train = [j for j in joined if j.grid_series_id in set(train_ids)]
    require_training_only([j.grid_series_id for j in train], first)

    allv, quotes, per = read_latencies([Path(p) for p in a.intl_jsonl],
                                       {j.intl.yes_token for j in train})
    allv.sort()
    neg = sum(1 for v in allv if v < 0)
    print(f"intl WS price_change messages: {len(allv):,}")
    print("venue stamp -> our receive (ms):")
    for q in QUANTILES:
        print(f"   p{q * 100:>6.1f}  {pct(allv, q):>10.1f}")
    print(f"   max      {allv[-1]:>10.1f}" if allv else "")
    print(f"   arriving 'before' their venue stamp: {neg:,} "
          f"({100 * neg / max(len(allv), 1):.2f}%) — bounds the clock skew")

    if not a.by_label:
        return 0

    pmus = load_pmus_quotes({j.pmus_market_slug for j in train}, a.days, dd)
    buckets: dict[str, list[float]] = defaultdict(list)
    for j in train:
        iq, pq = quotes.get(j.intl.yes_token, []), pmus.get(j.pmus_market_slug, [])
        if not iq or not pq:
            continue
        if not classify([(q.t_ns, q.bid, q.ask) for q in pq]).usable_for_basis:
            continue
        lats = per.get(j.intl.yes_token, {})
        for r in responses(intl_moves(iq), pq, j.orientation):
            v = lats.get(r.t_event_ns)
            if v is not None:
                buckets[r.label].append(v)

    print(f"\n{'label':<12}{'n':>6}{'p50 ms':>10}{'p90 ms':>10}{'p99 ms':>12}"
          f"{'>1s':>8}{'>5s':>8}")
    for lab in ("stale", "informed", "no_follow"):
        v = sorted(buckets.get(lab, []))
        if not v:
            continue
        print(f"{lab:<12}{len(v):>6}{pct(v, .5):>10.1f}{pct(v, .9):>10.1f}"
              f"{pct(v, .99):>12.1f}{sum(1 for x in v if x > 1000) / len(v):>8.3f}"
              f"{sum(1 for x in v if x > 5000) / len(v):>8.3f}")
    print("\nIf the medians match across labels, our ingest is NOT what gets us picked")
    print("off, and the fill-selection cost is genuine adverse selection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
