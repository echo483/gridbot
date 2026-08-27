"""Tests for executor/fills.py — venue truth → ledger fill/settle rows."""

from __future__ import annotations

from gridbot.executor import fills


def _intent(oid: str = "A", key: str = "k1", slug: str = "m1",
            side: str = "long", price: float = 0.53) -> dict:
    return {
        "type": "order_intent", "status": "placed", "order_id": oid,
        "dedup_key": key, "market_slug": slug, "us_side": side,
        "price": price, "shares": 5.0,
    }


def _order(cum: int, qty: int = 5, avg: str | None = "0.53",
           state: str = "ORDER_STATE_CANCELED") -> dict:
    o = {"id": "A", "cumQuantity": cum, "quantity": qty, "state": state}
    if avg is not None:
        o["avgPx"] = {"value": avg, "currency": "USD"}
    return o


class _FakeClient:
    def __init__(self, orders: dict[str, dict], resolutions: list[dict] | Exception = ()):  # noqa: B006
        self._orders = orders
        self._res = resolutions
        outer = self

        class _Orders:
            def retrieve(self, oid):
                if oid not in outer._orders:
                    raise KeyError(oid)
                return {"order": outer._orders[oid]}

        class _Portfolio:
            def activities(self, params=None):
                if isinstance(outer._res, Exception):
                    raise outer._res
                return {"activities": [
                    {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "positionResolution": r}
                    for r in outer._res
                ]}

        self.orders = _Orders()
        self.portfolio = _Portfolio()


def _resolution(slug: str = "m1", before: float = 0.0, after: float = 2.35,
                side: str = "") -> dict:
    return {
        "marketSlug": slug, "side": side,
        "beforePosition": {"realized": {"value": str(before), "currency": "USD"}},
        "afterPosition": {"realized": {"value": str(after), "currency": "USD"}},
    }


class TestFillRow:
    def test_no_execution_is_none(self) -> None:
        assert fills.fill_row(_intent(), _order(cum=0), 1) is None

    def test_partial_fill_uses_avg_px(self) -> None:
        row = fills.fill_row(_intent(), _order(cum=2, avg="0.52"), 7)
        assert row is not None
        assert row["shares"] == 2.0
        assert row["price"] == 0.52
        assert row["partial"] is True
        assert row["type"] == "fill" and row["ts_ns"] == 7

    def test_full_fill_not_partial(self) -> None:
        row = fills.fill_row(_intent(), _order(cum=5, qty=5), 1)
        assert row is not None and row["partial"] is False

    def test_missing_avg_px_falls_back_to_intent_price(self) -> None:
        row = fills.fill_row(_intent(price=0.53), _order(cum=1, avg=None), 1)
        assert row is not None and row["price"] == 0.53


class TestSettleRow:
    def test_win_and_loss_from_pnl_sign(self) -> None:
        won = fills.settle_row(_intent(), _resolution(after=2.35), 1)
        lost = fills.settle_row(_intent(), _resolution(after=-2.65), 1)
        assert won is not None and won["won"] is True and won["venue_pnl"] == 2.35
        assert lost is not None and lost["won"] is False

    def test_missing_realized_is_none(self) -> None:
        res = {"marketSlug": "m1", "beforePosition": {}, "afterPosition": {}}
        assert fills.settle_row(_intent(), res, 1) is None  # never guess


class TestMatchResolution:
    def test_single_candidate_matches_by_slug(self) -> None:
        i = _intent()
        assert fills.match_resolution({"m1": [i]}, _resolution()) is i

    def test_both_sides_always_skipped(self) -> None:
        # The venue side string is CONSTANT regardless of our position
        # (production 08-26), so multi-candidate matching NEVER guesses
        # (code-review f4 — substring matching mis-attributed settles).
        lo, sh = _intent(key="k1", side="long"), _intent(oid="B", key="k2", side="short")
        by = {"m1": [lo, sh]}
        assert fills.match_resolution(by, _resolution(side="POSITION_RESOLUTION_SIDE_SHORT")) is None
        assert fills.match_resolution(by, _resolution(side="")) is None


