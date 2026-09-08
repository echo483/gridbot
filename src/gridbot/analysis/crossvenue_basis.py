"""Basis between the intl Polymarket CLOB and Polymarket US, sign already settled.

The cross-venue thesis is that intl and PMUS settle the SAME CS2 event while
their participant sets are disjoint by regulation, so no arbitrageur closes the
gap and PMUS quotes go stale after information arrives. This module computes the
quantity that claim is about -- the basis -- and nothing else. It does not decide
whether to trade, does not size, and produces no PnL.

TWO ASYMMETRIES SHAPE EVERY FUNCTION HERE.

1. The channels are not alike. intl arrives as a WebSocket stream: every
   ``price_change`` event carries ``best_bid``/``best_ask`` for its token, so the
   intl series is EVENT-DRIVEN and complete. PMUS is polled by our own recorder,
   so the PMUS series is SAMPLED and, per H-MAPENTRY1-NOTE11, misses executions
   that never move the touch price. The join must therefore be as-of on the PMUS
   clock: for each PMUS observation, what intl believed at that instant. Doing it
   the other way round would invent PMUS observations we never made.

2. The sign is not free. ``intl_price_as_pmus_yes`` (crossvenue_join) restates an
   intl Yes price in the PMUS Yes convention and RAISES on an unresolved
   orientation. Every basis here goes through it. NOTE7 is the reason that is not
   paranoia: 15 of 24 shorts were venue-rejected from exactly this class of error
   while the ledger read "placed".

IRON RAIL 2. H-CROSSVENUE1 is NOT pre-registered, so nothing in this module may
be pointed at holdout data. ``split_by_boundary`` is the enforcement, and it
fails CLOSED: a series whose first-mapped time is unknown is treated as holdout,
never as training. An instrument that silently explores its own holdout is how a
lane dies without ever being falsified.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from gridbot.analysis.crossvenue_join import intl_price_as_pmus_yes

# The pre-registered H-CROSSVENUE1 holdout boundary, stamped before any
# measurement. Series FIRST MAPPED at or after this instant are holdout.
HOLDOUT_BOUNDARY_NS = 1788763167_000000000  # 2026-09-07T06:39:27Z


@dataclass(frozen=True)
class Quote:
    """One venue's touch at one instant, in that venue's own Yes convention."""

    t_ns: int
    bid: float | None
    ask: float | None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class BasisPoint:
    """One PMUS observation with the intl quote that was live at that instant."""

    t_ns: int
    pmus_mid: float
    intl_mid_as_pmus_yes: float
    basis: float              # intl - pmus, in PMUS Yes units
    intl_age_ns: int          # how stale the intl quote was when PMUS was sampled
    orientation: str

    @property
    def basis_ticks(self) -> float:
        """Basis in PMUS ticks. The venue tick is 0.01 (NOTE9 spread figures)."""
        return self.basis / 0.01


class HoldoutViolation(RuntimeError):
    """Raised when an unregistered lane's instrument is pointed at holdout data."""


def split_by_boundary(
    series_ids: list[str],
    first_mapped_ns: dict[str, int],
    boundary_ns: int = HOLDOUT_BOUNDARY_NS,
) -> tuple[list[str], list[str]]:
    """Partition series into (training, holdout) by FIRST-MAPPED time.

    Fails closed: a series with no recorded first-mapped time is holdout. The
    boundary is on when we first SAW the series, not on when its match was
    played -- a series discovered before the boundary stays training even if it
    resolves long after, which is what makes the partition unmanipulable after
    the fact.
    """
    training, holdout = [], []
    for sid in series_ids:
        t = first_mapped_ns.get(sid)
        (training if t is not None and t < boundary_ns else holdout).append(sid)
    return training, holdout


