"""Executor tests: arming gate fails closed, intents match frozen semantics."""

from __future__ import annotations

import pytest

from gridbot.executor import limits
from gridbot.executor.intents import OrderIntent, build_intent, entry_ceiling


def _instance(**over) -> dict:
    base = {
        "type": "instance",
        "hypothesis_id": "H-MANADV1",
        "series_id": "3000001",
        "map_number": 1,
        "round_number": 7,
        "team": "Alpha",
        "opponent": "Beta",
        "dedup_extra": "",
        "market": {
            "condition_id": "0xabc",
            "question": "Alpha vs Beta round 7 winner?",
            "outcomes": ["Alpha", "Beta"],
            "clob_token_ids": ["tokA", "tokB"],
            "kind": "round",
        },
        "books": {"tokA": {"bid": 0.60, "ask": 0.66}, "tokB": {"bid": 0.32, "ask": 0.40}},
    }
    base.update(over)
    return base


class TestArmingGate:
    def test_default_is_dry_run(self) -> None:
        # Without the user's GRIDBOT_LIVE=1, dry_run regardless of keys.
        state = limits.check_armed(env={})
        assert state.dry_run
        assert any("GRIDBOT_LIVE" in r for r in state.reasons)

    def test_env_alone_cannot_arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # GRIDBOT_LIVE=1 with an EMPTY keyring stays dry. The real keyring
        # may hold live keys, so mock the secret lookup — the test must not
        # depend on machine state.
        from gridbot import config as cfg

        monkeypatch.setattr(cfg, "_secret", lambda _name: None)
        state = limits.check_armed(env={"GRIDBOT_LIVE": "1"})
        assert state.dry_run
        assert any("US_KEY_ID" in r for r in state.reasons)

    def test_live_property_mirrors_dry_run(self) -> None:
        assert not limits.ArmingState(dry_run=True).live
        assert limits.ArmingState(dry_run=False).live


class TestCaps:
    def test_stake_cap_default_and_clamp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GRIDBOT_MAX_STAKE_USD", raising=False)
        assert limits.max_stake_usd() == limits.DEFAULT_STAKE_USD
        monkeypatch.setenv("GRIDBOT_MAX_STAKE_USD", "5000")
        assert limits.max_stake_usd() == limits.MAX_STAKE_USD_CEILING
        monkeypatch.setenv("GRIDBOT_MAX_STAKE_USD", "junk")
        assert limits.max_stake_usd() == limits.DEFAULT_STAKE_USD

    def test_open_positions_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIDBOT_MAX_OPEN", "99")
        assert limits.max_open_positions() == 10


class TestBuildIntent:
    def test_happy_path_maker_at_bid_plus_tick(self) -> None:
        intent = build_intent(_instance(), stake_cap_usd=25.0)
        assert isinstance(intent, OrderIntent)
        assert intent.token_id == "tokA"  # the TEAM's outcome token
        assert intent.price == pytest.approx(0.61)  # bid + tick
        assert intent.stake_usd <= 25.01
        assert intent.shares == pytest.approx(25.0 / 0.61, abs=0.01)

    def test_no_market_is_none(self) -> None:
        assert build_intent(_instance(market=None), 25.0) is None

    def test_crossed_quote_is_none(self) -> None:
        inst = _instance(books={"tokA": {"bid": 0.65, "ask": 0.66}})
        assert build_intent(inst, 25.0) is None  # 0.66 >= ask

    def test_one_sided_book_is_none(self) -> None:
        assert build_intent(_instance(books={"tokA": {"bid": 0.60}}), 25.0) is None
        assert build_intent(_instance(books={}), 25.0) is None

    def test_entry_ceiling_respected(self) -> None:
        # H-MANADV1 ceiling 0.85: a 0.85 bid quotes 0.86 -> refused.
        inst = _instance(books={"tokA": {"bid": 0.85, "ask": 0.95}})
        assert build_intent(inst, 25.0) is None

    def test_ceiling_lookup(self) -> None:
        assert entry_ceiling("H-MANADV1") == 0.85
        assert entry_ceiling("H-ECONBUY1") == 0.78
        assert entry_ceiling("H-MODELENTRY1") == 0.90

    def test_ambiguous_team_token_is_none(self) -> None:
        inst = _instance(team="Zeta")  # matches neither outcome
        assert build_intent(inst, 25.0) is None

    def test_dedup_key_stable(self) -> None:
        a = build_intent(_instance(), 25.0)
        b = build_intent(_instance(), 25.0)
        assert a is not None and b is not None
        assert a.dedup_key == b.dedup_key


