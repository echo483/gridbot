"""Risk: sizing, growth, and ruin — the math behind putting money down.

The live system's risk story is currently thin in a specific way: caps are
``stake x max_open`` because settles are not auto-written, so the daily-loss
cap cannot bind on realized PnL. That bounds the damage but it is not risk
management. These functions are the pieces a real one is assembled from:

* :func:`kelly_fraction` — how much to stake when you believe ``p`` and pay
  ``price``.
* :func:`flat_stake_stats` / :func:`log_growth_stats` — per-trade drift and
  variance, for a fixed stake or a bankroll fraction.
* :func:`barrier_up_probability` / :func:`expected_trades_to_barrier` —
  closed-form "double before halving" and how long it takes.
* :func:`mc_bankroll` — seeded Monte Carlo that draws the TRUE win rate from a
  Beta posterior, so a 13-trade sample honestly produces a fat, unhelpful
  outcome distribution instead of a confident fiction.

That last point is the whole reason to keep this honest: with 13 settled
trades, the Beta posterior over the true win rate is wide, and the simulation
is supposed to say so.
"""

from __future__ import annotations

import math
import random

#: Verified account balance at first fire, 2026-08-25 — the reference bankroll.
BANKROLL0 = 26.0
DEFAULT_SEED = 13


def kelly_fraction(p: float, price: float) -> float:
    """Optimal bankroll fraction for a binary contract, floored at 0.

    Net odds are ``b = (1 - price) / price`` and the classic solution is
    ``f* = (p*b - (1-p)) / b``. That simplifies algebraically to
    ``(p - price) / (1 - price)`` — i.e. edge over maximum loss — which is the
    form used here. Both forms appear in the repo; a test pins that they agree
    to floating-point tolerance, because they were derived twice and never
    checked against each other.
    """
    if not 0.0 < price < 1.0:
        return 0.0
    return max(0.0, (p - price) / (1.0 - price))


def kelly_fraction_b(p: float, price: float) -> float:
    """The ``b``-odds form of :func:`kelly_fraction`, kept for equivalence tests."""
    if not 0.0 < price < 1.0:
        return 0.0
    b = (1.0 - price) / price
    return max(0.0, (p * b - (1.0 - p)) / b)


def fractional_kelly(p: float, price: float, divisor: float = 4.0) -> float:
    """Kelly divided by ``divisor`` — quarter-Kelly is the house default.

    Full Kelly assumes the probability is KNOWN. Ours is estimated from a
    sample whose interval is wide, so the size is divided down until the
    estimate is trustworthy. Quarter-Kelly is the current cap rationale at the
    $26 bankroll (``$3/order`` on the strongest calibrated cell).
    """
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    return kelly_fraction(p, price) / divisor


def flat_stake_stats(p: float, price: float, stake: float) -> tuple[float, float]:
    """``(mean, std)`` of per-trade dollar PnL for a flat ``stake`` at ``price``."""
    b = (1.0 - price) / price
    win, loss = stake * b, -stake
    mu = p * win + (1 - p) * loss
    sigma = math.sqrt(p * (1 - p)) * (win - loss)
    return mu, sigma


def log_growth_stats(p: float, price: float, f: float) -> tuple[float, float]:
    """``(mean, std)`` of per-trade log bankroll growth staking fraction ``f``.

    Log growth is the quantity Kelly maximizes, so this is the right space to
    compute a growth-rate interval in — dollar PnL is not.

    ``f`` must be strictly inside (0, 1). At ``f = 1`` the losing branch is
    ``log1p(-1) = log(0)``, i.e. ruin, and ``math.log1p`` raises a domain
    error rather than returning ``-inf``. Ruin is a real outcome but not a
    useful growth *rate*, so it is rejected explicitly instead of surfacing as
    an opaque ValueError from the math library.
    """
    if not 0.0 < f < 1.0:
        raise ValueError(f"f={f} must lie in (0, 1); staking the whole bankroll is ruin")
    b = (1.0 - price) / price
    win, loss = math.log1p(f * b), math.log1p(-f)
    mu = p * win + (1 - p) * loss
    sigma = math.sqrt(p * (1 - p)) * (win - loss)
    return mu, sigma


