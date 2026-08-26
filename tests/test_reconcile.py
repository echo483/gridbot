"""Tests for executor/reconcile.py — pure diff logic, no client."""

from __future__ import annotations

from gridbot.executor.reconcile import diff, open_ledger_orders, venue_order_ids


def _placed(oid: str, key: str) -> dict:
    return {"type": "order_intent", "status": "placed", "order_id": oid, "dedup_key": key}


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