class TestPmusWire:
    def test_place_fails_closed_without_us_mapping(self) -> None:
        # An intent with no US market slug must be refused BEFORE any client
        # construction — no credentials are touched.
        from gridbot.executor import pmus

        intent = build_intent(_instance(), 25.0)
        assert intent is not None
        assert pmus.place(intent) == ("rejected:no_us_market_mapping", None)
        assert pmus._client is None  # never built

    def test_place_refuses_sub_contract_quantity(self) -> None:
        from dataclasses import replace

        from gridbot.executor import pmus

        intent = build_intent(_instance(), 25.0)
        assert intent is not None
        tiny = replace(intent, market_slug="cs2-a-vs-b", us_side="long", shares=0.4)
        assert pmus.place(tiny) == ("rejected:quantity_below_one_contract", None)

    def test_place_returns_order_id_for_ledger(self, monkeypatch) -> None:
        # The parsed order id must surface to the caller — the ledger row
        # persists it so cancel/reconciliation can reference the order.
        from dataclasses import replace

        from gridbot.executor import pmus

        class _Orders:
            def create(self, body: dict) -> dict:
                assert body["participateDontInitiate"] is True
                return {"orderId": "ORD123"}

        class _Client:
            orders = _Orders()

        monkeypatch.setattr(pmus, "_get_client", lambda: _Client())
        intent = build_intent(_instance(), 25.0)
        assert intent is not None
        wired = replace(intent, market_slug="cs2-a-vs-b", us_side="long")
        assert pmus.place(wired) == ("placed", "ORD123")

    def test_place_ambiguous_ack_has_no_order_id(self, monkeypatch) -> None:
        from dataclasses import replace

        from gridbot.executor import pmus

        class _Orders:
            def create(self, body: dict) -> dict:
                return {"unexpected": "shape"}

        class _Client:
            orders = _Orders()

        monkeypatch.setattr(pmus, "_get_client", lambda: _Client())
        intent = build_intent(_instance(), 25.0)
        assert intent is not None
        wired = replace(intent, market_slug="cs2-a-vs-b", us_side="long")
        assert pmus.place(wired) == ("error:ambiguous_ack", None)


def _mapentry_row(**over) -> dict:
    base = {
        "type": "mapentry",
        "hypothesis_id": "H-MAPENTRY1",
        "series_id": "2995296",
        "map_number": 2,
        "round_number": 7,
        "side": "ct",
        "team": "AURORA",
        "opponent": "G2 Esports",
        "a": 5,
        "b": 3,
        "q_round": 0.92,
        "p_map": 0.87,
        "market_slug": "astatc-cs2-aur-g2-map2",
        "us_side": "long",
        "bid": 0.70,
        "ask": 0.80,
        "side_ask": 0.80,
        "entry_price_max": 0.83,
        "qualifies": True,
        "dedup_extra": "ct:0.85",
    }
    base.update(over)
    return base


