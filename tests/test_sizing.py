"""H-SIZING0: params fail-closed + env overrides; kelly pure function gates."""

from __future__ import annotations

import itertools

import pytest

from gridbot.sizing.kelly import size_entry, wilson_lower
from gridbot.sizing.params import SizingParams

# ---------- params ----------

def test_defaults_construct():
    p = SizingParams()
    assert p.kelly_divisor == 8.0 and p.max_stake_frac == 0.02


@pytest.mark.parametrize("kw", [
    {"bankroll": 0}, {"bankroll": -5},
    {"kelly_divisor": 0.5}, {"kelly_divisor": 101},
    {"min_edge": 0.0}, {"min_edge": 0.6},
    {"max_price": 0.02}, {"max_price": 1.0},
    {"max_stake_frac": 0.0}, {"max_stake_frac": 0.11},
    {"max_open_per_series": 0},
    {"max_daily_loss_frac": 0.0}, {"max_daily_loss_frac": 0.6},
    {"wilson_margin": -0.1}, {"wilson_margin": 0.2},
    {"wilson_z": 0.5}, {"wilson_z": 5.0},
])
def test_invalid_params_fail_closed(kw):
    with pytest.raises(ValueError):
        SizingParams(**{**SizingParams().as_log_dict(), **kw})


def test_env_overrides_applied():
    p = SizingParams().with_overrides({"GRIDBOT_SIZING_BANKROLL": "2500",
                                       "GRIDBOT_SIZING_KELLY_DIVISOR": "6"})
    assert p.bankroll == 2500.0 and p.kelly_divisor == 6.0


def test_env_override_invalid_also_fails():
    with pytest.raises(ValueError):
        SizingParams().with_overrides({"GRIDBOT_SIZING_KELLY_DIVISOR": "0"})


def test_no_env_returns_self():
    p = SizingParams()
    assert p.with_overrides({}) is p


# ---------- wilson ----------

def test_wilson_lower_no_data():
    assert wilson_lower(0, 0) == 0.0


def test_wilson_lower_below_point_estimate():
    # 931/1000: lower bound must sit under 0.931
    lo = wilson_lower(931, 1000)
    assert 0.90 < lo < 0.931


def test_wilson_small_n_wider():
    lo_small = wilson_lower(9, 10)
    lo_big = wilson_lower(900, 1000)
    assert lo_small < lo_big  # less evidence -> more conservative


# ---------- sizing gates ----------

P = SizingParams(bankroll=1000.0)


def test_valid_entry_sizes_and_caps():
    # 93/100 state evidence: wilson lower ~0.866, -0.02 margin -> q_eff ~0.846
    d = size_entry(0.93, 0.70, P, state_wins=93, state_n=100)
    assert d.reason == "ok"
    assert d.stake > 0
    # kelly = edge/(1-price); frac = kelly/8 capped at 0.02
    assert d.stake <= P.bankroll * P.max_stake_frac + 1e-9


def test_edge_below_min_skips():
    d = size_entry(0.55, 0.52, P)
    assert d.stake == 0.0 and d.reason.startswith("edge_below_min")


def test_price_above_max_skips():
    # price alone violates gate even with a fat raw edge
    d = size_entry(0.99, 0.91, P)
    assert d.stake == 0.0 and d.reason.startswith("price_above_max")


def test_invalid_q_skips():
    assert size_entry(1.2, 0.5, P).reason.startswith("invalid_q")
    assert size_entry(0.0, 0.5, P).reason.startswith("invalid_q")


def test_invalid_price_skips():
    assert size_entry(0.9, 0.0, P).reason.startswith("invalid_price")
    assert size_entry(0.9, 1.0, P).reason.startswith("invalid_price")


def test_wilson_floor_shrinks_q_eff():
    # state evidence 9/10 -> wilson lower ~0.60; q_eff must respect floor
    d = size_entry(0.90, 0.70, P, state_wins=9, state_n=10)
    assert d.q_eff < 0.70  # floored well below raw q
    assert d.stake == 0.0


def test_q_eff_never_above_raw():
    d = size_entry(0.60, 0.50, P, state_wins=580, state_n=1000)
    assert d.q_eff <= 0.60


def test_decision_carries_param_snapshot():
    d = size_entry(0.9, 0.8, P)
    assert d.params["kelly_divisor"] == P.kelly_divisor
    assert d.params["bankroll"] == P.bankroll


def test_monotone_edge_grows_stake_until_cap():
    stakes = [size_entry(0.60 + i * 0.02, 0.50, P).stake for i in range(10)]
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(stakes))
    assert stakes[-1] <= P.bankroll * P.max_stake_frac + 1e-9
