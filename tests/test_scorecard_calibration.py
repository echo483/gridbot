"""Tests for calibration and executable-edge measurement.

Hermetic: literal fixtures only.
"""

from __future__ import annotations

import pytest

from gridbot.scorecard.calibration import (
    brier,
    brier_decomposition,
    brier_skill,
    ece,
    ece_equal_width,
    ece_quantile,
    reliability_table,
)
from gridbot.scorecard.edge import (
    conservative_ev,
    edge_per_opportunity,
    ev_per_contract,
    fill_conditional_edge,
    side_cost,
    venue_wire_price,
)

# Four forecasts, two of them right. Distinct values, so binning is unambiguous.
PAIRS = [(0.5, 1.0), (0.5, 0.0), (0.9, 1.0), (0.9, 1.0)]


class TestBrier:
    def test_known_value(self) -> None:
        assert brier([(0.8, 1.0), (0.8, 0.0)]) == pytest.approx((0.04 + 0.64) / 2)

    def test_perfect_forecast_is_zero(self) -> None:
        assert brier([(1.0, 1.0), (0.0, 0.0)]) == 0.0

    def test_empty_is_none(self) -> None:
        assert brier([]) is None

    def test_drops_unusable_rows(self) -> None:
        # None forecast, out-of-range forecast, and a non-binary outcome all drop.
        rows = [(0.5, 1.0), (None, 1.0), (1.5, 0.0), (0.5, 7.0)]
        assert brier(rows) == pytest.approx(0.25)

    def test_skill_of_a_perfect_forecast_is_one(self) -> None:
        assert brier_skill([(1.0, 1.0), (0.0, 0.0), (1.0, 1.0), (0.0, 0.0)]) == pytest.approx(1.0)

    def test_skill_of_the_base_rate_forecast_is_zero(self) -> None:
        pairs = [(0.5, 1.0), (0.5, 0.0), (0.5, 1.0), (0.5, 0.0)]
        assert brier_skill(pairs) == pytest.approx(0.0)

    def test_skill_undefined_when_every_row_is_one_outcome(self) -> None:
        # Base rate 1.0 -> reference Brier 0 -> skill undefined, not infinite.
        assert brier_skill([(0.9, 1.0), (0.8, 1.0)]) is None


class TestECE:
    def test_quantile_ece_known_value(self) -> None:
        # Four equal-count bins of one row each: |0.5-1| + |0.5-0| + |0.9-1| + |0.9-1|, /4.
        assert ece_quantile(PAIRS) == pytest.approx(0.30)

    def test_equal_width_ece_known_value(self) -> None:
        # Bin 2 holds both 0.5s (rate 0.5, forecast 0.5 -> gap 0); bin 4 holds both
        # 0.9s (rate 1.0, forecast 0.9 -> gap 0.1). Weighted 0.5 each.
        assert ece_equal_width(PAIRS) == pytest.approx(0.05)

    def test_the_two_conventions_disagree_on_the_same_data(self) -> None:
        """The finding this module exists to expose: one repo, one bar name,
        two different numbers. Here they differ by 6x."""
        q = ece_quantile(PAIRS)
        w = ece_equal_width(PAIRS)
        assert q is not None and w is not None
        assert q > 5 * w

    def test_empty_is_none(self) -> None:
        assert ece_quantile([]) is None
        assert ece_equal_width([]) is None

    def test_bins_are_capped_at_the_sample_size(self) -> None:
        # Asking for 10 bins over 4 rows must not invent empty bins.
        assert ece_quantile(PAIRS, bins=10) is not None

    def test_reliability_table_sums_to_the_sample(self) -> None:
        table = reliability_table(PAIRS, bins=5)
        assert sum(b["n"] for b in table) == len(PAIRS)
        assert sum(b["weight"] for b in table) == pytest.approx(1.0)

    def test_unknown_bin_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            ece(PAIRS, mode="nonsense")  # type: ignore[arg-type]


