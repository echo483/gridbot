"""Order population + observation assembly for the EXEC1 offline maker replay.

Each test encodes a trap from the exec_population build brief. Row fixtures
mirror the real mapentry/postquote/pmus_bbo capture shapes so the plumbing is
exercised against the actual field layout, not a fake one.
"""

from __future__ import annotations

from gridbot.analysis import exec_population as ep
from gridbot.analysis.exec_population import Order
from gridbot.instruments import mapentry_verdict as mv

NS = 1_000_000_000
HOLD = 1_788_000_000_000_000_000  # hold start ns; orders land after it


def row(**over):
    """A real-shaped qualifying mapentry row (verbatim field set from capture)."""
    base = {
        "type": "mapentry",
        "hypothesis_id": "H-MAPENTRY1",
        "src_hypothesis_id": "H-MAPENTRY1",
        "series_id": "2997461",
        "map_number": 2,
        "round_number": 1,
        "side": "ct",
        "team": "Total Domination",
        "opponent": "Omega",
        "a": 0,
        "b": 0,
        "q_round": 0.4986,
        "p_map": 0.4998,
        "dedup_extra": "",
        "market_slug": "astatc-cs2-omg-td-2026-08-30-map2",
        "us_side": "short",
        "bid": 0.57,
        "ask": 0.60,
        "side_ask": 0.43,
        "entry_price_max": 0.4598,
        "qualifies": True,
        "skip_reason": None,
        "src_ts_ns": None,
        "grid_ts_ns": 0,
        "ts_ns": HOLD,
    }
    base.update(over)
    return base


def orders(rows, first_mapping, series_target=None):
    return ep.heldout_orders(rows, first_mapping, HOLD, series_target=series_target)


# --- T1: every disqualifying population rule drops its row -------------------


def test_t1_only_the_good_row_survives():
    good = row(series_id="S1", us_side="long", ts_ns=HOLD)
    slate = row(series_id="S1", src_hypothesis_id="H-X-SLATE", ts_ns=HOLD)
    non_qual = row(series_id="S1", qualifies=False, ts_ns=HOLD)
    bookless = row(series_id="S1", bid=None, ask=None, ts_ns=HOLD)
    before = row(series_id="S1", ts_ns=HOLD - 1)
    early_first_map = row(series_id="S2", ts_ns=HOLD)
    first_map = {"S1": HOLD, "S2": HOLD - 1}

    out = orders([good, slate, non_qual, bookless, before, early_first_map], first_map)
    assert len(out) == 1
    assert out[0].series_id == "S1"
    assert out[0].t0_ns == HOLD


# --- T2: dedupe keeps the earliest ts_ns; long+short on a map are two orders -


def test_t2_dedupe_earliest_and_side_is_a_key():
    r1 = row(series_id="S1", map_number=1, us_side="long", ts_ns=HOLD)
    r2 = row(series_id="S1", map_number=1, us_side="long", ts_ns=HOLD + 5 * NS)
    short = row(series_id="S1", map_number=1, us_side="short", ts_ns=HOLD + 2 * NS)
    out = orders([r1, r2, short], {"S1": HOLD})

    assert len(out) == 2
    long_o = next(o for o in out if o.us_side == "long")
    short_o = next(o for o in out if o.us_side == "short")
    assert long_o.t0_ns == HOLD  # earliest long survived
    assert short_o.t0_ns == HOLD + 2 * NS


# --- T3: cost is the helper's price; top_size picks the displayed queue -------


def test_t3_cost_and_top_size_from_the_row_book():
    long_r = row(
        series_id="S1", us_side="long",
        book={"ask_size": 7.5, "bid_size": 2.0},
        ts_ns=HOLD,
    )
    short_r = row(
        series_id="S1", us_side="short",
        book={"ask_size": 7.5, "bid_size": 3.0},
        ts_ns=HOLD + NS,
    )
    no_book = row(series_id="S2", us_side="long", book=None, ts_ns=HOLD)

    out = orders([long_r, short_r, no_book], {"S1": HOLD, "S2": HOLD})
    assert {o.us_side for o in out} == {"long", "short"}
    assert len(out) == 3
    for o in out:
        assert o.cost == mv.posted_price(o.row)
    long_o = next(o for o in out if o.series_id == "S1" and o.us_side == "long")
    short_o = next(o for o in out if o.series_id == "S1" and o.us_side == "short")
    assert long_o.top_size == 7.5  # long posts against ask_size
    assert short_o.top_size == 3.0  # short posts against bid_size
    no_book_o = next(o for o in out if o.series_id == "S2")
    assert no_book_o.top_size is None


