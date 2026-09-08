"""Intl repricing events — the trigger half of the CROSSVENUE1 classifier.

The design (`docs/superpowers/specs/2026-09-07-crossvenue1-design.md` §4) labels
each PMUS quote SQUARE/STALE, INFORMED or NOISE, and one of the classifier's
inputs is "intl move size and direction since the GRID event". This module
computes that input, and only that input.

WHY IT IS SEPARATE FROM THE BASIS, AND WHY THAT MATTERS. The lane's clean
unconditional basis measured 1.5 ticks against a proposed 2-tick cost bar, so
CROSSVENUE1 now lives or dies on whether a signal selects the tail. A signal
tuned while watching the basis it selects would be exactly the p-hacking
Quantopian lecture 22 is about: with enough thresholds, some cut always clears 2
ticks. So the trigger is defined and characterised HERE, on the intl series
alone, with no PMUS book in scope and no basis computable from anything this
module can see. The parameters get frozen into the pre-registration BEFORE the
conditional measurement is possible -- which is only honest because the coverage
gate means it is not yet possible anyway.

CAUSAL BY CONSTRUCTION. A move at observation i is measured against the intl
quote as of ``t_i - window``, never against a later one, so the event is
computable at the instant it fires. ``as_of`` semantics are the same
backward-looking rule ``crossvenue_basis`` uses.

FAILS CLOSED AT THE START OF A MARKET, and this costs something. The predicate is
"moved at least ``min_ticks`` over the last ``window``". Until a quote at least
one window old exists, that predicate cannot be evaluated, so nothing fires --
including a genuinely enormous move two seconds after the book opens. The
alternative (measure over whatever history exists) would let early observations
fire on smaller real moves and would make the event rate depend on where a
market's tape happens to start. The cost is real: the first ``window`` of every
market is unsignalled, which for a pre-match book is exactly the listing moment.
It is accepted because a signal whose meaning drifts with tape length cannot be
pre-registered.

NO EDGE CLAIM. A repricing event is not a trade and not a prediction. This
module answers "how often, and how large", nothing more.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

TICK = 0.01

# Defaults are DELIBERATELY round numbers, not fitted ones: 2 ticks because that
# is the proposed clause (ii) cost floor, 30s because it is the order of the GRID
# round-end lead (13-27s measured), and a 60s cooldown so one repricing is one
# event rather than one per tick of the same move.
DEFAULT_MIN_TICKS = 2.0
DEFAULT_WINDOW_NS = 30_000_000_000
DEFAULT_COOLDOWN_NS = 60_000_000_000


@dataclass(frozen=True)
class Move:
    """One intl repricing event, as it could have been seen when it fired."""

    t_ns: int             # the observation that crossed the threshold
    ref_t_ns: int         # the reference quote it was measured against
    from_mid: float
    to_mid: float

    @property
    def ticks(self) -> float:
        """Signed size in venue ticks. Sign is in the intl Yes convention."""
        return (self.to_mid - self.from_mid) / TICK

    @property
    def up(self) -> bool:
        return self.to_mid > self.from_mid


def _mids(quotes) -> tuple[list[int], list[float]]:
    ts, ms = [], []
    for q in quotes:
        m = q.mid
        if m is None:
            continue
        ts.append(q.t_ns)
        ms.append(m)
    return ts, ms


def intl_moves(
    quotes,
    *,
    min_ticks: float = DEFAULT_MIN_TICKS,
    window_ns: int = DEFAULT_WINDOW_NS,
    cooldown_ns: int = DEFAULT_COOLDOWN_NS,
) -> list[Move]:
    """Repricing events in a time-ordered intl quote series.

    ``quotes`` are ``crossvenue_basis.Quote``. A move fires at observation *i*
    when ``|mid_i - mid_ref| >= min_ticks`` ticks, where *ref* is the last quote
    at or before ``t_i - window_ns``. After firing, further observations are
    suppressed for ``cooldown_ns`` so that a single repricing is a single event
    rather than one event per tick of the same move.

    A series with no two-sided quote yields no events rather than an error: a
    book we cannot price is not a book that repriced.
    """
    ts, ms = _mids(quotes)
    if len(ts) < 2:
        return []
    out: list[Move] = []
    thresh = min_ticks * TICK - 1e-12
    suppressed_until = None
    for i in range(len(ts)):
        t = ts[i]
        if suppressed_until is not None and t < suppressed_until:
            continue
        j = bisect.bisect_right(ts, t - window_ns) - 1
        if j < 0:
            continue
        if abs(ms[i] - ms[j]) >= thresh:
            out.append(Move(t_ns=t, ref_t_ns=ts[j], from_mid=ms[j], to_mid=ms[i]))
            suppressed_until = t + cooldown_ns
    return out


def summarise_moves(moves: list[Move], span_s: float) -> dict:
    """Rate and size distribution. Descriptive; no edge, no direction claim."""
    if not moves or span_s <= 0:
        return {"n": len(moves), "per_hour": 0.0}
    sizes = sorted(abs(m.ticks) for m in moves)

    def pct(xs: list[float], q: float) -> float:
        return xs[min(len(xs) - 1, max(0, int(q * len(xs))))]

    return {
        "n": len(moves),
        "per_hour": round(len(moves) / (span_s / 3600.0), 2),
        "median_ticks": round(pct(sizes, 0.5), 2),
        "p90_ticks": round(pct(sizes, 0.9), 2),
        "max_ticks": round(sizes[-1], 2),
        "frac_up": round(sum(1 for m in moves if m.up) / len(moves), 3),
    }


# --- persistence: is a move information, or noise? ---------------------------


@dataclass(frozen=True)
class Persistence:
    """How much of one intl move had unwound by each horizon.

    ``retrace`` maps horizon seconds -> fraction of the move given back, signed
    so that direction is already handled:

        0.0   the move fully held
        1.0   the mid returned exactly to where the move started
        < 0   the move continued further in its own direction
        > 1   the mid overshot back PAST the origin

    ``None`` for a horizon means the tape ended before it; a missing observation
    is never read as "held".
    """

    t_event_ns: int
    move_ticks: float
    retrace: dict[int, float | None]


def move_persistence(
    moves: list[Move],
    quotes,
    horizons_s: tuple[int, ...] = (30, 60, 150, 300, 900),
) -> list[Persistence]:
    """Follow each move forward and measure how much of it unwound.

    This is the test that separates the two readings of a PMUS non-response
    (``crossvenue_response.NO_FOLLOW``): if intl moves typically HOLD, an
    unfollowed move is a real uncrossed gap and PMUS was genuinely stale; if they
    typically REVERT, intl overshot and there was never a gap to take. It needs
    no PMUS book at all, so it runs on the whole intl population rather than on
    the handful of markets where both venues were captured.
    """
    ts, ms = _mids(quotes)
    if not ts:
        return []
    out: list[Persistence] = []
    for mv in moves:
        delta = mv.to_mid - mv.from_mid
        if delta == 0:
            continue
        retrace: dict[int, float | None] = {}
        for h in horizons_s:
            t = mv.t_ns + h * 1_000_000_000
            if t > ts[-1]:
                retrace[h] = None          # tape ended; never scored as "held"
                continue
            i = bisect.bisect_right(ts, t) - 1
            retrace[h] = round((mv.to_mid - ms[i]) / delta, 4) if i >= 0 else None
        out.append(Persistence(t_event_ns=mv.t_ns,
                               move_ticks=round(mv.ticks, 4),
                               retrace=retrace))
    return out


def summarise_persistence(ps: list[Persistence],
                          horizons_s: tuple[int, ...] = (30, 60, 150, 300, 900)) -> dict:
    """Median retrace and the reverted share, per horizon. Descriptive only."""
    out: dict = {"n": len(ps)}
    for h in horizons_s:
        vals = sorted(p.retrace[h] for p in ps if p.retrace.get(h) is not None)
        if not vals:
            out[f"h{h}s"] = {"n": 0}
            continue
        out[f"h{h}s"] = {
            "n": len(vals),
            "median_retrace": round(vals[len(vals) // 2], 3),
            # "mostly reverted" = at least half the move given back
            "frac_reverted_half": round(sum(1 for v in vals if v >= 0.5) / len(vals), 3),
            "frac_held": round(sum(1 for v in vals if v <= 0.0) / len(vals), 3),
        }
    return out

