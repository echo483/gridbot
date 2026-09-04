"""Tests for the H-MAPENTRY1-EXEC1 calibration fixture (exec_fixture).

Each test encodes a trap from the build brief. Row/state fixtures mirror the real
armed-night capture shapes (intents/jsonl, shadow/mapentry/jsonl,
settle_truth/json) so the plumbing is exercised against the actual field layout.
"""

from __future__ import annotations

import json

from gridbot.analysis import exec_fixture as ef


def intent(**over):
    """A real-shaped, placed order_intent row (verbatim field set from capture)."""
    base = {
        "type": "order_intent",
        "hypothesis_id": "H-MAPENTRY1",
        "series_id": "2998320",
        "map_number": 2,
        "round_number": 4,
        "team": "New Legends",
        "price": 0.6,
        "shares": 5.0,
        "stake_usd": 3.0,
        "dedup_key": "H-MAPENTRY1:2998320:2:long:slug-map2",
        "market_slug": "slug",
        "us_side": "long",
        "status": "placed",
        "order_id": "C46M6M44MFSJ",
        "p_map": 0.7773,
        "ts_ns": 1787795215252966025,
        "live": True,
    }
    base.update(over)
    return base


def _a(actual, sid):
    """The Actual for the (single) order of series ``sid``; keys are (sid, map, side, t0_ns)."""
    hits = [v for k, v in actual.items() if k[0] == sid]
    assert len(hits) <= 1
    return hits[0] if hits else None


def mrow(**over):
    """A real-shaped mapentry row (uses the exec_population field set + book)."""
    base = {
        "type": "mapentry",
        "hypothesis_id": "H-MAPENTRY1",
        "series_id": "2998320",
        "map_number": 2,
        "round_number": 4,
        "team": "New Legends",
        "opponent": "Astralis",
        "p_map": 0.7773,
        "market_slug": "slug",
        "us_side": "long",
        "bid": 0.57,
        "ask": 0.60,
        "side_ask": 0.43,
        "entry_price_max": 0.60,
        "ts_ns": 1787795000000000,  # earlier than the intent ts_ns; NOT used as t0
        "book": {"bid_size": 7, "ask_size": 10},
    }
    base.update(over)
    return base


def settle_doc(**states):
    """A settle_truth doc; kwargs give order_id -> venue state strings."""
    return {
        "day": "20260826",
        "pulled_at": "2026-08-26T23:59:00Z",
        "orders": [
            {
                "order_id": oid,
                "slug": "slug",
                "us_side": "long",
                "ledger_price": 0.5,
                "ledger_qty": 5.0,
                "venue": {
                    "state": st,
                    "price": {"value": "0.5", "currency": "USD"},
                    "quantity": 5,
                },
            }
            for oid, st in states.items()
        ],
    }


FILL_N_REQ = {"type": "fill", "market_slug": "slug", "price": 0.42, "shares": 7.0}
KEY = ("S1", 2, "long")


# --- T1: only status=="placed" with a non-null order_id becomes an Order.


def test_t1_only_placed_with_orderid_becomes_order():
    placed = intent(series_id="S1", order_id="O1", market_slug="sl1")
    dry = intent(series_id="S2", order_id="O2", market_slug="sl2", status="dry_run")
    band = intent(
        series_id="S3", order_id="O3", market_slug="sl3", status="skipped:band_gate"
    )
    cap = intent(series_id="S4", order_id="O4", market_slug="sl4", status="skipped:capacity")
    wire = intent(series_id="S5", order_id="O5", market_slug="sl5", status="wire_pending")
    noid = intent(series_id="S6", order_id=None, market_slug="sl6")

    rows = [
        mrow(series_id=s, map_number=2, round_number=4, us_side="long", market_slug=sl)
        for s, sl in (("S1", "sl1"), ("S2", "sl2"), ("S3", "sl3"),
                      ("S4", "sl4"), ("S5", "sl5"), ("S6", "sl6"))
    ]
    ints = [placed, dry, band, cap, wire, noid]
    orders, actual, counts = ef.fixture_orders(ints, rows, [])

    assert len(orders) == 1
    assert orders[0].slug == "sl1"  # only the real placed order survived
    assert counts == {
        "placed": 1, "joined": 1, "unjoined": 0,
        "unknown_state": 1, "rejected": 0, "rested": 0, "filled": 0,
    }
    assert actual == {}


