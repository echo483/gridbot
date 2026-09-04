"""Tests for gridbot.analysis.exec_replay (H-MAPENTRY1-EXEC1 runner)."""

from __future__ import annotations

import pytest

from gridbot.analysis import exec_fills as ef
from gridbot.analysis import exec_replay as er
from gridbot.analysis.exec_population import Order
from gridbot.analysis.exec_replay import Replayed

NS = 1_000_000_000
HOLD = 1_788_000_000_000_000_000  # order t0; observations land after it
SLUG = "astatc-cs2-omg-td-2026-08-30-map2"


def mk_order(sid="S1", us_side="long", map_number=1, cost=0.60,
             top_size=None, team="TD", opponent="OMG") -> Order:
    row = {"series_id": sid, "map_number": map_number, "team": team,
           "opponent": opponent}
    return Order(
        series_id=sid, map_number=map_number, us_side=us_side, slug=SLUG,
        t0_ns=HOLD, cost=cost, team=team, opponent=opponent, p_map=0.5,
        top_size=top_size, row=row,
    )


def mk_fill(filled: bool, qty=0.0, reason="filled", t=None) -> ef.Fill:
    return ef.Fill(filled=filled, t_fill_ns=t, qty=qty, touch_idx=None, reason=reason)


def replayed(order, lower, upper, regime="sized", y: float | None = 1.0,
             nobs=2) -> Replayed:
    return Replayed(order=order, lower=lower, upper=upper, regime=regime,
                    y=y, n_obs=nobs)


def _tap(row):  # shorthand for a long-side tape row driven by ask
    return {"type": "pmus_bbo", "slug": SLUG, "req_ns": row[0],
            "bid": 0.40, "ask": row[1], "bid_size": 1.0,
            "ask_size": row[2]}


def _pq(t_ns, ask):  # long-side postquote row (no sizes -> opp_size None)
    return {"type": "postquote", "us_side": "long", "src_ts_ns": HOLD,
            "ts_ns": t_ns, "bid": 0.40, "ask": ask}


# --- T1: regime chooses the right lower rule ----------------------------------


def test_t1_unsized_order_uses_lower_unsized_credits_one():
    # only postquote rows (no sizes) -> "unsized", LOWER_UNSIZED fills exactly 1.0
    order = mk_order(cost=0.60)
    pq = [_pq(HOLD + 1 * NS, 0.55), _pq(HOLD + 2 * NS, 0.55)]
    r = er.replay_orders([order], {HOLD: pq}, {}, {}, clip_qty=25.0)[0]
    assert r.regime == "unsized"
    assert r.lower.filled and r.lower.qty == 1.0
    assert r.upper.filled


def test_t1_sized_tape_row_uses_lower_sized():
    # a sized tape path is "sized" and LOWER sizes the fill beyond one contract
    order = mk_order(cost=0.60)
    tape = [_tap([HOLD + 1 * NS, 0.55, 5.0]), _tap([HOLD + 2 * NS, 0.55, 12.0])]
    r = er.replay_orders([order], {}, {SLUG: tape}, {}, clip_qty=25.0)[0]
    assert r.regime == "sized"
    # behind_displayed, queue_ahead=None -> eff_queue = window[0].opp_size = 5,
    # confirming obs size 12 -> qty = min(25, 12-5) = 7.0
    assert r.lower.filled and r.lower.qty == 7.0
    # upper (front): credits at the FIRST touch's displayed size = 5.0
    assert r.upper.filled and r.upper.qty == 5.0


# --- T2: invariant holds (upper fills at-touch, lower does not) ---------------


def test_t2_invariant_ok_true_when_upper_fills_not_lower():
    order = mk_order(cost=0.60)
    obs = [ef.Obs(t_ns=HOLD + 1 * NS, opp_ask=0.60, opp_size=10.0)]  # at-P touch
    lower = ef.simulate(ef.LOWER, 0.60, 25.0, HOLD, obs)
    upper = ef.simulate(ef.UPPER, 0.60, 25.0, HOLD, obs)
    assert not lower.filled and upper.filled
    summary = er.summarize([replayed(order, lower, upper)])
    assert summary["invariant_ok"] is True


# --- T3: pnl_rows / unresolved fills ------------------------------------------


def test_t3_pnl_rows_and_unresolved_count():
    r_long = replayed(mk_order(sid="S1", cost=0.60),
                      mk_fill(True, qty=1.0), mk_fill(True, qty=1.0), y=1.0)
    r_short = replayed(mk_order(sid="S2", map_number=2, cost=0.43),
                       mk_fill(True, qty=1.0), mk_fill(True, qty=1.0), y=0.0)
    r_un = replayed(mk_order(sid="S3", map_number=3),
                    mk_fill(True, qty=1.0), mk_fill(True, qty=1.0), y=None)

    rows = er.pnl_rows([r_long, r_short, r_un], "lower")
    assert rows == [("S1", 0.40, 1.0), ("S2", -0.43, 1.0)]

    summary = er.summarize([r_long, r_short, r_un])
    lower = summary["bound"]["lower"]
    assert lower["n_filled"] == 3
    assert lower["n_filled_resolved"] == 2
    assert lower["n_unresolved_fills"] == 1


# --- T4: summarize mean_pnl / ci95 / sensitivity -----------------------------