def barrier_up_probability(mu: float, sigma: float, up: float, down: float) -> float:
    """``P(drifted Brownian motion hits +up before -down)``.

    With ``X_t = mu*t + sigma*W_t`` and ``theta = 2*mu/sigma^2``, the process
    ``exp(-theta*X)`` is a martingale, giving

        P_up = (e^{theta*down} - 1) / (e^{theta*down} - e^{-theta*up})

    and the driftless limit ``down / (up + down)``. Exponentials are clamped
    so a strong drift cannot overflow to ``inf/inf``.
    """
    if sigma <= 0:
        return 1.0 if mu > 0 else 0.0
    theta = 2 * mu / (sigma * sigma)
    if abs(theta) < 1e-12:
        return down / (up + down)
    e_down = math.exp(min(theta * down, 700))
    e_up = math.exp(min(-theta * up, 700))
    return (e_down - 1) / (e_down - e_up)


def expected_trades_to_barrier(
    mu: float, sigma: float, up: float, down: float
) -> float | None:
    """Wald identity for expected hitting time; driftless limit ``up*down/sigma^2``."""
    if sigma <= 0 and mu == 0:
        return None
    if abs(mu) < 1e-12:
        return up * down / (sigma * sigma)
    p_up = barrier_up_probability(mu, sigma, up, down)
    return (p_up * up - (1 - p_up) * down) / mu


def mc_bankroll(
    wins: int,
    losses: int,
    price: float,
    *,
    stake: float | None = None,
    fraction: float | None = None,
    n_trades: int = 500,
    n_paths: int = 4000,
    bankroll0: float = BANKROLL0,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Seeded bankroll simulation with explicit parameter uncertainty.

    Each path first draws the TRUE win rate from ``Beta(wins+1, losses+1)`` —
    a uniform prior with one pseudo-count. At 13 trades that posterior is
    almost flat, so the outcome distribution comes out wide, which is the
    honest answer. A simulation that plugged in the point estimate instead
    would report a confident result the data cannot support.

    Exactly one of ``stake`` (flat dollars) or ``fraction`` (of bankroll) must
    be given. A path stops early when it can no longer cover its stake, and
    records whether it touched the 2x or 0.5x barrier first.
    """
    if (stake is None) == (fraction is None):
        raise ValueError("exactly one of stake/fraction")
    if not 0.0 < price < 1.0:
        raise ValueError("price must lie in (0, 1)")
    frac_of_bank: float = float(fraction) if fraction is not None else 0.0
    rng = random.Random(seed)
    b = (1.0 - price) / price
    terminals: list[float] = []
    max_dds: list[float] = []
    doubled = halved = profitable = 0
    for _ in range(n_paths):
        p_true = rng.betavariate(wins + 1, losses + 1)
        bank, peak, dd, hit = bankroll0, bankroll0, 0.0, None
        for _ in range(n_trades):
            s = stake if stake is not None else frac_of_bank * bank
            if bank < s or s <= 0:
                break
            bank += s * b if rng.random() < p_true else -s
            peak = max(peak, bank)
            dd = max(dd, (peak - bank) / peak if peak > 0 else 0.0)
            if hit is None:
                if bank >= 2 * bankroll0:
                    hit = "double"
                elif bank <= 0.5 * bankroll0:
                    hit = "halve"
        terminals.append(bank)
        max_dds.append(dd)
        doubled += hit == "double"
        halved += hit == "halve"
        profitable += bank > bankroll0
    terminals.sort()

    def q(frac: float) -> float:
        return terminals[min(len(terminals) - 1, int(frac * len(terminals)))]

    return {
        "n_paths": n_paths,
        "n_trades": n_trades,
        "terminal_p5": round(q(0.05), 2),
        "terminal_median": round(q(0.5), 2),
        "terminal_p95": round(q(0.95), 2),
        "p_profitable": round(profitable / n_paths, 3),
        "p_double_first": round(doubled / n_paths, 3),
        "p_halve_first": round(halved / n_paths, 3),
        "mean_max_drawdown": round(sum(max_dds) / n_paths, 3),
    }