# --- T4: short-side quote conversion + dead-book handling --------------------


def test_t4_short_quote_inverts_bid_and_rejects_dead_books():
    assert ep.our_side_quote("short", bid=0.62, ask=0.99) == (0.38, None)
    assert ep.our_side_quote("short", bid=0.62, ask=0.99, bid_size=4.0) == (0.38, 4.0)
    assert ep.our_side_quote("short", bid=None, ask=0.99) == (None, None)
    assert ep.our_side_quote("short", bid=0.0, ask=0.99) == (None, None)
    assert ep.our_side_quote("long", bid=0.1, ask=0.77, ask_size=9.0) == (0.77, 9.0)


# --- T5: observation window, sizes, dead rows, other-side, dup merge ----------


def test_t5_observations_window_merge_and_dead_rows():
    t0 = HOLD
    slug = "astatc-cs2-omg-td-2026-08-30-map1"
    order = Order(
        series_id="S1", map_number=1, us_side="long", slug=slug, t0_ns=t0,
        cost=0.74, team="TD", opponent="OMG", p_map=0.5, top_size=None, row={},
    )
    pq_long = row(  # same side, no sizes -> opp_size None
        type="postquote", slug=slug, us_side="long", src_ts_ns=t0,
        ts_ns=t0 + 30 * NS, bid=0.70, ask=0.75, mid=0.725,
    )
    pq_other = dict(pq_long, us_side="short", ts_ns=t0 + 45 * NS)  # ignored
    dup_pq = dict(pq_long, ts_ns=t0 + 35 * NS, bid=0.66, ask=0.70)
    dead = row(  # ask null -> opp_ask None but its time survives
        type="pmus_bbo", slug=slug, kind="match", bid=0.68, ask=None,
        req_ns=t0 + 60 * NS, ts_ns=t0 + 60 * NS + 1, bid_size=12.0, ask_size=None,
    )
    at_t0 = row(  # exactly t0 -> excluded (window is (t0, t0+ttl])
        type="pmus_bbo", slug=slug, kind="match", bid=0.72, ask=0.77,
        req_ns=t0, ts_ns=t0 + 5 * NS, bid_size=12.0, ask_size=30.0,
    )
    at_ttl = row(  # exactly t0+ttl -> included
        type="pmus_bbo", slug=slug, kind="match", bid=0.70, ask=0.77,
        req_ns=t0 + 150 * NS, ts_ns=t0 + 150 * NS + 1,
        bid_size=12.0, ask_size=30.0,
    )
    past_ttl = row(  # just past t0+ttl -> excluded
        type="pmus_bbo", slug=slug, kind="match", bid=0.70, ask=0.77,
        req_ns=t0 + 151 * NS, ts_ns=t0 + 151 * NS + 1, bid_size=12.0, ask_size=30.0,
    )
    dup_tape = row(  # same time as dup_pq; tape must win the merge
        type="pmus_bbo", slug=slug, kind="match", bid=0.66, ask=0.71,
        req_ns=t0 + 35 * NS, ts_ns=t0 + 35 * NS + 1, bid_size=5.0, ask_size=6.0,
    )
    pqs = [pq_long, pq_other, dup_pq]
    tapes = [dead, at_t0, at_ttl, past_ttl, dup_tape]

    obs = ep.observations(
        order,
        ep.index_postquotes(pqs),
        ep.index_tape(tapes),
    )
    assert [o.t_ns for o in obs] == [
        t0 + 30 * NS, t0 + 35 * NS, t0 + 60 * NS, t0 + 150 * NS,
    ]
    # +30s postquote survives with sizes None; the other side is never seen
    assert obs[0].opp_ask == 0.75 and obs[0].opp_size is None
    # +35s dup: the tape row wins (has sizes)
    assert obs[1].opp_ask == 0.71 and obs[1].opp_size == 6.0
    # +60s dead tape row: opp_ask None but its time is kept
    assert obs[2].opp_ask is None and obs[2].opp_size is None
    # +150s at exactly ttl is in with the new tape sizes
    assert obs[3].opp_ask == 0.77 and obs[3].opp_size == 30.0


