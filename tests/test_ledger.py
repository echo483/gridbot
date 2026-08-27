"""Tests for gridbot.executor.ledger — pure accounting over intent ledger."""

from __future__ import annotations

from datetime import UTC, datetime

from gridbot.executor.ledger import (
    daily_loss_usd,
    load_records,
    open_positions,
    realized_pnl,
    summarize,
)


def ns(y, m, d, h=0, minute=0):
    """Epoch-ns for a UTC wall time."""
    return int(datetime(y, m, d, h, minute, tzinfo=UTC).timestamp() * 1_000_000_000)


def intent(key, price=0.50, shares=10.0, status="placed", ts=None):
    return {
        "type": "order_intent",
        "hypothesis_id": "H-MODELENTRY1",
        "series_id": "s1",
        "map_number": 1,
        "round_number": 5,
        "market_condition_id": "c1",
        "token_id": "t1",
        "team": "A",
        "price": price,
        "shares": shares,
        "stake_usd": round(price * shares, 2),
        "dedup_key": key,
        "status": status,
        "ts_ns": ts or ns(2026, 8, 25),
        "live": status == "placed",
    }


def fill(key, price, shares, ts=None):
    return {"type": "fill", "dedup_key": key, "price": price, "shares": shares,
            "ts_ns": ts or ns(2026, 8, 25, 0, 1)}


def settle(key, won, ts):
    return {"type": "settle", "dedup_key": key, "won": won, "ts_ns": ts}


def test_empty_ledger_gives_zeros():
    records: list[dict] = []
    assert open_positions(records) == []
    assert realized_pnl(records) == []
    assert daily_loss_usd(records, "20260825") == 0.0
    assert summarize(records) == {
        "intents": 0, "placed": 0, "filled": 0, "settled": 0,
        "open": 0, "realized_pnl_total": 0.0,
    }


def test_open_position_requires_fill_and_no_settle():
    records = [intent("k1"), fill("k1", 0.51, 9.8)]
    positions = open_positions(records)
    assert len(positions) == 1
    assert positions[0]["dedup_key"] == "k1"
    assert positions[0]["price"] == 0.51
    assert positions[0]["shares"] == 9.8
    # settled -> no longer open
    records.append(settle("k1", True, ns(2026, 8, 25, 1)))
    assert open_positions(records) == []


def test_placed_without_fill_not_open():
    records = [intent("k1"), settle("k1", True, ns(2026, 8, 25, 1))]
    assert open_positions(records) == []
    # also: placed intent with no fill at all
    assert open_positions([intent("k2")]) == []


def test_unmatched_fill_ignored():
    records = [fill("ghost", 0.40, 5.0)]
    assert open_positions(records) == []
    assert realized_pnl(records) == []
    assert summarize(records)["filled"] == 0


def test_duplicate_fill_first_wins():
    records = [
        intent("k1"),
        fill("k1", 0.50, 10.0),
        fill("k1", 0.70, 3.0),  # later duplicate ignored
    ]
    positions = open_positions(records)
    assert len(positions) == 1
    assert positions[0]["price"] == 0.50
    assert positions[0]["shares"] == 10.0


def test_pnl_signs_both_ways():
    records = [
        intent("win", price=0.40, shares=10.0),
        fill("win", 0.40, 10.0),
        settle("win", True, ns(2026, 8, 25, 1)),
        intent("lose", price=0.40, shares=10.0),
        fill("lose", 0.40, 10.0),
        settle("lose", False, ns(2026, 8, 25, 2)),
    ]
    pnl = {k: p for k, p, _ in realized_pnl(records)}
    assert pnl["win"] == (1.0 - 0.40) * 10.0  # +6.0
    assert pnl["lose"] == -(0.40 * 10.0)      # -4.0
    assert summarize(records)["realized_pnl_total"] == 2.0


def test_pnl_uses_intent_price_when_no_fill():
    records = [
        intent("k1", price=0.30, shares=100.0),
        settle("k1", False, ns(2026, 8, 25, 3)),
    ]
    pnl = {k: p for k, p, _ in realized_pnl(records)}
    assert pnl["k1"] == -30.0


