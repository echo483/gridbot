"""Does PMUS follow intl? The mechanism claim, made measurable.

H-CROSSVENUE1 asserts that intl and PMUS are segmented, so PMUS quotes go STALE
after information arrives and a maker taking intl's side of that staleness has
positive expectancy. Everything else in the lane is plumbing for that one claim.
This module measures it: for each frozen intl repricing event
(`crossvenue_signal`), what was the basis, did PMUS follow, and how long did it
take.

THE THREE OUTCOMES ARE THE DESIGN'S THREE LABELS (§4), and the middle one is the
reason this is not merely a lead-lag study:

  STALE      intl moved, PMUS had not. This is the target.
  INFORMED   PMUS had ALREADY moved the same way before the intl event fired.
             Someone on the thin venue knew first, and a maker resting there gets
             picked off. This is the -6.5pt fill-selection cost (NOTE6) in its
             observable form, and a study that only measured "does PMUS
             eventually follow" would score these as successes.
  NO_FOLLOW  PMUS never moved inside the horizon. The gap may be real and simply
             never close, which is not tradeable by a maker waiting for it to.

SIGN DISCIPLINE. Direction is evaluated in the PMUS Yes convention, so BOTH
endpoints of the intl move are restated through ``intl_price_as_pmus_yes`` before
a direction is taken. Restating only one endpoint, or taking the direction on the
intl side and then flipping it, silently inverts every INVERTED pair -- which is
NOTE7's failure mode and the design's named highest-risk component (§7).

CAUSALITY AND ITS LIMIT. ``pmus_lag_ns`` looks FORWARD from the event, so it is a
measurement, never a signal: nothing in a live path may consume it. The
backward-looking half (``pmus_led``) is computable at the event and is the part a
classifier could use.

NO EDGE CLAIM. A followed gap is not a filled order. Fills come from
``trade_prints`` (NOTE11/NOTE12), and nothing here models one.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridbot.analysis.crossvenue_basis import Quote, as_of
from gridbot.analysis.crossvenue_join import intl_price_as_pmus_yes
from gridbot.analysis.crossvenue_signal import TICK, Move

DEFAULT_HORIZON_NS = 300_000_000_000   # 5 min; a stale quote that outlives this is not "lagging"
DEFAULT_MIN_PMUS_TICKS = 1.0           # one venue tick is the smallest observable follow

STALE = "stale"
INFORMED = "informed"
NO_FOLLOW = "no_follow"


@dataclass(frozen=True)
class Response:
    """One intl event, and what the PMUS book did around it."""

    t_event_ns: int
    intl_ticks_pmus_yes: float      # signed intl move, restated in PMUS Yes units
    pmus_mid_at_event: float
    basis_at_event: float           # intl (as PMUS Yes) - PMUS, at the event
    pmus_lag_ns: int | None         # forward-looking: time until PMUS followed
    pmus_led: bool                  # backward-looking: PMUS had already moved
    label: str

    @property
    def basis_ticks(self) -> float:
        return self.basis_at_event / TICK

    @property
    def pmus_lag_s(self) -> float | None:
        return None if self.pmus_lag_ns is None else self.pmus_lag_ns / 1e9


def _mid_at(quotes: list[Quote], t_ns: int) -> float | None:
    q = as_of(quotes, t_ns)
    return None if q is None else q.mid


def responses(
    moves: list[Move],
    pmus: list[Quote],
    orientation: str,
    *,
    horizon_ns: int = DEFAULT_HORIZON_NS,
    min_pmus_ticks: float = DEFAULT_MIN_PMUS_TICKS,
) -> list[Response]:
    """Label each intl event by what PMUS did before and after it.

    Events with no PMUS quote at the instant are DROPPED, not labelled: a book we
    never observed is not a book that failed to follow, and scoring it as
    ``no_follow`` would turn our own capture blindness into evidence for the
    thesis.
    """
    out: list[Response] = []
    thresh = min_pmus_ticks * TICK - 1e-12
    for mv in moves:
        pm = _mid_at(pmus, mv.t_ns)
        if pm is None:
            continue
        # BOTH endpoints restated before any direction is taken (NOTE7).
        to_pmus = intl_price_as_pmus_yes(mv.to_mid, orientation)
        from_pmus = intl_price_as_pmus_yes(mv.from_mid, orientation)
        delta = to_pmus - from_pmus
        if delta == 0:
            continue
        sign = 1.0 if delta > 0 else -1.0

        # Backward: had PMUS already moved this way across the intl move's window?
        pm_ref = _mid_at(pmus, mv.ref_t_ns)
        led = pm_ref is not None and (pm - pm_ref) * sign >= thresh

        # Forward: when does PMUS first move this way by min_pmus_ticks?
        lag = None
        for q in pmus:
            if q.t_ns <= mv.t_ns:
                continue
            if q.t_ns > mv.t_ns + horizon_ns:
                break
            m = q.mid
            if m is not None and (m - pm) * sign >= thresh:
                lag = q.t_ns - mv.t_ns
                break

        label = INFORMED if led else (STALE if lag is not None else NO_FOLLOW)
        out.append(Response(
            t_event_ns=mv.t_ns,
            intl_ticks_pmus_yes=round(delta / TICK, 4),
            pmus_mid_at_event=pm,
            basis_at_event=to_pmus - pm,
            pmus_lag_ns=lag,
            pmus_led=led,
            label=label,
        ))
    return out


def summarise_responses(rs: list[Response]) -> dict:
    """Label mix, basis at event, and follow latency. Descriptive only."""
    if not rs:
        return {"n": 0}
    labels = {k: sum(1 for r in rs if r.label == k) for k in (STALE, INFORMED, NO_FOLLOW)}
    absb = sorted(abs(r.basis_ticks) for r in rs)
    lags = sorted(r.pmus_lag_s for r in rs if r.pmus_lag_s is not None)

    def pct(xs: list[float], q: float) -> float | None:
        return xs[min(len(xs) - 1, max(0, int(q * len(xs))))] if xs else None

    stale = [r for r in rs if r.label == STALE]
    stale_b = sorted(abs(r.basis_ticks) for r in stale)
    return {
        "n": len(rs),
        **{f"n_{k}": v for k, v in labels.items()},
        "frac_informed": round(labels[INFORMED] / len(rs), 4),
        "median_abs_basis_ticks": round(pct(absb, 0.5) or 0.0, 2),
        "median_abs_basis_ticks_STALE": (round(pct(stale_b, 0.5), 2) if stale_b else None),
        "frac_stale_basis_ge_2t": (
            round(sum(1 for b in stale_b if b >= 2.0) / len(stale_b), 4) if stale_b else None
        ),
        "median_follow_lag_s": (round(pct(lags, 0.5), 1) if lags else None),
        "p90_follow_lag_s": (round(pct(lags, 0.9), 1) if lags else None),
    }


# --- what a maker would have to rest, to take intl's side --------------------


def maker_quote(basis: float, bid: float | None, ask: float | None,
                improve_ticks: float = 0.0) -> tuple[str, float] | None:
    """``(side, our-side cost)`` for a resting order taking intl's side of the gap.

    The basis is ``intl (as PMUS Yes) - PMUS``, so a POSITIVE basis says intl
    prices the Yes higher than PMUS does -- the Yes is cheap here and we want to
    BUY it, resting at the PMUS bid. A NEGATIVE basis says the Yes is expensive,
    and on this venue selling Yes is buying No: ``BUY_SHORT`` at an our-side cost
    of ``1 - ask`` (H-MAPENTRY1-NOTE7 -- the venue price is ALWAYS the Yes price,
    and a short at P costs 1-P. Sending the Yes price verbatim for a short is what
    got 15 of 24 shorts venue-rejected while the ledger read "placed").

    ``improve_ticks`` posts inside the touch by that many ticks, which raises our
    cost and our fill probability together.

    Returns ``None`` when the side we would need is missing, or when the resulting
    cost leaves ``(0, 1)`` -- never a clamped price.
    """
    if basis > 0:
        if bid is None:
            return None
        side, cost = "long", bid + improve_ticks * TICK
    elif basis < 0:
        if ask is None:
            return None
        side, cost = "short", (1.0 - ask) + improve_ticks * TICK
    else:
        return None
    cost = round(cost, 6)
    return None if not (0.0 < cost < 1.0) else (side, cost)

