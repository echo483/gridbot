"""Tests for the interval kernel: Wilson, percentiles, clustered bootstrap.

Hermetic: no files, no network, no keyring. Everything here is pure math on
literal fixtures.
"""

from __future__ import annotations

import pytest

from gridbot.scorecard.intervals import (
    clustered_bootstrap,
    clustered_bootstrap_delta,
    clustered_bootstrap_mean,
    percentile,
    wilson_interval,
    wilson_lower,
    z_for,
)
from gridbot.scorecard.result import Score


class TestWilson:
    def test_zero_n_is_degenerate(self) -> None:
        assert wilson_interval(0, 0) == (0.0, 0.0)
        assert wilson_lower(0, 0) == 0.0

    def test_brackets_the_point_estimate(self) -> None:
        lo, hi = wilson_interval(8, 10)
        assert lo < 0.8 < hi

    def test_stays_inside_unit_interval_at_the_extremes(self) -> None:
        # The normal approximation goes outside [0,1] here; Wilson must not.
        # Tolerance because lo lands exactly on 0 (and hi on 1) up to fp error.
        for k, n in ((0, 5), (5, 5), (1, 5)):
            lo, hi = wilson_interval(k, n)
            assert lo >= -1e-12 and hi <= 1.0 + 1e-12

    def test_narrows_as_n_grows(self) -> None:
        narrow = wilson_interval(80, 100)
        wide = wilson_interval(8, 10)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])

    def test_rejects_impossible_counts(self) -> None:
        with pytest.raises(ValueError):
            wilson_interval(11, 10)
        with pytest.raises(ValueError):
            wilson_interval(-1, 10)

    def test_unlisted_alpha_raises_rather_than_interpolating(self) -> None:
        with pytest.raises(ValueError):
            z_for(0.037)


class TestPercentile:
    def test_linear_interpolates(self) -> None:
        assert percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)

    def test_linear_endpoints(self) -> None:
        assert percentile([1, 2, 3, 4], 0.0) == 1
        assert percentile([1, 2, 3, 4], 1.0) == 4

    def test_nearest_rank_matches_the_legacy_convention(self) -> None:
        # sorted[int(q * n)] — what most of the hand-rolled copies did.
        assert percentile([1, 2, 3, 4], 0.5, "nearest_rank") == 3

    def test_single_value(self) -> None:
        assert percentile([7.0], 0.025) == 7.0

    def test_unsorted_input_is_sorted_first(self) -> None:
        assert percentile([4, 1, 3, 2], 0.5) == pytest.approx(2.5)

    def test_empty_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 0.5)

    def test_bad_q_raises(self) -> None:
        with pytest.raises(ValueError):
            percentile([1, 2], 1.5)


def _by_series(values: dict[str, float], rows_per_series: int = 20) -> dict[str, list[float]]:
    """Each series contributes a constant — maximal between-series clustering."""
    return {k: [v] * rows_per_series for k, v in values.items()}


def _as_singletons(by_series: dict[str, list[float]]) -> dict[str, list[float]]:
    """Emulate ROW-level resampling: every row becomes its own series.

    This is the naive bootstrap a row-pooled interval implies. Keeping it
    available is the point — the tests below use it to show how much narrower
    that interval is than the honest series-clustered one.
    """
    out: dict[str, list[float]] = {}
    for i, row in enumerate(v for rows in by_series.values() for v in rows):
        out[f"row{i}"] = [row]
    return out