def test_daily_loss_utc_windowing():
    # loss at 2026-08-25 23:59 UTC vs win at 2026-08-26 00:01 UTC
    records = [
        intent("a", price=0.50, shares=10.0),
        fill("a", 0.50, 10.0),
        settle("a", False, ns(2026, 8, 25, 23, 59)),  # -5.0
        intent("b", price=0.50, shares=10.0),
        fill("b", 0.50, 10.0),
        settle("b", True, ns(2026, 8, 26, 0, 1)),    # +5.0
    ]
    assert daily_loss_usd(records, "20260825") == 5.0
    assert daily_loss_usd(records, "20260826") == 0.0  # gain floored
    assert daily_loss_usd(records, "20260827") == 0.0


def test_daily_loss_floored_at_zero_when_net_positive():
    records = [
        intent("a", price=0.10, shares=10.0),
        fill("a", 0.10, 10.0),
        settle("a", True, ns(2026, 8, 25, 1)),  # +9.0
        intent("b", price=0.10, shares=10.0),
        fill("b", 0.10, 10.0),
        settle("b", False, ns(2026, 8, 25, 2)),  # -1.0
    ]
    assert daily_loss_usd(records, "20260825") == 0.0


def test_non_placed_intents_excluded():
    records = [
        intent("dry", status="dry_run"),
        fill("dry", 0.5, 10.0),
        intent("rej", status="rejected:ceiling"),
        settle("rej", True, ns(2026, 8, 25, 1)),
        intent("err", status="error:timeout"),
        intent("skip", status="skipped_max_open"),
    ]
    assert open_positions(records) == []
    assert realized_pnl(records) == []
    s = summarize(records)
    assert s["intents"] == 4
    assert s["placed"] == 0


def test_unknown_types_and_missing_fields_skipped():
    records = [
        {"type": "weird", "dedup_key": "x"},
        "not a dict",
        {"type": "fill"},  # missing dedup_key
        {"type": "settle", "dedup_key": "k", "won": True},  # missing ts_ns ok
        {"type": "order_intent", "dedup_key": "k", "status": "placed"},  # no price fields
        intent("ok"),
        fill("ok", "bad", 5),  # corrupt fill row (non-numeric price) ignored WHOLE
        settle("ok", True, ns(2026, 8, 25, 1)),
    ]
    # 'ok' settles won: corrupt fill falls back to the intent's price AND
    # shares (mixed-source price/shares was arbitrary; whole-row fallback
    # since the multi-fill aggregation rework).
    pnl = {k: p for k, p, _ in realized_pnl(records)}
    assert pnl["ok"] == (1.0 - 0.50) * 10.0


def test_load_records_reads_and_skips_bad_lines(tmp_path):
    d = tmp_path / "executor"
    d.mkdir()
    (d / "intents-20260825.jsonl").write_text(
        '{"type":"order_intent","dedup_key":"a","status":"placed",'
        '"price":0.5,"shares":2,"ts_ns":1,"live":true}\n'
        "garbage line\n"
        "\n"
        '{"type":"fill","dedup_key":"a","price":0.5,"shares":2,"ts_ns":2}\n'
        "[1,2,3]\n",
        encoding="utf-8",
    )
    (d / "intents-20260824.jsonl").write_text(
        '{"type":"order_intent","dedup_key":"b","status":"placed",'
        '"price":0.4,"shares":1,"ts_ns":3,"live":true}\n',
        encoding="utf-8",
    )
    (d / "other.txt").write_text("ignore me\n", encoding="utf-8")
    records = load_records(d)
    assert len(records) == 3  # skips garbage, blank, and the JSON array
    assert summarize(records)["placed"] == 2


def test_load_records_missing_dir(tmp_path):
    assert load_records(tmp_path / "nope") == []


def test_summarize_counts():
    records = [
        intent("a"), fill("a", 0.5, 10.0), settle("a", True, ns(2026, 8, 25, 1)),
        intent("b"), fill("b", 0.5, 10.0),  # open
        intent("c"),                        # placed, unfilled
        intent("d", status="dry_run"),
    ]
    s = summarize(records)
    assert s == {
        "intents": 4,
        "placed": 3,
        "filled": 2,
        "settled": 1,
        "open": 1,
        "realized_pnl_total": (1.0 - 0.5) * 10.0,
    }


