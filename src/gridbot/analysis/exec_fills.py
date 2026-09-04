"""Maker fill bracket for offline replay of a MAKER strategy on Polymarket books.

We replay a MAKER strategy on US-map order books offline. A resting BUY at posted
price ``P`` (our-side cost) fills only if the market comes to it. We have NO trade
prints, only sparse book observations inside the order's 150s lifetime -- so we
bracket the truth with a LOWER bound (strict adverse touch, persistence, queue behind
the displayed size) and an UPPER bound (any touch, front of queue). This module is
those two rules and nothing else: no file I/O, no data loading, no gridbot imports.

All prices are OUR-SIDE cost, already converted by the caller: for a LONG the price is
the book ask, for a SHORT it is ``1 - bid`` -- the conversion is NOT this module's job.
A short-side caller passes ``1 - bid`` so that both sides are handled identically here.

The observation model: an ``Obs`` is a tuple-like record ``(t_ns, opp_ask, opp_size)``
where ``opp_ask`` is the price at which the market would SELL to us right now on our
side and ``opp_size`` is the displayed size at that level (None when the venue gave
none). Observations are sorted by ``t_ns``; the order is posted at ``t0_ns`` and
expires at ``t0_ns + ttl_ns``. Observations outside ``(t0_ns, t0_ns + ttl_ns]`` are
ignored. A None ``opp_ask`` is a dead/one-sided book: never a touch.

Outcomes (``Fill.reason``): ``no_touch`` -- in-window obs exist but none touch;
``no_persist`` -- persist needs a next in-window obs and none satisfied it;
``no_size`` -- behind_displayed cannot size the fill; ``queue_ahead`` -- the entire
displayed queue is ahead of us; ``filled`` -- credited; ``expired`` -- empty window
(unobserved orders are NOT fills under either bound).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Obs:
    """A sparse book observation on our side, already converted to our-side cost."""

    t_ns: int
    opp_ask: float | None
    opp_size: float | None


@dataclass(frozen=True)
class FillRule:
    """Touch / persistence / queue policy defining one edge of the fill bracket."""

    touch: str  # "strict" -> opp_ask < P ; "at" -> opp_ask <= P
    persist: bool  # True -> the NEXT observation in the window must also touch
    queue: str  # "behind_displayed" -> sized out from behind the queue ; "front"


LOWER = FillRule(touch="strict", persist=True, queue="behind_displayed")
UPPER = FillRule(touch="at", persist=False, queue="front")
# Review finding #1/#8 (deepseek-pro, 2026-09-04): the BBO tape before the size
# extension carries NO sizes, so a size-dependent lower bound credits nothing there.
# ``unsized`` keeps the strict touch + persistence and credits exactly ONE contract
# when the confirming observation has no size (a sized observation behaves like
# ``behind_displayed`` with the queue at the first observation).
LOWER_UNSIZED = FillRule(touch="strict", persist=True, queue="unsized")


@dataclass(frozen=True)
class Fill:
    """One order's bracket outcome under a single rule."""

    filled: bool
    t_fill_ns: int | None  # the observation that credits the fill (the confirming one)
    qty: float  # contracts credited (0.0 when not filled)
    touch_idx: int | None  # index of the FIRST touching observation in the window
    reason: str  # no_touch | no_persist | no_size | queue_ahead | filled | expired


def _touches(rule: FillRule, P: float, opp_ask: float | None) -> bool:
    """Return whether ``opp_ask`` satisfies ``rule.touch`` at price ``P``."""
    if opp_ask is None:
        return False
    if rule.touch == "strict":
        return opp_ask < P
    return opp_ask <= P


def simulate(
    rule: FillRule,
    P: float,
    qty: float,
    t0_ns: int,
    obs: list[Obs],
    ttl_ns: int = 150_000_000_000,
    queue_ahead: float | None = None,
) -> Fill:
    """Simulate one order under ``rule`` over the observations in its lifetime window."""
    end_ns = t0_ns + ttl_ns
    # Open at t0 (excluded), closed at t0+ttl (included).
    window = [o for o in obs if o.t_ns > t0_ns and o.t_ns <= end_ns]

    if not window:
        return Fill(False, None, 0.0, None, "expired")

    first = next((i for i, o in enumerate(window) if _touches(rule, P, o.opp_ask)), None)
    if first is None:
        return Fill(False, None, 0.0, None, "no_touch")

    if rule.persist:
        conf = first + 1
        if conf >= len(window):
            return Fill(False, None, 0.0, first, "no_persist")
        if not _touches(rule, P, window[conf].opp_ask):
            return Fill(False, None, 0.0, first, "no_persist")
    else:
        conf = first

    conf_obs = window[conf]

    if rule.queue in ("behind_displayed", "unsized"):
        eff_queue = queue_ahead if queue_ahead is not None else window[0].opp_size
        conf_size = conf_obs.opp_size
        if rule.queue == "unsized" and (eff_queue is None or conf_size is None):
            fill_qty = min(qty, 1.0)  # size unmeasured: one contract, never more
            return Fill(True, conf_obs.t_ns, float(fill_qty), first, "filled")
        if eff_queue is None:
            return Fill(False, None, 0.0, first, "no_size")
        if conf_size is None:
            return Fill(False, None, 0.0, first, "no_size")
        if conf_size < eff_queue + 1.0:
            return Fill(False, None, 0.0, first, "queue_ahead")
        fill_qty = min(qty, conf_size - eff_queue)
    else:  # front of queue: sizeless obs credits the full order (the upper bound)
        fill_qty = min(qty, conf_obs.opp_size) if conf_obs.opp_size is not None else qty

    return Fill(True, conf_obs.t_ns, float(fill_qty), first, "filled")


def bracket(
    P: float,
    qty: float,
    t0_ns: int,
    obs: list[Obs],
    ttl_ns: int = 150_000_000_000,
    queue_ahead: float | None = None,
) -> tuple[Fill, Fill]:
    """Return ``(lower, upper)`` fill bounds for one order under the two default rules."""
    lower = simulate(LOWER, P, qty, t0_ns, obs, ttl_ns, queue_ahead)
    upper = simulate(UPPER, P, qty, t0_ns, obs, ttl_ns, queue_ahead)
    return lower, upper


def assert_bracket(lower: Fill, upper: Fill) -> None:
    """Raise if the bracket invariant is broken: a lower fill must be an upper fill too,
    and the lower bound may never credit more contracts than the upper bound."""
    if lower.filled and not upper.filled:
        raise AssertionError("lower bound filled but upper bound did not")
    if lower.qty > upper.qty:
        raise AssertionError(
            f"lower qty {lower.qty} > upper qty {upper.qty}"
        )