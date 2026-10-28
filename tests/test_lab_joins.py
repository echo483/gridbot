"""Join tests: the ASOF book join and the staleness bound, on synthetic capture.

These are the tests that would have caught the two ways a backtest manufactures
edge: matching a stale quote, and guessing which team a slug names.
"""

from __future__ import annotations

import duckdb
import pytest
from synth_tape import won_map, write_book_day, write_day

from gridbot.lab.joins import JoinSpec, build_scan_input
from gridbot.lab.scan import check_scan_input

BASE_TS = 1_760_000_000_000_000_000


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    monkeypatch.setenv("GRIDBOT_RAW_DIR", str(root))
    monkeypatch.setenv("GRIDBOT_LAKE_DIR", str(tmp_path / "lake"))
    return root


def _quotes(start=BASE_TS, n=30, step_s=30, bid=0.40, ask=0.44, slug="s"):
    return [
        {"slug": slug, "series_id": "999", "map_number": 1, "bid": bid, "ask": ask,
         "ts_ns": start + i * step_s * 1_000_000_000}
        for i in range(n)
    ]


def _build_lake(root, quotes, step_s=30.0):
    from gridbot.lake import rebuild_raw
    from gridbot.lake.events import build_events

    write_day(root / "grid", [won_map("999")], day="20261028", step_s=step_s,
              base_ts_ns=BASE_TS)
    write_book_day(root / "pmus", quotes)
    rebuild_raw(root=root)
    build_events()


def _markets(con, rows) -> None:
    con.execute("CREATE OR REPLACE TABLE markets(slug VARCHAR, series_id VARCHAR, "
                "map_number INTEGER, slot VARCHAR)")
    if rows:
        con.executemany("INSERT INTO markets VALUES (?,?,?,?)", rows)


def test_join_produces_a_scannable_input(lake):
    quotes = _quotes(slug="aur-vs-navi-map1")
    _build_lake(lake, quotes)
    con = duckdb.connect()
    _markets(con, [("aur-vs-navi-map1", "999", 1, "a")])
    rows = build_scan_input(con, JoinSpec(staleness_s=45.0))
    assert rows > 0
    assert check_scan_input(con) == []
    # Long pays the ask; our team is the slug's slot, which won the map.
    prices = [r[0] for r in con.execute("SELECT DISTINCT price FROM scan_input").fetchall()]
    assert prices == [pytest.approx(0.44)]
    outcomes = {r[0] for r in con.execute("SELECT outcome FROM scan_input").fetchall()}
    assert outcomes == {1.0}


def test_stale_quotes_are_excluded_not_used(lake):
    """One quote early, then silence: rows past the staleness bound must vanish."""
    quotes = _quotes(n=1)
    _build_lake(lake, quotes, step_s=600.0)  # events every 10 minutes
    con = duckdb.connect()
    _markets(con, [("s", "999", 1, "a")])
    tight = build_scan_input(con, JoinSpec(staleness_s=30.0))
    loose = build_scan_input(con, JoinSpec(staleness_s=100_000.0))
    assert tight < loose, "an unbounded staleness must admit strictly more rows"
    assert tight >= 1  # the event at the quote's own instant still matches


def test_short_side_prices_off_the_bid_and_backs_the_other_team(lake):
    _build_lake(lake, _quotes())
    con = duckdb.connect()
    # The slug names slot a, which WON. A short backs slot b, which lost.
    _markets(con, [("s", "999", 1, "a")])
    build_scan_input(con, JoinSpec(staleness_s=45.0, side="short"))
    row = con.execute("SELECT DISTINCT price, outcome FROM scan_input").fetchall()
    assert row == [(pytest.approx(0.60), 0.0)]


def test_dead_book_prices_are_null_not_zero(lake):
    quotes = _quotes(n=1, bid=0, ask=0)
    _build_lake(lake, quotes)
    con = duckdb.connect()
    _markets(con, [("s", "999", 1, "a")])
    # A dead book is not a book at price zero: nothing scan-able comes out.
    assert build_scan_input(con, JoinSpec(staleness_s=100_000.0)) == 0


def test_unmapped_slug_is_dropped_not_guessed(lake):
    _build_lake(lake, _quotes())
    con = duckdb.connect()
    _markets(con, [])  # the venue mapping is missing
    assert build_scan_input(con, JoinSpec(staleness_s=45.0)) == 0


def test_unfinished_map_has_no_outcome(lake):
    """A map that never reached 13 rounds cannot score a trade."""
    from gridbot.lake import rebuild_raw
    from gridbot.lake.events import build_events

    write_day(lake / "grid", [won_map("999", rounds=5)], day="20261028", step_s=30.0,
              base_ts_ns=BASE_TS)
    write_book_day(lake / "pmus", _quotes())
    rebuild_raw(root=lake)
    build_events(force=True)
    con = duckdb.connect()
    _markets(con, [("s", "999", 1, "a")])
    assert build_scan_input(con, JoinSpec(staleness_s=45.0)) == 0


def test_join_spec_rejects_an_unbounded_staleness():
    with pytest.raises(ValueError, match="staleness"):
        JoinSpec(staleness_s=0)
