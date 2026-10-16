"""Tests for the risk kernel: Kelly, growth, barriers, Monte Carlo."""

from __future__ import annotations

import pytest

from gridbot.scorecard.risk import (
    BANKROLL0,
    barrier_up_probability,
    expected_trades_to_barrier,
    flat_stake_stats,
    fractional_kelly,
    kelly_fraction,
    kelly_fraction_b,
    log_growth_stats,
    mc_bankroll,
)


class TestKelly:
    def test_both_derivations_agree(self) -> None:
        """``(p*b - (1-p))/b`` and ``(p - price)/(1 - price)`` were derived twice
        in this repo and never checked against each other. Pin them."""
        for p in (0.3, 0.5, 0.7, 0.9, 0.99):
            for price in (0.05, 0.25, 0.5, 0.75, 0.95):
                assert kelly_fraction(p, price) == pytest.approx(kelly_fraction_b(p, price))

    def test_no_edge_means_no_stake(self) -> None:
        assert kelly_fraction(0.5, 0.5) == 0.0
        assert kelly_fraction(0.4, 0.5) == 0.0

    def test_edge_produces_a_positive_stake(self) -> None:
        assert kelly_fraction(0.8, 0.5) == pytest.approx(0.6)

    def test_degenerate_prices_are_skipped_not_divided(self) -> None:
        assert kelly_fraction(0.9, 0.0) == 0.0
        assert kelly_fraction(0.9, 1.0) == 0.0

    def test_fractional_kelly_divides(self) -> None:
        assert fractional_kelly(0.8, 0.5, 4.0) == pytest.approx(0.15)

    def test_zero_divisor_raises(self) -> None:
        with pytest.raises(ValueError):
            fractional_kelly(0.8, 0.5, 0.0)


class TestGrowthStats:
    def test_flat_stake_known_value(self) -> None:
        # p=0.6 at price 0.5: b=1, so win=+1, loss=-1 on a $1 stake.
        mu, sigma = flat_stake_stats(0.6, 0.5, 1.0)
        assert mu == pytest.approx(0.2)
        assert sigma == pytest.approx((0.6 * 0.4) ** 0.5 * 2)

    def test_log_growth_positive_for_a_genuine_edge(self) -> None:
        mu, sigma = log_growth_stats(0.8, 0.5, 0.1)
        assert mu > 0
        assert sigma > 0

    def test_log_growth_goes_negative_when_overstaking(self) -> None:
        # Staking 99% of the bankroll on an edge that still loses 20% of the
        # time destroys growth, however good the edge looks.
        mu, _ = log_growth_stats(0.8, 0.5, 0.99)
        assert mu < 0

    def test_staking_the_whole_bankroll_is_rejected_not_silently_infinite(self) -> None:
        # At f=1 the losing branch is log1p(-1) = log(0), which math.log1p
        # raises a domain error for. The kernel refuses it with a reason.
        with pytest.raises(ValueError, match="ruin"):
            log_growth_stats(0.8, 0.5, 1.0)

    def test_nothing_staked_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            log_growth_stats(0.8, 0.5, 0.0)


class TestBarriers:
    def test_driftless_is_the_odds_ratio(self) -> None:
        assert barrier_up_probability(0.0, 1.0, 1.0, 1.0) == pytest.approx(0.5)
        assert barrier_up_probability(0.0, 1.0, 3.0, 1.0) == pytest.approx(0.25)

    def test_zero_volatility_is_deterministic(self) -> None:
        assert barrier_up_probability(1.0, 0.0, 1.0, 1.0) == 1.0
        assert barrier_up_probability(-1.0, 0.0, 1.0, 1.0) == 0.0

    def test_positive_drift_raises_the_upside_probability(self) -> None:
        assert barrier_up_probability(0.05, 1.0, 1.0, 1.0) > 0.5

    def test_strong_drift_does_not_overflow(self) -> None:
        p = barrier_up_probability(1e6, 1.0, 1.0, 1.0)
        assert 0.0 <= p <= 1.0

    def test_driftless_hitting_time_is_the_textbook_form(self) -> None:
        assert expected_trades_to_barrier(0.0, 2.0, 1.0, 1.0) == pytest.approx(0.25)

    def test_no_process_means_no_hitting_time(self) -> None:
        assert expected_trades_to_barrier(0.0, 0.0, 1.0, 1.0) is None


class TestMonteCarlo:
    def test_deterministic_for_a_fixed_seed(self) -> None:
        a = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=100, n_paths=200, seed=13)
        b = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=100, n_paths=200, seed=13)
        assert a == b

    def test_seed_changes_the_paths(self) -> None:
        a = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=100, n_paths=200, seed=13)
        b = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=100, n_paths=200, seed=99)
        assert a != b

    def test_requires_exactly_one_sizing_mode(self) -> None:
        with pytest.raises(ValueError):
            mc_bankroll(8, 5, 0.5, n_trades=10, n_paths=10)
        with pytest.raises(ValueError):
            mc_bankroll(8, 5, 0.5, stake=1.0, fraction=0.1, n_trades=10, n_paths=10)

    def test_rejects_an_unpriced_contract(self) -> None:
        with pytest.raises(ValueError):
            mc_bankroll(8, 5, 0.0, stake=1.0, n_trades=10, n_paths=10)

    def test_percentiles_are_ordered(self) -> None:
        r = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=200, n_paths=500, seed=13)
        assert r["terminal_p5"] <= r["terminal_median"] <= r["terminal_p95"]

    def test_small_sample_produces_an_honest_spread(self) -> None:
        """13 trades is 0.9% of what is needed to separate +2pt from zero. The
        simulation must not manufacture confidence: the posterior over the true
        win rate is nearly flat there, so the paths must disagree wildly."""
        r = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=500, n_paths=2000, seed=13)
        assert 0.05 < r["p_double_first"] < 0.95
        assert 0.05 < r["p_halve_first"] < 0.95
        assert r["terminal_p5"] < BANKROLL0 < r["terminal_p95"]

    def test_a_large_confident_sample_concentrates_the_outcome(self) -> None:
        r = mc_bankroll(800, 500, 0.5, stake=1.0, n_trades=500, n_paths=2000, seed=13)
        # ~61.5% at even money is a big edge; the outcome should be lopsided.
        assert r["p_profitable"] > 0.9

    def test_flat_and_fractional_sizing_both_run(self) -> None:
        flat = mc_bankroll(8, 5, 0.5, stake=1.0, n_trades=50, n_paths=100, seed=13)
        frac = mc_bankroll(8, 5, 0.5, fraction=0.05, n_trades=50, n_paths=100, seed=13)
        assert set(flat) == set(frac)