class TestUsIntent:
    def test_qualifying_row_builds_capped_maker(self) -> None:
        from gridbot.executor.intents import build_us_intent

        intent = build_us_intent(_mapentry_row(), 25.0)
        assert intent is not None
        assert intent.market_slug == "astatc-cs2-aur-g2-map2"
        assert intent.us_side == "long"
        # one US tick inside the taker price, integer contracts
        assert intent.price == pytest.approx(0.79)
        assert intent.shares == float(int(25.0 // 0.79))
        assert intent.stake_usd <= 25.0
        assert intent.dedup_key == "H-MAPENTRY1:2995296:2:long:astatc-cs2-aur-g2-map2"

    def test_ceiling_clamp_only_lowers(self) -> None:
        from gridbot.executor.intents import build_us_intent

        row = _mapentry_row(side_ask=0.93, entry_price_max=0.93, p_map=0.97)
        intent = build_us_intent(row, 25.0)
        assert intent is not None
        assert intent.price == pytest.approx(0.90)  # DEFAULT_ENTRY_CEILING

    def test_non_qualifying_refused(self) -> None:
        from gridbot.executor.intents import build_us_intent

        assert build_us_intent(_mapentry_row(qualifies=False), 25.0) is None
        assert build_us_intent(_mapentry_row(type="instance"), 25.0) is None
        assert build_us_intent(_mapentry_row(market_slug=""), 25.0) is None
        assert build_us_intent(_mapentry_row(us_side="yes"), 25.0) is None
        assert build_us_intent(_mapentry_row(side_ask=None), 25.0) is None

    def test_sub_contract_stake_refused(self) -> None:
        from gridbot.executor.intents import build_us_intent

        assert build_us_intent(_mapentry_row(), 0.5) is None

    def test_short_side_carries_through(self) -> None:
        from gridbot.executor.intents import build_us_intent

        intent = build_us_intent(_mapentry_row(us_side="short", side_ask=0.30,
                                               entry_price_max=0.35), 25.0)
        assert intent is not None
        assert intent.us_side == "short"
        assert intent.price == pytest.approx(0.29)

    def test_engine_dispatches_mapentry_to_dry_run_ledger(self, tmp_path, monkeypatch) -> None:
        import json

        from gridbot import config
        from gridbot.executor import engine as engine_mod

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        (tmp_path / "shadow").mkdir()
        eng = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))
        status = eng.handle(_mapentry_row(p_map=0.75), limits.ArmingState(dry_run=True))
        assert status == "dry_run"
        rows = [
            json.loads(line)
            for f in (tmp_path / "executor").glob("intents-*.jsonl")
            for line in f.read_text().splitlines()
        ]
        assert len(rows) == 1
        assert rows[0]["market_slug"] == "astatc-cs2-aur-g2-map2"
        assert rows[0]["order_id"] is None
        assert rows[0]["live"] is False
        # same map book dedups — a second qualifying round does not re-enter
        again = eng.handle(
            _mapentry_row(round_number=9, p_map=0.75), limits.ArmingState(dry_run=True)
        )
        assert again is None


class TestBandGate:
    def test_pure_band_edges(self) -> None:
        from gridbot.executor.intents import us_band_gated

        assert us_band_gated(0.49) is True
        assert us_band_gated(0.50) is False
        assert us_band_gated(0.80) is False
        assert us_band_gated(0.81) is True
        assert us_band_gated(None) is True  # fail closed
        assert us_band_gated("0.7") is True

    def _engine(self, tmp_path, monkeypatch):
        from gridbot import config
        from gridbot.executor import engine as engine_mod

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        (tmp_path / "shadow").mkdir()
        return engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))

    def _rows(self, tmp_path):
        import json

        return [
            json.loads(line)
            for f in (tmp_path / "executor").glob("intents-*.jsonl")
            for line in f.read_text().splitlines()
        ]

    def test_armed_out_of_band_never_places_and_keeps_dedup(
        self, tmp_path, monkeypatch
    ) -> None:
        from gridbot.executor import pmus

        calls: list = []
        monkeypatch.setattr(
            pmus, "place", lambda i: (calls.append(i) or ("placed", "OID1"))
        )
        eng = self._engine(tmp_path, monkeypatch)
        status = eng.handle(_mapentry_row(), limits.ArmingState(dry_run=False))
        assert status == "skipped:band_gate"
        assert calls == []  # wire never touched
        assert eng.resting == {}
        # dedup NOT consumed: a later in-band crossing on the same book enters
        status = eng.handle(
            _mapentry_row(round_number=12, p_map=0.75), limits.ArmingState(dry_run=False)
        )
        assert status == "placed"
        assert len(calls) == 1
        rows = self._rows(tmp_path)
        assert [r["status"] for r in rows] == ["skipped:band_gate", "placed"]
        assert rows[0]["p_map"] == 0.87
        assert rows[0]["live"] is True

    def test_dry_run_gates_identically(self, tmp_path, monkeypatch) -> None:
        eng = self._engine(tmp_path, monkeypatch)
        status = eng.handle(_mapentry_row(), limits.ArmingState(dry_run=True))
        assert status == "skipped:band_gate"
        rows = self._rows(tmp_path)
        assert rows[0]["status"] == "skipped:band_gate"
        assert rows[0]["live"] is False