class TestSync:
    def test_partial_fill_then_settle_rows(self) -> None:
        records = [_intent(), {"type": "cancel", "order_id": "A", "dedup_key": "k1",
                               "status": "cancelled"}]
        client = _FakeClient({"A": _order(cum=2)}, [_resolution(after=0.94)])
        checked: set[str] = set()
        rows = fills.sync(records, client, checked, now_ns=9)
        types = [r["type"] for r in rows]
        assert types == ["fill", "settle"]
        assert rows[0]["shares"] == 2.0
        assert rows[1]["won"] is True
        assert "A" in checked

    def test_terminal_unfilled_without_own_cancel_gets_corrective(self) -> None:
        # No clean-cancel row of its own (hand-cancel / venue auto-cancel /
        # crash orphan): terminal-zero-fill writes the corrective release
        # (code-review f5 widened the glm f8 condition).
        client = _FakeClient({"A": _order(cum=0)})
        checked: set[str] = set()
        rows = fills.sync([_intent()], client, checked, 1)
        assert [r["status"] for r in rows] == ["cancelled"]
        assert rows[0]["source"] == "venue_terminal_no_fill"
        assert "A" in checked
        # and it is never retrieved again
        client2 = _FakeClient({})  # retrieve would raise
        assert fills.sync([_intent()], client2, checked, 2) == []

    def test_terminal_unfilled_with_own_cancel_no_rows(self) -> None:
        records = [
            _intent(),
            {"type": "cancel", "order_id": "A", "dedup_key": "k1",
             "status": "cancelled"},
        ]
        client = _FakeClient({"A": _order(cum=0)})
        checked: set[str] = set()
        assert fills.sync(records, client, checked, 1) == []
        assert "A" in checked

    def test_existing_fill_row_not_duplicated(self) -> None:
        records = [
            _intent(),
            {"type": "fill", "dedup_key": "k1", "order_id": "A", "price": 0.53,
             "shares": 2.0, "ts_ns": 1},
            {"type": "settle", "dedup_key": "k1", "won": True, "ts_ns": 2},
        ]
        client = _FakeClient({"A": _order(cum=2)})
        assert fills.sync(records, client, set(), 3) == []

    def test_live_partial_not_recorded_until_terminal(self) -> None:
        # glm trade-path review finding 4: a live order's cum can still grow;
        # first-wins fill rows would freeze an understated quantity forever.
        client = _FakeClient({"A": _order(cum=2, state="ORDER_STATE_PARTIALLY_FILLED")})
        checked: set[str] = set()
        assert fills.sync([_intent()], client, checked, 1) == []
        assert "A" not in checked  # re-fetched next pass
        client2 = _FakeClient({"A": _order(cum=4, state="ORDER_STATE_CANCELED")})
        rows = fills.sync([_intent()], client2, checked, 2)
        assert [r["type"] for r in rows] == ["fill"]
        assert rows[0]["shares"] == 4.0  # the FINAL quantity, not the frozen 2

    def test_gone_no_fill_writes_corrective_release(self) -> None:
        # glm trade-path review finding 8: gone_before_cancel + venue says
        # terminal-with-zero-fill (expired/hand-cancelled) must release the
        # slot via a corrective clean-cancel row, else it leaks forever.
        records = [
            _intent(),
            {"type": "cancel", "order_id": "A", "dedup_key": "k1",
             "status": "gone_before_cancel"},
        ]
        client = _FakeClient({"A": _order(cum=0, state="ORDER_STATE_EXPIRED")})
        rows = fills.sync(records, client, set(), 5)
        assert len(rows) == 1
        assert rows[0]["type"] == "cancel" and rows[0]["status"] == "cancelled"
        assert rows[0]["source"] == "venue_terminal_no_fill"

    def test_retrieve_error_retries_next_pass(self) -> None:
        client = _FakeClient({})  # every retrieve raises
        checked: set[str] = set()
        assert fills.sync([_intent()], client, checked, 1) == []
        assert checked == set()  # NOT marked checked — transient

    def test_activities_error_skips_settles(self) -> None:
        records = [_intent()]
        client = _FakeClient({"A": _order(cum=2)}, RuntimeError("down"))
        rows = fills.sync(records, client, set(), 1)
        assert [r["type"] for r in rows] == ["fill"]  # fill lands, settle waits


def test_pos_size_parses_real_venue_netposition() -> None:
    # Live capture 2026-08-27 13:10Z while HOLDING: the real venue keys
    # positions by slug with the size under "netPosition".
    from gridbot.executor.fills import _row_sizes

    real = {"netPosition": "5", "qtyBought": "5", "qtySold": "0",
            "cost": {"value": "2.8300", "currency": "USD"}, "expired": False}
    assert 5.0 in _row_sizes(real)
