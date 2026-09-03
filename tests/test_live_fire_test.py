"""Pure quote math of the first-fire plumbing test (the network legs are
user-run, armed). The SHORT side exists because the 08-25 first fire was
long-only, so the venue's Yes-price convention (registry H-MAPENTRY1-NOTE7)
went unverified for three armed nights."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_fire_test as lft


def test_long_far_quote_sits_well_below_the_bid():
    q = lft.far_quote("long", bid=0.438, ask=0.46, px=0.45)
    assert q.cost == pytest.approx(0.21)          # half the bid, floored to a cent
    assert q.wire_price == pytest.approx(0.21)    # long: venue price == our cost
    assert q.cost < 0.438


def test_short_far_quote_sits_well_above_the_ask_in_yes_terms():
    q = lft.far_quote("short", bid=0.438, ask=0.46, px=0.45)
    # Yes price halfway from the ask to 1.00 (ceil to a cent): selling Yes far
    # above the market can never fill; our cost is 1 - that.
    assert q.wire_price == pytest.approx(0.73)
    assert q.cost == pytest.approx(0.27)
    assert q.wire_price > 0.46


def test_short_wire_goes_through_the_engine_conversion():
    from gridbot.executor.pmus import wire_price

    q = lft.far_quote("short", bid=0.30, ask=0.34, px=0.32)
    assert q.wire_price == pytest.approx(wire_price(q.cost, "short"))


def test_refuses_quotes_at_or_inside_the_touch():
    with pytest.raises(SystemExit):
        lft.far_quote("long", bid=0.01, ask=0.02, px=0.01)   # cannot go below 0.01
    with pytest.raises(SystemExit):
        lft.far_quote("short", bid=0.97, ask=0.99, px=0.98)  # cannot go above 0.99


def test_order_body_intent_follows_side():
    assert lft.order_body("m", 0.21, 4, "long")["intent"] == "ORDER_INTENT_BUY_LONG"
    assert lft.order_body("m", 0.73, 4, "short")["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert lft.order_body("m", 0.73, 4, "short")["price"]["value"] == "0.730"