def test_t4_mean_pnl_weighted_and_ci95_single_series_none():
    o1 = mk_order(sid="S1", map_number=1, cost=0.60)
    o2 = mk_order(sid="S1", map_number=2, cost=0.60)  # same series -> one cluster
    r1 = replayed(o1, mk_fill(True, qty=1.0), mk_fill(True, qty=1.0), y=0.70)  # +0.10
    r2 = replayed(o2, mk_fill(True, qty=3.0), mk_fill(True, qty=3.0), y=0.50)  # -0.10
    summary = er.summarize([r1, r2])
    lower = summary["bound"]["lower"]
    # (1*0.1 + 3*-0.1) / 4 = -0.2/4 = -0.05
    assert lower["mean_pnl"] == pytest.approx(-0.05)
    assert lower["ci95"] is None  # single series -> bootstrap returns None ci95
    assert lower["n_series"] == 1
    assert lower["contracts"] == 4.0


def test_t4_ci95_tuple_with_two_series():
    o1 = mk_order(sid="S1", cost=0.60)
    o2 = mk_order(sid="S2", cost=0.45)
    r1 = replayed(o1, mk_fill(True, qty=1.0), mk_fill(True, qty=1.0), y=1.0)
    r2 = replayed(o2, mk_fill(True, qty=1.0), mk_fill(True, qty=1.0), y=0.0)
    lower = er.summarize([r1, r2])["bound"]["lower"]
    assert lower["ci95"] is not None and len(lower["ci95"]) == 2
    assert lower["n_series"] == 2


# --- T5: calibration confusion / recall by one-sidedness ---------------------


def _calib_fixture():
    """(orders, actual): 4 rested with real fills on S1,S2; one unrested order S5."""
    orders = [mk_order(sid=s, map_number=1, us_side="long")
              for s in ("S1", "S2", "S3", "S4", "S5")]
    truth = {
        "S1": {"rested": True, "filled": True, "t_fill_ns": HOLD + 5 * NS, "one_sided": True},
        "S2": {"rested": True, "filled": True, "t_fill_ns": HOLD + 8 * NS, "one_sided": False},
        "S3": {"rested": True, "filled": False, "t_fill_ns": None, "one_sided": False},
        "S4": {"rested": True, "filled": False, "t_fill_ns": None, "one_sided": False},
        "S5": {"rested": False, "filled": True, "t_fill_ns": HOLD + 9 * NS, "one_sided": False},
    }
    # keyed by order_key(order) = (series, map, side, t0_ns): re-entries stay distinct
    actual = {er.order_key(o): truth[o.series_id] for o in orders}
    return orders, actual


def test_t5_perfect_prediction():
    orders, actual = _calib_fixture()
    rep = [replayed(o, mk_fill(o.series_id in ("S1", "S2"), qty=1.0),
                    mk_fill(o.series_id in ("S1", "S2"), qty=1.0))
           for o in orders]
    cal = er.calibration(rep, actual)
    assert cal["n_rested"] == 4  # unrested S5 excluded
    assert cal["n_actual_fills"] == 2
    lower = cal["lower"]
    assert lower["tp"] == 2 and lower["fp"] == 0 and lower["fn"] == 0 and lower["tn"] == 2
    assert lower["precision"] == 1.0 and lower["recall"] == 1.0
    # one-sided split: S1 (one_sided) and S2 (two_sided) each predicted -> recall 1.0
    assert lower["recall_by_one_sidedness"]["one_sided"] == 1.0
    assert lower["recall_by_one_sidedness"]["two_sided"] == 1.0


def test_t5_misclassification():
    orders, actual = _calib_fixture()
    # upper predicts fills for {S1, S3}; actual fills {S1, S2} -> tp=1 fp=1 fn=1 tn=1
    rep = [replayed(o, mk_fill(True, qty=1.0),
                    mk_fill(o.series_id in ("S1", "S3"), qty=1.0))
           for o in orders]
    cal = er.calibration(rep, actual)
    upper = cal["upper"]
    assert upper["tp"] == 1 and upper["fp"] == 1 and upper["fn"] == 1 and upper["tn"] == 1
    assert upper["precision"] == 0.5 and upper["recall"] == 0.5
    # S1 (one_sided) predicted, S2 (two_sided) not -> 1.0 vs 0.0
    assert upper["recall_by_one_sidedness"]["one_sided"] == 1.0
    assert upper["recall_by_one_sidedness"]["two_sided"] == 0.0


# --- T6: render never says PASS/FAIL, carries label + n= ----------------------


def test_t6_render_contains_label_nex_and_no_pass_fail():
    r1 = replayed(mk_order(sid="S1", cost=0.60), mk_fill(True, qty=1.0),
                  mk_fill(True, qty=1.0), y=1.0)
    r2 = replayed(mk_order(sid="S2", map_number=2, cost=0.45), mk_fill(True, qty=3.0),
                  mk_fill(True, qty=3.0), y=0.0)
    summary = er.summarize([r1, r2])
    text = er.render(summary, "H-MAPENTRY1-EXEC1")
    assert "H-MAPENTRY1-EXEC1" in text
    assert "n_orders=" in text and "n_series=" in text
    assert "sized=" in text and "unsized=" in text  # regime mix present
    assert "PASS" not in text and "FAIL" not in text


# --- T7: replay_orders passes order.top_size as queue_ahead -------------------


def test_t7_top_size_is_queue_ahead():
    # sized case: queue 10 at detection (top_size) + confirming obs size 12 -> qty 2.0
    order = mk_order(cost=0.60, top_size=10.0)
    tape = [_tap([HOLD + 1 * NS, 0.55, 12.0]), _tap([HOLD + 2 * NS, 0.55, 12.0])]
    r = er.replay_orders([order], {}, {SLUG: tape}, {}, clip_qty=25.0)[0]
    assert r.regime == "sized"
    # behind_displayed with queue_ahead=top_size=10, confirming size 12 -> min(25,2)=2.0
    assert r.lower.filled and r.lower.qty == 2.0