class TestOpenExposure:
    def test_placed_without_settle_counts(self) -> None:
        from gridbot.executor.ledger import open_exposure

        records = [
            {"type": "order_intent", "status": "placed", "dedup_key": "a", "ts_ns": 1},
            {"type": "order_intent", "status": "placed", "dedup_key": "b", "ts_ns": 2},
            {"type": "order_intent", "status": "dry_run", "dedup_key": "c", "ts_ns": 3},
            {"type": "settle", "dedup_key": "a", "won": True, "ts_ns": 4},
        ]
        # "a" settled, "b" still resting, "c" never placed.
        assert open_exposure(records) == 1

    def test_empty_is_zero(self) -> None:
        from gridbot.executor.ledger import open_exposure

        assert open_exposure([]) == 0

    def test_clean_cancel_releases_only_unfilled(self) -> None:
        from gridbot.executor.ledger import open_exposure

        records = [
            {"type": "order_intent", "status": "placed", "dedup_key": "a",
             "order_id": "A1", "ts_ns": 1},
            {"type": "order_intent", "status": "placed", "dedup_key": "b",
             "order_id": "B1", "ts_ns": 2},
            {"type": "cancel", "status": "cancelled", "dedup_key": "a",
             "order_id": "A1", "ts_ns": 3},
            {"type": "cancel", "status": "cancelled", "dedup_key": "b",
             "order_id": "B1", "ts_ns": 4},
            # "b" partially filled BEFORE its clean cancel: contracts are
            # held to settlement, so its slot must stay counted.
            {"type": "fill", "dedup_key": "b", "price": 0.5, "shares": 2.0, "ts_ns": 3},
        ]
        assert open_exposure(records) == 1

    def test_settle_releases_filled_cancelled(self) -> None:
        from gridbot.executor.ledger import open_exposure

        records = [
            {"type": "order_intent", "status": "placed", "dedup_key": "b",
             "order_id": "B1", "ts_ns": 1},
            {"type": "cancel", "status": "cancelled", "dedup_key": "b",
             "order_id": "B1", "ts_ns": 2},
            {"type": "fill", "dedup_key": "b", "price": 0.5, "shares": 2.0, "ts_ns": 2},
            {"type": "settle", "dedup_key": "b", "won": False, "ts_ns": 3},
        ]
        assert open_exposure(records) == 0

    def test_reentry_second_order_stays_counted(self) -> None:
        # glm trade-path review finding 2: one key, two orders — the first
        # entry's clean cancel must not hide the second entry's exposure.
        from gridbot.executor.ledger import open_exposure

        records = [
            {"type": "order_intent", "status": "placed", "dedup_key": "k",
             "order_id": "O1", "ts_ns": 1},
            {"type": "cancel", "status": "cancelled", "dedup_key": "k",
             "order_id": "O1", "ts_ns": 2},
            {"type": "order_intent", "status": "placed", "dedup_key": "k",
             "order_id": "O2", "ts_ns": 3},  # re-entry, still resting
        ]
        assert open_exposure(records) == 1
        records.append({"type": "cancel", "status": "cancelled",
                        "dedup_key": "k", "order_id": "O2", "ts_ns": 4})
        assert open_exposure(records) == 0


