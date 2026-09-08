#!/usr/bin/env python3
"""Audit the BBO fill channel against ACTUAL trade prints.

Answers one question: does the EXEC1 fill bracket contain the truth? It does
not. The old UPPER bound (a sampled BBO quote touching our price) fires LESS
often than the new CERTAIN floor (a print strictly through our price, which
price priority guarantees we would have filled). A ceiling below a floor is not
a bracket, and every number computed through it is unbounded in the direction
that matters -- which is the direction that decides whether a maker strategy is
tradeable.

METHOD. Every BBO observation is treated as a hypothetical posting instant. At
each one we post a resting BUY at two prices -- at the bid, and improving it by
one tick -- and give the order the engine's real MAKER_TTL_S lifetime. Then we
ask the two channels the same question over the same window:

  BBO channel     did a later SAMPLED quote reach our price?   (exec_fills)
  PRINT channel   did a trade actually print at/through it?    (trade_prints)

Both channels are reported at both edges, so the nesting is visible rather than
asserted: `bbo_strict <= bbo_at` and `print_through <= print_any` by
construction, and the finding is that `bbo_at < print_through`.

WHAT THIS IS NOT. Not an edge measurement and not a PnL: the postings are
hypothetical and unconditional (every book observation, no signal, no
qualification), so nothing here says whether entering was profitable -- only how
often a resting order would have traded. That separation is deliberate. The
successor lane H-CROSSVENUE1 is NOT pre-registered (iron rail 2), so no
instrument in this repo may measure its edge yet; the fill channel underneath it
is framework-independent and required either way, which is why it is built
first.

Run: uv run python scripts/fill_print_audit.py [--days 20260907 20260908] [--out FILE]
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

from gridbot.analysis.trade_prints import (
    DEFAULT_TTL_NS,
    EPS,
    load_day,
    print_evidence,
)
from gridbot.jsonl import iter_jsonl

US_TICK = 0.01
BBO_ROW = "pmus_bbo"


def load_bbo(day: str, data_dir: Path) -> dict[str, list[tuple]]:
    """One day of BBO observations, indexed by slug, sorted by receive clock."""
    out: dict[str, list[tuple]] = defaultdict(list)
    for r in iter_jsonl(Path(data_dir) / "pmus" / f"books-{day}.jsonl"):
        if r.get("type") != BBO_ROW:
            continue
        try:
            out[str(r["slug"])].append(
                (int(r["ts_ns"]), r.get("bid"), r.get("ask"),
                 r.get("bid_size"), r.get("ask_size"))
            )
        except (KeyError, TypeError, ValueError):
            continue
    for v in out.values():
        v.sort(key=lambda o: o[0])
    return dict(out)


def audit_day(day: str, data_dir: Path, ttl_ns: int = DEFAULT_TTL_NS) -> dict:
    """Run both channels over every hypothetical posting instant in one day."""
    books = load_bbo(day, data_dir)
    prints = load_day(day, data_dir)

    rules = {"at_bid": 0.0, "bid_plus_1tick": US_TICK}
    counts = {k: defaultdict(int) for k in rules}
    # Prints outside BOTH bracketing quotes: executions a 30s poll cannot see.
    invisible = traded = 0
    vol_invisible = vol_traded = 0.0

    for slug, bk in books.items():
        pr = prints.get(slug, [])
        pts = [p.t_ns for p in pr]
        bts = [o[0] for o in bk]

        for i, (t0, bid, ask, _bs, _as) in enumerate(bk):
            if bid is None or ask is None:
                continue
            end = t0 + ttl_ns
            window = [o for o in bk[i + 1:] if o[0] <= end]
            for label, improve in rules.items():
                cost = round(bid + improve, 4)
                if not (0.0 < cost < 1.0):
                    continue
                c = counts[label]
                c["n"] += 1
                # BBO channel: a later SAMPLED quote reaches our price.
                if any(o[2] is not None and o[2] < cost - EPS for o in window):
                    c["bbo_strict"] += 1
                if any(o[2] is not None and o[2] <= cost + EPS for o in window):
                    c["bbo_at"] += 1
                # PRINT channel: the venue's own executions.
                lo = bisect.bisect_right(pts, t0)
                hi = bisect.bisect_right(pts, end)
                ev = print_evidence(pr[lo:hi], cost, t0, "long", ttl_ns)
                if ev.certain:
                    c["print_through"] += 1
                if ev.possible:
                    c["print_any"] += 1
                if ev.certain and not (
                    any(o[2] is not None and o[2] <= cost + EPS for o in window)
                ):
                    c["certain_but_bbo_blind"] += 1

        # Invisibility census: a print whose price sits outside the quoted range
        # of BOTH the poll before it and the poll after it.
        for p in pr:
            j = bisect.bisect_right(bts, p.t_ns) - 1
            if j < 0 or j + 1 >= len(bk):
                continue
            traded += 1
            vol_traded += p.qty

            def outside(o, px: float = p.yes_price) -> bool:
                b, a = o[1], o[2]
                if b is None or a is None:
                    return False
                return px < b - EPS or px > a + EPS

            if outside(bk[j]) and outside(bk[j + 1]):
                invisible += 1
                vol_invisible += p.qty

    return {
        "day": day,
        "slugs": len(books),
        "postings": {k: dict(v) for k, v in counts.items()},
        "invisibility": {
            "prints_bracketed": traded,
            "prints_outside_both_quotes": invisible,
            "volume_bracketed": round(vol_traded, 2),
            "volume_outside_both_quotes": round(vol_invisible, 2),
        },
    }


def merge(reports: list[dict]) -> dict:
    """Pool per-day reports; every count here is additive by construction."""
    total: dict = {"days": [r["day"] for r in reports],
                   "postings": defaultdict(lambda: defaultdict(int)),
                   "invisibility": defaultdict(float)}
    for r in reports:
        for label, c in r["postings"].items():
            for k, v in c.items():
                total["postings"][label][k] += v
        for k, v in r["invisibility"].items():
            total["invisibility"][k] += v
    total["postings"] = {k: dict(v) for k, v in total["postings"].items()}
    total["invisibility"] = dict(total["invisibility"])
    return total


def render(total: dict) -> str:
    """Markdown, with the bracket violation stated explicitly or denied."""
    out = ["# Fill-channel audit: BBO quotes vs actual prints", ""]
    out.append(f"Days: {', '.join(total['days'])}")
    inv = total["invisibility"]
    n = inv.get("prints_bracketed", 0) or 1
    v = inv.get("volume_bracketed", 0.0) or 1.0
    out += [
        "",
        "## Prints a 30s poll cannot see",
        "",
        f"- prints bracketed by two polls: **{int(inv.get('prints_bracketed', 0)):,}**",
        (f"- priced outside BOTH bracketing quotes: "
         f"**{int(inv.get('prints_outside_both_quotes', 0)):,}** "
         f"({100 * inv.get('prints_outside_both_quotes', 0) / n:.1f}%)"),
        (f"- as notional: **${inv.get('volume_outside_both_quotes', 0):,.0f}** of "
         f"${inv.get('volume_bracketed', 0):,.0f} "
         f"({100 * inv.get('volume_outside_both_quotes', 0) / v:.1f}%)"),
        "",
        "## Fill rate by channel",
        "",
        ("| posting | n | BBO strict | BBO at (old UPPER) | "
         "print through (CERTAIN) | print any | bracket |"),
        "|---|---|---|---|---|---|---|",
    ]
    violated = False
    for label, c in total["postings"].items():
        m = c.get("n", 0) or 1
        bbo_at, thru = c.get("bbo_at", 0), c.get("print_through", 0)
        bad = bbo_at < thru
        violated = violated or bad
        out.append(
            f"| `{label}` | {m:,} | {100 * c.get('bbo_strict', 0) / m:.1f}% | "
            f"{100 * bbo_at / m:.1f}% | **{100 * thru / m:.1f}%** | "
            f"{100 * c.get('print_any', 0) / m:.1f}% | "
            f"{'**VIOLATED**' if bad else 'holds'} |"
        )
    out += ["", "## Verdict", ""]
    if violated:
        out.append(
            "The old UPPER bound fires less often than a fill price priority "
            "GUARANTEES. The EXEC1 bracket does not contain the truth, so every "
            "fill-rate and per-fill number computed through it is invalid in the "
            "conservative direction — it understates how often a resting order "
            "trades. This is a specification error in the observation channel, "
            "not a parameter error: no additional 30s BBO data corrects it."
        )
    else:
        out.append("No bracket violation detected on these days.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", nargs="+", default=["20260907", "20260908"])
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=None, help="write JSON here")
    ap.add_argument("--md", default=None, help="write markdown here")
    a = ap.parse_args(argv)

    reports = [audit_day(d, Path(a.data_dir)) for d in a.days]
    total = merge(reports)
    md = render(total)
    print(md)
    if a.out:
        Path(a.out).write_text(json.dumps({"per_day": reports, "total": total}, indent=2))
    if a.md:
        Path(a.md).write_text(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
