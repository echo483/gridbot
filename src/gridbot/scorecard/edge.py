"""Edge: what a trade is worth AFTER the price you can actually get.

Two disciplines live here, both learned by losing money first.

**Executable prices only.** A mid-priced edge is not an edge. The repo's
``H-CROSSIN1`` lane died because a fallen-favourite edge did not survive
repricing to the ask. Every function here takes the price you would PAY, not
the price you would like.

**The venue price convention, in exactly one place.** PMUS stores the
``price`` field as the Yes/long price, always. A short at cost ``P`` is
recorded as ``SELL @ P`` and costs ``1 - P`` per contract. Before this was
pinned, every armed-night short was posted at the Yes price instead of its
cost: shorts with cost below 0.50 crossed the bid, were rejected post-only
(15 of 24), and ``realized_pnl`` read fine while ``venue_pnl`` did not. The
conversion lived at the wire boundary and was easy to re-derive wrongly.

:func:`side_cost` is now the single definition of "what our side pays".
Callers convert once, at the adapter boundary, and nowhere else.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from gridbot.scorecard.intervals import DEFAULT_ALPHA, wilson_lower
from gridbot.scorecard.result import Score

Side = Literal["long", "short"]
ShortSide = Literal["long", "short", "unknown"]


def ev_per_contract(p: float, price: float) -> float:
    """Expected profit per $1-payout contract bought at ``price``.

    A binary contract settles at 1 or 0, so the expectation is ``p - price``.
    Trivially simple, and the reason a 21-point calibration bias in the armed
    band translates almost one-for-one into an expected loss at the posted
    price.
    """
    return p - price


def conservative_ev(
    wins: int, n: int, price: float, alpha: float = DEFAULT_ALPHA
) -> float | None:
    """EV per contract priced off the Wilson LOWER bound on the win rate.

    The single most useful number for a decision: it answers "if the world is
    at the pessimistic edge of what this sample can support, do we still make
    money at this price". A lane whose conservative EV is negative is not a
    lane, however good its point estimate looks.
    """
    if n <= 0:
        return None
    return ev_per_contract(wilson_lower(wins, n, alpha), price)


def side_cost(side: Side, long_bid: float, long_ask: float) -> float:
    """What OUR side pays per contract, from a long-side (Yes) book.

    * long  — pay the ask.
    * short — sell the long bid; cost is ``1 - long_bid``.

    The venue only ever quotes the long side for match/map winner books, so
    every short price in this system is derived here. Never re-derive it at a
    call site: that is the bug this function replaces.
    """
    if side == "long":
        return long_ask
    if side == "short":
        return 1.0 - long_bid
    raise ValueError(f"unknown side {side!r}")


def venue_wire_price(side: Side, our_cost: float) -> float:
    """Inverse of :func:`side_cost` — the Yes price to send the venue.

    PMUS records ``BUY_SHORT`` at ``P`` as ``SELL @ P`` costing ``1 - P``, so a
    short whose cost is ``c`` must be sent at ``1 - c``. Rounding happens at
    the wire, not here.
    """
    if side == "long":
        return our_cost
    if side == "short":
        return 1.0 - our_cost
    raise ValueError(f"unknown side {side!r}")


def fill_conditional_edge(
    rows: Sequence[dict],
    *,
    entry_key: str = "entry_price",
    outcome_key: str = "won",
    fill_key: str = "would_fill",
) -> dict:
    """Split a would-trade population by whether the quote would have filled.

    This is the measurement that reframed the whole MAPENTRY1 question. On the
    real population the paper edge lived entirely in the orders that NEVER
    filled: the 41% that would have filled ran a negative EV while the 59%
    that never filled ran a positive one, at the SAME posted price. A signal
    that only pays when it fails to trade is not a signal — it is a selection
    effect, and it cost an estimated 6.5 points per contract.

    Returns three Scores: ``edge_would_fill``, ``edge_never_fill`` and their
    difference ``selection_cost`` (positive means the unfillable orders were
    the profitable ones). Every one carries n and n_series, because at the
    sample sizes this repo actually has, the split is directional evidence and
    nothing more.
    """

    def _group(pred) -> tuple[list[float], int]:
        vals, series = [], set()
        for r in rows:
            if not pred(r):
                continue
            price = r.get(entry_key)
            won = r.get(outcome_key)
            if price is None or won is None:
                continue
            vals.append((1.0 if won else 0.0) - float(price))
            series.add(r.get("series_id"))
        return vals, len(series)

    filled, s_filled = _group(lambda r: bool(r.get(fill_key)))
    unfilled, s_unfilled = _group(lambda r: not r.get(fill_key))

    def _score(name: str, vals: list[float], series: int, note: str | None = None) -> Score:
        if not vals:
            return Score(name, None, None, 0, 0, kind="usd", note="no observations")
        return Score(name, sum(vals) / len(vals), None, len(vals), series, kind="usd", note=note)

    a = _score("edge_would_fill", filled, s_filled)
    b = _score("edge_never_fill", unfilled, s_unfilled)
    if a.value is not None and b.value is not None:
        cost = Score(
            "selection_cost",
            b.value - a.value,
            None,
            a.n + b.n,
            max(s_filled, s_unfilled),
            kind="usd",
            note="unfilled-minus-filled spread at the same price",
        )
    else:
        cost = Score("selection_cost", None, None, a.n + b.n, 0, kind="usd")
    return {"edge_would_fill": a, "edge_never_fill": b, "selection_cost": cost}


def edge_per_opportunity(rows: Sequence[dict], *, entry_key: str = "entry_price") -> Score:
    """Mean edge over EVERY opportunity, whether or not it would have filled.

    The honest counterpart to edge-per-fill. A rule that fills 10% of the time
    at +20 points can be worse than one that fills 60% of the time at +4, and
    only this number can tell the two apart.
    """
    vals, series = [], set()
    for r in rows:
        price = r.get(entry_key)
        won = r.get("won")
        if price is None or won is None:
            continue
        vals.append((1.0 if won else 0.0) - float(price))
        series.add(r.get("series_id"))
    if not vals:
        return Score("edge_per_opportunity", None, None, 0, 0, kind="usd")
    return Score(
        "edge_per_opportunity",
        sum(vals) / len(vals),
        None,
        len(vals),
        len(series),
        kind="usd",
    )
