"""Confidence intervals: one canonical implementation of each.

Two families live here, and the distinction is load-bearing:

* :func:`wilson_interval` — an analytic interval for a binomial rate. Use it
  when the observations are genuinely independent draws.
* :func:`clustered_bootstrap` — a resampling interval where the resampling
  unit is the SERIES, not the row. This is the default for anything measured
  on the capture corpus, because rows inside a series are correlated.

Before this module the repo carried at least six hand-rolled copies of the
clustered bootstrap (``mapentry_verdict``, ``verdict``, ``mapentry2_score``,
``roundwin2_poolb``, ``crossin_backtest``, ``queue16_phase2_outcomes``,
``queue16_bandskip_crossin``, ``timing_chain``, ``postshock_fade``,
``pcal_fit``) that disagreed on the percentile index convention and on whether
the drop-out of undefined reps rescaled the index or not. Two of them indexed
against the requested rep count and the rest against the surviving count, so
for identical inputs they returned different intervals. Nothing in the tree
could detect that.

Deliberately stdlib-only: no numpy. The kernel stays trivially embeddable and
its outputs stay bit-reproducible, which matters when a Rust port has to
reproduce them exactly.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

#: Default 95% confidence, matching every frozen bar in the registry.
DEFAULT_ALPHA = 0.05
DEFAULT_REPS = 2000
#: The corpus-wide seed. Fixed so every interval in the repo is reproducible.
DEFAULT_SEED = 13

PercentileMethod = Literal["linear", "nearest_rank", "lower"]


def z_for(alpha: float = DEFAULT_ALPHA) -> float:
    """Two-sided z critical value. Only the commonly used alphas are pinned.

    Interpolating a table beyond these would silently misprice an interval,
    so an unlisted alpha raises instead.
    """
    table = {0.10: 1.6449, 0.05: 1.9600, 0.02: 2.3263, 0.01: 2.5758}
    if alpha not in table:
        raise ValueError(f"alpha {alpha} not tabulated; use one of {sorted(table)}")
    return table[alpha]


# --------------------------------------------------------------------- Wilson


def wilson_interval(k: int, n: int, alpha: float = DEFAULT_ALPHA) -> tuple[float, float]:
    """95% Wilson score interval for ``k`` successes in ``n`` Bernoulli trials.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves sanely at small ``n`` and at rates near 0 or 1 — which is exactly
    where a prediction-market book lives.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if k < 0 or k > n:
        raise ValueError(f"k={k} outside [0, {n}]")
    if n == 0:
        return 0.0, 0.0
    z = z_for(alpha)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


