"""Metrics tests: hand-checkable answers, no data, no network.

The point of these is that a wrong formula fails loudly. Beta is checked against
an exactly constructed relationship (strategy = 2 x reference), the Sharpe
against a hand-computed standard deviation, and the deflated Sharpe against the
properties that make it real: it must not exist without a trial count, and it
must fall as the trial count rises.
"""

from __future__ import annotations

import math

import pytest

from gridbot.lab.metrics import (
    beta_alpha,
    deflated_sharpe,
    expected_max_sharpe,
    max_drawdown,
    minimum_detectable_effect,
    pnl_report,
    required_n,
    sharpe_ratio,
    sharpe_score,
)


def test_sharpe_matches_hand_computed_value():
    # mean 1, sample sd (ddof=1) of [0,1,2] = 1 -> sharpe 1
    assert sharpe_ratio([0.0, 1.0, 2.0]) == pytest.approx(1.0)
    # a single observation has no dispersion to divide by: not a number
    assert math.isnan(sharpe_ratio([0.5]))
    # zero variance is undefined, not infinite
    assert math.isnan(sharpe_ratio([1.0, 1.0, 1.0]))


def test_sharpe_annualises_by_declared_periods():
    base = sharpe_ratio([0.0, 1.0, 2.0])
    annual = sharpe_ratio([0.0, 1.0, 2.0], periods_per_year=252)
    assert annual == pytest.approx(base * math.sqrt(252))


def test_beta_recovers_an_exact_relationship():
    """strategy = 2 x reference across every series: beta 2, alpha 0, corr 1."""
    ref = {"s1": [0.01, -0.02, 0.03, 0.00], "s2": [0.02, 0.01, -0.01, 0.04]}
    strat = {k: [2.0 * x for x in v] for k, v in ref.items()}
    ba = beta_alpha(strat, ref)
    assert ba.beta.value == pytest.approx(2.0)
    assert ba.alpha.value == pytest.approx(0.0, abs=1e-12)
    assert ba.corr == pytest.approx(1.0)
    assert ba.r_squared == pytest.approx(1.0)
    assert ba.beta.n == 8 and ba.beta.n_series == 2


def test_beta_zero_when_strategy_has_no_reference_exposure():
    """A strategy uncorrelated with the reference must not be credited with beta."""
    ref = {"s1": [1.0, -1.0, 1.0, -1.0], "s2": [1.0, -1.0, 1.0, -1.0]}
    strat = {"s1": [1.0, 1.0, -1.0, -1.0], "s2": [1.0, 1.0, -1.0, -1.0]}
    ba = beta_alpha(strat, ref)
    assert abs(ba.beta.value) < 1e-9
    assert ba.alpha.value == pytest.approx(0.0, abs=1e-9)


def test_alpha_survives_a_market_that_moved():
    """A pure market ride: alpha must be zero, and the interval must say so."""
    ref = {"s1": [0.05] * 4, "s2": [0.03] * 4}
    strat = {"s1": [0.05] * 4, "s2": [0.03] * 4}
    ba = beta_alpha(strat, ref)
    assert ba.beta.value == pytest.approx(1.0)
    assert ba.alpha.value == pytest.approx(0.0, abs=1e-12)
    # ...but a constant reference has no variance; the guard must not divide by it
    assert ba.beta.n == 8


def test_clustered_sharpe_interval_is_reported():
    by_series = {
        f"s{i}": [0.02, -0.01, 0.03, 0.01, -0.02] for i in range(10)
    }
    score = sharpe_score(by_series, reps=500)
    assert score.defined
    assert score.ci is not None
    assert score.n == 50 and score.n_series == 10
    assert score.ci[0] <= score.value <= score.ci[1]


def test_deflated_sharpe_needs_a_trial_count():
    """The function cannot be called without declaring the search size."""
    by_series = {"s1": [0.01, 0.02, -0.005, 0.015]}
    with pytest.raises(TypeError):
        deflated_sharpe(by_series, variance_of_trial_sr=0.1)  # type: ignore[call-arg]


def test_deflated_sharpe_falls_as_trials_multiply():
    by_series = {f"s{i}": [0.02, 0.01, -0.01, 0.03, 0.0] for i in range(6)}
    few = deflated_sharpe(by_series, n_trials=2, variance_of_trial_sr=0.05)
    many = deflated_sharpe(by_series, n_trials=200, variance_of_trial_sr=0.05)
    assert few.value > many.value, "more search must make the same Sharpe less impressive"
    assert many.meta["n_trials"] == 200
    assert many.meta["expected_max_sharpe"] > few.meta["expected_max_sharpe"]


def test_expected_max_sharpe_is_zero_for_one_trial_and_grows():
    assert expected_max_sharpe(1, 0.5) == 0.0
    assert expected_max_sharpe(2, 0.5) < expected_max_sharpe(100, 0.5) < expected_max_sharpe(
        1000, 0.5
    )
    assert expected_max_sharpe(100, 0.0) == 0.0


def test_pnl_report_totals_and_drawdown():
    by_series = {"s1": [1.0, -2.0], "s2": [3.0, -1.0]}
    rep = pnl_report(by_series, name="t", pnl_path=[1.0, -2.0, 3.0, -1.0])
    assert rep.total_pnl == pytest.approx(1.0)
    assert rep.n_trades == 4 and rep.n_series == 2
    # cumulative path 1, -1, 2, 1 -> trough is -2 below the running peak of 1
    assert rep.max_drawdown == pytest.approx(-2.0)
    assert max_drawdown([1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_mde_and_required_n_are_inverses():
    sigma, n = 0.4, 500
    effect = minimum_detectable_effect(sigma, n)
    assert required_n(sigma, effect) == pytest.approx(n, rel=0.02)
    # doubling the noise doubles the effect you can detect
    assert minimum_detectable_effect(2 * sigma, n) == pytest.approx(2 * effect)
    # and a million trades is a finite number, not infinity
    assert math.isfinite(required_n(sigma, 0.01))


def test_empty_population_reports_undefined_rather_than_zero():
    rep = pnl_report({}, name="nothing")
    assert rep.n_trades == 0
    assert not rep.mean_pnl.defined
    assert rep.total_pnl == 0.0


def test_degenerate_population_yields_no_sharpe_and_no_deflation():
    """Every trade won: sigma is zero, so a Sharpe is arithmetic not evidence."""
    by_series = {f"s{i}": [0.45, 0.45, 0.45] for i in range(5)}
    assert math.isnan(sharpe_ratio([0.45] * 10))
    score = sharpe_score(by_series, reps=200)
    assert not score.defined
    dsr = deflated_sharpe(by_series, n_trials=10, variance_of_trial_sr=0.05)
    assert not dsr.defined, "a constant sample must not produce a deflated Sharpe"
