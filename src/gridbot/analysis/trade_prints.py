"""Fill evidence from ACTUAL trade prints — the channel NOTE10 made mandatory.

``exec_fills`` opens with "We have NO trade prints, only sparse book
observations". That premise expired on 2026-09-07, when ``gridbot-pmus-trades``
began recording the venue's own executions. This module is the replacement
observation channel, and it exists because the BBO one is not merely imprecise —
it is MISSPECIFIED. Measured 2026-09-07/08 over 2,620 hypothetical postings:

    resting BUY at the bid   BBO "any touch" (the old UPPER)  19.0%
                             prints traded THROUGH us         30.6%   <- certain

The old upper bound sits BELOW a certainty. A bracket whose ceiling is under the
floor does not contain the truth, so every number computed through it is
unbounded in the direction that matters. The cause is not a bad parameter: the
BBO tape is a ~30s poll of a continuous execution process, and 14.1% of prints
(18.5% of dollar volume) land outside BOTH bracketing snapshots — the book moved
and reverted entirely between two looks. No amount of additional 30s data fixes
that; only a different observation channel does.

WHAT A PRINT PROVES (price priority, not statistics)

All venue prices are Yes prices (registry ``H-MAPENTRY1-NOTE7``); callers work in
OUR-SIDE COST, exactly as ``exec_fills`` does, so a LONG's cost is the Yes price
and a SHORT's cost is ``1 - yes``. Under that single convention, for a resting
order at cost ``P``:

- a print STRICTLY BELOW ``P`` means someone accepted a worse price than we were
  offering. Price priority puts our order ahead of theirs, so we fill: CERTAIN.
- a print AT exactly ``P`` shares our price, so time priority in the queue
  decides. Evidence, not proof: an UPPER bound.

That asymmetry is the whole point — ``through`` is a floor you can stand on and
``at`` is a ceiling, which is the bracket the BBO channel could never produce.

THE COUNTERFACTUAL CAVEAT, STATED RATHER THAN HIDDEN

These are hypothetical orders: adding one to the book perturbs the book it is
measured against. A taker who sold below ``P`` would have hit OUR bid first, so
that print would not have printed where it did. The fill conclusion survives
(we are filled either way — that is the direction of the perturbation), but the
PRICE and the residual size do not, and neither does any claim about what the
book looked like afterwards. This module therefore reports fill evidence and
traded volume only; it computes no PnL and no edge.

It also cannot be back-validated against our own fills: the trades tape begins
2026-09-07 and the armed nights were 2026-08-26/27. There is no overlap, so the
argument here is market mechanics, never calibration. Anything requiring
calibration against real venue outcomes must wait for a live-small session.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gridbot.jsonl import iter_jsonl

# The venue quotes and prints on a 1-cent grid (verified across 5,597 prints,
# 2026-09-07/08). EPS separates "through" from "at" without tripping on the
# float noise in a string-decimal payload like "0.8900".
EPS = 1e-9
DEFAULT_TTL_NS = 150_000_000_000

TRADE_ROW = "pmus_trade"


@dataclass(frozen=True)
class Print:
    """One executed trade, verbatim from the venue plus our receive clock."""

    t_ns: int          # OUR receive clock (median 85ms behind venue tradeTime)
    yes_price: float   # the venue price — ALWAYS the Yes price (NOTE7)
    qty: float         # notional, in the payload's currency units
    maker_intent: str
    taker_intent: str


@dataclass(frozen=True)
class PrintEvidence:
    """What the prints in one order's lifetime window prove about its fill."""

    through: bool          # a print strictly better than our cost -> CERTAIN fill
    at: bool               # a print exactly at our cost -> queue-dependent
    t_first_ns: int | None  # first print that is `through`, else first that is `at`
    vol_through: float     # traded notional strictly better than our cost
    vol_at: float          # traded notional exactly at our cost
    n_prints: int          # every print in the window, at any price

    @property
    def certain(self) -> bool:
        """The lower edge: a fill price priority guarantees."""
        return self.through

    @property
    def possible(self) -> bool:
        """The upper edge: `through` or a queue-dependent print at our price."""
        return self.through or self.at


