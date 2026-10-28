"""Calibration: does a forecast mean what it says?

The contract every function here answers is "when the model says 0.70, how
often is it right, and by how much is it wrong" — measured on the population
that was actually traded, because that is the number that decides an entry.

THE ECE PROBLEM THIS MODULE EXISTS TO END
-----------------------------------------
The repo named "ECE (quintile bins)" as a frozen verdict bar, then implemented
three mutually incompatible things under that name:

* ``scripts/quant_eval.py`` — EQUAL-COUNT (true quantile) bins, bin forecast =
  the MEAN forecast in the bin.
* ``src/gridbot/instruments/mapentry_verdict.py`` — EQUAL-WIDTH bins, bin
  forecast = the bin MIDPOINT ``(i + 0.5) / bins``.
* ``src/gridbot/model/train.py`` / ``scripts/mapentry2_score.py`` (the latter
  removed with its closed lane; tag ``evidence-2026-10``) — more variants again.

"Quintile" means equal-count. The midpoint convention answers a different
question entirely: for a bin spanning [0.6, 0.8) it assumes every forecast in
the bin was 0.70, which is badly wrong when forecasts cluster — and ours do
(live P_map concentrates in 0.65–0.97). Two files in the same repo therefore
report two different numbers under one frozen bar, and the bar gates a lane.

Both are implemented here, explicitly named, and a test pins the size of the
disagreement on realistic data. Which one the registry bar MEANS is a
pre-registration decision for the user, not a refactor decision for this
module — see the module's tests for the measured delta.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Literal

Pairs = Sequence[tuple[float, float]]
BinMode = Literal["quantile", "equal_width"]
BinForecast = Literal["mean", "midpoint"]


def _clean(pairs: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop rows with a missing or out-of-range forecast, or a non-0/1 outcome."""
    out = []
    for p, y in pairs:
        if p is None or y is None:
            continue
        if math.isnan(float(p)) or math.isnan(float(y)):
            continue
        p = float(p)
        y = float(y)
        if not 0.0 <= p <= 1.0:
            continue
        if y not in (0.0, 1.0):
            continue
        out.append((p, y))
    return out


# ------------------------------------------------------------------- Brier


def brier(pairs: Pairs) -> float | None:
    """Mean squared error of probability forecasts."""
    rows = _clean(pairs)
    if not rows:
        return None
    return sum((p - y) ** 2 for p, y in rows) / len(rows)


def brier_skill(pairs: Pairs) -> float | None:
    """``1 - BS / BS_ref`` against a constant forecast at the pooled base rate.

    A constant forecast at rate ``p̄`` scores exactly ``p̄(1 - p̄)``, so skill
    greater than zero means the forecasts carry information beyond "always
    predict the average". This is the statistic whose row-pooled version was
    retracted; the point estimate is fine, the INTERVAL was the lie.
    """
    rows = _clean(pairs)
    if not rows:
        return None
    base = sum(y for _, y in rows) / len(rows)
    ref = base * (1 - base)
    if ref == 0.0:
        return None
    bs = brier(rows)
    if bs is None:
        return None
    return 1 - bs / ref


# -------------------------------------------------------------- binning


def bin_index(p: float, i: int, bins: int, mode: BinMode) -> int:
    """Bin index for a forecast under either convention. Kept public: a caller
    that has already sorted its rows must reuse this exact mapping."""
    if mode == "equal_width":
        return min(int(p * bins), bins - 1)
    raise ValueError("equal-count binning depends on the whole sample; use relabel()")


def relabel(pairs: Pairs, bins: int = 5, mode: BinMode = "quantile") -> list[tuple[int, float, float]]:
    """Attach a bin index to every row under the chosen convention.

    ``"quantile"`` sorts by forecast and slices into ``bins`` equal-COUNT
    chunks (adaptive binning). ``"equal_width"`` isolates [0, 1] into ``bins``
    fixed buckets. Equal-width is retained only for reproducing legacy
    numbers; it goes empty when forecasts concentrate, which ours do.
    """
    rows = _clean(pairs)
    if not rows:
        return []
    if mode == "equal_width":
        return [(min(int(p * bins), bins - 1), p, y) for p, y in rows]
    if mode != "quantile":
        raise ValueError(f"unknown bin mode {mode!r}")
    ordered = sorted(rows, key=lambda t: t[0])
    n = len(ordered)
    k = min(bins, n)
    out: list[tuple[int, float, float]] = []
    for i in range(k):
        lo, hi = i * n // k, (i + 1) * n // k
        out.extend((i, p, y) for p, y in ordered[lo:hi])
    return out


def _bin_forecast(
    idx: int, members: list[tuple[float, float]], bins: int, forecast: BinForecast
) -> float:
    if forecast == "midpoint":
        return (idx + 0.5) / bins
    return sum(p for p, _ in members) / len(members)


def reliability_table(
    pairs: Pairs,
    bins: int = 5,
    mode: BinMode = "quantile",
    forecast: BinForecast = "mean",
) -> list[dict]:
    """Per-bin (n, mean forecast, observed rate, gap) — the plot behind an ECE.

    A number without its table is unauditable; every calibration claim in this
    package can be traced to one of these rows.
    """
    labelled = relabel(pairs, bins=bins, mode=mode)
    if not labelled:
        return []
    grouped: dict[int, list[tuple[float, float]]] = {}
    for i, p, y in labelled:
        grouped.setdefault(i, []).append((p, y))
    total = len(labelled)
    table = []
    for i in sorted(grouped):
        members = grouped[i]
        rate = sum(y for _, y in members) / len(members)
        fcast = _bin_forecast(i, members, bins, forecast)
        table.append(
            {
                "bin": i,
                "n": len(members),
                "weight": len(members) / total,
                "mean_forecast": fcast,
                "observed_rate": rate,
                "gap": fcast - rate,
            }
        )
    return table


