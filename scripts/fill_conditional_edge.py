#!/usr/bin/env python3
"""Edge CONDITIONAL ON FILLING — the number that explains the PnL.

pmap_calibration says the entry signal is +2.8pt/contract on paper. Three
armed nights say -61pt/trade. Both cannot be describing the same trades,
and the suspect is fill selection: a resting maker BUY does not fill when
we are right, it fills when someone is willing to sell to us at our price
— which is disproportionately when the book is moving AGAINST the side we
took. Paper edge assumes a fill at the posted price always. This measures
the subset that would actually have filled.

FILL PROXY. For each qualifying mapentry row the executor would post at
``min(side_ask - tick, entry_price_max, ceiling)``. The postquote sampler
already snapshots the same book at 30/60/120s after that evaluation
(join: postquote.src_ts_ns == mapentry.ts_ns), and MAKER_TTL_S is 150s,
so all three horizons fall inside the resting window. The order is
counted FILLED when any snapshot shows our side's effective ask at or
below our price:
    long  -> ask <= price          (someone crossed down to us)
    short -> 1 - bid <= price      (our side's effective ask; short buys
                                    the No leg, so its ask is 1 - bid)

The proxy errs in BOTH directions and neither is corrected here:
  UNDER-counts — three snapshots miss a touch that happens between them.
  OVER-counts  — the ask reaching our price is not proof that volume
                 traded through our queue position at it.
It is a bound-setting instrument, not a fill simulator. What matters is
the SIGN of the difference between filled and unfilled edge, which no
plausible sampling correction flips.

Run: uv run python scripts/fill_conditional_edge.py [--out FILE]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from pmap_calibration import US_TICK, load_rows, load_winners, outcome, posted_price

MAKER_TTL_S = 150.0  # mirrors engine.MAKER_TTL_S


def side_ask_at(us_side: str, bid: float | None, ask: float | None) -> float | None:
    """Effective ask for OUR side from a book snapshot (mirrors
    instruments.mapentry.side_ask: long pays the ask, short pays 1 - bid)."""
    if us_side == "long":
        return ask if isinstance(ask, (int, float)) else None
    if isinstance(bid, (int, float)):
        return round(1.0 - float(bid), 4)
    return None


def load_postquotes(pattern: str) -> dict[int, list[dict]]:
    """src_ts_ns -> its snapshots, keeping only horizons inside the TTL."""
    out: dict[int, list[dict]] = defaultdict(list)
    for f in sorted(glob.glob(pattern)):
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") != "postquote":
                    continue
                dt = r.get("dt_s")
                src = r.get("src_ts_ns")
                if isinstance(src, int) and isinstance(dt, (int, float)) \
                        and 0 < float(dt) <= MAKER_TTL_S:
                    out[src].append(r)
    return out


def would_fill(row: dict, price: float, snaps: list[dict]) -> bool:
    """True when any in-TTL snapshot puts our side's ask at or below us."""
    us_side = row.get("us_side")
    for s in snaps:
        eff = side_ask_at(str(us_side), s.get("bid"), s.get("ask"))
        if eff is not None and eff <= price + 1e-9:
            return True
    return False


def summarize(items: list[tuple[float, float]]) -> dict:
    """items are (outcome, price) pairs.

    Carries a normal-approximation 95% CI on the EV. Binary settlement
    makes per-contract PnL a high-variance quantity (sigma ~ 0.5), so a
    few dozen samples cannot resolve a few points of edge — reporting the
    point estimate alone is how a noise reading becomes a "finding".
    """
    n = len(items)
    if not n:
        return {"n": 0}
    pnl = [y - p for y, p in items]
    mean = sum(pnl) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in pnl) / (n - 1)
        se = (var / n) ** 0.5
    else:
        se = float("nan")
    return {
        "n": n,
        "win_rate": round(sum(y for y, _ in items) / n, 4),
        "mean_price": round(sum(p for _, p in items) / n, 4),
        "ev_per_contract": round(mean, 4),
        "ev_stderr": round(se, 4),
        "ev_ci95": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
        "significant_at_95": bool(abs(mean) > 1.96 * se),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapentry", default=str(ROOT / "data/shadow/mapentry-*.jsonl"))
    ap.add_argument("--postquote", default=str(ROOT / "data/shadow/postquote-*.jsonl"))
    ap.add_argument("--state", default=str(ROOT / "data/grid/series_state-*.jsonl"))
    ap.add_argument("--out")
    a = ap.parse_args()

    winners = load_winners(a.state)
    quotes = load_postquotes(a.postquote)
    rows = [r for r in load_rows(a.mapentry) if r.get("qualifies")]

    # One entry per (series, map, side) — the engine's dedup key. Without
    # this every re-qualifying round counts the same book again and both
    # the fill rate and the edge inflate together.
    seen: set[tuple] = set()
    filled: list[tuple[float, float]] = []
    unfilled: list[tuple[float, float]] = []
    no_quotes = 0
    for r in sorted(rows, key=lambda x: x.get("ts_ns") or 0):
        key = (r.get("series_id"), r.get("map_number"), r.get("us_side"))
        if key in seen:
            continue
        y, price = outcome(r, winners), posted_price(r)
        if y is None or price is None:
            continue
        seen.add(key)
        snaps = quotes.get(r.get("ts_ns")) or []
        if not snaps:
            no_quotes += 1
            continue
        (filled if would_fill(r, price, snaps) else unfilled).append((y, price))

    both = filled + unfilled
    report = {
        "generated": __import__("datetime").datetime.now(
            __import__("datetime").UTC).isoformat(timespec="seconds"),
        "maker_ttl_s": MAKER_TTL_S,
        "us_tick": US_TICK,
        "opportunities": len(both),
        "dropped_no_postquote_samples": no_quotes,
        "fill_rate": round(len(filled) / len(both), 4) if both else None,
        "all_opportunities": summarize(both),
        "filled": summarize(filled),
        "unfilled": summarize(unfilled),
        "selection_cost_per_contract": (
            round(summarize(filled)["ev_per_contract"]
                  - summarize(both)["ev_per_contract"], 4)
            if filled and both else None
        ),
        # How many FILLED orders it would take to call the selection cost
        # real at 95%, from the observed dispersion. Reported so the number
        # above is read as "how far in" rather than "how far off".
        "filled_n_needed_at_95": (
            int((1.96 * summarize(filled)["ev_stderr"] * len(filled) ** 0.5
                 / abs(summarize(filled)["ev_per_contract"])) ** 2) + 1
            if len(filled) > 1 and summarize(filled)["ev_per_contract"] else None
        ),
        "notes": [
            ("Fill proxy: any postquote snapshot within MAKER_TTL_S showing "
            "our side's effective ask at or below the posted price. It "
            "under-counts (3 snapshots miss touches between them) and "
            "over-counts (the ask reaching our price is not proof of a "
            "trade through our queue position). Read the SIGN, not the "
            "magnitude."),
            ("Deduped to one entry per (series, map, side), matching the "
            "engine's dedup key."),
            ("selection_cost_per_contract is filled EV minus unconditional "
            "EV: how much of the paper edge the fill condition destroys. "
            "Negative means the orders that fill are worse than the "
            "average order we would like to place."),
        ],
    }
    out = json.dumps(report, indent=2)
    if a.out:
        Path(a.out).write_text(out + "\n")
        print(f"wrote {a.out}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