class TestMurphyDecomposition:
    def test_exact_identity_holds_for_continuous_forecasts(self) -> None:
        """The five-term form is exact for any binning of continuous forecasts."""
        pairs = [(0.2 + 0.01 * i % 0.7, float(i % 3 == 0)) for i in range(200)]
        d = brier_decomposition(pairs, bins=10)
        assert d is not None
        assert d["identity_residual"] == pytest.approx(0.0, abs=1e-12)

    def test_exact_identity_holds_under_equal_width_binning(self) -> None:
        pairs = [(0.05 * i % 0.9 + 0.05, float(i % 4 == 0)) for i in range(150)]
        d = brier_decomposition(pairs, bins=10, mode="equal_width")
        assert d is not None
        assert d["identity_residual"] == pytest.approx(0.0, abs=1e-12)

    def test_classic_three_term_form_is_incomplete_for_continuous_forecasts(self) -> None:
        """Documented finding, not a bug.

        Murphy's identity assumes forecasts are constant within a bin (he
        binned by discrete forecast value). Our bins are quantile slices of a
        continuous P_map, so a within-bin variance/covariance term survives.
        The module reports it rather than silently mismatching.
        """
        pairs = [(0.2 + 0.01 * i % 0.7, float(i % 3 == 0)) for i in range(200)]
        d = brier_decomposition(pairs, bins=10)
        assert d is not None
        assert d["within_bin_forecast_variance"] > 0
        assert d["classic_residual"] == pytest.approx(
            abs(d["within_bin_forecast_variance"] - 2 * d["within_bin_covariance"])
        )

    def test_classic_form_is_exact_when_forecasts_are_constant_within_bins(self) -> None:
        """Equal-width bins + forecasts sitting on bin centres => no residual."""
        pairs = [(0.5, 1.0), (0.5, 0.0), (0.5, 1.0), (0.5, 1.0)]
        d = brier_decomposition(pairs, bins=2, mode="equal_width")
        assert d is not None
        assert d["within_bin_forecast_variance"] == pytest.approx(0.0)
        assert d["classic_residual"] == pytest.approx(0.0, abs=1e-12)

    def test_a_constant_forecaster_has_no_resolution(self) -> None:
        pairs = [(0.5, 1.0), (0.5, 0.0), (0.5, 1.0), (0.5, 1.0)]
        d = brier_decomposition(pairs, bins=2, mode="equal_width")
        assert d is not None
        assert d["resolution"] == pytest.approx(0.0)
        # Base rate is 3/4, so uncertainty is 0.75 * 0.25.
        assert d["uncertainty"] == pytest.approx(0.75 * 0.25)

    def test_miscalibration_shows_up_in_reliability_not_resolution(self) -> None:
        """A constant 0.5 forecaster on a 75% population is wrong, and the
        decomposition must say WHY: reliability, not resolution."""
        pairs = [(0.5, 1.0), (0.5, 0.0), (0.5, 1.0), (0.5, 1.0)]
        d = brier_decomposition(pairs, bins=2, mode="equal_width")
        assert d is not None
        assert d["reliability"] == pytest.approx(0.25**2)
        assert d["resolution"] == pytest.approx(0.0)

    def test_quantile_binning_of_tied_forecasts_manufactures_resolution(self) -> None:
        """A caveat on the resolution term, not on the ECE.

        With forecasts tied in bulk, equal-COUNT bins slice the outcomes
        arbitrarily, so bin rates spread away from the base rate even though
        the forecaster never moved. Resolution then reads positive for a model
        with none. Equal-width binning does not do this. Low severity — it
        needs bulk ties — but it is why the canonical ECE pairs with the
        reliability term and not the resolution one.
        """
        pairs = [(0.5, 1.0), (0.5, 0.0), (0.5, 1.0), (0.5, 1.0)]
        quantile = brier_decomposition(pairs, bins=2, mode="quantile")
        assert quantile is not None
        assert quantile["resolution"] > 0.0

    def test_empty_is_none(self) -> None:
        assert brier_decomposition([]) is None


class TestExecutableEdge:
    def test_ev_is_probability_minus_price(self) -> None:
        assert ev_per_contract(0.8, 0.5) == pytest.approx(0.3)

    def test_conservative_ev_is_below_the_point_estimate(self) -> None:
        point = ev_per_contract(8 / 10, 0.5)
        cons = conservative_ev(8, 10, 0.5)
        assert cons is not None
        assert cons < point

    def test_conservative_ev_undefined_without_data(self) -> None:
        assert conservative_ev(0, 0, 0.5) is None

    def test_long_pays_the_ask(self) -> None:
        assert side_cost("long", 0.40, 0.44) == 0.44

    def test_short_costs_one_minus_the_bid(self) -> None:
        assert side_cost("short", 0.40, 0.44) == pytest.approx(0.60)

    def test_wire_price_is_the_inverse_of_side_cost(self) -> None:
        """The NOTE7 convention, in one place. A short whose cost is c must be
        sent to the venue at 1-c, because PMUS stores the Yes price."""
        for side in ("long", "short"):
            cost = side_cost(side, 0.37, 0.41)
            assert venue_wire_price(side, cost) == pytest.approx(
                side_cost(side, 0.37, 0.41) if side == "long" else 1.0 - 0.63
            )
        # And concretely: a short costing 0.49 is sent at 0.51.
        assert venue_wire_price("short", 0.49) == pytest.approx(0.51)

    def test_unknown_side_raises(self) -> None:
        with pytest.raises(ValueError):
            side_cost("both", 0.4, 0.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            venue_wire_price("both", 0.4)  # type: ignore[arg-type]


class TestFillConditionalSplit:
    def _rows(self) -> list[dict]:
        rows = []
        # Filled quotes: 55% winners at 0.50 -> negative edge.
        for i in range(20):
            rows.append(
                {
                    "series_id": f"s{i % 5}",
                    "entry_price": 0.50,
                    "won": i < 11,
                    "would_fill": True,
                }
            )
        # Never-filled quotes: 70% winners at the SAME price -> positive edge.
        for i in range(20):
            rows.append(
                {
                    "series_id": f"s{i % 5}",
                    "entry_price": 0.50,
                    "won": i < 14,
                    "would_fill": False,
                }
            )
        return rows

    def test_selection_cost_is_positive_when_the_unfilled_side_is_better(self) -> None:
        out = fill_conditional_edge(self._rows())
        filled = out["edge_would_fill"]
        unfilled = out["edge_never_fill"]
        assert filled.value == pytest.approx(0.55 - 0.50)
        assert unfilled.value == pytest.approx(0.70 - 0.50)
        assert out["selection_cost"].value == pytest.approx(0.15)

    def test_every_score_carries_n_and_series(self) -> None:
        out = fill_conditional_edge(self._rows())
        assert out["edge_would_fill"].n == 20
        assert out["edge_never_fill"].n == 20
        assert out["selection_cost"].n == 40
        for score in out.values():
            assert score.n_series == 5

    def test_empty_population_is_undefined_not_zero(self) -> None:
        out = fill_conditional_edge([])
        assert out["edge_would_fill"].value is None
        assert out["selection_cost"].value is None
        assert not out["selection_cost"].significant

    def test_edge_per_opportunity_pools_both_groups(self) -> None:
        s = edge_per_opportunity(self._rows())
        assert s.n == 40
        assert s.value == pytest.approx(((11 + 14) / 40) - 0.50)
