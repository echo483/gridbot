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
        status = eng.handle(_mapentry_row(), limits.ArmingState(dry_run=True))
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
        again = eng.handle(_mapentry_row(round_number=9), limits.ArmingState(dry_run=True))
        assert again is None
