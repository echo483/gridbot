"""Performance metrics: edge, beta, and the cost of having searched for it.

This is the measurement layer the strategy engine and the scan engine both
report through. It answers four questions, each as a :class:`gridbot.scorecard.Score`
so nothing prints without its ``n`` and its series-clustered interval:

1. **How much did it make, and is that distinguishable from zero?**
   ``pnl_report`` — total, mean per trade, hit rate, and the interval that
   resamples *series*, not trades.
2. **How much of it is just the market moving?** ``beta_alpha`` — the OLS beta of
   the strategy's returns on a reference return series, and the alpha that
   survives it. A strategy that is long the reference and calls the result edge
   has beta 1 and alpha 0; this is the number that says so.
3. **Does the Sharpe survive the search that produced it?**
   ``deflated_sharpe`` — Bailey & López de Prado's deflated Sharpe ratio. A
   breadth-first search over many hypotheses WILL produce a high in-sample
   Sharpe from noise alone; deflation is the correction, and ``n_trials`` is a
   required argument because a Sharpe without it is the exact number that has
   already fooled this project twice.
4. **How many trades would it take to resolve the effect we think we see?**
   ``minimum_detectable_effect`` — the smallest mean edge this sample could
   have distinguished from zero. Small n plus a plausible-looking point estimate
   is indistinguishable from luck, and this makes the required n explicit.

Why ``n_trials`` is mandatory
-----------------------------
The registry holds dozens of hypotheses and their amendments, and the search
that generated them is the real sample. With ``k`` independent trials the
expected maximum Sharpe under a true null is strictly positive and grows like
``sqrt(2 ln k)``; at k=30 it is already ~2.6 in per-observation units over a
typical sample. Reporting a raw Sharpe while ignoring that is not optimism, it
is arithmetic error. So ``deflated_sharpe`` refuses to run without a trial count.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from gridbot.scorecard import Score, clustered_bootstrap, clustered_bootstrap_mean

EULER_GAMMA = 0.5772156649015329


@dataclass
class BetaAlpha:
    """OLS relation of strategy returns to a reference return series."""

    beta: Score
    alpha: Score
    corr: float
    r_squared: float

    def render(self) -> str:
        return (
            f"beta  {_num(self.beta.value)}  CI {_ci(self.beta)}  (n={self.beta.n}, "
            f"series={self.beta.n_series})\n"
            f"alpha {_num(self.alpha.value, '+.5f')}  CI {_ci(self.alpha)}  "
            f"r^2={_num(self.r_squared)}  corr={_num(self.corr)}"
        )


@dataclass
class PnlReport:
    """The trade-level performance of one strategy or scan on one population."""

    name: str
    n_trades: int
    n_series: int
    total_pnl: float
    mean_pnl: Score
    hit_rate: Score
    sharpe: Score
    max_drawdown: float
    avg_stake: float
    metrics: dict[str, float] = field(default_factory=dict)

    def render(self) -> str:
        return (
            f"{self.name}: n={self.n_trades} series={self.n_series} "
            f"pnl={self.total_pnl:+.2f} mean={_num(self.mean_pnl.value, '+.4f')} "
            f"CI {_ci(self.mean_pnl)} hit={_num(self.hit_rate.value, '.3f')} "
            f"sharpe={_num(self.sharpe.value)} CI {_ci(self.sharpe)} "
            f"maxDD={self.max_drawdown:.2f}"
        )


def _ci(score: Score) -> str:
    if score.ci is None:
        return "n/a"
    return f"({score.ci[0]:+.4f}, {score.ci[1]:+.4f})"


def _num(value: float | None, fmt: str = "+.3f") -> str:
    if value is None or not math.isfinite(float(value)):
        return "undefined"
    return format(value, fmt)


def _flatten(by_series: Mapping[str, Sequence[float]]) -> list[float]:
    return [float(x) for values in by_series.values() for x in values]


def cluster_returns(pairs: Sequence[tuple[str, float]]) -> dict[str, list[float]]:
    """Group ``(series_id, value)`` pairs into the mapping the kernel wants.

    The cluster key is the resampling unit, and every metric in this module takes
    its input already grouped: that way there is no code path where a list of
    returns loses its series identity on the way to an interval.
    """
    out: dict[str, list[float]] = {}
    for series_id, value in pairs:
        out.setdefault(str(series_id), []).append(float(value))
    return out


def _score(name: str, value: float, ci, n: int, n_series: int, kind: str = "mean") -> Score:
    """Build a Score, refusing to let a non-finite value read as a real number.

    ``nan`` is the natural return of every undefined statistic here (one
    observation, zero variance, an empty population), and a ``Score`` carrying
    ``nan`` would still report ``defined is True`` — a number that prints as a
    number while meaning nothing. So non-finite becomes ``None`` and the
    interval goes with it, which is the only honest representation.
    """
    finite = value is not None and math.isfinite(float(value))
    return Score(
        name=name,
        value=float(value) if finite else None,
        ci=ci if finite else None,
        n=n,
        n_series=n_series,
        kind=kind,  # type: ignore[arg-type]
    )


def sharpe_ratio(values: Sequence[float], periods_per_year: float | None = None) -> float:
    """Mean/σ of a return series. ``periods_per_year`` annualises, if declared.

    Undefined (``nan``) when fewer than two observations, when the sample has no
    dispersion, **or when every value is identical**. The last case is the
    dangerous one: a population where every trade won has σ ≈ 0 and the ratio
    explodes to a number like 2.6e15, which reads as a spectacular result and is
    actually a statement that the sample carries no information. A Sharpe from
    one trade is not a number, and returning a large one would be the worst
    possible failure mode.
    """
    arr = np.asarray([float(v) for v in values], dtype=float)
    if arr.size < 2:
        return float("nan")
    # min==max catches a fully degenerate sample in O(n); np.unique would sort,
    # and this runs once per bootstrap replicate.
    if float(arr.min()) == float(arr.max()):
        return float("nan")
    sd = float(arr.std(ddof=1))
    if sd == 0.0 or not math.isfinite(sd):
        return float("nan")
    sr = float(arr.mean()) / sd
    if periods_per_year:
        sr *= math.sqrt(periods_per_year)
    return sr


def sharpe_score(
    by_series: Mapping[str, Sequence[float]],
    *,
    name: str = "sharpe",
    periods_per_year: float | None = None,
    reps: int = 2000,
    seed: int = 7,
) -> Score:
    """Sharpe with a series-clustered interval.

    The interval resamples whole series, because trades inside a series share a
    map, a roster and a book. A row-resampled interval on clustered returns is
    the mistake that produced the retracted ``+0.198 skill`` headline.
    """
    flat = _flatten(by_series)
    n_series = sum(1 for v in by_series.values() if list(v))
    if not flat:
        return _score(name, float("nan"), None, 0, 0)
    value = sharpe_ratio(flat, periods_per_year)

    def _stat(sample: list[float]) -> float | None:
        sr = sharpe_ratio(sample, periods_per_year)
        return None if not math.isfinite(sr) else sr

    ci = clustered_bootstrap(by_series, _stat, reps=reps, seed=seed)
    return _score(name, value, ci, len(flat), n_series)


def hit_rate_score(
    by_series: Mapping[str, Sequence[float]], *, name: str = "hit_rate", reps: int = 2000
) -> Score:
    """Share of trades with positive PnL, clustered interval."""
    flat = _flatten(by_series)
    n_series = sum(1 for v in by_series.values() if list(v))
    if not flat:
        return _score(name, float("nan"), None, 0, 0, kind="rate")
    hits = sum(1 for v in flat if v > 0)

    def _stat(sample: list[float]) -> float | None:
        return (sum(1 for v in sample if v > 0) / len(sample)) if sample else None

    ci = clustered_bootstrap(by_series, _stat, reps=reps, seed=11)
    return _score(name, hits / len(flat), ci, len(flat), n_series, kind="rate")


def max_drawdown(pnl_path: Sequence[float]) -> float:
    """Largest peak-to-trough drop of a cumulative PnL path (<= 0)."""
    equity = np.cumsum(np.asarray([float(v) for v in pnl_path], dtype=float))
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float((equity - peak).min())


def pnl_report(
    by_series: Mapping[str, Sequence[float]],
    *,
    name: str = "strategy",
    pnl_path: Sequence[float] | None = None,
    periods_per_year: float | None = None,
    avg_stake: float = 0.0,
    reps: int = 2000,
) -> PnlReport:
    """Full trade-level report for one population of returns, clustered by series."""
    flat = _flatten(by_series)
    n_series = sum(1 for v in by_series.values() if list(v))
    if not flat:
        empty = _score("mean_pnl", float("nan"), None, 0, 0)
        return PnlReport(
            name=name,
            n_trades=0,
            n_series=0,
            total_pnl=0.0,
            mean_pnl=empty,
            hit_rate=empty,
            sharpe=empty,
            max_drawdown=0.0,
            avg_stake=avg_stake,
        )
    mean_ci = clustered_bootstrap_mean(by_series, reps=reps, seed=7)
    return PnlReport(
        name=name,
        n_trades=len(flat),
        n_series=n_series,
        total_pnl=float(sum(flat)),
        mean_pnl=_score("mean_pnl", float(np.mean(flat)), mean_ci, len(flat), n_series),
        hit_rate=hit_rate_score(by_series, reps=reps),
        sharpe=sharpe_score(by_series, periods_per_year=periods_per_year, reps=reps),
        max_drawdown=max_drawdown(pnl_path if pnl_path is not None else flat),
        avg_stake=avg_stake,
        metrics={"sigma": float(np.std(flat, ddof=1)) if len(flat) > 1 else 0.0},
    )


def beta_alpha(
    strategy_by_series: Mapping[str, Sequence[float]],
    reference_by_series: Mapping[str, Sequence[float]],
    *,
    reps: int = 2000,
    seed: int = 13,
) -> BetaAlpha:
    """Beta and alpha of strategy returns against a reference return series.

    Both inputs are mapped by series and each series' lists are the SAME length
    and order — they are the reference returns realised over the identical
    holding windows as the strategy's trades. Anything else is not a regression,
    it is a coincidence.

    The interval is a paired series-clustered bootstrap: whole series are
    resampled once and both legs are recomputed on the same draw, so the
    between-series variance that pairing is meant to cancel is not added back.
    """
    shared = sorted(set(strategy_by_series) & set(reference_by_series))
    pairs = [
        (float(s), float(r))
        for sid in shared
        for s, r in zip(strategy_by_series[sid], reference_by_series[sid])
    ]
    if len(pairs) < 3:
        nan = _score("beta", float("nan"), None, len(pairs), len(shared))
        return BetaAlpha(beta=nan, alpha=nan, corr=float("nan"), r_squared=float("nan"))
    s_arr = np.array([p[0] for p in pairs], dtype=float)
    r_arr = np.array([p[1] for p in pairs], dtype=float)
    beta, alpha, corr = _ols(s_arr, r_arr)

    rng = np.random.default_rng(seed)
    betas: list[float] = []
    alphas: list[float] = []
    for _ in range(reps):
        idx = rng.integers(0, len(shared), size=len(shared))
        ss = np.concatenate([np.asarray(strategy_by_series[shared[i]], dtype=float) for i in idx])
        rr = np.concatenate([np.asarray(reference_by_series[shared[i]], dtype=float) for i in idx])
        if ss.size < 3 or rr.size < 3 or float(rr.std()) == 0.0:
            continue
        b, a, _ = _ols(ss, rr)
        betas.append(b)
        alphas.append(a)

    beta_ci = (float(np.percentile(betas, 2.5)), float(np.percentile(betas, 97.5))) if betas else None
    alpha_ci = (float(np.percentile(alphas, 2.5)), float(np.percentile(alphas, 97.5))) if alphas else None
    n = len(pairs)
    return BetaAlpha(
        beta=_score("beta", beta, beta_ci, n, len(shared)),
        alpha=_score("alpha", alpha, alpha_ci, n, len(shared)),
        corr=corr,
        r_squared=corr * corr,
    )


def _ols(strategy: np.ndarray, reference: np.ndarray) -> tuple[float, float, float]:
    """OLS of ``strategy`` on ``reference`` (with intercept). Returns ``(b, a, r)``."""
    var = float(np.var(reference, ddof=1))
    if var == 0.0:
        return 0.0, float(np.mean(strategy)), 0.0
    beta = float(np.cov(strategy, reference, ddof=1)[0, 1] / var)
    alpha = float(np.mean(strategy) - beta * np.mean(reference))
    corr = float(np.corrcoef(strategy, reference)[0, 1])
    return beta, alpha, corr


def expected_max_sharpe(n_trials: int, variance_of_trial_sr: float) -> float:
    """E[max Sharpe] over ``n_trials`` independent trials under a true null.

    ``sqrt(V) * ((1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e)))`` with γ the Euler–Mascheroni
    constant — the extreme-value limit in Bailey & López de Prado (2014). This
    is the Sharpe a pure-noise search is expected to *hand you*; anything at or
    below it is not evidence.
    """
    if n_trials < 2 or variance_of_trial_sr <= 0:
        return 0.0
    n = float(n_trials)
    z1 = float(stats.norm.ppf(1.0 - 1.0 / n))
    z2 = float(stats.norm.ppf(1.0 - 1.0 / (n * math.e)))
    return math.sqrt(variance_of_trial_sr) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe(
    by_series: Mapping[str, Sequence[float]],
    *,
    n_trials: int,
    variance_of_trial_sr: float,
    skew: float | None = None,
    kurtosis: float | None = None,
    name: str = "deflated_sharpe",
) -> Score:
    """Probability the observed Sharpe is real, given how many trials were run.

    ``n_trials`` counts every configuration the search evaluated — every
    hypothesis, every parameter cell, every amendment that was scored. It is
    required, not optional: the value of this function is precisely that it
    cannot be quietly skipped.

    ``variance_of_trial_sr`` is the cross-sectional variance of the trial
    Sharpes (per-observation units). When the search recorded its candidates,
    pass the real spread; passing 0 disables deflation and the docstring of the
    call site must say so.

    Returns a Score whose ``value`` is the deflated probability in ``[0, 1]``;
    the point estimate is only meaningful alongside the ``expected_max_sharpe``
    stored in ``meta``, so it is attached there rather than left implicit.

    **Known optimism (open, USER to decide):** the formula's ``sqrt(T - 1)``
    assumes ``T`` independent returns. Trades inside a series are correlated,
    so the effective sample is smaller than ``T`` and this probability is too
    high. ``meta["prob_if_series_independent"]`` recomputes it with
    ``T = n_series``, the conservative bound; the truth lies between the two.
    """
    flat = np.asarray(_flatten(by_series), dtype=float)
    n_series = sum(1 for v in by_series.values() if list(v))
    if flat.size < 2:
        return _score(name, float("nan"), None, int(flat.size), n_series)
    sr = sharpe_ratio(flat)
    if not math.isfinite(sr):
        return _score(name, float("nan"), None, int(flat.size), n_series)
    sd = float(flat.std(ddof=1)) if flat.size > 1 else 0.0
    if sd <= 1e-9:
        # Nearly-constant sample: scipy's moment estimators hit catastrophic
        # cancellation and the result is noise. Fall back to the Gaussian
        # defaults and say so in meta rather than reporting a number built
        # from numerical garbage.
        sk, ku = 0.0, 3.0
    else:
        sk = float(stats.skew(flat)) if skew is None else float(skew)
        ku = float(stats.kurtosis(flat, fisher=False)) if kurtosis is None else float(kurtosis)
    sr0 = expected_max_sharpe(n_trials, variance_of_trial_sr)
    denom = math.sqrt(max(1e-12, 1.0 - sk * sr + ((ku - 1.0) / 4.0) * sr * sr))
    z = (sr - sr0) * math.sqrt(flat.size - 1) / denom
    prob = float(stats.norm.cdf(z))
    z_series = (sr - sr0) * math.sqrt(max(n_series - 1, 0)) / denom
    score = _score(name, prob, None, int(flat.size), n_series, kind="fraction")
    score.meta.update(
        {
            "sharpe_per_obs": sr,
            "expected_max_sharpe": sr0,
            "n_trials": n_trials,
            "skew": sk,
            "kurtosis": ku,
            "prob_if_series_independent": float(stats.norm.cdf(z_series)),
        }
    )
    return score


def minimum_detectable_effect(
    sigma: float,
    n: int,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> float:
    """Smallest mean effect distinguishable from zero at ``alpha`` and ``power``.

    Two-sided test. Used to answer "is this n enough?" honestly: if the point
    estimate is smaller than this, the sample could not have resolved it either
    way, and no amount of narrative fixes that.

    ``n`` must be the *effective* sample size. For trades clustered in series
    that is ``n / (1 + (m - 1) * icc)`` (m trades per series, icc the
    within-series correlation), not the trade count; passing raw ``n`` makes
    the effect look more detectable than it is.
    """
    if n <= 0:
        return float("inf")
    z_a = float(stats.norm.ppf(1.0 - alpha / 2.0))
    z_b = float(stats.norm.ppf(power))
    return (z_a + z_b) * sigma / math.sqrt(n)


def required_n(sigma: float, effect: float, *, alpha: float = 0.05, power: float = 0.80) -> int:
    """Trades needed to resolve ``effect`` at the given size and noise."""
    if effect == 0:
        return 0
    z_a = float(stats.norm.ppf(1.0 - alpha / 2.0))
    z_b = float(stats.norm.ppf(power))
    return math.ceil(((z_a + z_b) * sigma / abs(effect)) ** 2)