def ece(
    pairs: Pairs,
    bins: int = 5,
    mode: BinMode = "quantile",
    forecast: BinForecast = "mean",
) -> float | None:
    """Expected calibration error: the sample-weighted mean absolute bin gap.

    Read it as the L1 sibling of Brier's reliability term (which squares the
    same gaps). ``mode="quantile", forecast="mean"`` is the canonical reading
    of the registry's "quintile bins" bar — it answers "when we say 0.7, how
    far off is the observed rate". ``mode="equal_width",
    forecast="midpoint"`` reproduces the legacy ``mapentry_verdict`` number.
    """
    labelled = relabel(pairs, bins=bins, mode=mode)
    if not labelled:
        return None
    grouped: dict[int, list[tuple[float, float]]] = {}
    for i, p, y in labelled:
        grouped.setdefault(i, []).append((p, y))
    total = len(labelled)
    out = 0.0
    for i in sorted(grouped):
        members = grouped[i]
        rate = sum(y for _, y in members) / len(members)
        out += (len(members) / total) * abs(_bin_forecast(i, members, bins, forecast) - rate)
    return out


def ece_quantile(pairs: Pairs, bins: int = 5) -> float | None:
    """Canonical "quintile bins" ECE: equal-count bins, mean-forecast bins."""
    return ece(pairs, bins=bins, mode="quantile", forecast="mean")


def ece_equal_width(pairs: Pairs, bins: int = 5, forecast: BinForecast = "mean") -> float | None:
    """Legacy equal-width ECE. Default forecast is the bin mean; pass
    ``forecast="midpoint"`` for the exact ``mapentry_verdict`` convention."""
    return ece(pairs, bins=bins, mode="equal_width", forecast=forecast)


# ------------------------------------------------------- Murphy decomposition


def brier_decomposition(pairs: Pairs, bins: int = 10, mode: BinMode = "quantile") -> dict | None:
    """Murphy decomposition of the Brier score, with the term the classic form omits.

    The textbook identity is ``BS = reliability - resolution + uncertainty``:

    * **reliability** — miscalibration. High means the model's confidence is
      wrong. This is what the armed-band −21pt over-forecast is.
    * **resolution** — how far bin outcome rates move from the base rate. High
      is good. A constant forecaster has resolution 0 and can still be
      perfectly reliable.
    * **uncertainty** — base-rate variance, a property of the sample, not the
      model.

    That three-term identity is EXACT only when every forecast inside a bin is
    identical — which is how Murphy's original paper binned (by discrete
    forecast value). Our bins are adaptive quantile slices of a continuous
    ``P_map``, so forecasts vary within bins and a residual survives:

        BS = reliability - resolution + uncertainty
             + sum_k w_k * Var_k(p) - 2 * sum_k w_k * Cov_k(p, y)

    Expanding ``(p_i - y_i) = (p_i - p̄_k) + (p̄_k - ȳ_k) + (ȳ_k - y_i)`` makes
    this exact: the two ``mean-deviation`` cross terms vanish, but the
    ``(p_i - p̄_k)(ȳ_k - y_i)`` term is a genuine within-bin covariance and does
    not. Both pieces are reported below, so a reader can see whether the
    three-term reading is trustworthy for a given binning instead of assuming
    it. ``identity_residual`` is the residual of the EXACT (five-term) form and
    should be ~0; ``classic_residual`` is what the three-term form misses.
    """
    rows = _clean(pairs)
    if not rows:
        return None
    base = sum(y for _, y in rows) / len(rows)
    labelled = relabel(rows, bins=bins, mode=mode)
    grouped: dict[int, list[tuple[float, float]]] = {}
    for i, p, y in labelled:
        grouped.setdefault(i, []).append((p, y))
    total = len(labelled)
    reliability = resolution = within_var = within_cov = 0.0
    for members in grouped.values():
        n_k = len(members)
        w = n_k / total
        ps = [p for p, _ in members]
        ys = [y for _, y in members]
        rate = sum(ys) / n_k
        fcast = sum(ps) / n_k
        reliability += w * (fcast - rate) ** 2
        resolution += w * (rate - base) ** 2
        within_var += w * sum((p - fcast) ** 2 for p in ps) / n_k
        within_cov += w * sum((p - fcast) * (y - rate) for p, y in members) / n_k
    uncertainty = base * (1 - base)
    bs = brier(rows)
    classic = reliability - resolution + uncertainty
    exact = classic + within_var - 2 * within_cov
    return {
        "n": total,
        "bins": bins,
        "mode": mode,
        "base_rate": base,
        "brier": bs,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "within_bin_forecast_variance": within_var,
        "within_bin_covariance": within_cov,
        "reconstructed_3term": classic,
        "reconstructed_exact": exact,
        "identity_residual": None if bs is None else abs(bs - exact),
        "classic_residual": None if bs is None else abs(bs - classic),
    }
