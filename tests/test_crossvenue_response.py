"""Does PMUS follow intl: labels, sign discipline, and the traps.

This is the module that measures the lane's actual mechanism claim, so the tests
pin the ways it could report a following market that isn't one: an inverted pair
whose direction silently flips (NOTE7), an INFORMED event scored as a success
(the -6.5pt fill-selection cost, NOTE6), or our own capture blindness counted as
evidence that PMUS is stale.
"""

from __future__ import annotations

import pytest

from gridbot.analysis.crossvenue_basis import Quote
from gridbot.analysis.crossvenue_response import (
    INFORMED,
    NO_FOLLOW,
    STALE,
    responses,
    summarise_responses,
)
from gridbot.analysis.crossvenue_signal import Move

S = 1_000_000_000


def _q(t_s: float, mid: float, half: float = 0.005) -> Quote:
    return Quote(int(t_s * S), round(mid - half, 6), round(mid + half, 6))


def _move(t_s: float = 100, ref_s: float = 70, frm: float = 0.50, to: float = 0.54) -> Move:
    return Move(t_ns=int(t_s * S), ref_t_ns=int(ref_s * S), from_mid=frm, to_mid=to)


class TestLabels:
    def test_intl_moves_and_pmus_follows_later_is_STALE(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.53)]
        r = responses([_move()], pmus, "aligned")[0]
        assert r.label == STALE
        assert r.pmus_lag_s == pytest.approx(30.0)
        assert r.basis_ticks == pytest.approx(4.0)

    def test_pmus_moving_FIRST_is_INFORMED_not_a_success(self):
        """THE ADVERSE-SELECTION GUARD. PMUS already moved across the intl move's
        own window: someone on the thin venue knew first, and a resting maker is
        picked off. A study that only asked 'did PMUS eventually follow' would
        score this as evidence FOR the thesis."""
        pmus = [_q(70, 0.50), _q(100, 0.53), _q(130, 0.55)]
        r = responses([_move()], pmus, "aligned")[0]
        assert r.label == INFORMED
        assert r.pmus_led is True

    def test_pmus_never_moving_inside_the_horizon_is_NO_FOLLOW(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(1000, 0.50)]
        r = responses([_move()], pmus, "aligned")[0]
        assert r.label == NO_FOLLOW
        assert r.pmus_lag_ns is None

    def test_a_follow_beyond_the_horizon_does_not_count(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(500, 0.53)]
        assert responses([_move()], pmus, "aligned", horizon_ns=100 * S)[0].label == NO_FOLLOW

    def test_a_follow_in_the_WRONG_direction_does_not_count(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.47)]
        assert responses([_move()], pmus, "aligned")[0].label == NO_FOLLOW


class TestSignDiscipline:
    def test_an_inverted_pair_flips_the_direction(self):
        """NOTE7. intl Yes UP is PMUS Yes DOWN on an inverted pair. PMUS falling
        is the FOLLOW here; a naive implementation calls it no_follow."""
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.47)]
        r = responses([_move()], pmus, "inverted")[0]
        assert r.intl_ticks_pmus_yes == pytest.approx(-4.0)
        assert r.label == STALE

    def test_the_same_tape_gives_opposite_labels_under_the_two_orientations(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.53)]
        assert responses([_move()], pmus, "aligned")[0].label == STALE
        assert responses([_move()], pmus, "inverted")[0].label == NO_FOLLOW

    def test_basis_is_computed_in_pmus_yes_units(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.50)]
        r = responses([_move()], pmus, "inverted")[0]
        # intl 0.54 -> 0.46 as PMUS Yes; PMUS is 0.50 -> basis -0.04
        assert r.basis_ticks == pytest.approx(-4.0)

    def test_an_unresolved_orientation_raises_rather_than_guessing(self):
        with pytest.raises(ValueError, match="refusing to guess a sign"):
            responses([_move()], [_q(100, 0.50)], "sideways")


class TestBlindnessIsNotEvidence:
    def test_an_event_with_no_pmus_quote_is_DROPPED_not_scored(self):
        """Our own capture blindness must never become evidence that PMUS is
        stale -- which is exactly what labelling it no_follow would do."""
        assert responses([_move()], [], "aligned") == []

    def test_an_event_before_the_pmus_tape_starts_is_dropped(self):
        assert responses([_move()], [_q(500, 0.50)], "aligned") == []

    def test_a_one_sided_pmus_book_at_the_event_is_dropped(self):
        assert responses([_move()], [Quote(100 * S, None, None)], "aligned") == []


class TestDegenerate:
    def test_a_zero_size_move_is_skipped(self):
        assert responses([_move(frm=0.5, to=0.5)], [_q(100, 0.5)], "aligned") == []


class TestSummarise:
    def test_empty_is_reported(self):
        assert summarise_responses([]) == {"n": 0}

    def test_label_mix_and_informed_fraction(self):
        pmus_stale = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.53)]
        pmus_inf = [_q(70, 0.50), _q(100, 0.53), _q(130, 0.55)]
        rs = responses([_move()], pmus_stale, "aligned") + \
             responses([_move()], pmus_inf, "aligned")
        s = summarise_responses(rs)
        assert s["n"] == 2 and s[f"n_{STALE}"] == 1 and s[f"n_{INFORMED}"] == 1
        assert s["frac_informed"] == pytest.approx(0.5)

    def test_the_stale_basis_is_reported_separately_from_the_pooled_one(self):
        """Clause (ii) is about the basis at a TRADEABLE event, not at every
        event -- pooling INFORMED events into it flatters the number."""
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.53)]
        s = summarise_responses(responses([_move()], pmus, "aligned"))
        assert s["median_abs_basis_ticks_STALE"] == pytest.approx(4.0)
        assert s["frac_stale_basis_ge_2t"] == pytest.approx(1.0)

    def test_follow_latency_is_reported(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(130, 0.53)]
        s = summarise_responses(responses([_move()], pmus, "aligned"))
        assert s["median_follow_lag_s"] == pytest.approx(30.0)

    def test_no_stale_events_reports_none_rather_than_zero(self):
        pmus = [_q(70, 0.50), _q(100, 0.50), _q(1000, 0.50)]
        s = summarise_responses(responses([_move()], pmus, "aligned"))
        assert s["median_abs_basis_ticks_STALE"] is None
        assert s["median_follow_lag_s"] is None
