"""Tests for executor/reconcile.py — pure diff logic, no client."""

from __future__ import annotations

import logging

from gridbot.executor.reconcile import diff, open_ledger_orders, run, venue_order_ids


def _placed(oid: str, key: str) -> dict:
    return {"type": "order_intent", "status": "placed", "order_id": oid, "dedup_key": key}


def _cancel(oid: str, status: str = "cancelled") -> dict:
    return {"type": "cancel", "order_id": oid, "status": status}


class _FakeOrders:
    def __init__(self, orders: list[dict] | Exception) -> None:
        self._orders = orders

    def list(self, params=None):
        if isinstance(self._orders, Exception):
            raise self._orders
        return {"orders": self._orders}


class _FakeClient:
    def __init__(self, orders: list[dict] | Exception) -> None:
        self.orders = _FakeOrders(orders)


class TestLedgerSide:
    def test_only_placed_with_ids_count(self) -> None:
        records = [
            _placed("A", "k1"),
            {"type": "order_intent", "status": "dry_run", "order_id": None, "dedup_key": "k2"},
            {"type": "order_intent", "status": "rejected:x", "dedup_key": "k3"},
        ]
        assert set(open_ledger_orders(records)) == {"A"}

    def test_settled_orders_drop_out(self) -> None:
        records = [
            _placed("A", "k1"),
            _placed("B", "k2"),
            {"type": "settle", "dedup_key": "k1", "won": True},
        ]
        assert set(open_ledger_orders(records)) == {"B"}

    def test_clean_cancel_releases(self) -> None:
        # The first armed night: every TTL-cancelled order was re-flagged
        # gone_from_venue forever because only settles were subtracted.
        records = [_placed("A", "k1"), _placed("B", "k2"), _cancel("A")]
        assert set(open_ledger_orders(records)) == {"B"}

    def test_gone_before_cancel_stays_open(self) -> None:
        # Likely a fill — MUST keep flagging until a settle row lands.
        records = [_placed("A", "k1"), _cancel("A", status="gone_before_cancel")]
        assert set(open_ledger_orders(records)) == {"A"}

    def test_clean_cancel_with_fill_stays_open(self) -> None:
        # Cancel-after-partial-fill reports "cancelled" too; a filled book
        # stays visible until it settles (peer review 08-26, finding 3).
        records = [
            _placed("A", "k1"),
            {"type": "fill", "dedup_key": "k1", "price": 0.5, "shares": 2.0},
            _cancel("A"),
        ]
        assert set(open_ledger_orders(records)) == {"A"}
        records.append({"type": "settle", "dedup_key": "k1", "won": True})
        assert set(open_ledger_orders(records)) == set()


class TestDiff:
    def test_agreement_is_quiet(self) -> None:
        report = diff([_placed("A", "k1")], [{"id": "A", "cumQuantity": "0"}])
        assert report["gone_from_venue"] == []
        assert report["unknown_on_venue"] == []
        assert report["partial_fills"] == []

    def test_gone_from_venue_flagged(self) -> None:
        report = diff([_placed("A", "k1"), _placed("B", "k2")], [{"id": "B"}])
        assert report["gone_from_venue"] == ["A"]

    def test_orphan_on_venue_flagged(self) -> None:
        report = diff([], [{"id": "ZZZ"}])
        assert report["unknown_on_venue"] == ["ZZZ"]

    def test_partial_fill_surfaced(self) -> None:
        report = diff([_placed("A", "k1")], [{"id": "A", "cumQuantity": "2.0"}])
        assert report["partial_fills"] == ["A"]

    def test_orderid_key_variant(self) -> None:
        assert venue_order_ids([{"orderId": "Q1"}, {"junk": 1}]) == {"Q1"}


class TestRun:
    def test_fetch_error_skips_diff(self) -> None:
        # A failed fetch must NOT read as "venue empty → everything gone".
        report = run([_placed("A", "k1")], _FakeClient(RuntimeError("down")))
        assert report == {"error": "venue_fetch_failed"}

    def test_unchanged_anomaly_logs_debug_not_warning(self, caplog) -> None:
        records = [_placed("A", "k1")]
        client = _FakeClient([])
        with caplog.at_level(logging.DEBUG, logger="gridbot.executor.reconcile"):
            first = run(records, client, last=None)
            caplog.clear()
            second = run(records, client, last=first)
        assert first["gone_from_venue"] == ["A"] == second["gone_from_venue"]
        assert all(r.levelno < logging.WARNING for r in caplog.records)

    def test_changed_anomaly_warns_again(self, caplog) -> None:
        client = _FakeClient([])
        first = run([_placed("A", "k1")], client, last=None)
        with caplog.at_level(logging.DEBUG, logger="gridbot.executor.reconcile"):
            run([_placed("A", "k1"), _placed("B", "k2")], client, last=first)
        assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_cancelled_sibling_of_filled_reentry_leaves_open_view() -> None:
    # Audit F5 mirror of ledger: reconcile must not list the cancelled
    # sibling as gone-from-venue forever after a re-entry fill.
    from gridbot.executor.reconcile import open_ledger_orders

    records = [
        {"type": "order_intent", "status": "placed", "order_id": "O1",
         "dedup_key": "k1"},
        {"type": "cancel", "order_id": "O1", "status": "cancelled"},
        {"type": "order_intent", "status": "placed", "order_id": "O2",
         "dedup_key": "k1"},
        {"type": "fill", "order_id": "O2", "dedup_key": "k1",
         "price": 0.5, "shares": 5.0, "ts_ns": 1},
    ]
    assert set(open_ledger_orders(records)) == {"O2"}
