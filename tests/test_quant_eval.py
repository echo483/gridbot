"""Tests for scripts/quant_eval.py pure math — no files, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from quant_eval import (
    barrier_up_probability,
    brier,
    brier_skill,
    ece,
    ev_per_contract,
    expected_trades_to_barrier,
    flat_stake_stats,
    kelly_fraction,
    log_growth_stats,
    mc_bankroll,
    wilson,
)


class TestCalibration:
    def test_brier_known_value(self) -> None:
        assert brier([(0.8, 1.0), (0.8, 0.0)]) == pytest.approx((0.04 + 0.64) / 2)

    def test_brier_perfect_is_zero(self) -> None:
        assert brier([(1.0, 1.0), (0.0, 0.0)]) == 0.0

    def test_skill_perfect_forecast_is_one(self) -> None:
        assert brier_skill([(1.0, 1.0), (0.0, 0.0), (1.0, 1.0), (0.0, 0.0)]) == pytest.approx(1.0)

    def test_skill_base_rate_forecast_is_zero(self) -> None:
        pairs = [(0.5, 1.0), (0.5, 0.0), (0.5, 1.0), (0.5, 0.0)]
        assert brier_skill(pairs) == pytest.approx(0.0)

    def test_skill_none_when_degenerate(self) -> None:
        assert brier_skill([(0.9, 1.0), (0.8, 1.0)]) is None

    def test_ece_matched_constant_forecast_is_zero(self) -> None:
        # Forecast 0.75 with exactly 3-of-4 outcomes: perfectly calibrated.
        pairs = [(0.75, 1.0), (0.75, 1.0), (0.75, 1.0), (0.75, 0.0)]
        assert ece(pairs, bins=1) == pytest.approx(0.0)

    def test_ece_overconfident(self) -> None:
        pairs = [(0.9, 0.0)] * 10
        assert ece(pairs) == pytest.approx(0.9)

    def test_wilson_contains_point_estimate(self) -> None:
        lo, hi = wilson(84, 100)
        assert lo < 0.84 < hi
        assert wilson(0, 0) == (0.0, 0.0)


class TestEv:
    def test_ev_zero_at_fair_price(self) -> None:
        assert ev_per_contract(0.8, 0.8) == pytest.approx(0.0)

    def test_kelly_zero_without_edge(self) -> None:
        assert kelly_fraction(0.8, 0.8) == pytest.approx(0.0)
        assert kelly_fraction(0.5, 0.8) == 0.0  # negative edge floors at 0

    def test_kelly_even_money(self) -> None:
        # p=0.6 at price 0.5 (even odds): f* = 2*0.6 - 1 = 0.2.
        assert kelly_fraction(0.6, 0.5) == pytest.approx(0.2)


class TestBrownian:
    def test_driftless_symmetric_is_half(self) -> None:
        assert barrier_up_probability(0.0, 1.0, 5.0, 5.0) == pytest.approx(0.5)

    def test_driftless_asymmetric(self) -> None:
        # Nearer barrier is hit more often: P(up) = down/(up+down).
        assert barrier_up_probability(0.0, 1.0, 1.0, 3.0) == pytest.approx(0.75)

    def test_positive_drift_beats_half(self) -> None:
        assert barrier_up_probability(0.05, 1.0, 5.0, 5.0) > 0.5

    def test_strong_drift_saturates(self) -> None:
        assert barrier_up_probability(10.0, 0.1, 5.0, 5.0) == pytest.approx(1.0)

    def test_zero_vol_follows_drift_sign(self) -> None:
        assert barrier_up_probability(0.1, 0.0, 1.0, 1.0) == 1.0
        assert barrier_up_probability(-0.1, 0.0, 1.0, 1.0) == 0.0

    def test_expected_trades_driftless(self) -> None:
        assert expected_trades_to_barrier(0.0, 1.0, 2.0, 3.0) == pytest.approx(6.0)

    def test_expected_trades_positive_drift_finite(self) -> None:
        t = expected_trades_to_barrier(0.05, 0.5, 10.0, 5.0)
        assert t is not None and 0 < t < 10.0 / 0.05 + 1

    def test_flat_stake_fair_price_no_drift(self) -> None:
        mu, sigma = flat_stake_stats(0.8, 0.8, 1.0)
        assert mu == pytest.approx(0.0)
        assert sigma > 0

    def test_log_growth_positive_for_positive_edge(self) -> None:
        mu, _ = log_growth_stats(0.9, 0.8, 0.1)
        assert mu > 0


class TestMonteCarlo:
    def test_deterministic_under_seed(self) -> None:
        a = mc_bankroll(80, 20, 0.7, stake=1.0, n_trades=50, n_paths=200)
        b = mc_bankroll(80, 20, 0.7, stake=1.0, n_trades=50, n_paths=200)
        assert a == b

    def test_dominant_edge_profits(self) -> None:
        out = mc_bankroll(500, 5, 0.5, stake=1.0, n_trades=200, n_paths=300)
        assert out["p_profitable"] > 0.95
        assert out["p_halve_first"] < 0.05
        assert out["terminal_median"] > 26.0

    def test_negative_edge_loses(self) -> None:
        out = mc_bankroll(5, 500, 0.5, stake=1.0, n_trades=200, n_paths=300)
        assert out["terminal_median"] < 26.0

    def test_exactly_one_staking_mode(self) -> None:
        with pytest.raises(ValueError):
            mc_bankroll(1, 1, 0.5, stake=1.0, fraction=0.1)
        with pytest.raises(ValueError):
            mc_bankroll(1, 1, 0.5)

    def test_fraction_mode_never_ruins_completely(self) -> None:
        out = mc_bankroll(20, 80, 0.5, fraction=0.05, n_trades=100, n_paths=200)
        assert out["terminal_p5"] > 0.0
