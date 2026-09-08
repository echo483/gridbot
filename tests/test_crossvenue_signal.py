"""Intl repricing events: causality, dedup, and refusal to invent a move.

The signal is the half of CROSSVENUE1 that decides whether the lane is viable at
all -- the clean unconditional basis (1.5 ticks) sits below the proposed 2-tick
cost bar, so everything depends on the trigger selecting the tail. These tests
pin the ways a trigger could look better than it is: lookahead, double-counting
one repricing as many, or firing on a book that has no price.
"""

from __future__ import annotations

import pytest

from gridbot.analysis.crossvenue_basis import Quote
from gridbot.analysis.crossvenue_signal import (
    DEFAULT_COOLDOWN_NS,
    DEFAULT_WINDOW_NS,
    Move,
    intl_moves,
    summarise_moves,
)

S = 1_000_000_000


def _q(t_s: float, mid: float, half: float = 0.005) -> Quote:
    return Quote(int(t_s * S), round(mid - half, 6), round(mid + half, 6))


class TestMove:
    def test_ticks_is_signed_and_in_venue_ticks(self):
        assert Move(0, 0, 0.50, 0.53).ticks == pytest.approx(3.0)
        assert Move(0, 0, 0.53, 0.50).ticks == pytest.approx(-3.0)

    def test_direction(self):
        assert Move(0, 0, 0.50, 0.53).up is True
        assert Move(0, 0, 0.53, 0.50).up is False


class TestFiring:
    def test_a_two_tick_move_inside_the_window_fires(self):
        qs = [_q(0, 0.50), _q(10, 0.50), _q(45, 0.52)]
        moves = intl_moves(qs)
        assert len(moves) == 1
        assert moves[0].ticks == pytest.approx(2.0)
        assert moves[0].ref_t_ns == int(10 * S)   # newest quote at or before t-30s

    def test_a_one_tick_move_does_not_fire(self):
        assert intl_moves([_q(0, 0.50), _q(45, 0.51)]) == []

    def test_a_move_spread_beyond_the_window_does_not_fire(self):
        """Drift of 2 ticks over 10 minutes is not a repricing event."""
        qs = [_q(i * 60, 0.50 + 0.002 * i) for i in range(11)]
        assert intl_moves(qs) == []

    def test_a_downward_move_fires_too(self):
        moves = intl_moves([_q(0, 0.50), _q(45, 0.47)])
        assert len(moves) == 1 and moves[0].up is False


class TestCausality:
    def test_the_reference_is_never_after_the_firing_observation(self):
        qs = [_q(0, 0.50), _q(40, 0.55), _q(80, 0.60)]
        for m in intl_moves(qs):
            assert m.ref_t_ns <= m.t_ns

    def test_nothing_fires_before_a_full_window_of_history_exists(self):
        """FAILS CLOSED. The signal is "moved >= 2 ticks over the last 30s". With
        less than 30s of history that predicate cannot be evaluated, so it does
        not fire -- rather than quietly measuring over whatever exists, which
        would make early-market observations fire on smaller real moves."""
        assert intl_moves([_q(0, 0.50)]) == []
        assert intl_moves([_q(0, 0.50), _q(5, 0.60)]) == []   # huge, but too early

    def test_it_fires_once_a_reference_a_full_window_old_exists(self):
        assert len(intl_moves([_q(0, 0.50), _q(5, 0.50), _q(40, 0.60)])) == 1

    def test_the_reference_is_the_quote_as_of_t_minus_window(self):
        qs = [_q(0, 0.50), _q(5, 0.50), _q(36, 0.54)]
        m = intl_moves(qs, window_ns=DEFAULT_WINDOW_NS)[0]
        assert m.ref_t_ns == int(5 * S)   # the newest quote at or before t-30s


class TestDedup:
    def test_one_repricing_is_one_event_not_one_per_tick(self):
        """THE DOUBLE-COUNT GUARD. Without a cooldown a single 5-tick move
        reports as many events and inflates every rate computed from it."""
        qs = [_q(0, 0.50)] + [_q(40 + i, 0.50 + 0.01 * (i + 1)) for i in range(5)]
        assert len(intl_moves(qs)) == 1

    def test_a_later_independent_move_still_fires(self):
        qs = [_q(0, 0.50), _q(40, 0.53), _q(200, 0.53), _q(240, 0.57)]
        assert len(intl_moves(qs)) == 2

    def test_cooldown_is_tunable(self):
        qs = [_q(0, 0.50), _q(40, 0.53), _q(100, 0.50), _q(140, 0.53)]
        assert len(intl_moves(qs, cooldown_ns=DEFAULT_COOLDOWN_NS)) == 2
        assert len(intl_moves(qs, cooldown_ns=10_000 * S)) == 1


class TestUnpriceableBooks:
    def test_a_one_sided_series_yields_no_events(self):
        """A book we cannot price is not a book that repriced."""
        qs = [Quote(i * S, None, 0.5) for i in range(50)]
        assert intl_moves(qs) == []

    def test_one_sided_observations_are_skipped_not_interpolated(self):
        qs = [_q(0, 0.50), Quote(10 * S, None, None), _q(45, 0.52)]
        moves = intl_moves(qs)
        assert len(moves) == 1 and moves[0].ref_t_ns == 0

    def test_an_empty_series_is_empty(self):
        assert intl_moves([]) == []


