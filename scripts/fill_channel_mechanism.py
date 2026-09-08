#!/usr/bin/env python3
"""Why the BBO channel is blind: the mechanism test behind H-MAPENTRY1-NOTE11.

The first writeup of NOTE11 blamed POLL CADENCE ("a 30s poll of a continuous
process"). That was wrong twice over. 30.7s is the interval between tape ROWS,
and ``BookTape`` is change-only with a 30s heartbeat, so most of it is the write
policy; the recorder actually polls ~54 times a minute. And cadence was never
the binding constraint anyway.

The binding constraint is that **a price series is not an execution record**. A
trade that partially consumes a resting order leaves the touch PRICE unchanged,
so no poll rate -- however fast -- registers it. This script measures that
directly: for every print, compare the BBO row immediately before it with the
one immediately after.

  price identical across the print  -> invisible to a price-only channel, always
  ...and size moved                 -> and provably a real execution against depth

Two complementary blindnesses are reported. The second (a print priced outside
BOTH bracketing quotes) IS cadence-related -- the book moved and reverted
between looks -- and is the one a faster poll would shrink. The first is not.

WHAT THIS IS NOT. Not an edge measurement, not a PnL, and not a claim about
H-CROSSVENUE1, which is not pre-registered (iron rail 2). It measures one
property of an observation channel.

Run: uv run python scripts/fill_channel_mechanism.py [--days 20260907 20260908]
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot.analysis.trade_prints import EPS, load_day
from gridbot.jsonl import iter_jsonl

BBO_ROW = "pmus_bbo"


def load_bbo(day: str, data_dir: Path) -> dict[str, list[tuple]]:
    """(ts_ns, bid, ask, bid_size, ask_size) per slug, sorted by receive clock."""
    out: dict[str, list[tuple]] = defaultdict(list)
    for r in iter_jsonl(Path(data_dir) / "pmus" / f"books-{day}.jsonl"):
        if r.get("type") != BBO_ROW:
            continue
        try:
            out[str(r["slug"])].append((int(r["ts_ns"]), r.get("bid"), r.get("ask"),
                                        r.get("bid_size"), r.get("ask_size")))
        except (KeyError, TypeError, ValueError):
            continue
    for v in out.values():
        v.sort(key=lambda o: o[0])
    return dict(out)


def audit(days: list[str], data_dir: Path) -> dict:
    c: dict[str, float] = defaultdict(float)
    for day in days:
        books = load_bbo(day, data_dir)
        prints = load_day(day, data_dir)
        for slug, prs in prints.items():
            bk = books.get(slug) or []
            if len(bk) < 2:
                continue
            ts = [o[0] for o in bk]
            for p in prs:
                j = bisect.bisect_right(ts, p.t_ns) - 1
                if j < 0 or j + 1 >= len(bk):
                    continue
                before, after = bk[j], bk[j + 1]
                c["bracketed"] += 1
                if before[1] == after[1] and before[2] == after[2]:
                    c["price_identical"] += 1
                    if None not in (before[3], after[3]):
                        c["sized"] += 1
                        if (abs((before[3] or 0) - (after[3] or 0)) > EPS
                                or abs((before[4] or 0) - (after[4] or 0)) > EPS):
                            c["size_moved"] += 1

                def outside(o, px: float = p.yes_price) -> bool:
                    b, a = o[1], o[2]
                    return False if b is None or a is None else (px < b - EPS or px > a + EPS)

                if outside(before) and outside(after):
                    c["outside_both"] += 1
                    c["vol_outside_both"] += p.qty
                c["vol_bracketed"] += p.qty
    return dict(c)


def render(c: dict, days: list[str]) -> str:
    n = c.get("bracketed", 0) or 1
    sized = c.get("sized", 0) or 1
    v = c.get("vol_bracketed", 0.0) or 1.0
    return "\n".join([
        "# Fill-channel mechanism: what a price-only tape cannot see", "",
        f"Days: {', '.join(days)}", "",
        f"- prints bracketed by two BBO rows: **{int(c.get('bracketed', 0)):,}**",
        (f"- BBO price **identical** before and after: "
         f"**{int(c.get('price_identical', 0)):,}** "
         f"({100 * c.get('price_identical', 0) / n:.1f}%) — invisible at ANY poll rate"),
        (f"  - of those with sizes recorded ({int(c.get('sized', 0)):,}), "
         f"top-of-book size moved: **{int(c.get('size_moved', 0)):,}** "
         f"({100 * c.get('size_moved', 0) / sized:.1f}%)"),
        (f"- priced outside BOTH bracketing quotes: "
         f"**{int(c.get('outside_both', 0)):,}** "
         f"({100 * c.get('outside_both', 0) / n:.1f}%), "
         f"${c.get('vol_outside_both', 0):,.0f} of ${c.get('vol_bracketed', 0):,.0f} "
         f"({100 * c.get('vol_outside_both', 0) / v:.1f}% of notional) — this one IS cadence"),
        "",
        "A trade that partially consumes a resting order does not move the touch",
        "price. Faster polling returns the same quote on both sides of it. Only a",
        "size-aware or print-based channel observes those executions at all.",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=None, help="write JSON here")
    a = ap.parse_args(argv)
    counts = audit(a.days, Path(a.data_dir))
    print(render(counts, a.days))
    if a.out:
        Path(a.out).write_text(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