# --- T2: SHORT cost = 1 - price (NOTE7); LONG cost = price.


def test_t2_short_cost_is_one_minus_price():
    long_o = intent(us_side="long", price=0.6, series_id="S1", market_slug="sl1")
    short_o = intent(us_side="short", price=0.43, series_id="S1", market_slug="sl2")
    rows = [
        mrow(series_id="S1", us_side="long", market_slug="sl1", round_number=4),
        mrow(series_id="S1", us_side="short", market_slug="sl2", round_number=4),
    ]
    orders, _, _ = ef.fixture_orders([long_o, short_o], rows, [])
    by_slug = {o.slug: o.cost for o in orders}
    assert by_slug == {"sl1": 0.6, "sl2": round(1.0 - 0.43, 4)}
    assert by_slug["sl2"] == 0.57


# --- T3: venue-state / fill semantics.


def test_t3_states_and_fill_semantics():
    ints = [
        intent(series_id="S1", order_id="O1", market_slug="sl1"),
        intent(series_id="S2", order_id="O2", market_slug="sl2"),
        intent(series_id="S3", order_id="O3", market_slug="sl3"),
        intent(series_id="S4", order_id="O4", market_slug="sl4"),
        dict(FILL_N_REQ, order_id="O3", market_slug="sl3", ts_ns=781_795_744),  # fill
    ]
    rows = [
        mrow(series_id=s, us_side="long", round_number=4, market_slug=sl)
        for s, sl in (("S1", "sl1"), ("S2", "sl2"), ("S3", "sl3"), ("S4", "sl4"))
    ]
    d = settle_doc(
        O1="ORDER_STATE_REJECTED",
        O2="ORDER_STATE_CANCELED",
        O4="ORDER_STATE_FILLED",
    )
    orders, actual, counts = ef.fixture_orders(ints, rows, [d])

    assert len(orders) == 4
    a1 = _a(actual, "S1")
    assert a1.rested is False and a1.filled is False and a1.state == "ORDER_STATE_REJECTED"
    a2 = _a(actual, "S2")
    assert a2.rested is True and a2.filled is False and a2.t_fill_ns is None
    a3 = _a(actual, "S3")  # fill row -> filled with its ts_ns
    assert a3.rested is True and a3.filled is True and a3.t_fill_ns == 781_795_744
    a4 = _a(actual, "S4")  # FILLED state, no fill row -> t_fill_ns None
    assert a4.rested is True and a4.filled is True and a4.t_fill_ns is None

    assert counts["rejected"] == 1 and counts["rested"] == 3 and counts["filled"] == 2

    # actual_dicts drops the order_id/state and keeps the runner's input shape.
    # keys are (series, map, side, t0_ns); compare on the first three parts here
    assert {k[:3]: v for k, v in ef.actual_dicts(actual).items()} == {
        ("S1", 2, "long"): {"rested": False, "filled": False, "t_fill_ns": None, "one_sided": False},
        ("S2", 2, "long"): {"rested": True, "filled": False, "t_fill_ns": None, "one_sided": False},
        ("S3", 2, "long"): {"rested": True, "filled": True, "t_fill_ns": 781_795_744, "one_sided": False},
        ("S4", 2, "long"): {"rested": True, "filled": True, "t_fill_ns": None, "one_sided": False},
    }


# --- T4: state None -> excluded from actual, counted unknown_state, Order kept.


