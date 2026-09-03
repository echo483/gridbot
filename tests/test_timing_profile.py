"""Tests for scripts/timing_profile.py rested re-cut (STATE queue 15 / NOTE7).

Hermetic: all day files are written under tmp_path and the module's DATA
constant is monkeypatched there; no real data is read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `scripts` is importable (mirror
# the sys.path shim in tests/test_timing_chain.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import timing_profile as tp

DAY = "20260827"
FILLED = "ORDER_STATE_FILLED"
CANCELED = "ORDER_STATE_CANCELED"
REJECTED = "ORDER_STATE_REJECTED"


def _write_intents(directory: Path, day: str, rows: list[dict]) -> None:
    (directory / "executor").mkdir(parents=True, exist_ok=True)
    (directory / "executor" / f"intents-{day}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))


def _write_settle(directory: Path, day: str, orders: list[dict]) -> None:
    (directory / "executor").mkdir(parents=True, exist_ok=True)
    (directory / "executor" / f"settle_truth-{day}.json").write_text(
        json.dumps({"day": day, "orders": orders}))


def _intent(order_id: str, side: str, status: str = "placed") -> dict:
    return {"type": "order_intent", "hypothesis_id": "H-MAPENTRY1",
            "series_id": "S", "map_number": 2, "round_number": 4, "team": "T",
            "price": 0.5, "shares": 5.0, "stake_usd": 2.5,
            "dedup_key": f"k:{order_id}", "market_slug": f"m{order_id}",
            "us_side": side, "status": status, "order_id": order_id,
            "p_map": 0.5, "ts_ns": 2_000_000_000_000, "live": True}


def _venue(order_id: str, state: str) -> dict:
    return {"order_id": order_id, "slug": f"m{order_id}", "us_side": "long",
            "ledger_price": 0.5, "ledger_qty": 5.0,
            "venue": {"state": state, "cumQuantity": 0}}


def _run_main(directory: Path, monkeypatch, capsys, day: str = DAY) -> str:
    monkeypatch.setattr(tp, "DATA", directory)
    monkeypatch.setattr(sys, "argv", ["timing_profile.py", day])
    tp.main()
    return capsys.readouterr().out


# ---- venue_states: resolution precedence -----------------------------------

def test_settle_truth_precedes_ledger_cancel():
    vs = tp.venue_states(
        [{"type": "cancel", "order_id": "O", "venue_state": REJECTED}],
        [{"orders": [_venue("O", CANCELED)]}])
    assert vs["O"] == CANCELED


def test_fill_check_venue_state_without_settle():
    vs = tp.venue_states(
        [{"type": "fill_check", "order_id": "O", "venue_state": REJECTED}], [])
    assert vs["O"] == REJECTED


def test_fill_implies_rested():
    vs = tp.venue_states([{"type": "fill", "order_id": "O"}], [])
    assert vs["O"] == FILLED


def test_unknown_when_no_source():
    assert tp.venue_states([{"type": "order_intent", "order_id": "O"}], []) == {}


def test_later_settle_doc_wins_for_repeated_order_id():
    vs = tp.venue_states([], [{"orders": [_venue("O", CANCELED)]},
                              {"orders": [_venue("O", FILLED)]}])
    assert vs["O"] == FILLED


def test_order_ids_are_string_keys():
    vs = tp.venue_states([], [{"orders": [{"order_id": 7, "venue": {"state": REJECTED}}]}])
    assert "7" in vs and vs["7"] == REJECTED


# ---- full main() re-cut output ---------------------------------------------

def test_rejected_excluded_from_rested_and_lifetimes(
        tmp_path, monkeypatch, capsys):
    d = tmp_path
    # Rejected order carries a distinctive TTL-artifact age that must NOT
    # appear in the rested lifetime list.
    _write_intents(d, DAY, [
        _intent("O_REJ", "long"),
        {"type": "cancel", "order_id": "O_REJ", "age_s": 999.9,
         "status": "cancelled", "source": "venue_terminal_no_fill",
         "ts_ns": 1},
        _intent("O_FILL", "long"),
        {"type": "cancel", "order_id": "O_FILL", "age_s": 100.0,
         "status": "cancelled", "source": "known_filled", "ts_ns": 1},
        {"type": "fill", "order_id": "O_FILL", "shares": 5.0, "price": 0.5,
         "partial": False, "source": "venue_order", "ts_ns": 1},
        _intent("O_CXL", "short"),
        {"type": "cancel", "order_id": "O_CXL", "age_s": 200.0,
         "status": "cancelled", "source": "venue_terminal_no_fill",
         "ts_ns": 1},
        _intent("O_UNK", "short"),
    ])
    _write_settle(d, DAY, [
        _venue("O_REJ", REJECTED), _venue("O_FILL", FILLED),
        _venue("O_CXL", CANCELED)])  # O_UNK unknown
    out = _run_main(d, monkeypatch, capsys)
    # overall: placed 4, rested 2 (O_FILL, O_CXL), rejected 1, unknown 1,
    # filled 1
    assert "overall: placed 4 | rested 2 | rejected 1 | unknown 1" in out
    assert "filled 1 | fill@rested 50.0%" in out
    # rejected age (999.9) excluded from the RESTED lifetime list — only the
    # two resting orders' ages appear, with n=2 (the per-intent line above
    # still shows every order's own ttl, rejected included, per spec).
    assert "quote lifetime s among rested (n=2): [100, 200]" in out
    # per-side split: long rested 1 + filled 1; short rested 1 + unknown 1,
    # no rejected short
    assert "long: placed 2 | rested 1 | rejected 1 | unknown 0" in out
    assert "short: placed 2 | rested 1 | rejected 0 | unknown 1" in out


def test_dry_run_excluded(tmp_path, monkeypatch, capsys):
    d = tmp_path
    _write_intents(d, DAY, [
        _intent("O1", "long"),                      # placed -> counts
        _intent("O_DRY", "long", status="dry_run"),  # dry_run -> does not rest
    ])
    _write_settle(d, DAY, [_venue("O1", FILLED), _venue("O_DRY", FILLED)])
    out = _run_main(d, monkeypatch, capsys)
    assert "overall: placed 1 | rested 1 | rejected 0 | unknown 0" in out


def test_unknown_not_counted_as_rested_or_rejected(tmp_path, monkeypatch, capsys):
    d = tmp_path
    _write_intents(d, DAY, [
        _intent("O_UNK", "long"),      # no settle, no fill, no cancel -> unknown
        _intent("O_CXL", "long"),
        {"type": "cancel", "order_id": "O_CXL", "age_s": 150.0,
         "status": "cancelled", "ts_ns": 1},
    ])
    _write_settle(d, DAY, [_venue("O_CXL", CANCELED)])
    out = _run_main(d, monkeypatch, capsys)
    # enabled: rejected 0 with a genuine unknown bucket of 1
    assert "overall: placed 2 | rested 1 | rejected 0 | unknown 1" in out
    assert "filled 0 | fill@rested " in out


def test_no_placed_intents_prints_zeros(tmp_path, monkeypatch, capsys):
    d = tmp_path
    _write_intents(d, DAY, [
        _intent("O_DRY", "long", status="dry_run"),
        {"type": "cancel", "order_id": "X", "age_s": 150.0, "status": "cancelled",
         "ts_ns": 1},
    ])
    out = _run_main(d, monkeypatch, capsys)
    assert "no placed intents for this day" in out
    assert "placed 0 | rested 0 | rejected 0 | unknown 0 | filled 0" in out
