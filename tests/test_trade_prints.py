"""Tests for the trade-print fill channel.

Hermetic: synthetic prints and tmp_path tapes, never the real data directory.

The tests that matter here are the ones about the two things this channel exists
to get right — the NOTE7 price convention (a sign error here does not look
broken, it looks like a stable tradeable signal) and the through/at asymmetry
that makes `through` a floor rather than another guess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gridbot.analysis.trade_prints import (
    DEFAULT_TTL_NS,
    Print,
    load_day,
    our_side_price,
    parse_print,
    print_evidence,
)

T0 = 1_000_000_000_000


def p(t_off_s: float, yes: float, qty: float = 10.0) -> Print:
    return Print(
        t_ns=T0 + int(t_off_s * 1e9),
        yes_price=yes,
        qty=qty,
        maker_intent="ORDER_INTENT_BUY_LONG",
        taker_intent="ORDER_INTENT_BUY_SHORT",
    )


# --- NOTE7: the price convention -------------------------------------------

def test_long_cost_is_the_yes_price():
    assert our_side_price(0.62, "long") == 0.62


def test_short_cost_is_one_minus_yes():
    """BUY_SHORT at P costs 1-P — the inversion that cost 20 of 48 orders."""
    assert our_side_price(0.62, "short") == pytest.approx(0.38)


def test_an_unknown_side_refuses_rather_than_guessing():
    with pytest.raises(ValueError, match="refusing to guess"):
        our_side_price(0.62, "yes")


# --- the through/at asymmetry ----------------------------------------------

def test_a_print_through_our_price_is_a_certain_fill():
    """Someone accepted a worse price than we were offering; price priority
    puts our resting order ahead of theirs."""
    ev = print_evidence([p(10, 0.60)], cost=0.62, t0_ns=T0, side="long")
    assert ev.through and ev.certain and ev.possible
    assert ev.vol_through == 10.0 and ev.vol_at == 0.0


def test_a_print_at_our_price_is_only_possible():
    """Same price means time priority decides — evidence, never proof."""
    ev = print_evidence([p(10, 0.62)], cost=0.62, t0_ns=T0, side="long")
    assert not ev.through and not ev.certain
    assert ev.at and ev.possible
    assert ev.vol_at == 10.0


def test_a_print_worse_than_our_price_is_no_evidence_at_all():
    ev = print_evidence([p(10, 0.70)], cost=0.62, t0_ns=T0, side="long")
    assert not ev.possible and ev.vol_through == 0.0 and ev.vol_at == 0.0
    assert ev.n_prints == 1  # counted as traffic, not as evidence


def test_certain_always_implies_possible():
    """The bracket invariant this channel is built to preserve."""
    for yes in (0.10, 0.55, 0.61, 0.62, 0.63, 0.99):
        ev = print_evidence([p(5, yes)], cost=0.62, t0_ns=T0, side="long")
        assert not ev.certain or ev.possible


def test_the_short_side_is_the_mirror_of_the_long_side():
    """A short at cost 0.38 must read a Yes print of 0.60 exactly as a long at
    cost 0.62 reads it — one convention, both sides."""
    long_ev = print_evidence([p(10, 0.60)], cost=0.62, t0_ns=T0, side="long")
    short_ev = print_evidence([p(10, 0.40)], cost=0.62, t0_ns=T0, side="short")
    assert long_ev.certain and short_ev.certain


# --- the lifetime window (must match exec_fills.simulate exactly) ----------

def test_a_print_at_the_posting_instant_is_excluded():
    """Open at t0: our order does not exist yet at t0 itself."""
    ev = print_evidence([Print(T0, 0.50, 1.0, "", "")], cost=0.62,
                        t0_ns=T0, side="long")
    assert not ev.possible and ev.n_prints == 0


def test_a_print_at_the_expiry_instant_is_included():
    """Closed at t0+ttl — the same half-open convention exec_fills uses."""
    ev = print_evidence([Print(T0 + DEFAULT_TTL_NS, 0.50, 1.0, "", "")],
                        cost=0.62, t0_ns=T0, side="long")
    assert ev.certain


def test_a_print_after_expiry_is_ignored():
    ev = print_evidence([Print(T0 + DEFAULT_TTL_NS + 1, 0.50, 1.0, "", "")],
                        cost=0.62, t0_ns=T0, side="long")
    assert not ev.possible and ev.n_prints == 0


def test_volume_accumulates_across_prints_and_first_time_is_the_through():
    ev = print_evidence([p(1, 0.62, 5.0), p(2, 0.60, 7.0), p(3, 0.59, 3.0)],
                        cost=0.62, t0_ns=T0, side="long")
    assert ev.vol_at == 5.0
    assert ev.vol_through == 10.0
    assert ev.t_first_ns == T0 + 2_000_000_000  # the first THROUGH, not the at
    assert ev.n_prints == 3


def test_an_empty_window_proves_nothing():
    ev = print_evidence([], cost=0.62, t0_ns=T0, side="long")
    assert not ev.possible and ev.t_first_ns is None and ev.n_prints == 0


# --- tape parsing ----------------------------------------------------------

TRADE_ROW = {
    "type": "pmus_trade",
    "ts_ns": T0,
    "payload": {"trade": {
        "marketSlug": "aec-cs2-a-b-2026-09-08",
        "price": {"value": "0.8900"},
        "quantity": {"value": "17.2700"},
        "maker": {"intent": "ORDER_INTENT_BUY_LONG"},
        "taker": {"intent": "ORDER_INTENT_BUY_SHORT"},
    }},
}


def test_parses_a_real_row_shape():
    hit = parse_print(TRADE_ROW)
    assert hit is not None
    slug, pr = hit
    assert slug == "aec-cs2-a-b-2026-09-08"
    assert pr.yes_price == 0.89 and pr.qty == 17.27
    assert pr.maker_intent == "ORDER_INTENT_BUY_LONG"


@pytest.mark.parametrize("row", [
    {"type": "pmus_trades_status", "ts_ns": 1, "event": "subscribed"},
    {"type": "pmus_trades_ack", "ts_ns": 1},
    {"type": "pmus_trade", "ts_ns": 1, "payload": {}},
    {"type": "pmus_trade", "ts_ns": 1, "payload": {"trade": {"marketSlug": "s"}}},
    {"type": "pmus_trade", "payload": {"trade": {
        "marketSlug": "s", "price": {"value": "0.5"}, "quantity": {"value": "1"}}}},
])
def test_non_print_frames_are_skipped_not_raised(row):
    """The recorder stores frames VERBATIM and is free to gain new ones."""
    assert parse_print(row) is None


def test_load_day_indexes_by_slug_and_sorts_by_time(tmp_path):
    d = tmp_path / "pmus"
    d.mkdir(parents=True)
    rows = []
    for i, (slug, ts) in enumerate([("b", 30), ("a", 20), ("a", 10)]):
        r = json.loads(json.dumps(TRADE_ROW))
        r["ts_ns"] = ts
        r["payload"]["trade"]["marketSlug"] = slug
        rows.append(r)
    (d / "trades-20260908.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows)
        + "{not json\n"  # a torn final line is a fact of the append-only format
    )
    got = load_day("20260908", tmp_path)
    assert set(got) == {"a", "b"}
    assert [x.t_ns for x in got["a"]] == [10, 20]


def test_a_missing_day_is_empty_not_a_crash(tmp_path):
    """Capture gaps are a fact of the record; reports must render a zero."""
    assert load_day("20200101", tmp_path) == {}
