"""Pure fractional-Kelly sizing: (q, price, params) -> stake. No side effects.

Per H-SIZING0: q_eff = min(q, wilson_lower - margin); skip unless edge and
price gates pass; stake = bankroll * kelly/edge capped at max_stake_frac.
Every rejected entry returns a reason string so shadow logs explain WHY a
stake is zero — that's the audit trail for parameter tuning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from gridbot.sizing.params import SizingParams


@dataclass(frozen=True)
class SizingDecision:
    stake: float                 # 0.0 when skipped
    q_eff: float
    edge: float
    reason: str                  # "ok" or the first failed gate
    params: dict                 # exact reproducible parameter snapshot


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score lower bound for a binomial rate; 0.0 if no data."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - h) / denom)


def size_entry(
    q: float,
    price: float,
    params: SizingParams,
    *,
    state_wins: int | None = None,
    state_n: int | None = None,
) -> SizingDecision:
    """Compute the stake for one binary-market entry. Pure function."""
    snap = params.as_log_dict()
    if not (0.0 < q < 1.0):
        return SizingDecision(0.0, q, 0.0, f"invalid_q:{q}", snap)
    if not (0.01 <= price <= 0.99):
        return SizingDecision(0.0, q, 0.0, f"invalid_price:{price}", snap)

    q_eff = q
    if state_wins is not None and state_n:
        ci_lo = wilson_lower(state_wins, state_n, params.wilson_z)
        q_eff = min(q, ci_lo - params.wilson_margin)

    edge = q_eff - price
    if edge < params.min_edge:
        return SizingDecision(0.0, q_eff, edge, f"edge_below_min:{edge:.4f}", snap)
    if price > params.max_price:
        return SizingDecision(0.0, q_eff, edge, f"price_above_max:{price}", snap)
    if q_eff <= price:
        return SizingDecision(0.0, q_eff, edge, "q_eff_not_above_price", snap)

    # binary-market Kelly: f* = edge / (1 - price); fractional + cap
    kelly = edge / (1.0 - price)
    frac = min(kelly / params.kelly_divisor, params.max_stake_frac)
    stake = round(params.bankroll * frac, 2)
    return SizingDecision(stake, q_eff, edge, "ok", snap)