def test_t4_unknown_state_excluded_from_actual():
    i1 = intent(series_id="S1", order_id="O1")
    orders, actual, counts = ef.fixture_orders([i1], [mrow(series_id="S1")], [])
    assert len(orders) == 1
    assert _a(actual, "S1") is None
    assert counts["unknown_state"] == 1

# --- T5: join uses all five keys; unjoined intents are counted.


def test_t5_join_uses_all_five_keys():
    i = intent(series_id="S1", map_number=2, round_number=4, market_slug="sl1")
    wrong = mrow(series_id="S1", map_number=2, round_number=5, market_slug="sl1", ts_ns=0)
    correct = mrow(series_id="S1", map_number=2, round_number=4, market_slug="sl1")
    i_unjoined = intent(series_id="S2", round_number=9, market_slug="sl2", order_id="OX")

    orders, _, counts = ef.fixture_orders([i, i_unjoined], [wrong, correct], [])

    assert len(orders) == 1
    assert orders[0].slug == "sl1"
    assert orders[0].row["round_number"] == 4  # the FIVE-key join picked the right row
    assert counts == {
        "placed": 2, "joined": 1, "unjoined": 1,
        "unknown_state": 1, "rejected": 0, "rested": 0, "filled": 0,
    }


# --- T6: one_sided from the joined row; top_size long/short/None.


def test_t6_one_sided_and_topsize():
    ints = [
        intent(us_side="long", series_id="S1", market_slug="sl1", order_id="O1"),
        intent(us_side="long", series_id="S2", market_slug="sl2", order_id="O2"),
        intent(us_side="short", series_id="S3", market_slug="sl3", order_id="O3"),
        intent(us_side="long", series_id="S4", market_slug="sl4", order_id="O4"),
    ]
    rows = [
        mrow(series_id="S1", market_slug="sl1", us_side="long", bid=0.57, ask=0.60,
             book={"bid_size": 7, "ask_size": 10}),
        mrow(series_id="S2", market_slug="sl2", us_side="long", bid=0.57, ask=None,
             book={"bid_size": 7, "ask_size": 10}),
        mrow(series_id="S3", market_slug="sl3", us_side="short", bid=0.57, ask=0.60,
             book={"bid_size": 7, "ask_size": 10}),
        mrow(series_id="S4", market_slug="sl4", us_side="long", bid=0.57, ask=0.60,
             book=None),
    ]
    d = settle_doc(
        O1="ORDER_STATE_CANCELED", O2="ORDER_STATE_CANCELED",
        O3="ORDER_STATE_CANCELED", O4="ORDER_STATE_CANCELED",
    )
    orders, actual, _ = ef.fixture_orders(ints, rows, [d])

    assert _a(actual, "S1").one_sided is False
    assert _a(actual, "S2").one_sided is True  # ask None
    top = {o.slug: o.top_size for o in orders}
    assert top == {"sl1": 10, "sl2": 10, "sl3": 7, "sl4": None}  # long/short/None


# --- T7: load_day_files skips a torn final line and a missing settle_truth file.


def test_t7_load_day_files_is_tolerant(tmp_path):
    exec_dir = tmp_path / "executor"
    exec_dir.mkdir(parents=True)
    (exec_dir / "intents-20260826.jsonl").write_text(
        '{"type":"order_intent","order_id":"A"}\n{"torn final line\n'
    )
    sh_dir = tmp_path / "shadow"
    sh_dir.mkdir(parents=True)
    (sh_dir / "mapentry-20260826.jsonl").write_text('{"type":"mapentry","series_id":"S1"}\n')
    # day 27 contributes a settle doc; day 26 has none (missing file is skipped).
    (exec_dir / "settle_truth-20260827.json").write_text(json.dumps({"day": "20260827"}))

    intents, rows, settles = ef.load_day_files(tmp_path, ("20260826", "20260827"))

    assert len(intents) == 1 and intents[0]["order_id"] == "A"  # torn line skipped
    assert len(rows) == 1
    assert len(settles) == 1 and settles[0]["day"] == "20260827"  # missing 26 skipped