def wilson_lower(k: int, n: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Lower Wilson bound, floored at 0 — the conservative sizing input.

    H-SIZING0 shrinks a model's q toward this bound so a stake is never sized
    off a point estimate that a small sample cannot support.
    """
    if n <= 0:
        return 0.0
    return max(0.0, wilson_interval(k, n, alpha)[0])


# ------------------------------------------------------------ percentiles


def percentile(values: Sequence[float], q: float, method: PercentileMethod = "linear") -> float:
    """``q``-quantile of an already-sorted-or-not sequence.

    ``method`` exists because the legacy implementations disagreed:

    * ``"linear"`` (canonical default) — interpolates between order statistics,
      the convention numpy, R type-7, and every stats text use. A quantile
      between two samples is reported as a value between them.
    * ``"nearest_rank"`` — ``sorted[int(q * len)]``, what most of the legacy
      bootstrap copies did. Fine for large ``len``; drifts at the edges.
    * ``"lower"`` — ``sorted[floor(q * (len - 1))]``, an order statistic with
      no interpolation.
    """
    if not values:
        raise ValueError("percentile of an empty sequence is undefined")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q={q} must lie in [0, 1]")
    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return xs[0]
    if method == "nearest_rank":
        idx = int(q * n)
        return xs[min(idx, n - 1)]
    if method == "lower":
        idx = max(0, min(n - 1, math.floor(q * (n - 1))))
        return xs[idx]
    pos = q * (n - 1)
    lo_i = math.floor(pos)
    frac = pos - lo_i
    if lo_i + 1 >= n:
        return xs[n - 1]
    return xs[lo_i] + frac * (xs[lo_i + 1] - xs[lo_i])


# ------------------------------------------------------- clustered bootstrap


def _resample_stat[T](
    by_series: Mapping[str, Sequence[T]],
    stat: Callable[[list[T]], float | None],
    reps: int,
    seed: int,
) -> list[float]:
    """Draw ``reps`` series-resampled replicates of ``stat``, dropping undefined."""
    keys = list(by_series)
    if not keys:
        return []
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(reps):
        pooled: list[T] = []
        for _ in range(len(keys)):
            pooled.extend(by_series[rng.choice(keys)])
        if not pooled:
            continue
        v = stat(pooled)
        if v is None:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            continue
        out.append(float(v))
    return out


def clustered_bootstrap[T](
    by_series: Mapping[str, Sequence[T]],
    stat: Callable[[list[T]], float | None],
    *,
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
    method: PercentileMethod = "linear",
) -> tuple[float, float] | None:
    """Percentile CI for ``stat``, resampling SERIES with replacement.

    Each replicate draws ``len(by_series)`` series keys with replacement and
    pools their rows, so the replicate carries the same series-count as the
    original sample. That is what makes the interval honest about clustered
    data: sampling 80 series out of 80, with replacement, reproduces the
    between-series variance that row-level resampling erases.

    Returns ``(lo, hi)``, or ``None`` when no replicate produced a defined
    statistic. Replicates that return ``None``/NaN/inf are dropped, and the
    percentile is taken over the SURVIVING replicates — not over ``reps``.
    That distinction is the single most common reproduction bug this module
    exists to remove.
    """
    if reps <= 0:
        raise ValueError("reps must be positive")
    draws = _resample_stat(by_series, stat, reps, seed)
    if not draws:
        return None
    lo_q, hi_q = alpha / 2, 1 - alpha / 2
    return percentile(draws, lo_q, method), percentile(draws, hi_q, method)


def clustered_bootstrap_mean(
    by_series: Mapping[str, Sequence[float]],
    *,
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
    method: PercentileMethod = "linear",
) -> tuple[float, float] | None:
    """Clustered CI for the mean of a numeric sample — the common case.

    Pass ``method="nearest_rank"`` to reproduce the legacy implementations
    exactly (see ``tests/test_scorecard_consolidation.py``).
    """

    def _mean(sample: list[float]) -> float | None:
        return sum(sample) / len(sample) if sample else None

    return clustered_bootstrap(by_series, _mean, reps=reps, seed=seed, alpha=alpha, method=method)


def clustered_bootstrap_delta[T](
    by_series_a: Mapping[str, Sequence[T]],
    by_series_b: Mapping[str, Sequence[T]],
    stat_a: Callable[[list[T]], float | None],
    stat_b: Callable[[list[T]], float | None],
    *,
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> tuple[float, float] | None:
    """Clustered CI for ``stat_a - stat_b`` using a PAIRED series resample.

    Both arms are read on the same resampled series in each replicate, which
    is the only correct way to compare two candidate rules scored on the same
    population. Resampling each arm independently would add back the
    between-series variance the pairing is meant to cancel, and would make a
    genuinely better rule look insignificant.

    Only series present in BOTH arms are resampled, so the comparison stays
    paired instead of silently degrading to an unpaired one.
    """
    shared = sorted(set(by_series_a) & set(by_series_b))
    if not shared:
        return None
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(reps):
        pool_a: list[T] = []
        pool_b: list[T] = []
        for _ in range(len(shared)):
            k = rng.choice(shared)
            pool_a.extend(by_series_a[k])
            pool_b.extend(by_series_b[k])
        va, vb = stat_a(pool_a), stat_b(pool_b)
        if va is None or vb is None:
            continue
        deltas.append(float(va) - float(vb))
    if not deltas:
        return None
    return percentile(deltas, alpha / 2), percentile(deltas, 1 - alpha / 2)