class TestCancel:
    def _stub(self, monkeypatch, behavior):
        from gridbot.executor import pmus

        class _Orders:
            def __init__(self):
                self.calls = 0

            def cancel(self, order_id, params):
                self.calls += 1
                behavior(self.calls)

        class _Client:
            orders = _Orders()

        monkeypatch.setattr(pmus, "_get_client", lambda: _Client())
        monkeypatch.setattr(pmus.time, "sleep", lambda _s: None)
        return _Client.orders

    def test_cancel_success(self, monkeypatch) -> None:
        from gridbot.executor import pmus

        self._stub(monkeypatch, lambda _n: None)
        assert pmus.cancel("OID", "slug-x") == "cancelled"

    def test_cancel_gone_is_not_retried(self, monkeypatch) -> None:
        from gridbot.executor import pmus

        class NotFoundError(Exception):
            pass

        orders = self._stub(monkeypatch, lambda _n: (_ for _ in ()).throw(NotFoundError()))
        assert pmus.cancel("OID", "slug-x") == "gone"
        assert orders.calls == 1

    def test_cancel_retries_then_fails(self, monkeypatch) -> None:
        from gridbot.executor import pmus

        orders = self._stub(monkeypatch, lambda _n: (_ for _ in ()).throw(RuntimeError("x")))
        assert pmus.cancel("OID", "slug-x") == "error:cancel_failed"
        assert orders.calls == 3

    def test_cancel_recovers_on_retry(self, monkeypatch) -> None:
        from gridbot.executor import pmus

        def flaky(n: int) -> None:
            if n < 3:
                raise RuntimeError("transient")

        orders = self._stub(monkeypatch, flaky)
        assert pmus.cancel("OID", "slug-x") == "cancelled"
        assert orders.calls == 3


class TestStaleSweep:
    def _armed_engine(self, tmp_path, monkeypatch):
        import time as _time

        from gridbot import config
        from gridbot.executor import engine as engine_mod
        from gridbot.executor import pmus

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        (tmp_path / "shadow").mkdir()
        monkeypatch.setattr(pmus, "place", lambda _i: ("placed", "OID9"))
        eng = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))
        status = eng.handle(_mapentry_row(p_map=0.75), limits.ArmingState(dry_run=False))
        assert status == "placed"
        assert "OID9" in eng.resting
        return eng, engine_mod, _time.monotonic()

    def _ledger_rows(self, tmp_path):
        import json

        return [
            json.loads(line)
            for f in (tmp_path / "executor").glob("intents-*.jsonl")
            for line in f.read_text().splitlines()
        ]

    def test_fresh_orders_left_alone(self, tmp_path, monkeypatch) -> None:
        eng, engine_mod, now = self._armed_engine(tmp_path, monkeypatch)
        assert eng.sweep_stale(now + 1) == 0
        assert "OID9" in eng.resting

    def test_stale_order_cancelled_releases_slot(self, tmp_path, monkeypatch) -> None:
        from gridbot.executor import ledger as ledger_mod
        from gridbot.executor import pmus

        eng, engine_mod, now = self._armed_engine(tmp_path, monkeypatch)
        monkeypatch.setattr(pmus, "cancel", lambda _o, _s: "cancelled")
        assert eng.sweep_stale(now + engine_mod.MAKER_TTL_S + 1) == 1
        assert eng.resting == {}
        rows = self._ledger_rows(tmp_path)
        cancels = [r for r in rows if r["type"] == "cancel"]
        assert len(cancels) == 1 and cancels[0]["status"] == "cancelled"
        assert ledger_mod.open_exposure(rows) == 0  # clean cancel frees the cap

    def test_gone_keeps_exposure_held(self, tmp_path, monkeypatch) -> None:
        from gridbot.executor import ledger as ledger_mod
        from gridbot.executor import pmus

        eng, engine_mod, now = self._armed_engine(tmp_path, monkeypatch)
        monkeypatch.setattr(pmus, "cancel", lambda _o, _s: "gone")
        eng.sweep_stale(now + engine_mod.MAKER_TTL_S + 1)
        rows = self._ledger_rows(tmp_path)
        cancels = [r for r in rows if r["type"] == "cancel"]
        assert cancels[0]["status"] == "gone_before_cancel"
        assert ledger_mod.open_exposure(rows) == 1  # likely fill: stays counted

    def test_failed_cancel_stays_tracked(self, tmp_path, monkeypatch) -> None:
        from gridbot.executor import pmus

        eng, engine_mod, now = self._armed_engine(tmp_path, monkeypatch)
        monkeypatch.setattr(pmus, "cancel", lambda _o, _s: "error:cancel_failed")
        assert eng.sweep_stale(now + engine_mod.MAKER_TTL_S + 1) == 0
        assert "OID9" in eng.resting  # retried next sweep