def test_multi_fill_key_aggregates_and_dedupes():
    # qwen review f3: a re-entry key carries fills from two orders — shares
    # sum, price is the weighted average, duplicate rows (crash replay,
    # same order_id) count once.
    from gridbot.executor.ledger import _fill_totals, realized_pnl

    records = [
        {"type": "order_intent", "status": "placed", "dedup_key": "k",
         "order_id": "O1", "price": 0.5, "shares": 4.0, "ts_ns": 1},
        {"type": "fill", "dedup_key": "k", "order_id": "O1", "price": 0.5,
         "shares": 4.0, "ts_ns": 2},
        {"type": "fill", "dedup_key": "k", "order_id": "O1", "price": 0.5,
         "shares": 4.0, "ts_ns": 2},  # duplicate — must not double
        {"type": "fill", "dedup_key": "k", "order_id": "O2", "price": 0.6,
         "shares": 2.0, "ts_ns": 3},
    ]
    totals = _fill_totals(records)
    shares, wavg, first_ts = totals["k"]
    assert shares == 6.0
    assert abs(wavg - (0.5 * 4 + 0.6 * 2) / 6.0) < 1e-9
    assert first_ts == 2
    records.append({"type": "settle", "dedup_key": "k", "won": True, "ts_ns": 9})
    pnl = {k: p for k, p, _ in realized_pnl(records)}
    assert abs(pnl["k"] - (1.0 - wavg) * 6.0) < 1e-6


def test_settle_venue_pnl_preferred_over_formula():
    # qwen review f9 + 08-26 production evidence: venue short PnL is
    # price x qty, not (1-price) x qty — the venue's number is the truth.
    from gridbot.executor.ledger import realized_pnl

    records = [
        {"type": "order_intent", "status": "placed", "dedup_key": "k",
         "order_id": "O1", "price": 0.52, "shares": 5.0, "ts_ns": 1},
        {"type": "fill", "dedup_key": "k", "order_id": "O1", "price": 0.52,
         "shares": 5.0, "ts_ns": 2},
        {"type": "settle", "dedup_key": "k", "won": True, "venue_pnl": 2.6,
         "ts_ns": 3},
    ]
    assert realized_pnl(records) == [("k", 2.6, 3)]


def test_manual_exit_settle_releases_without_pnl():
    # fills.py manual-exit release (08-27): a settle row with won=None and
    # venue_pnl=None must release the exposure slot but contribute NOTHING
    # to realized PnL — no crash, no miscount, conservative for the
    # daily-loss cap.
    from gridbot.executor.ledger import _day_of, open_exposure

    records = [
        {"type": "order_intent", "status": "placed", "dedup_key": "k",
         "order_id": "O1", "price": 0.42, "shares": 7.0, "ts_ns": 1},
        {"type": "fill", "dedup_key": "k", "order_id": "O1", "price": 0.42,
         "shares": 7.0, "ts_ns": 2},
        {"type": "settle", "dedup_key": "k", "won": None, "venue_pnl": None,
         "voided": False, "source": "manual_exit_position_closed", "ts_ns": 3},
    ]
    assert realized_pnl(records) == []          # 0 to PnL, silently skipped
    assert daily_loss_usd(records, _day_of(3)) == 0.0
    assert open_positions(records) == []        # slot released
    assert open_exposure(records) == 0          # capacity slot freed



class TestOrderScopedFillOverride:
    def test_cancelled_sibling_of_filled_reentry_releases(self) -> None:
        # Audit F5: O1 clean-cancelled (venue-confirmed unfilled), key
        # released, re-entry O2 FILLED. O1 must not be held open by the
        # key-scoped fill — exposure is exactly the one real position.
        from gridbot.executor.ledger import open_exposure

        records = [
            {"type": "order_intent", "status": "placed", "order_id": "O1",
             "dedup_key": "k1"},
            {"type": "cancel", "order_id": "O1", "status": "cancelled"},
            {"type": "order_intent", "status": "placed", "order_id": "O2",
             "dedup_key": "k1"},
            {"type": "fill", "order_id": "O2", "dedup_key": "k1",
             "price": 0.5, "shares": 5.0, "ts_ns": 1},
        ]
        assert open_exposure(records) == 1  # O2 only

    def test_keyless_fill_row_falls_back_to_key_scope(self) -> None:
        from gridbot.executor.ledger import open_exposure

        records = [
            {"type": "order_intent", "status": "placed", "order_id": "O1",
             "dedup_key": "k1"},
            {"type": "cancel", "order_id": "O1", "status": "cancelled"},
            {"type": "fill", "dedup_key": "k1", "price": 0.5, "shares": 5.0,
             "ts_ns": 1},  # no order_id -> fail closed: O1 stays held
        ]
        assert open_exposure(records) == 1
