"""Tests for instruments/mapentry.py — frozen mapwin-race-v1 math + joins."""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from gridbot.instruments.mapentry import (
    ENTRY_MARGIN,
    attach_book,
    build_row,
    instance_scores,
    load_latest_mappings,
    map_win_prob,
    market_for_instance,
    p_future,
    parse_bbo,
    race_win_prob,
    round_probability,
    side_ask,
)


def _race_brute(a: int, b: int, p: float) -> float:
    """Independent recursive reference (no memo) for small remaining races."""
    if a == 12 and b == 12:
        return 0.5
    if a >= 13:
        return 1.0
    if b >= 13:
        return 0.0
    return p * _race_brute(a + 1, b, p) + (1 - p) * _race_brute(a, b + 1, p)


class TestRace:
    def test_symmetric_start_is_half(self) -> None:
        assert race_win_prob(0, 0, 0.5) == pytest.approx(0.5)

    def test_certain_round_wins_map(self) -> None:
        assert race_win_prob(5, 3, 1.0) == pytest.approx(1.0)
        assert race_win_prob(5, 3, 0.0) == pytest.approx(0.0)

    def test_matches_bruteforce(self) -> None:
        for a, b, p in [(10, 8, 0.6), (12, 0, 0.5), (7, 11, 0.45), (11, 12, 0.5)]:
            assert race_win_prob(a, b, p) == pytest.approx(_race_brute(a, b, p))

    def test_12_12_is_frozen_coin(self) -> None:
        assert race_win_prob(12, 12, 0.9) == pytest.approx(0.5)

    def test_one_from_win_vs_coin(self) -> None:
        # From 12-11 with p=0.5: win now (0.5) or reach the 12-12 coin.
        assert race_win_prob(12, 11, 0.5) == pytest.approx(0.5 + 0.5 * 0.5)

    def test_monotone_in_p(self) -> None:
        assert race_win_prob(6, 6, 0.6) > race_win_prob(6, 6, 0.5) > race_win_prob(6, 6, 0.4)


class TestMapWin:
    def test_prior_smoothing(self) -> None:
        assert p_future(0, 0) == pytest.approx(0.5)
        assert p_future(8, 0) == pytest.approx(0.75)
        assert p_future(0, 8) == pytest.approx(0.25)

    def test_certain_current_round_at_map_point(self) -> None:
        # 12-0 and the current round is won -> 13-0, map over.
        assert map_win_prob(12, 0, 1.0) == pytest.approx(1.0)

    def test_overtime_out_of_domain(self) -> None:
        assert map_win_prob(13, 12, 0.9) is None
        assert map_win_prob(12, 14, 0.9) is None

    def test_12_12_freeze(self) -> None:
        assert map_win_prob(12, 12, 0.97) == pytest.approx(0.5)

    def test_monotone_in_q(self) -> None:
        assert map_win_prob(6, 6, 0.9) > map_win_prob(6, 6, 0.5) > map_win_prob(6, 6, 0.1)

    def test_blend_is_between_branches(self) -> None:
        p = p_future(9, 9)
        hi = race_win_prob(10, 9, p)
        lo = race_win_prob(9, 10, p)
        got = map_win_prob(9, 9, 0.7)
        assert lo < got < hi