class TestCycleResilience:
    """Peer review 08-26 finding 4: nothing may starve the TTL sweep."""

    def test_broken_follow_and_gate_still_sweep(self, tmp_path, monkeypatch) -> None:
        import time as _time

        from gridbot import config
        from gridbot.executor import engine as engine_mod
        from gridbot.executor import pmus

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        (tmp_path / "shadow").mkdir()
        monkeypatch.setattr(pmus, "place", lambda _i: ("placed", "OID9"))
        eng = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))
        assert eng.handle(_mapentry_row(p_map=0.75), limits.ArmingState(dry_run=False)) == "placed"

        # Make the order stale, then break EVERYTHING after the sweep:
        eng.resting["OID9"]["placed_mono"] = _time.monotonic() - engine_mod.MAKER_TTL_S - 1
        cancelled: list[str] = []

        def _cancel(oid, _slug):
            cancelled.append(oid)
            return "cancelled"

        monkeypatch.setattr(pmus, "cancel", _cancel)

        def _boom(*_a, **_k):
            raise RuntimeError("deterministic gate failure")

        monkeypatch.setattr(limits, "check_armed", _boom)

        class _BrokenFollow:
            def lines(self):
                raise OSError("corrupt shadow file")

        eng.follow = _BrokenFollow()
        eng.follow_us = _BrokenFollow()

        eng.cycle()  # must not raise, and must have swept first
        assert cancelled == ["OID9"]
        assert eng.resting == {}