def our_side_price(yes_price: float, side: str) -> float:
    """Restate a venue Yes price as OUR-SIDE COST for ``side``.

    The same conversion ``exec_fills`` requires of its callers, in one place so
    the two channels cannot drift apart. NOTE7 exists because this repo already
    shipped the inverse of it once, on 20 of 48 venue-verified orders.
    """
    if side == "long":
        return yes_price
    if side == "short":
        return round(1.0 - yes_price, 6)
    raise ValueError(f"unknown side {side!r}; refusing to guess a price convention")


def print_evidence(
    prints: Iterable[Print],
    cost: float,
    t0_ns: int,
    side: str,
    ttl_ns: int = DEFAULT_TTL_NS,
) -> PrintEvidence:
    """Fill evidence for a resting order at ``cost`` over its lifetime window.

    The window matches ``exec_fills.simulate`` exactly — open at ``t0_ns``,
    closed at ``t0_ns + ttl_ns`` — so the two channels bracket the SAME order
    and their outputs are directly comparable.
    """
    end_ns = t0_ns + ttl_ns
    through = at = False
    t_through: int | None = None
    t_at: int | None = None
    vol_through = vol_at = 0.0
    n = 0

    for p in prints:
        if p.t_ns <= t0_ns or p.t_ns > end_ns:
            continue
        n += 1
        ours = our_side_price(p.yes_price, side)
        if ours < cost - EPS:
            through = True
            vol_through += p.qty
            if t_through is None:
                t_through = p.t_ns
        elif abs(ours - cost) <= EPS:
            at = True
            vol_at += p.qty
            if t_at is None:
                t_at = p.t_ns

    return PrintEvidence(
        through=through,
        at=at,
        t_first_ns=t_through if t_through is not None else t_at,
        vol_through=vol_through,
        vol_at=vol_at,
        n_prints=n,
    )


def parse_print(row: dict[str, Any]) -> tuple[str, Print] | None:
    """A tape row -> ``(slug, Print)``, or None when it is not a usable print.

    Returns None rather than raising: the tape interleaves status, ack and
    unknown frames with the prints (154 status / 50 ack / 1 unknown against
    5,597 prints on 2026-09-07/08), and a recorder that stores frames verbatim
    is free to gain new ones.
    """
    if row.get("type") != TRADE_ROW:
        return None
    trade = (row.get("payload") or {}).get("trade")
    if not isinstance(trade, dict):
        return None
    slug = trade.get("marketSlug")
    if not slug:
        return None
    try:
        price = float((trade.get("price") or {}).get("value"))
        qty = float((trade.get("quantity") or {}).get("value"))
        t_ns = int(row["ts_ns"])
    except (TypeError, ValueError, KeyError):
        return None
    return str(slug), Print(
        t_ns=t_ns,
        yes_price=price,
        qty=qty,
        maker_intent=str((trade.get("maker") or {}).get("intent") or ""),
        taker_intent=str((trade.get("taker") or {}).get("intent") or ""),
    )


def load_day(day: str, data_dir: Path) -> dict[str, list[Print]]:
    """Load one day's prints, indexed by slug and sorted by our receive clock.

    ``day`` is bare ``YYYYMMDD`` (scripts/_jsonl.py owns that convention; dashed
    dates do not exist on this disk). A missing file is an empty day, not a
    crash — capture gaps are a fact of the record, and this reader is used by
    reports that must render a zero rather than die.
    """
    out: dict[str, list[Print]] = {}
    path = Path(data_dir) / "pmus" / f"trades-{day}.jsonl"
    for row in iter_jsonl(path):
        hit = parse_print(row)
        if hit is None:
            continue
        slug, p = hit
        out.setdefault(slug, []).append(p)
    for prints in out.values():
        prints.sort(key=lambda p: p.t_ns)
    return out