class TestVenueJoin:
    MAPPING: ClassVar[dict] = {
        "grid_series_id": "123",
        "match": {"pmus_event_slug": "cs2-x"},
        "us_markets": {
            "match": {"market_slug": "m-match", "sides": {"MEIA NOITE": "long", "BORRA": "short"}},
            "map2": {"market_slug": "m-map2", "sides": {"MEIA NOITE": "long"}},
        },
    }

    def test_named_team_buys_long(self) -> None:
        assert market_for_instance(self.MAPPING, 2, "Meia Noite", "BORRA") == ("m-map2", "long")

    def test_opponent_of_named_team_buys_short(self) -> None:
        assert market_for_instance(self.MAPPING, 2, "BORRA", "Meia Noite") == ("m-map2", "short")

    def test_missing_map_book_is_none(self) -> None:
        assert market_for_instance(self.MAPPING, 1, "Meia Noite", "BORRA") is None

    def test_unrelated_team_is_none(self) -> None:
        assert market_for_instance(self.MAPPING, 2, "Galorys", "FarmVille") is None

    def test_latest_mapping_wins(self, tmp_path) -> None:
        rows = [
            {"type": "grid_pmus_mapping", "grid_series_id": "9", "match": {"s": 1}},
            {"type": "grid_pmus_mapping", "grid_series_id": "9", "match": {"s": 2}},
            {"type": "grid_pmus_mapping", "grid_series_id": "10", "match": None},
        ]
        f = tmp_path / "mappings-20260826.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        out = load_latest_mappings(tmp_path)
        assert out["9"]["match"] == {"s": 2}
        assert "10" not in out  # unmatched rows are not joinable

    def test_match_survives_utc_day_rollover(self, tmp_path) -> None:
        # 00:00Z opens a new day file; the venue can no longer re-match an
        # in-play series (its event left the discovery lanes), so today's
        # rows come back match=None. Yesterday's match must still serve —
        # reading only the newest file blinded the 08-27 armed slate.
        yesterday = [
            {"type": "grid_pmus_mapping", "grid_series_id": "9",
             "match": {"s": 1}, "us_markets": {"map1": {"market_slug": "m"}}},
        ]
        today = [
            {"type": "grid_pmus_mapping", "grid_series_id": "9",
             "match": None, "us_markets": {}},
            {"type": "grid_pmus_mapping", "grid_series_id": "11",
             "match": {"s": 3}},
        ]
        (tmp_path / "mappings-20260826.jsonl").write_text(
            "\n".join(json.dumps(r) for r in yesterday) + "\n")
        (tmp_path / "mappings-20260827.jsonl").write_text(
            "\n".join(json.dumps(r) for r in today) + "\n")
        out = load_latest_mappings(tmp_path)
        assert out["9"]["match"] == {"s": 1}   # yesterday's match survives
        assert out["11"]["match"] == {"s": 3}  # today's new match also seen

    def test_todays_rematch_beats_yesterdays(self, tmp_path) -> None:
        (tmp_path / "mappings-20260826.jsonl").write_text(json.dumps(
            {"type": "grid_pmus_mapping", "grid_series_id": "9",
             "match": {"s": "old"}}) + "\n")
        (tmp_path / "mappings-20260827.jsonl").write_text(json.dumps(
            {"type": "grid_pmus_mapping", "grid_series_id": "9",
             "match": {"s": "new"}}) + "\n")
        assert load_latest_mappings(tmp_path)["9"]["match"] == {"s": "new"}


class TestBook:
    def test_parse_bbo(self) -> None:
        payload = {
            "marketData": {
                "bestBid": {"value": "0.2200"},
                "bestAsk": {"value": "0.5600"},
            }
        }
        assert parse_bbo(payload) == (0.22, 0.56)

    def test_zero_prices_are_absent(self) -> None:
        payload = {"marketData": {"bestBid": {"value": "0.0000"}, "bestAsk": {}}}
        assert parse_bbo(payload) == (None, None)

    def test_side_ask_long_is_ask(self) -> None:
        assert side_ask("long", 0.22, 0.56) == 0.56

    def test_side_ask_short_is_one_minus_bid(self) -> None:
        assert side_ask("short", 0.22, 0.56) == pytest.approx(0.78)
        assert side_ask("short", None, 0.56) is None


class TestRow:
    INSTANCE: ClassVar[dict] = {
        "type": "instance",
        "hypothesis_id": "H-MODELENTRY1",
        "series_id": "2984240",
        "map_number": 2,
        "round_number": 6,
        "side": "ct",
        "team": "Galorys",
        "opponent": "BORRACHEIROS",
        "q": 0.9212,
        "fair_value": 0.9212,
        "state": {"ct": {"name": "Galorys", "score": 5}, "t": {"name": "B", "score": 0}},
        "dedup_extra": "ct:0.65",
        "grid_ts_ns": 1,
        "ts_ns": 2,
    }

    def test_scores_follow_entry_side(self) -> None:
        assert instance_scores(self.INSTANCE) == (5, 0)
        t_side = {**self.INSTANCE, "side": "t"}
        assert instance_scores(t_side) == (0, 5)

    def test_round_probability_prefers_q(self) -> None:
        assert round_probability(self.INSTANCE) == 0.9212
        event = {"fair_value": 0.93}
        assert round_probability(event) == 0.93
        assert round_probability({"q": 1.5}) is None
        assert round_probability({}) is None

    def test_build_row_computes_fair(self) -> None:
        row = build_row(self.INSTANCE, ts_ns=99)
        assert row["a"] == 5 and row["b"] == 0
        assert row["p_map"] is not None and 0.9 < row["p_map"] <= 1.0
        assert row["entry_price_max"] == pytest.approx(row["p_map"] - ENTRY_MARGIN)
        assert row["qualifies"] is False  # no book attached yet

    def test_attach_book_qualification(self) -> None:
        row = build_row(self.INSTANCE, ts_ns=99)
        cheap = row["entry_price_max"] - 0.01
        attach_book(row, "m-map2", "long", bid=cheap - 0.02, ask=cheap)
        assert row["qualifies"] is True
        row2 = build_row(self.INSTANCE, ts_ns=99)
        attach_book(row2, "m-map2", "long", bid=0.9, ask=0.99)
        assert row2["qualifies"] is False

    def test_overtime_row_has_no_fair(self) -> None:
        ot = {
            **self.INSTANCE,
            "state": {"ct": {"score": 13}, "t": {"score": 13}},
        }
        row = build_row(ot, ts_ns=99)
        assert row["p_map"] is None and row["entry_price_max"] is None