def require_training_only(
    series_ids: list[str],
    first_mapped_ns: dict[str, int],
    boundary_ns: int = HOLDOUT_BOUNDARY_NS,
) -> list[str]:
    """Return the series, or raise if ANY of them is holdout. The gate, not a filter.

    Deliberately not a silent filter: an instrument that quietly drops the
    holdout rows it was handed teaches its caller nothing, and the next caller
    passes the same bad set. The lane is unregistered; the correct response to
    holdout input is to stop.
    """
    training, holdout = split_by_boundary(series_ids, first_mapped_ns, boundary_ns)
    if holdout:
        raise HoldoutViolation(
            f"{len(holdout)} of {len(series_ids)} series are HOLDOUT "
            f"(first mapped at/after the pre-registered boundary): "
            f"{sorted(holdout)[:5]}{'...' if len(holdout) > 5 else ''}. "
            f"H-CROSSVENUE1 is not registered (iron rail 2); training only."
        )
    return training


def as_of(quotes: list[Quote], t_ns: int) -> Quote | None:
    """The most recent quote at or before ``t_ns``. ``quotes`` must be sorted.

    Strictly backward-looking: a quote stamped after ``t_ns`` is information we
    did not have, and letting one through is how a basis series acquires
    lookahead without anyone noticing.
    """
    if not quotes:
        return None
    i = bisect.bisect_right([q.t_ns for q in quotes], t_ns) - 1
    return quotes[i] if i >= 0 else None


def basis_series(
    pmus: list[Quote],
    intl: list[Quote],
    orientation: str,
    max_intl_age_ns: int | None = None,
) -> list[BasisPoint]:
    """As-of join on the PMUS clock, sign settled, one point per PMUS observation.

    ``max_intl_age_ns`` drops points whose intl quote is staler than the bound.
    Left as None here rather than defaulting: what counts as "stale" is a
    pre-registration parameter, not an implementation detail, and picking one
    silently would smuggle a free parameter into a lane that has not filed one.
    """
    out: list[BasisPoint] = []
    for p in pmus:
        pm = p.mid
        if pm is None:
            continue
        q = as_of(intl, p.t_ns)
        if q is None:
            continue
        im = q.mid
        if im is None:
            continue
        age = p.t_ns - q.t_ns
        if max_intl_age_ns is not None and age > max_intl_age_ns:
            continue
        # The flip happens HERE, before the subtraction -- never after.
        intl_as_pmus = intl_price_as_pmus_yes(im, orientation)
        out.append(BasisPoint(
            t_ns=p.t_ns,
            pmus_mid=pm,
            intl_mid_as_pmus_yes=intl_as_pmus,
            basis=intl_as_pmus - pm,
            intl_age_ns=age,
            orientation=orientation,
        ))
    return out


def summarise(points: list[BasisPoint]) -> dict:
    """Distribution of the basis. Descriptive only -- no edge, no PnL, no verdict.

    Reports the MEDIAN ABSOLUTE basis rather than the mean signed basis: the
    thesis is about the size of the gap, and a mean over a symmetric gap is
    approximately zero no matter how tradeable it is.
    """
    if not points:
        return {"n": 0}
    vals = sorted(p.basis for p in points)
    absv = sorted(abs(p.basis) for p in points)
    ages = sorted(p.intl_age_ns for p in points)

    def pct(xs: list[float], q: float) -> float:
        return xs[min(len(xs) - 1, max(0, int(q * len(xs))))]

    return {
        "n": len(points),
        "median_basis": round(pct(vals, 0.5), 5),
        "median_abs_basis": round(pct(absv, 0.5), 5),
        "median_abs_basis_ticks": round(pct(absv, 0.5) / 0.01, 2),
        "p90_abs_basis_ticks": round(pct(absv, 0.9) / 0.01, 2),
        "frac_abs_ge_2_ticks": round(sum(1 for v in absv if v >= 0.02 - 1e-9) / len(absv), 4),
        "median_intl_age_s": round(pct(ages, 0.5) / 1e9, 2),
        "p90_intl_age_s": round(pct(ages, 0.9) / 1e9, 2),
    }
