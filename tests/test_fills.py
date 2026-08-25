"""Frozen maker-fill accounting semantics."""

from __future__ import annotations

from gridbot.instruments.fills import MakerQuote, infer_fills, maker_quote

NS = 1_000_000_000


def inst(bid: float | None = 0.80, ask: float | None = 0.84, ts: int = 100 * NS) -> dict:
    books = {}
    if bid is not None:
        books["tok"] = {"bid": bid, "ask": ask}
    return {"books": books, "grid_ts_ns": ts, "row_key": "k"}


def test_quote_at_bid_plus_tick_when_spread_allows() -> None:
    q = maker_quote(inst(), "tok")
    assert q is not None and q.price == 0.81 and q.after_ns == 100 * NS


def test_no_quote_on_tight_spread_or_missing_book() -> None:
    assert maker_quote(inst(bid=0.80, ask=0.81), "tok") is None  # bid+tick == ask
    assert maker_quote(inst(bid=None), "tok") is None
    assert maker_quote(inst(), None) is None


def _record_trade(ts: int, price: float) -> dict:
    return {"type": "ws", "ts_ns": ts,
            "raw": [{"event_type": "last_trade_price", "asset_id": "tok", "price": str(price)}]}


def _record_ask(ts: int, ask: float) -> dict:
    return {"type": "ws", "ts_ns": ts,
            "raw": {"price_changes": [{"asset_id": "tok", "best_ask": str(ask)}]}}


def test_fill_via_trade_print_at_or_below_quote() -> None:
    q = MakerQuote("k", "tok", 0.81, 100 * NS, 280 * NS)
    infer_fills([q], [_record_trade(150 * NS, 0.81)])
    assert q.filled and q.filled_ns == 150 * NS


def test_fill_via_ask_crossing_down() -> None:
    q = MakerQuote("k", "tok", 0.81, 100 * NS, 280 * NS)
    infer_fills([q], [_record_ask(120 * NS, 0.85), _record_ask(130 * NS, 0.81)])
    assert q.filled and q.filled_ns == 130 * NS


def test_no_fill_outside_window_or_above_quote() -> None:
    q = MakerQuote("k", "tok", 0.81, 100 * NS, 280 * NS)
    infer_fills([q], [
        _record_trade(90 * NS, 0.70),    # before the quote existed
        _record_trade(150 * NS, 0.82),   # above the quote
        _record_trade(300 * NS, 0.70),   # after the window
    ])
    assert not q.filled


def test_first_fill_wins() -> None:
    q = MakerQuote("k", "tok", 0.81, 100 * NS, 280 * NS)
    infer_fills([q], [_record_trade(150 * NS, 0.80), _record_trade(160 * NS, 0.75)])
    assert q.filled_ns == 150 * NS
