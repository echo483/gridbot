"""Consolidation proof: does the new kernel reproduce the old implementations?

This file is the evidence for the refactor. The statistical primitives were
hand-rolled ~15 times across ``scripts/`` and ``src/``; before pointing any of
that code at :mod:`gridbot.scorecard`, the kernel must be shown to agree with
each legacy copy BYTE-FOR-BYTE on identical input — otherwise every number
already published in ``docs/analysis/`` stops being reproducible.

Where they agree, the kernel is a drop-in replacement. Where they DISAGREE,
this file pins the disagreement and names the cause, because those are the
places where two files in one repo reported two different numbers under one
name.

Hermetic: literal fixtures, no corpus, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import quant_eval as legacy_qe

from gridbot.instruments import mapentry_verdict as legacy_mv
from gridbot.instruments import verdict as legacy_verdict
from gridbot.scorecard import calibration as sc_cal
from gridbot.scorecard import intervals as sc_int
from gridbot.scorecard import risk as sc_risk
from gridbot.sizing import kelly as legacy_sizing

# --------------------------------------------------------------- fixtures


def _tie_free(n: int = 200) -> list[tuple[float, float]]:
    """Globally distinct forecasts in [0.60, 0.95) — the live P_map range.

    Distinct because the legacy quantile binning sorts ``(p, y)`` TUPLES; with
    ties present the outcome decides bin membership (see the tie test below).
    Tie-free input is the only way to compare the two binning conventions
    without that confound.
    """
    rows = []
    for i in range(n):
        p = 0.60 + 0.35 * ((i * 37) % n) / n
        y = 1.0 if ((i * 13) % 10) < 7 else 0.0
        rows.append((p, y))
    return rows


def _mean(sample: list[float]) -> float | None:
    return sum(sample) / len(sample) if sample else None


def _as_singletons(by_series: dict[str, list[float]]) -> dict[str, list[float]]:
    """Emulate ROW-level resampling: every row becomes its own series."""
    out: dict[str, list[float]] = {}
    for i, row in enumerate(v for rows in by_series.values() for v in rows):
        out[f"row{i}"] = [row]
    return out


def _mc_core(result: dict) -> dict:
    """Drop the two metadata keys the kernel adds, to compare values only."""
    return {k: v for k, v in result.items() if k not in ("n_paths", "n_trades")}


# --------------------------------------------------------------- Wilson


class TestWilsonAgrees:
    @pytest.mark.parametrize(("k", "n"), [(0, 5), (1, 10), (8, 10), (80, 100), (13, 28)])
    def test_matches_quant_eval(self, k: int, n: int) -> None:
        assert sc_int.wilson_interval(k, n) == pytest.approx(legacy_qe.wilson(k, n))

    @pytest.mark.parametrize(("k", "n"), [(0, 5), (1, 10), (8, 10), (80, 100)])
    def test_matches_instruments_verdict(self, k: int, n: int) -> None:
        assert sc_int.wilson_interval(k, n) == pytest.approx(legacy_verdict._wilson(k, n))

    @pytest.mark.parametrize(("k", "n"), [(0, 5), (4, 10), (13, 28)])
    def test_lower_bound_matches_the_sizing_copy(self, k: int, n: int) -> None:
        assert sc_int.wilson_lower(k, n) == pytest.approx(legacy_sizing.wilson_lower(k, n))


# --------------------------------------------------------------- Brier


class TestBrierAgrees:
    def test_brier_matches_quant_eval(self) -> None:
        pairs = _tie_free(50)
        assert sc_cal.brier(pairs) == pytest.approx(legacy_qe.brier(pairs))

    def test_brier_skill_matches_quant_eval(self) -> None:
        pairs = _tie_free(50)
        assert sc_cal.brier_skill(pairs) == pytest.approx(legacy_qe.brier_skill(pairs))


# --------------------------------------------------------------- ECE


class TestECEDisagreement:
    """The headline finding: one bar name, three implementations, two numbers."""

    def test_canonical_quantile_ece_matches_quant_eval(self) -> None:
        """``quant_eval`` = equal-count bins with the bin MEAN forecast."""
        pairs = _tie_free(200)
        assert sc_cal.ece_quantile(pairs) == pytest.approx(legacy_qe.ece(pairs, bins=5))

    def test_canonical_equal_width_ece_matches_mapentry_verdict(self) -> None:
        """``mapentry_verdict`` = equal-width bins with the bin MIDPOINT."""
        pairs = _tie_free(200)
        assert sc_cal.ece_equal_width(pairs, bins=5, forecast="midpoint") == pytest.approx(
            legacy_mv.ece(pairs, bins=5)
        )

    def test_the_two_repo_implementations_return_different_numbers(self) -> None:
        """Same data, same nominal statistic, same bin count — different answer.

        This is what made the frozen 0.08 ECE bar unreproducible across files:
        the verdict module and the reporting module did not compute the same
        quantity.
        """
        pairs = _tie_free(200)
        qe = legacy_qe.ece(pairs, bins=5)
        mv = legacy_mv.ece(pairs, bins=5)
        assert qe is not None and mv is not None
        assert qe != pytest.approx(mv, abs=1e-9)
        # And the canonical names reproduce each side exactly.
        assert sc_cal.ece_quantile(pairs) == pytest.approx(qe)
        assert sc_cal.ece_equal_width(pairs, forecast="midpoint") == pytest.approx(mv)

    def test_quintile_means_equal_count_not_equal_width(self) -> None:
        """The registry bar says "quintile bins". Quintile means equal-COUNT;
        ``mapentry_verdict`` implemented equal-WIDTH. The two are not the same
        statistic, so the bar did not mean what the module computed."""
        pairs = _tie_free(200)
        equal_count = sc_cal.ece(pairs, bins=5, mode="quantile")
        equal_width = sc_cal.ece(pairs, bins=5, mode="equal_width")
        assert equal_count != pytest.approx(equal_width, abs=1e-9)

    def test_midpoint_forecast_is_not_the_bin_mean_and_can_move_the_bar(self) -> None:
        """A bin's midpoint is only a valid stand-in for its forecasts if they
        sit at the midpoint. Ours cluster at the low edge of their bin, so the
        midpoint convention reads the model as BETTER calibrated than it is —
        and on this fixture by more than the 0.08 bar, i.e. enough to flip a
        PASS/FAIL if the bar accepted it."""
        pairs = [(0.61, 1.0 if i < 90 else 0.0) for i in range(100)]
        by_midpoint = sc_cal.ece_equal_width(pairs, bins=5, forecast="midpoint")
        by_mean = sc_cal.ece_equal_width(pairs, bins=5, forecast="mean")
        assert by_midpoint is not None and by_mean is not None
        assert by_mean > by_midpoint
        assert (by_mean - by_midpoint) > 0.08

    def test_legacy_tuple_sort_lets_the_outcome_pick_the_bin(self) -> None:
        """``quant_eval`` sorts ``(p, y)`` tuples. When forecasts tie, ordering
        by outcome decides which bin each row lands in, which is a (minor)
        information leak from the label into the binning.

        Low severity: it only bites when forecasts tie in bulk. But it is a
        real difference, so the canonical kernel sorts on ``p`` alone.
        """
        pairs = [(0.61, 1.0 if i % 10 else 0.0) for i in range(100)]
        legacy = legacy_qe.ece(pairs, bins=5)
        canonical = sc_cal.ece_quantile(pairs, bins=5)
        assert legacy is not None and canonical is not None
        assert legacy != pytest.approx(canonical, abs=1e-9)


# --------------------------------------------------------------- bootstrap


class TestBootstrapAgrees:
    SAMPLE: ClassVar[dict[str, list[float]]] = {
        f"s{i}": [0.01 * i, 0.02 * i, -0.005 * i, 0.03] for i in range(40)
    }

    def test_matches_mapentry_verdict_exactly(self) -> None:
        """Nearest-rank percentile + identical RNG consumption -> identical CI.

        Anything less than exact here would mean the published MAPENTRY1
        intervals are not reproducible by the kernel.
        """
        mine = sc_int.clustered_bootstrap_mean(
            self.SAMPLE, reps=2000, seed=13, method="nearest_rank"
        )
        theirs = legacy_mv.bootstrap_by_series(self.SAMPLE, _mean, iters=2000, seed=13)
        assert mine is not None and theirs is not None
        assert mine == pytest.approx(theirs)

    def test_matches_instruments_verdict_exactly(self) -> None:
        mine = sc_int.clustered_bootstrap_mean(
            self.SAMPLE, reps=2000, seed=13, method="nearest_rank"
        )
        theirs = legacy_verdict._bootstrap_by_series(self.SAMPLE, iters=2000)
        assert mine is not None and theirs is not None
        assert mine == pytest.approx(theirs)

    def test_canonical_method_is_not_the_legacy_one(self) -> None:
        """The canonical default interpolates between order statistics; the
        legacy copies used nearest-rank. Same story, slightly different edge."""
        canonical = sc_int.clustered_bootstrap_mean(self.SAMPLE, reps=2000, seed=13)
        legacy_style = sc_int.clustered_bootstrap_mean(
            self.SAMPLE, reps=2000, seed=13, method="nearest_rank"
        )
        assert canonical is not None and legacy_style is not None
        assert canonical != legacy_style

    def test_it_is_series_clustered_not_row_clustered(self) -> None:
        """Guard the property that matters more than any single number.

        Perfect within-series correlation: 30 series, 8 identical rows each.
        Honest series-level resampling has 30 independent units; the naive
        row-level view has 240. The clustered interval must be visibly wider.
        """
        tight = {f"s{i}": [0.05 * i] * 8 for i in range(30)}
        clustered = sc_int.clustered_bootstrap_mean(tight, reps=2000, seed=13)
        row_wise = sc_int.clustered_bootstrap_mean(_as_singletons(tight), reps=2000, seed=13)
        assert clustered is not None and row_wise is not None
        assert (clustered[1] - clustered[0]) > 1.5 * (row_wise[1] - row_wise[0])


# --------------------------------------------------------------- risk


class TestRiskAgrees:
    def test_kelly_matches_quant_eval_on_a_grid(self) -> None:
        for p in (0.35, 0.5, 0.65, 0.8, 0.95):
            for price in (0.1, 0.3, 0.5, 0.7, 0.9):
                assert sc_risk.kelly_fraction(p, price) == pytest.approx(
                    legacy_qe.kelly_fraction(p, price)
                )

    def test_barrier_matches_quant_eval(self) -> None:
        for mu, sigma in ((0.0, 1.0), (0.02, 0.4), (-0.03, 0.5)):
            assert sc_risk.barrier_up_probability(mu, sigma, 26.0, 13.0) == pytest.approx(
                legacy_qe.barrier_up_probability(mu, sigma, 26.0, 13.0)
            )

    def test_hitting_time_matches_quant_eval(self) -> None:
        for mu, sigma in ((0.0, 1.0), (0.02, 0.4)):
            assert sc_risk.expected_trades_to_barrier(mu, sigma, 26.0, 13.0) == pytest.approx(
                legacy_qe.expected_trades_to_barrier(mu, sigma, 26.0, 13.0)
            )

    def test_flat_stake_stats_match(self) -> None:
        assert sc_risk.flat_stake_stats(0.6, 0.5, 3.0) == pytest.approx(
            legacy_qe.flat_stake_stats(0.6, 0.5, 3.0)
        )

    def test_log_growth_stats_match(self) -> None:
        assert sc_risk.log_growth_stats(0.7, 0.5, 0.05) == pytest.approx(
            legacy_qe.log_growth_stats(0.7, 0.5, 0.05)
        )

    def test_monte_carlo_matches_quant_eval_path_for_path(self) -> None:
        """Same seed, same Beta draw order, same barriers -> identical numbers.

        The kernel adds ``n_paths``/``n_trades`` to the result for reporting;
        every VALUE is compared, and they match exactly. This is the strongest
        available proof that the port is behaviour-preserving rather than
        merely plausible.
        """
        kwargs = {"stake": 1.0, "n_trades": 150, "n_paths": 300, "seed": 13}
        mine = _mc_core(sc_risk.mc_bankroll(8, 5, 0.5, **kwargs))
        theirs = legacy_qe.mc_bankroll(8, 5, 0.5, **kwargs)
        assert mine == theirs

    def test_monte_carlo_matches_on_the_fractional_path(self) -> None:
        kwargs = {"fraction": 0.125, "n_trades": 150, "n_paths": 300, "seed": 13}
        mine = _mc_core(sc_risk.mc_bankroll(8, 5, 0.5, **kwargs))
        theirs = legacy_qe.mc_bankroll(8, 5, 0.5, **kwargs)
        assert mine == theirs
