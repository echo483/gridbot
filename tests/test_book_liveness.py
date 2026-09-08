"""Book liveness: the classifier that keeps NOTE8's artifact out of a population.

The failure this guards against is not a crash. It is a number that looks
excellent: a frozen book wins 95% of any comparison against a live one and pays
nothing. Every test below pins one way a book that cannot be traded could be
called `live`.
"""

from __future__ import annotations

import pytest

from gridbot.analysis.book_liveness import (
    DEFAULT_MIN_SPAN_S,
    classify,
)

HOUR_NS = 3_600_000_000_000


def _series(n: int, span_s: float, bid: float, ask: float,
            reprice_every: int | None = None
            ) -> list[tuple[int, float | None, float | None]]:
    """n evenly spaced observations across span_s, optionally moving the quote.

    A repricing book WALKS its quote rather than toggling between two states.
    That matters: a two-state fixture puts ~67% of observations at one quote,
    which real books never do (measured modal-quote share across 79 live match
    books: p50 0.112, p90 0.277) and which the `pinned` rule correctly rejects.
    """
    step = int(span_s * 1e9 / max(1, n - 1))
    out = []
    for i in range(n):
        b, a = bid, ask
        if reprice_every and i >= reprice_every:
            # a small bounded walk, so no single quote dominates the series
            bump = 0.01 * (((i // reprice_every) % 7) - 3)
            b, a = round(bid + bump, 4), round(ask + bump, 4)
        out.append((i * step, b, a))
    return out


class TestFrozen:
    def test_a_two_sided_book_that_never_moves_is_frozen_not_live(self):
        """THE ARTIFACT. ~300 rows, 3.5h, a perfect two-sided quote, zero moves.

        Three real books looked exactly like this on 2026-09-06 and would have
        won any backtest they were pooled into.
        """
        lv = classify(_series(300, 3.5 * 3600, 0.45, 0.47))
        assert lv.verdict == "frozen"
        assert lv.tradeable is False
        assert lv.usable_for_basis is False
        assert lv.n_two_sided == 300 and lv.n_reprices == 0

    def test_a_frozen_book_still_reports_its_metrics(self):
        lv = classify(_series(300, 3.5 * 3600, 0.45, 0.47))
        assert lv.median_spread == pytest.approx(0.02)
        assert lv.frac_one_sided == 0.0


class TestOneSided:
    def test_a_book_with_no_two_sided_rows_is_one_sided(self):
        q = [(i * 10**11, None, 0.5) for i in range(40)]
        lv = classify(q)
        assert lv.verdict == "one_sided"
        assert lv.n_two_sided == 0 and lv.median_spread is None

    def test_a_zero_price_is_a_dead_placeholder_not_a_quote(self):
        """Venue min tick is 0.01, so a 0 is a placeholder, never a real bid."""
        q = [(i * 10**11, 0.0, 0.0) for i in range(40)]
        assert classify(q).verdict == "one_sided"

    def test_mostly_two_sided_is_not_one_sided(self):
        q = _series(100, 3600, 0.45, 0.47, reprice_every=5)
        q[:10] = [(t, None, None) for t, _b, _a in q[:10]]
        assert classify(q).verdict != "one_sided"


class TestWidth:
    def test_a_book_whose_mid_sits_inside_a_huge_spread_is_wide(self):
        """aec-cs2-oldm-pre-2026-09-07: median spread 0.37, and a 12.5-tick
        'basis' that was an artifact of exactly that."""
        lv = classify(_series(85, 7.9 * 3600, 0.30, 0.67, reprice_every=3))
        assert lv.verdict == "wide"
        assert lv.usable_for_basis is False
        assert lv.median_spread > 0.05

    def test_a_tight_active_book_is_live(self):
        lv = classify(_series(300, 3.5 * 3600, 0.45, 0.47, reprice_every=3))
        assert lv.verdict == "live"
        assert lv.tradeable and lv.usable_for_basis

    def test_width_is_judged_per_book_not_pooled(self):
        """NOTE9's 0.02 is a row median across books; per book they diverge."""
        tight = classify(_series(300, 3.5 * 3600, 0.45, 0.47, reprice_every=3))
        wide = classify(_series(300, 3.5 * 3600, 0.10, 0.90, reprice_every=3))
        assert tight.verdict == "live" and wide.verdict == "wide"


class TestSluggish:
    def test_a_slow_but_real_book_is_sluggish_and_still_usable_for_basis(self):
        """Slowness is the staleness the cross-venue thesis is ABOUT — excluding
        it would throw away the signal."""
        q = _series(60, 8 * 3600, 0.45, 0.47, reprice_every=30)
        lv = classify(q)
        assert lv.verdict == "sluggish"
        assert lv.tradeable is False
        assert lv.usable_for_basis is True

    def test_sluggish_reports_a_rate(self):
        lv = classify(_series(60, 8 * 3600, 0.45, 0.47, reprice_every=30))
        assert lv.reprices_per_hour is not None and lv.reprices_per_hour < 1.0


class TestFailsClosed:
    def test_too_few_observations_is_no_data_never_live(self):
        lv = classify(_series(5, 3600, 0.45, 0.47, reprice_every=2))
        assert lv.verdict == "no_data"
        assert lv.tradeable is False and lv.usable_for_basis is False

    def test_too_short_a_span_cannot_distinguish_frozen_from_quiet(self):
        """A 4-minute window with no move is not evidence of a frozen book."""
        lv = classify(_series(100, DEFAULT_MIN_SPAN_S - 1, 0.45, 0.47))
        assert lv.verdict == "no_data"

    def test_an_empty_series_is_no_data(self):
        lv = classify([])
        assert lv.verdict == "no_data"
        assert lv.n_obs == 0 and lv.frac_one_sided == 1.0

    def test_no_rate_is_reported_when_the_span_is_too_short(self):
        assert classify(_series(100, 60, 0.45, 0.47)).reprices_per_hour is None


class TestThresholdsAreArguments:
    def test_a_caller_can_retune_width_without_recomputing_the_series(self):
        q = _series(300, 3.5 * 3600, 0.40, 0.50, reprice_every=3)
        assert classify(q).verdict == "wide"
        assert classify(q, max_median_spread=0.20).verdict == "live"

    def test_a_caller_can_retune_activity(self):
        q = _series(60, 8 * 3600, 0.45, 0.47, reprice_every=30)
        assert classify(q).verdict == "sluggish"
        assert classify(q, min_reprices_per_hour=0.01).verdict == "live"


class TestSeverityOrder:
    def test_one_sidedness_outranks_freezing(self):
        q = [(i * 10**11, None, None) for i in range(40)]
        assert classify(q).verdict == "one_sided"

    def test_freezing_outranks_width(self):
        """A book that never moves is not merely wide; it has no discovery."""
        lv = classify(_series(300, 3.5 * 3600, 0.10, 0.90))
        assert lv.verdict == "frozen"


class TestPinned:
    """A book that MOVES but spends almost all its life at one quote.

    THE CASE. aec-cs2-all-faze-2026-09-08 held the identical quote (0.26, 0.27)
    for 185 of 189 two-sided observations — 95% — while intl ranged 0.245 to
    0.670. Nine transitions in and out of degenerate states scored 4.45
    reprices/hour and passed as `live`. It then supplied 54% of the entire
    >=10-tick tail of the cross-venue basis. Reprice FREQUENCY passed it;
    reprice CONCENTRATION does not.
    """

    def _mostly_one_quote(self, n=200, span_s=7200.0, share=0.95):
        step = int(span_s * 1e9 / max(1, n - 1))
        k = int(n * share)
        out = [(i * step, 0.26, 0.27) for i in range(k)]
        out += [(i * step, round(0.45 + 0.01 * i, 4), round(0.46 + 0.01 * i, 4))
                for i in range(k, n)]
        return out

    def test_a_book_pinned_to_one_quote_is_not_live(self):
        lv = classify(self._mostly_one_quote())
        assert lv.verdict == "pinned"
        assert lv.tradeable is False
        assert lv.usable_for_basis is False

    def test_it_would_have_passed_on_reprice_rate_alone(self):
        """The blips really do clear the activity bar — that is the whole point."""
        lv = classify(self._mostly_one_quote())
        assert lv.n_reprices > 0
        assert lv.reprices_per_hour >= 1.0

    def test_the_modal_share_is_reported(self):
        lv = classify(self._mostly_one_quote(share=0.95))
        assert lv.modal_quote_share == pytest.approx(0.95, abs=0.02)

    def test_a_normally_varied_book_is_not_pinned(self):
        lv = classify(_series(300, 3.5 * 3600, 0.45, 0.47, reprice_every=3))
        assert lv.verdict == "live"
        assert lv.modal_quote_share < 0.5

    def test_the_threshold_is_an_argument(self):
        q = self._mostly_one_quote(share=0.6)
        assert classify(q).verdict == "pinned"
        assert classify(q, max_modal_share=0.9).verdict != "pinned"

    def test_freezing_still_outranks_pinning(self):
        """A book with ZERO reprices is frozen, not merely pinned."""
        assert classify(_series(300, 3.5 * 3600, 0.45, 0.47)).verdict == "frozen"

    def test_one_sidedness_still_outranks_pinning(self):
        q = [(i * 10**11, None, None) for i in range(40)]
        assert classify(q).verdict == "one_sided"
