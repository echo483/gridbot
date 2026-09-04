"""Tests for the maker fill bracket rules in gridbot.analysis.exec_fills."""

from __future__ import annotations

import random

import pytest

from gridbot.analysis import exec_fills as ef

NS = 1_000_000_000
T0 = 1_788_423_268_881_643_594
TTL = 150 * NS


def O(t_ns: int, opp_ask: float | None, opp_size: float | None = None) -> ef.Obs:
    return ef.Obs(t_ns=t_ns, opp_ask=opp_ask, opp_size=opp_size)


# --- T1: touch at exactly P — "at" fills, "strict" does not -------------------


def test_t1_at_touch_at_exact_price_fills_upper_not_lower():
    obs = [O(T0 + 1, opp_ask=0.50)]  # strict needs < P, so 0.50 == P does not touch
    upper = ef.simulate(ef.UPPER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    lower = ef.simulate(ef.LOWER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    assert upper.filled and upper.reason == "filled"
    assert upper.qty == 5.0
    assert lower.reason == "no_touch" and not lower.filled


def test_t1_strict_touches_only_below_p():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=10.0), O(T0 + 5, opp_ask=0.47, opp_size=10.0)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=1.0, t0_ns=T0, obs=obs, queue_ahead=0.0)
    assert lower.filled
    # a touch exactly at P under strict is no touch at all
    obs_at = [O(T0 + 1, opp_ask=0.50, opp_size=10.0), O(T0 + 5, opp_ask=0.5, opp_size=10.0)]
    lower_at = ef.simulate(ef.LOWER, P=0.50, qty=1.0, t0_ns=T0, obs=obs_at, queue_ahead=0.0)
    assert lower_at.reason == "no_touch"


# --- T2: persistence needs a NEXT obs inside the window -----------------------


def test_t2_touch_on_last_in_window_obs_is_no_persist():
    # only touch is the LAST in-window obs (0.49); the earlier 0.60 never touches
    obs = [O(T0 + 1, opp_ask=0.60), O(T0 + 5, opp_ask=0.49)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=1.0, t0_ns=T0, obs=obs)
    assert not lower.filled and lower.reason == "no_persist"
    assert lower.touch_idx == 1  # first touch is the last obs; no confirming obs


def test_t2_persist_sets_fill_time_to_confirming_obs():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=10.0), O(T0 + 5, opp_ask=0.47, opp_size=10.0)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=1.0, t0_ns=T0, obs=obs, queue_ahead=0.0)
    assert lower.filled and lower.t_fill_ns == T0 + 5


# --- T3: window is open at t0, closed at t0+ttl ------------------------------


def test_t3_obs_at_t0_excluded_at_t0_plus_ttl_included():
    at_t0 = O(T0, opp_ask=0.49)
    at_end = O(T0 + TTL, opp_ask=0.49)
    upper_t0 = ef.simulate(ef.UPPER, P=0.50, qty=1.0, t0_ns=T0, obs=[at_t0])
    upper_end = ef.simulate(ef.UPPER, P=0.50, qty=1.0, t0_ns=T0, obs=[at_end])
    assert upper_t0.reason == "expired"  # only excluded obs -> empty window
    assert upper_end.filled


def test_t3_expired_outside_window_ignored():
    obs = [O(T0 - 10, opp_ask=0.49), O(T0 + TTL + 10, opp_ask=0.49)]
    upper = ef.simulate(ef.UPPER, P=0.50, qty=1.0, t0_ns=T0, obs=obs)
    assert upper.reason == "expired"


# --- T4: dead book (None opp_ask) between two touches breaks persistence ------


def test_t4_dead_book_between_touches_is_no_persist():
    obs = [O(T0 + 1, opp_ask=0.49), O(T0 + 5, opp_ask=None), O(T0 + 9, opp_ask=0.49)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=1.0, t0_ns=T0, obs=obs, queue_ahead=0.0)
    assert not lower.filled and lower.reason == "no_persist"


# --- T5: behind_displayed queue sizing ----------------------------------------


def test_t5_behind_displayed_defaults_queue_to_first_obs_size():
    # first obs displays 10 -> queue_ahead defaults to 10; confirming shows 12 -> qty 2
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=10.0), O(T0 + 5, opp_ask=0.47, opp_size=12.0)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    assert lower.filled and lower.qty == 2.0


def test_t5_behind_displayed_confirms_show_queue_only():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=10.0), O(T0 + 5, opp_ask=0.47, opp_size=10.0)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    assert not lower.filled and lower.reason == "queue_ahead"


def test_t5_behind_displayed_confirming_none_is_no_size():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=10.0), O(T0 + 5, opp_ask=0.47)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    assert not lower.filled and lower.reason == "no_size"


