"""Basis instrument: sign, as-of discipline, and the holdout gate.

Every test here pins a way this instrument could produce a number that looks
excellent and means nothing: a missed inversion (NOTE7 cost 15 of 24 shorts),
lookahead from a forward-looking join, or an unregistered lane quietly reading
its own holdout (iron rail 2).
"""

from __future__ import annotations

import pytest

from gridbot.analysis.crossvenue_basis import (
    HOLDOUT_BOUNDARY_NS,
    BasisPoint,
    HoldoutViolation,
    Quote,
    as_of,
    basis_series,
    require_training_only,
    split_by_boundary,
    summarise,
)

BEFORE = HOLDOUT_BOUNDARY_NS - 10**9
AFTER = HOLDOUT_BOUNDARY_NS + 10**9


def _q(t: int, bid: float | None, ask: float | None) -> Quote:
    return Quote(t_ns=t, bid=bid, ask=ask)


class TestQuote:
    def test_mid_is_the_touch_midpoint(self):
        assert _q(1, 0.40, 0.44).mid == pytest.approx(0.42)

    def test_a_one_sided_book_has_no_mid(self):
        assert _q(1, None, 0.44).mid is None
        assert _q(1, 0.40, None).mid is None


class TestAsOf:
    def test_returns_the_most_recent_quote_at_or_before(self):
        qs = [_q(10, 0.1, 0.2), _q(20, 0.3, 0.4), _q(30, 0.5, 0.6)]
        assert as_of(qs, 25).t_ns == 20
        assert as_of(qs, 20).t_ns == 20      # inclusive at the instant
        assert as_of(qs, 30).t_ns == 30

    def test_never_returns_a_future_quote(self):
        """THE LOOKAHEAD GUARD. A quote after t is information we did not have."""
        qs = [_q(100, 0.1, 0.2)]
        assert as_of(qs, 99) is None

    def test_empty_history_is_none_not_an_error(self):
        assert as_of([], 5) is None


class TestBasisSign:
    def test_aligned_subtracts_directly(self):
        pts = basis_series([_q(100, 0.40, 0.44)], [_q(90, 0.50, 0.54)], "aligned")
        assert len(pts) == 1
        assert pts[0].pmus_mid == pytest.approx(0.42)
        assert pts[0].intl_mid_as_pmus_yes == pytest.approx(0.52)
        assert pts[0].basis == pytest.approx(0.10)

    def test_inverted_flips_intl_before_subtracting(self):
        """1 - 0.52 = 0.48 against a PMUS mid of 0.42 -> +0.06, not +0.10."""
        pts = basis_series([_q(100, 0.40, 0.44)], [_q(90, 0.50, 0.54)], "inverted")
        assert pts[0].intl_mid_as_pmus_yes == pytest.approx(0.48)
        assert pts[0].basis == pytest.approx(0.06)

    def test_an_unresolved_orientation_raises_rather_than_guessing(self):
        with pytest.raises(ValueError, match="refusing to guess a sign"):
            basis_series([_q(100, 0.40, 0.44)], [_q(90, 0.50, 0.54)], "unknown")

    def test_basis_in_ticks_uses_the_venue_tick(self):
        p = BasisPoint(1, 0.42, 0.45, 0.03, 0, "aligned")
        assert p.basis_ticks == pytest.approx(3.0)


class TestBasisSeriesJoin:
    def test_one_point_per_pmus_observation_on_the_pmus_clock(self):
        pmus = [_q(100, 0.40, 0.44), _q(200, 0.41, 0.45)]
        intl = [_q(50, 0.50, 0.54), _q(150, 0.60, 0.64)]
        pts = basis_series(pmus, intl, "aligned")
        assert [p.t_ns for p in pts] == [100, 200]
        assert pts[0].intl_mid_as_pmus_yes == pytest.approx(0.52)   # the t=50 quote
        assert pts[1].intl_mid_as_pmus_yes == pytest.approx(0.62)   # the t=150 quote

    def test_a_pmus_observation_before_any_intl_quote_is_dropped(self):
        pts = basis_series([_q(10, 0.40, 0.44)], [_q(100, 0.5, 0.54)], "aligned")
        assert pts == []

    def test_one_sided_books_on_either_venue_are_dropped(self):
        assert basis_series([_q(100, None, 0.44)], [_q(90, 0.5, 0.54)], "aligned") == []
        assert basis_series([_q(100, 0.40, 0.44)], [_q(90, None, 0.54)], "aligned") == []

    def test_intl_age_is_measured_and_can_bound_the_join(self):
        pmus, intl = [_q(1_000_000_000, 0.40, 0.44)], [_q(0, 0.50, 0.54)]
        assert basis_series(pmus, intl, "aligned")[0].intl_age_ns == 1_000_000_000
        assert basis_series(pmus, intl, "aligned", max_intl_age_ns=999_999_999) == []
        assert len(basis_series(pmus, intl, "aligned", max_intl_age_ns=10**9)) == 1


class TestHoldoutGate:
    def test_splits_on_first_mapped_time(self):
        train, hold = split_by_boundary(["a", "b"], {"a": BEFORE, "b": AFTER})
        assert train == ["a"] and hold == ["b"]

    def test_a_series_exactly_on_the_boundary_is_holdout(self):
        _train, hold = split_by_boundary(["x"], {"x": HOLDOUT_BOUNDARY_NS})
        assert hold == ["x"]

    def test_an_unknown_series_fails_CLOSED_into_holdout(self):
        """THE RAIL. Unknown provenance must never be explorable."""
        train, hold = split_by_boundary(["ghost"], {})
        assert train == [] and hold == ["ghost"]

    def test_require_training_only_passes_a_clean_set_through(self):
        assert require_training_only(["a"], {"a": BEFORE}) == ["a"]

    def test_require_training_only_RAISES_on_any_holdout_series(self):
        with pytest.raises(HoldoutViolation, match="not registered"):
            require_training_only(["a", "b"], {"a": BEFORE, "b": AFTER})

    def test_it_raises_rather_than_silently_filtering(self):
        """A silent filter teaches the caller nothing; the next call repeats it."""
        with pytest.raises(HoldoutViolation):
            require_training_only(["b"], {"b": AFTER})


class TestSummarise:
    def test_empty_is_reported_not_faked(self):
        assert summarise([]) == {"n": 0}

    def test_reports_absolute_basis_because_a_symmetric_gap_means_zero(self):
        pts = [BasisPoint(1, 0.5, 0.53, 0.03, 0, "aligned"),
               BasisPoint(2, 0.5, 0.47, -0.03, 0, "aligned")]
        s = summarise(pts)
        assert s["n"] == 2
        assert abs(s["median_basis"]) == pytest.approx(0.03)   # signed median is +/-
        assert s["median_abs_basis_ticks"] == pytest.approx(3.0)
        assert s["frac_abs_ge_2_ticks"] == pytest.approx(1.0)

    def test_counts_the_fraction_clearing_a_two_tick_cost_floor(self):
        pts = [BasisPoint(i, 0.5, 0.5 + b, b, 0, "aligned")
               for i, b in enumerate([0.001, 0.005, 0.02, 0.05])]
        assert summarise(pts)["frac_abs_ge_2_ticks"] == pytest.approx(0.5)

    def test_reports_how_stale_the_intl_side_was(self):
        pts = [BasisPoint(1, 0.5, 0.5, 0.0, 2_000_000_000, "aligned")]
        assert summarise(pts)["median_intl_age_s"] == pytest.approx(2.0)