class TestClusteredBootstrap:
    def test_is_deterministic_for_a_fixed_seed(self) -> None:
        data = _by_series({f"s{i}": 0.1 * i for i in range(10)})
        a = clustered_bootstrap_mean(data, reps=200, seed=13)
        b = clustered_bootstrap_mean(data, reps=200, seed=13)
        assert a == b

    def test_seed_changes_the_draws(self) -> None:
        data = _by_series({f"s{i}": 0.1 * i for i in range(10)})
        a = clustered_bootstrap_mean(data, reps=200, seed=13)
        b = clustered_bootstrap_mean(data, reps=200, seed=99)
        assert a != b

    def test_clustered_interval_is_wider_than_a_row_level_one(self) -> None:
        """The entire point. Rows inside a series are not independent draws.

        Ten series, 20 perfectly correlated rows each. Honest series-level
        resampling has only TEN independent units to work with, so the interval
        must be far wider than the naive one that pretends it has 200.
        """
        vals = {f"s{i}": 0.1 * i for i in range(10)}
        clustered = clustered_bootstrap_mean(_by_series(vals), reps=2000, seed=13)
        row_wise = clustered_bootstrap_mean(_as_singletons(_by_series(vals)), reps=2000, seed=13)
        assert clustered is not None and row_wise is not None
        assert (clustered[1] - clustered[0]) > 2.0 * (row_wise[1] - row_wise[0])

    def test_undefined_replicates_do_not_shift_the_index(self) -> None:
        """Replicates returning None are dropped; the percentile is taken over
        the survivors. A copy that indexed against the requested rep count
        instead returned a different interval for identical input."""
        data = {f"s{i}": [float(i)] for i in range(8)}

        def half_undefined(sample: list[float]) -> float | None:
            return None if sample[0] > 3 else sum(sample) / len(sample)

        out = clustered_bootstrap(data, half_undefined, reps=500, seed=13)
        assert out is not None
        assert out[0] <= out[1]

    def test_empty_input_is_null(self) -> None:
        assert clustered_bootstrap_mean({}) is None

    def test_all_undefined_is_null(self) -> None:
        assert clustered_bootstrap({"a": [1.0]}, lambda s: None, reps=50) is None

    def test_reps_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            clustered_bootstrap_mean({"a": [1.0]}, reps=0)

    def test_single_series_still_returns_an_interval(self) -> None:
        out = clustered_bootstrap_mean({"only": [1.0, 2.0, 3.0]}, reps=100)
        assert out is not None
        assert out[0] <= out[1]


class TestPairedDelta:
    def test_paired_resample_cancels_shared_series_variance(self) -> None:
        """A uniformly better rule on the same population must read significant.

        Both arms share the between-series spread, so a paired resample sees a
        consistent +0.10 and reports an interval excluding zero. Resampling the
        arms independently would drown that in the shared spread.
        """
        a = {f"s{i}": [1.0 + 0.01 * i] * 5 for i in range(12)}
        b = {f"s{i}": [0.9 + 0.01 * i] * 5 for i in range(12)}
        mean = lambda s: sum(s) / len(s)
        ci = clustered_bootstrap_delta(a, b, mean, mean, reps=2000, seed=13)
        assert ci is not None
        assert ci[0] > 0.0

    def test_no_shared_series_is_null(self) -> None:
        mean = lambda s: sum(s) / len(s)
        assert clustered_bootstrap_delta({"a": [1.0]}, {"b": [1.0]}, mean, mean) is None


class TestScoreContract:
    def test_rejects_more_series_than_rows(self) -> None:
        with pytest.raises(ValueError):
            Score("x", 0.5, None, n=3, n_series=4)

    def test_rejects_interval_on_an_undefined_value(self) -> None:
        with pytest.raises(ValueError):
            Score("x", None, (0.1, 0.2), n=5, n_series=2)

    def test_rejects_inverted_interval(self) -> None:
        with pytest.raises(ValueError):
            Score("x", 0.5, (0.6, 0.4), n=5, n_series=2)

    def test_significance_requires_an_interval_that_excludes_zero(self) -> None:
        assert Score("x", 0.2, (0.05, 0.35), n=10, n_series=3).significant
        assert not Score("x", 0.2, (-0.05, 0.35), n=10, n_series=3).significant

    def test_missing_interval_is_never_significant(self) -> None:
        # Nothing may be called significant by omission.
        assert not Score("x", 0.2, None, n=10, n_series=3).significant
        assert not Score("x", None, None, n=10, n_series=3).significant

    def test_render_always_carries_n_and_series(self) -> None:
        s = Score("band_gap", -0.2129, (-0.309, -0.114), n=434, n_series=80, kind="fraction")
        text = s.render()
        assert "n=434" in text
        assert "series=80" in text
        assert "[" in text and "]" in text

    def test_render_of_undefined_says_so(self) -> None:
        assert "undefined" in Score("x", None, None, n=0, n_series=0).render()