def test_t5_behind_displayed_first_size_none_with_default_queue_is_no_size():
    obs = [O(T0 + 1, opp_ask=0.49), O(T0 + 5, opp_ask=0.47, opp_size=12.0)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    assert not lower.filled and lower.reason == "no_size"


def test_t5_behind_displayed_explicit_queue_ahead_overrides():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=1.0), O(T0 + 5, opp_ask=0.47, opp_size=12.0)]
    # explicit queue_ahead=0.0 overrides the first-obs size of 1.0 -> qty = 12
    lower = ef.simulate(ef.LOWER, P=0.50, qty=5.0, t0_ns=T0, obs=obs, queue_ahead=0.0)
    assert lower.filled and lower.qty == 5.0  # min(qty, 12 - 0)


def test_t5_behind_displayed_qty_capped_by_order():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=10.0), O(T0 + 5, opp_ask=0.47, opp_size=100.0)]
    lower = ef.simulate(ef.LOWER, P=0.50, qty=3.0, t0_ns=T0, obs=obs)
    assert lower.filled and lower.qty == 3.0


# --- T6: front-of-queue sizing ------------------------------------------------


def test_t6_front_sizeless_credits_full_qty():
    upper = ef.simulate(ef.UPPER, P=0.50, qty=5.0, t0_ns=T0, obs=[O(T0 + 1, opp_ask=0.49)])
    assert upper.filled and upper.qty == 5.0


def test_t6_front_sized_obs_caps_qty():
    obs = [O(T0 + 1, opp_ask=0.49, opp_size=2.0)]
    upper = ef.simulate(ef.UPPER, P=0.50, qty=5.0, t0_ns=T0, obs=obs)
    assert upper.filled and upper.qty == 2.0


# --- T7: empty window + randomized bracket invariant ---------------------------


def test_t7_empty_window_is_expired_for_both_bounds():
    lower, upper = ef.bracket(P=0.50, qty=1.0, t0_ns=T0, obs=[])
    assert lower.reason == "expired" and upper.reason == "expired"


def test_t7_bracket_invariant_on_random_cases():
    rng = random.Random(20260904)
    for _ in range(200):
        n = rng.randint(0, 6)
        obs = []
        for _ in range(n):
            ask = None if rng.random() < 0.2 else round(rng.uniform(0.3, 0.9), 3)
            size = None if rng.random() < 0.3 else rng.randint(0, 50)
            obs.append(O(T0 + rng.randint(1, TTL - 1), opp_ask=ask, opp_size=size))
        obs.sort(key=lambda o: o.t_ns)
        P = round(rng.uniform(0.3, 0.9), 3)
        q = rng.randint(1, 10)
        lower, upper = ef.bracket(P=P, qty=q, t0_ns=T0, obs=obs)
        ef.assert_bracket(lower, upper)  # raises if invariant broken
        if lower.filled:
            assert upper.filled and lower.qty <= upper.qty


# --- T8: short-side conversion is documented, not implemented here -------------


def test_t8_docstring_documents_one_minus_bid_conversion():
    assert ef.__doc__ is not None
    assert "1 - bid" in ef.__doc__


# --- bracket invariants and helpers -------------------------------------------


def test_assert_bracket_raises_when_lower_filled_upper_not():
    lower = ef.Fill(True, 1, 1.0, 0, "filled")
    upper = ef.Fill(False, None, 0.0, 0, "no_touch")
    with pytest.raises(AssertionError):
        ef.assert_bracket(lower, upper)


def test_assert_bracket_raises_when_lower_qty_exceeds_upper():
    lower = ef.Fill(True, 1, 5.0, 0, "filled")
    upper = ef.Fill(True, 1, 2.0, 0, "filled")
    with pytest.raises(AssertionError):
        ef.assert_bracket(lower, upper)


def test_default_rules_have_expected_shape():
    assert ef.LOWER == ef.FillRule(touch="strict", persist=True, queue="behind_displayed")
    assert ef.UPPER == ef.FillRule(touch="at", persist=False, queue="front")


def test_lower_unsized_credits_one_contract_when_sizes_are_missing():
    """Review #1/#8: on the sizeless BBO tape the strict lower bound must still be
    computable — touch + persistence, exactly one contract, never more."""
    from gridbot.analysis.exec_fills import LOWER, LOWER_UNSIZED, Obs, simulate
    t0 = 1_000_000_000_000
    obs = [Obs(t0 + 10 * 10**9, 0.51, None), Obs(t0 + 40 * 10**9, 0.50, None)]
    sized_lower = simulate(LOWER, 0.52, 25.0, t0, obs)
    assert not sized_lower.filled and sized_lower.reason == "no_size"
    unsized = simulate(LOWER_UNSIZED, 0.52, 25.0, t0, obs)
    assert unsized.filled and unsized.qty == 1.0 and unsized.t_fill_ns == t0 + 40 * 10**9
    # a sized confirming observation behaves like behind_displayed
    sized_obs = [Obs(t0 + 10 * 10**9, 0.51, 10.0), Obs(t0 + 40 * 10**9, 0.50, 13.0)]
    assert simulate(LOWER_UNSIZED, 0.52, 25.0, t0, sized_obs).qty == 3.0
    # no touch is still no fill
    assert not simulate(LOWER_UNSIZED, 0.52, 25.0, t0, [Obs(t0 + 10 * 10**9, 0.55, None)]).filled