class TestReentry:
    """H-MAPENTRY1-REENTRY1 (frozen + user-approved 08-26): a clean cancel
    releases the dedup key for exactly one more entry; fills never release;
    2 lifetime placed entries per key, ledger-derived."""

    def _armed(self, tmp_path, monkeypatch):
        import time as _time

        from gridbot import config
        from gridbot.executor import engine as engine_mod
        from gridbot.executor import pmus

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        (tmp_path / "shadow").mkdir()
        self._oid = 0

        def _place(_i):
            self._oid += 1
            return "placed", f"OID{self._oid}"

        monkeypatch.setattr(pmus, "place", _place)
        eng = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))
        return eng, engine_mod, _time.monotonic()

    def _sweep_all(self, eng, engine_mod, monkeypatch, outcome="cancelled"):
        from gridbot.executor import pmus

        monkeypatch.setattr(pmus, "cancel", lambda _o, _s: outcome)
        import time as _time

        return eng.sweep_stale(_time.monotonic() + engine_mod.MAKER_TTL_S + 1)

    def test_clean_cancel_allows_exactly_one_reentry(self, tmp_path, monkeypatch) -> None:
        eng, engine_mod, _ = self._armed(tmp_path, monkeypatch)
        armed = limits.ArmingState(dry_run=False)
        assert eng.handle(_mapentry_row(p_map=0.75), armed) == "placed"
        assert eng.handle(_mapentry_row(p_map=0.70), armed) is None  # dedup holds
        assert self._sweep_all(eng, engine_mod, monkeypatch) == 1
        assert eng.handle(_mapentry_row(p_map=0.70), armed) == "placed"  # re-entry
        assert self._sweep_all(eng, engine_mod, monkeypatch) == 1
        # third entry refused by the lifetime 2-cap even though key released
        assert eng.handle(_mapentry_row(p_map=0.65), armed) is None

    def test_gone_before_cancel_never_releases(self, tmp_path, monkeypatch) -> None:
        eng, engine_mod, _ = self._armed(tmp_path, monkeypatch)
        armed = limits.ArmingState(dry_run=False)
        assert eng.handle(_mapentry_row(p_map=0.75), armed) == "placed"
        assert self._sweep_all(eng, engine_mod, monkeypatch, outcome="gone") == 1
        assert eng.handle(_mapentry_row(p_map=0.70), armed) is None

    def test_filled_key_never_releases(self, tmp_path, monkeypatch) -> None:
        eng, engine_mod, _ = self._armed(tmp_path, monkeypatch)
        armed = limits.ArmingState(dry_run=False)
        assert eng.handle(_mapentry_row(p_map=0.75), armed) == "placed"
        key = next(iter(eng.resting.values()))["dedup_key"]
        eng._filled_keys.add(key)  # fills.sync discovered a partial fill
        assert self._sweep_all(eng, engine_mod, monkeypatch) == 1
        assert eng.handle(_mapentry_row(p_map=0.70), armed) is None

    def test_restart_enforces_ledger_cap(self, tmp_path, monkeypatch) -> None:
        # glm review finding 7: the ledger derivation is the whole point —
        # a fresh process must refuse a third entry from ledger counts alone.
        eng, engine_mod, _ = self._armed(tmp_path, monkeypatch)
        armed = limits.ArmingState(dry_run=False)
        assert eng.handle(_mapentry_row(p_map=0.75), armed) == "placed"
        assert self._sweep_all(eng, engine_mod, monkeypatch) == 1
        assert eng.handle(_mapentry_row(p_map=0.70), armed) == "placed"
        assert self._sweep_all(eng, engine_mod, monkeypatch) == 1

        eng2 = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))
        eng2.cycle()  # loads the ledger; dry_run in tests, counts refresh anyway
        assert eng2.handle(_mapentry_row(p_map=0.65), armed) is None

    def test_fill_row_via_ledger_blocks_release(self, tmp_path, monkeypatch) -> None:
        # Derivation coverage: a fill ROW in the ledger (not an injected set)
        # must reach _filled_keys and block the release.
        import json

        eng, engine_mod, _ = self._armed(tmp_path, monkeypatch)
        armed = limits.ArmingState(dry_run=False)
        assert eng.handle(_mapentry_row(p_map=0.75), armed) == "placed"
        key = next(iter(eng.resting.values()))["dedup_key"]
        eng.ledger.append({"type": "fill", "dedup_key": key, "price": 0.5,
                           "shares": 2.0, "ts_ns": 1})
        eng.cycle()  # refresh _filled_keys from the ledger
        assert key in eng._filled_keys
        assert self._sweep_all(eng, engine_mod, monkeypatch) == 1
        assert eng.handle(_mapentry_row(p_map=0.70), armed) is None

    def test_ambiguous_ack_counts_toward_cap(self, tmp_path, monkeypatch) -> None:
        # glm review finding 1: an ambiguous ack may be live on the venue —
        # after restart it must consume a lifetime entry slot.
        import json

        eng, engine_mod, _ = self._armed(tmp_path, monkeypatch)
        armed = limits.ArmingState(dry_run=False)
        assert eng.handle(_mapentry_row(p_map=0.75), armed) == "placed"
        key = next(iter(eng.resting.values()))["dedup_key"]
        row = {"type": "order_intent", "status": "error:ambiguous_ack",
               "dedup_key": key, "order_id": None, "ts_ns": 2}
        eng.ledger.append(row)
        eng2 = engine_mod.Engine(ledger=engine_mod.IntentLedger(tmp_path))
        eng2.cycle()
        assert eng2._placed_counts.get(key, 0) >= 2
        assert eng2.handle(_mapentry_row(p_map=0.70), armed) is None
