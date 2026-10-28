"""Fill-model tests: hand-computed fills, and the direction of the bias.

The model's claim is narrow and checkable: a resting buy at ``p`` fills when a
later quote on the same slug shows an ask at or below ``p``, inside the TTL. These
tests pin that, pin the TTL boundary, and pin the two ways it must NOT fill — a
quote from before the order existed, and a book that moved away.
"""

from __future__ import annotations

import duckdb
import pytest

from gridbot.lab.fills import apply_touch_fill_model, fill_summary

BASE = 1_760_000_000_000_000_000


def _setup(quotes, scan_rows):
    con = duckdb.connect()
    con.execute(
        "CREATE OR REPLACE TABLE v_books(slug VARCHAR, series_id VARCHAR, map_number INTEGER, "
        "ts_ns BIGINT, bid DOUBLE, ask DOUBLE)"
    )
    if quotes:
        con.executemany("INSERT INTO v_books VALUES (?,?,?,?,?,?)", quotes)
    con.execute(
        "CREATE OR REPLACE TABLE scan_input(series_id VARCHAR, ts_ns BIGINT, side VARCHAR, "
        "price DOUBLE, outcome DOUBLE, filled BOOLEAN, market_slug VARCHAR)"
    )
    if scan_rows:
        con.executemany("INSERT INTO scan_input VALUES (?,?,?,?,?,?,?)", scan_rows)
    return con


def test_fills_when_the_tape_comes_to_our_price():
    quotes = [("s", "999", 1, BASE + 30 * 10**9, 0.53, 0.55)]
    con = _setup(quotes, [("999", BASE, "long", 0.55, 1.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 1


def test_does_not_fill_when_the_book_moves_away():
    quotes = [("s", "999", 1, BASE + 30 * 10**9, 0.58, 0.60)]
    con = _setup(quotes, [("999", BASE, "long", 0.55, 1.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 0


def test_does_not_fill_outside_the_ttl():
    quotes = [("s", "999", 1, BASE + 400 * 10**9, 0.53, 0.55)]  # 400s later
    con = _setup(quotes, [("999", BASE, "long", 0.55, 1.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 0
    assert apply_touch_fill_model(con, ttl_s=500) == 1


def test_a_quote_from_before_the_order_cannot_fill_it():
    quotes = [("s", "999", 1, BASE - 30 * 10**9, 0.53, 0.55)]
    con = _setup(quotes, [("999", BASE, "long", 0.55, 1.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 0


def test_a_dead_book_never_fills():
    quotes = [("s", "999", 1, BASE + 30 * 10**9, None, None)]
    con = _setup(quotes, [("999", BASE, "long", 0.55, 1.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 0


def test_short_fills_off_the_bid_side():
    # A short's cost is 1 - bid; at bid 0.45 the cost is 0.55, so a later
    # bid of 0.45 or better fills it.
    quotes = [("s", "999", 1, BASE + 30 * 10**9, 0.45, 0.47)]
    con = _setup(quotes, [("999", BASE, "short", 0.55, 0.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 1


def test_unknown_slug_stays_unfilled():
    quotes = [("other", "999", 1, BASE + 30 * 10**9, 0.53, 0.55)]
    con = _setup(quotes, [("999", BASE, "long", 0.55, 1.0, False, "s")])
    assert apply_touch_fill_model(con, ttl_s=150) == 0


def test_fill_summary_reports_the_selection_signal():
    quotes = [
        ("s", "999", 1, BASE + 30 * 10**9, 0.53, 0.55),  # fills the 0.55 order
        ("t", "998", 1, BASE + 30 * 10**9, 0.60, 0.62),  # does not fill the 0.55 order
    ]
    con = _setup(
        quotes,
        [
            ("999", BASE, "long", 0.55, 1.0, False, "s"),
            ("998", BASE, "long", 0.55, 1.0, False, "t"),
        ],
    )
    apply_touch_fill_model(con, ttl_s=150)
    stats = fill_summary(con)
    assert stats["n"] == 2 and stats["filled"] == 1
    assert stats["fill_rate"] == pytest.approx(0.5)


def test_ttl_must_be_positive():
    con = _setup([], [])
    with pytest.raises(ValueError, match="ttl_s"):
        apply_touch_fill_model(con, ttl_s=0)
