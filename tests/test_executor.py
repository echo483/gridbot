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
        # No GRIDBOT_LIVE, no Polymarket keys in the keyring yet.
        state = limits.check_armed(env={})
        assert state.dry_run
        assert any("GRIDBOT_LIVE" in r for r in state.reasons)

    def test_env_alone_cannot_arm(self) -> None:
        # GRIDBOT_LIVE=1 without keyring keys stays dry.
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
        assert pmus.place(intent) == "rejected:no_us_market_mapping"
        assert pmus._client is None  # never built

    def test_place_refuses_sub_contract_quantity(self) -> None:
        from dataclasses import replace

        from gridbot.executor import pmus

        intent = build_intent(_instance(), 25.0)
        assert intent is not None
        tiny = replace(intent, market_slug="cs2-a-vs-b", us_side="long", shares=0.4)
        assert pmus.place(tiny) == "rejected:quantity_below_one_contract"
