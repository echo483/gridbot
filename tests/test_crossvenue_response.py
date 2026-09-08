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


class TestMakerQuote:
    """Which side to rest, and at what OUR-SIDE cost, to take intl's side.

    NOTE7 is the whole reason this is a tested function rather than two lines at
    a call site: the venue price is ALWAYS the Yes price, a BUY_SHORT at P costs
    1-P, and sending the Yes price verbatim for a short got 15 of 24 shorts
    venue-rejected while the ledger read "placed".
    """

    def test_a_positive_basis_buys_the_yes_at_the_bid(self):
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(0.03, 0.40, 0.44) == ("long", 0.40)

    def test_a_negative_basis_buys_the_NO_at_one_minus_the_ask(self):
        """Selling Yes on this venue IS buying No; the cost is 1 - ask, never ask."""
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(-0.03, 0.40, 0.44) == ("short", pytest.approx(0.56))

    def test_improving_the_quote_raises_our_cost(self):
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(0.03, 0.40, 0.44, improve_ticks=1)[1] == pytest.approx(0.41)
        assert maker_quote(-0.03, 0.40, 0.44, improve_ticks=1)[1] == pytest.approx(0.57)

    def test_the_side_we_need_being_missing_returns_None(self):
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(0.03, None, 0.44) is None      # want the bid
        assert maker_quote(-0.03, 0.40, None) is None     # want the ask

    def test_the_other_side_being_missing_does_not_matter(self):
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(0.03, 0.40, None) == ("long", 0.40)

    def test_a_zero_basis_has_no_side(self):
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(0.0, 0.40, 0.44) is None

    def test_a_cost_outside_the_unit_interval_is_refused_not_clamped(self):
        from gridbot.analysis.crossvenue_response import maker_quote
        assert maker_quote(0.03, 0.99, 1.0, improve_ticks=2) is None
        assert maker_quote(0.03, 0.0, 0.02) is None

    def test_the_two_sides_are_not_symmetric_in_the_yes_price(self):
        """A long at the bid and a short at the ask are NOT the same number."""
        from gridbot.analysis.crossvenue_response import maker_quote
        long_cost = maker_quote(0.03, 0.40, 0.44)[1]
        short_cost = maker_quote(-0.03, 0.40, 0.44)[1]
        # the long rests AT the Yes bid; the short's cost is the complement of
        # the Yes ask, so it is NOT the ask and NOT the bid
        assert long_cost == pytest.approx(0.40)
        assert short_cost + 0.44 == pytest.approx(1.0)
        assert short_cost != pytest.approx(0.44)


class TestPmusFreshnessBound:
    """A PMUS quote we have not refreshed looks like a book that has not moved.

    That pushes events toward the STALE label and inflates the basis measured
    against it — the thesis-favouring direction, which is the one worth guarding.
    The sibling basis instrument WAS contaminated this way (it joins on the PMUS
    clock, so intl can go stale between samples); this one joins on the intl
    event clock, where intl is fresh by construction. Measured: bounding PMUS age
    at 30s moves STALE 121 -> 115 and leaves the basis at 2.5 ticks.
    """

    def test_age_is_recorded_on_every_response(self):
        pmus = [_q(40, 0.50), _q(100, 0.50), _q(130, 0.53)]
        r = responses([_move()], pmus, "aligned")[0]
        assert r.pmus_age_ns == 0          # a quote stamped exactly at the event
        assert r.pmus_age_s == 0.0

    def test_a_stale_quote_reports_its_age(self):
        pmus = [_q(40, 0.50), _q(130, 0.53)]   # nothing at t=100
        r = responses([_move()], pmus, "aligned")[0]
        assert r.pmus_age_s == pytest.approx(60.0)

    def test_a_quote_older_than_the_bound_drops_the_event(self):
        pmus = [_q(40, 0.50), _q(130, 0.53)]
        assert responses([_move()], pmus, "aligned", max_pmus_age_ns=30 * S) == []
        assert len(responses([_move()], pmus, "aligned", max_pmus_age_ns=90 * S)) == 1

    def test_unbounded_is_the_default_because_the_bound_is_a_registration_choice(self):
        pmus = [_q(40, 0.50), _q(130, 0.53)]
        assert len(responses([_move()], pmus, "aligned")) == 1

    def test_the_summary_surfaces_the_age_distribution(self):
        pmus = [_q(40, 0.50), _q(130, 0.53)]
        s = summarise_responses(responses([_move()], pmus, "aligned"))
        assert s["median_pmus_age_s"] == pytest.approx(60.0)
        assert s["p90_pmus_age_s"] == pytest.approx(60.0)