# --- T6: short inverts the outcome, None stays None --------------------------


def test_t6_y_side_is_our_teams_outcome_on_both_sides_and_preserves_none():
    """A SHORT row's ``team`` is the team WE back (the No leg); p_map and the posted
    cost are already in that leg's terms, so the settlement is map_outcome for both
    sides. Inverting it for shorts would double-orient (review 2026-09-04)."""
    winners = {"S1:1": {"winner": "TD", "loser": "OMG"}}
    long_o = Order(
        series_id="S1", map_number=1, us_side="long", slug="s", t0_ns=HOLD,
        cost=0.74, team="TD", opponent="OMG", p_map=0.5, top_size=None,
        row=row(series_id="S1", map_number=1, us_side="long", team="TD"),
    )
    short_backing_winner = Order(
        series_id="S1", map_number=1, us_side="short", slug="s", t0_ns=HOLD,
        cost=0.26, team="TD", opponent="OMG", p_map=0.5, top_size=None,
        row=row(series_id="S1", map_number=1, us_side="short", team="TD"),
    )
    short_backing_loser = Order(
        series_id="S1", map_number=1, us_side="short", slug="s", t0_ns=HOLD,
        cost=0.26, team="OMG", opponent="TD", p_map=0.5, top_size=None,
        row=row(series_id="S1", map_number=1, us_side="short", team="OMG"),
    )
    unresolved = Order(
        series_id="S9", map_number=9, us_side="long", slug="s", t0_ns=HOLD,
        cost=0.5, team="TD", opponent="OMG", p_map=0.5, top_size=None,
        row=row(series_id="S9", map_number=9, us_side="long", team="TD"),
    )
    assert ep.y_side(long_o, winners) == 1.0
    assert ep.y_side(short_backing_winner, winners) == 1.0
    assert ep.y_side(short_backing_loser, winners) == 0.0
    assert ep.y_side(unresolved, winners) is None


# --- T7: a series absent from first_mapping is excluded, never guessed --------


def test_t7_missing_series_excluded_even_after_holdout():
    present = row(series_id="S1", ts_ns=HOLD)
    missing = row(series_id="S9", ts_ns=HOLD + 10 * NS)  # not in first_mapping
    out = orders([present, missing], {"S1": HOLD})
    assert len(out) == 1
    assert out[0].series_id == "S1"

def test_our_side_bid_and_observations_carry_own_bid():
    """EXEC1-AMEND1: every observation carries the best bid on OUR leg."""
    assert ep.our_side_bid("long", 0.57, 0.60) == 0.57
    assert ep.our_side_bid("short", 0.57, 0.60) == 0.40   # 1 - yes_ask
    assert ep.our_side_bid("short", 0.57, None) is None
    assert ep.our_side_bid("long", 0.0, 0.60) is None
    o = Order(series_id="S1", map_number=1, us_side="short", slug="s", t0_ns=HOLD,
              cost=0.40, team="TD", opponent="OMG", p_map=0.5, top_size=None,
              row=row(series_id="S1", map_number=1, us_side="short", team="TD", ts_ns=HOLD))
    pq = {HOLD: [{"type": "postquote", "us_side": "short", "bid": 0.57, "ask": 0.60,
                  "ts_ns": HOLD + 30 * 10**9}]}
    obs = ep.observations(o, pq, {})
    assert len(obs) == 1 and obs[0].own_bid == 0.40 and obs[0].opp_ask == 0.43