class TestThresholdIsAnArgument:
    def test_raising_the_threshold_fires_less(self):
        qs = [_q(0, 0.50), _q(45, 0.52)]
        assert len(intl_moves(qs, min_ticks=2.0)) == 1
        assert intl_moves(qs, min_ticks=3.0) == []


class TestSummarise:
    def test_empty_reports_zero_rate_not_a_crash(self):
        assert summarise_moves([], 3600.0) == {"n": 0, "per_hour": 0.0}

    def test_rate_is_per_hour_of_span(self):
        moves = [Move(0, 0, 0.50, 0.53)] * 4
        assert summarise_moves(moves, 7200.0)["per_hour"] == pytest.approx(2.0)

    def test_sizes_are_absolute_but_direction_is_reported_separately(self):
        moves = [Move(0, 0, 0.50, 0.53), Move(0, 0, 0.53, 0.50)]
        s = summarise_moves(moves, 3600.0)
        assert s["median_ticks"] == pytest.approx(3.0)
        assert s["frac_up"] == pytest.approx(0.5)

    def test_a_zero_span_does_not_divide_by_zero(self):
        assert summarise_moves([Move(0, 0, 0.5, 0.53)], 0.0)["per_hour"] == 0.0


class TestPersistence:
    """Does an intl move hold or unwind?

    This is what separates the two readings of a PMUS non-response: a move that
    HOLDS means PMUS was genuinely stale and the gap was real; a move that
    REVERTS means intl overshot and there was never a gap to take.
    """

    def _p(self, mids, horizons=(30, 60)):
        from gridbot.analysis.crossvenue_signal import move_persistence
        qs = [_q(t, m) for t, m in mids]
        mv = [Move(t_ns=int(60 * S), ref_t_ns=int(20 * S), from_mid=0.50, to_mid=0.54)]
        return move_persistence(mv, qs, horizons_s=horizons)[0]

    def test_a_move_that_holds_retraces_zero(self):
        p = self._p([(20, 0.50), (60, 0.54), (90, 0.54), (120, 0.54)])
        assert p.retrace[30] == pytest.approx(0.0)
        assert p.retrace[60] == pytest.approx(0.0)

    def test_a_full_reversion_retraces_one(self):
        p = self._p([(20, 0.50), (60, 0.54), (90, 0.50), (120, 0.50)])
        assert p.retrace[30] == pytest.approx(1.0)

    def test_a_half_reversion_retraces_one_half(self):
        p = self._p([(20, 0.50), (60, 0.54), (90, 0.52), (120, 0.52)])
        assert p.retrace[30] == pytest.approx(0.5)

    def test_continuation_is_negative(self):
        p = self._p([(20, 0.50), (60, 0.54), (90, 0.58), (120, 0.58)])
        assert p.retrace[30] == pytest.approx(-1.0)

    def test_overshooting_back_past_the_origin_exceeds_one(self):
        p = self._p([(20, 0.50), (60, 0.54), (90, 0.46), (120, 0.46)])
        assert p.retrace[30] == pytest.approx(2.0)

    def test_a_downward_move_uses_the_same_sign_convention(self):
        from gridbot.analysis.crossvenue_signal import move_persistence
        qs = [_q(20, 0.50), _q(60, 0.46), _q(90, 0.50)]
        mv = [Move(int(60 * S), int(20 * S), 0.50, 0.46)]
        p = move_persistence(mv, qs, horizons_s=(30,))[0]
        assert p.retrace[30] == pytest.approx(1.0)   # fully reverted, not -1

    def test_a_horizon_past_the_end_of_the_tape_is_None_not_held(self):
        """THE SURVIVORSHIP GUARD. Scoring a missing observation as 0.0 would
        report every truncated move as having held perfectly."""
        p = self._p([(20, 0.50), (60, 0.54), (95, 0.54)], horizons=(30, 600))
        assert p.retrace[30] is not None
        assert p.retrace[600] is None

    def test_a_zero_size_move_is_skipped(self):
        from gridbot.analysis.crossvenue_signal import move_persistence
        mv = [Move(int(60 * S), int(20 * S), 0.50, 0.50)]
        assert move_persistence(mv, [_q(20, 0.5), _q(90, 0.5)]) == []

    def test_summary_reports_reverted_and_held_shares(self):
        from gridbot.analysis.crossvenue_signal import (
            Persistence,
            summarise_persistence,
        )
        ps = [Persistence(0, 4.0, {30: 0.0}), Persistence(1, 4.0, {30: 1.0}),
              Persistence(2, 4.0, {30: 0.6}), Persistence(3, 4.0, {30: None})]
        s = summarise_persistence(ps, horizons_s=(30,))
        assert s["n"] == 4
        assert s["h30s"]["n"] == 3            # the None is excluded, not counted as held
        assert s["h30s"]["frac_reverted_half"] == pytest.approx(2 / 3, abs=1e-3)
        assert s["h30s"]["frac_held"] == pytest.approx(1 / 3, abs=1e-3)

    def test_summary_of_an_empty_horizon_reports_zero(self):
        from gridbot.analysis.crossvenue_signal import (
            Persistence,
            summarise_persistence,
        )
        s = summarise_persistence([Persistence(0, 4.0, {30: None})], horizons_s=(30,))
        assert s["h30s"] == {"n": 0}