def test_consumption_channel_credits_one_contract_when_our_level_vanishes():
    """EXEC1-AMEND1: a taker hitting OUR resting bid never moves the opposing quote.
    UPPER: one later observation with own_bid < P; LOWER: two consecutive; the first
    in-window observation must show us at the top (own_bid >= P) or it is undetectable."""
    from gridbot.analysis.exec_fills import LOWER_CONSUMPTION, UPPER_CONSUMPTION, Obs, simulate
    t0 = 1_000_000_000_000
    S = 10**9
    # at the top at +30s, consumed by +60s, still gone at +120s; opposing ask never touches
    obs = [Obs(t0 + 30 * S, 0.62, None, own_bid=0.60), Obs(t0 + 60 * S, 0.62, None, own_bid=0.51),
           Obs(t0 + 120 * S, 0.62, None, own_bid=0.49)]
    up = simulate(UPPER_CONSUMPTION, 0.60, 25.0, t0, obs)
    assert up.filled and up.reason == "consumed" and up.qty == 1.0 and up.t_fill_ns == t0 + 60 * S
    lo = simulate(LOWER_CONSUMPTION, 0.60, 25.0, t0, obs)
    assert lo.filled and lo.reason == "consumed" and lo.t_fill_ns == t0 + 120 * S
    # single dip then back: upper fills, lower (two consecutive) does not
    flick = [Obs(t0 + 30 * S, 0.62, None, own_bid=0.60), Obs(t0 + 60 * S, 0.62, None, own_bid=0.51),
             Obs(t0 + 120 * S, 0.62, None, own_bid=0.60)]
    assert simulate(UPPER_CONSUMPTION, 0.60, 25.0, t0, flick).filled
    assert not simulate(LOWER_CONSUMPTION, 0.60, 25.0, t0, flick).filled
    # someone sat above us at the first look: undetectable, no fill either bound
    above = [Obs(t0 + 30 * S, 0.35, None, own_bid=0.30), Obs(t0 + 60 * S, 0.34, None, own_bid=0.27)]
    assert not simulate(UPPER_CONSUMPTION, 0.28, 25.0, t0, above).filled
    assert not simulate(LOWER_CONSUMPTION, 0.28, 25.0, t0, above).filled
    # own_bid unknown (legacy Obs without the field) -> channel inert, touch path unchanged
    legacy = [Obs(t0 + 30 * S, 0.62, None), Obs(t0 + 60 * S, 0.62, None)]
    assert not simulate(UPPER_CONSUMPTION, 0.60, 25.0, t0, legacy).filled
    # a touch still fills first
    touch = [Obs(t0 + 30 * S, 0.59, None, own_bid=0.60)]
    assert simulate(UPPER_CONSUMPTION, 0.60, 25.0, t0, touch).reason == "filled"


def test_market_referenced_consumption_for_hypothetical_orders():
    """AMEND1-NOTE1: a dry-run order improves the market bid, so 'bid below our cost' is
    true at the first look by construction and must NOT count. The reference is the
    market bid we would sit in front of; only its disappearance counts, upper edge only."""
    from gridbot.analysis.exec_fills import (
        UPPER_CONSUMPTION,
        UPPER_CONSUMPTION_MARKET,
        Obs,
        simulate,
    )
    t0 = 1_000_000_000_000
    S = 10**9
    # we would post 0.60 in front of a 0.57 market bid; the bid never moves: no fill
    steady = [Obs(t0 + 30 * S, 0.62, None, own_bid=0.57), Obs(t0 + 60 * S, 0.62, None, own_bid=0.57)]
    assert simulate(UPPER_CONSUMPTION, 0.60, 25.0, t0, steady).filled          # cost-referenced would over-credit
    assert not simulate(UPPER_CONSUMPTION_MARKET, 0.60, 25.0, t0, steady).filled
    # the 0.57 bid is taken out (0.52 later): a sale went through the level we'd front -> upper fill
    taken = [Obs(t0 + 30 * S, 0.62, None, own_bid=0.57), Obs(t0 + 60 * S, 0.62, None, own_bid=0.52)]
    f = simulate(UPPER_CONSUMPTION_MARKET, 0.60, 25.0, t0, taken)
    assert f.filled and f.reason == "consumed" and f.qty == 1.0
    # the market bid rises above our cost: someone in front of us -> undetectable
    above = [Obs(t0 + 30 * S, 0.62, None, own_bid=0.57), Obs(t0 + 60 * S, 0.66, None, own_bid=0.63)]
    assert simulate(UPPER_CONSUMPTION_MARKET, 0.60, 25.0, t0, above).reason == "undetectable"
