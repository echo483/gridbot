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
    parse_book,
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


class TestSlateCoverage:
    """Queue 16 full-slate coverage: every live series gets a mapentry
    evaluation per coherent round — a series starting mid-session must
    produce rows from its first polled round, without any registered
    hypothesis trigger firing."""

    @staticmethod
    def _state(series_id: str = "777") -> dict:
        return {
            "id": series_id,
            "started": True,
            "finished": False,
            "games": [
                {
                    "started": True,
                    "finished": False,
                    "sequenceNumber": 1,
                    "teams": [
                        {"__typename": "TeamStateCs2", "name": "Alpha", "side": "counter-terrorists", "score": 2,
                         "players": [{"alive": True}] * 5},
                        {"__typename": "TeamStateCs2", "name": "Beta", "side": "terrorists", "score": 1,
                         "players": [{"alive": True}] * 5},
                    ],
                    "segments": [
                        {"type": "round", "finished": True, "sequenceNumber": 1,
                         "teams": [{"__typename": "TeamStateCs2", "name": "Beta", "side": "terrorists", "won": True}]},
                        {"type": "round", "finished": True, "sequenceNumber": 2,
                         "teams": [{"__typename": "TeamStateCs2", "name": "Alpha", "side": "counter-terrorists", "won": True}]},
                        {"type": "round", "finished": True, "sequenceNumber": 3,
                         "teams": [{"__typename": "TeamStateCs2", "name": "Alpha", "side": "counter-terrorists", "won": True}]},
                        {"type": "round", "finished": False, "sequenceNumber": 4},
                    ],
                    "clock": {"currentSeconds": 75.0},
                }
            ],
        }

    @staticmethod
    def _ss_record(series_id: str = "777") -> dict:
        return {"type": "ss", "title": "cs2", "series_id": series_id,
                "ts_ns": 1, "data": {"seriesState": TestSlateCoverage._state(series_id)}}

    def test_slate_instance_built_from_coherent_snapshot(self) -> None:
        from gridbot.instruments.mapentry import slate_instance

        inst = slate_instance(self._state(), q_ct=0.6)
        assert inst is not None
        assert inst["hypothesis_id"] == "H-MAPENTRY1-SLATE"
        assert inst["series_id"] == "777"
        assert inst["round_number"] == 4
        assert inst["q"] == 0.6

    def test_slate_skips_transition_and_no_game(self) -> None:
        from gridbot.instruments.mapentry import slate_instance

        mid_switch = self._state()
        mid_switch["games"][0]["teams"][0]["score"] = 5  # incoherent with winners
        assert slate_instance(mid_switch, q_ct=0.6) is None
        assert slate_instance({"id": "x", "games": []}, q_ct=0.6) is None

    def test_new_series_mid_session_produces_mapentry_row(self, tmp_path) -> None:
        from gridbot.instruments.mapentry_recorder import Recorder

        class FakeBooks:
            def top(self, slug: str):
                return (0.40, 0.60)

        rec = Recorder(books=FakeBooks(), out_dir=tmp_path)
        rec.mappings = {"777": {"match": {"s": 1}, "us_markets": {"map1": {"market_slug": "m"}}}}
        disposition = rec.handle_slate(self._ss_record())
        assert disposition is not None
        day_files = list(tmp_path.glob("mapentry-*.jsonl"))
        assert day_files, "slate eval must land in the day file"
        row = json.loads(day_files[0].read_text().splitlines()[0])
        assert row["series_id"] == "777"
        assert row["src_hypothesis_id"] == "H-MAPENTRY1-SLATE"

    def test_second_snapshot_same_round_is_deduped(self, tmp_path) -> None:
        from gridbot.instruments.mapentry_recorder import Recorder

        class FakeBooks:
            def top(self, slug: str):
                return (0.40, 0.60)

        rec = Recorder(books=FakeBooks(), out_dir=tmp_path)
        rec.mappings = {}
        assert rec.handle_slate(self._ss_record()) is not None
        assert rec.handle_slate(self._ss_record()) is None  # same round, once only

    def test_slate_ignores_test_series_and_non_ss(self, tmp_path) -> None:
        from gridbot.instruments.mapentry_recorder import Recorder

        class FakeBooks:
            def top(self, slug: str):
                return (0.40, 0.60)

        rec = Recorder(books=FakeBooks(), out_dir=tmp_path)
        assert rec.handle_slate({"type": "book"}) is None

    def test_slate_row_carries_full_depth_book(self, tmp_path) -> None:
        from types import SimpleNamespace

        from gridbot.instruments.mapentry import parse_book
        from gridbot.instruments.mapentry_recorder import Recorder

        summary = parse_book(_book_payload())

        class FakeBooks:
            def top(self, slug: str):
                return (0.40, 0.60)

            def book(self, slug: str):
                return summary

        rec = Recorder(books=FakeBooks(), out_dir=tmp_path)
        rec.slate = SimpleNamespace(predict=lambda _f: 0.6)  # arm a q provider
        rec.mappings = {"777": {"match": {"s": 1}, "us_markets": {"map1": {"market_slug": "m", "sides": {"Alpha": "long"}}}}}
        assert rec.handle_slate(self._ss_record()) is not None
        day_file = list(tmp_path.glob("mapentry-*.jsonl"))[0]
        row = json.loads(day_file.read_text().splitlines()[0])
        assert row["book"] == summary
        # entry semantics unchanged against the top-only baseline
        assert row["bid"] == 0.40 and row["ask"] == 0.60

    def test_slate_records_row_when_book_fetch_fails(self, tmp_path) -> None:
        # A slow/timed-out book fetch must fail closed: the row is still recorded
        # with no depth, qualifies is untouched by the empty book, and the eval
        # loop is not killed. (F2 fail-closed + F3)
        from types import SimpleNamespace

        from gridbot.instruments.mapentry_recorder import Recorder

        class FakeBooks:
            def top(self, slug: str):
                # A deep cheap long ask -> qualifies should hold regardless of book.
                return (0.30, 0.40)

            def book(self, slug: str):
                raise TimeoutError("simulated gateway hang")  # noqa: TRY301

        rec = Recorder(books=FakeBooks(), out_dir=tmp_path)
        rec.slate = SimpleNamespace(predict=lambda _f: 0.9)  # strong q -> high p_map
        rec.mappings = {"777": {"match": {"s": 1}, "us_markets": {"map1": {"market_slug": "m", "sides": {"Alpha": "long"}}}}}
        disposition = rec.handle_slate(self._ss_record())
        assert disposition is not None  # row still recorded
        day_file = list(tmp_path.glob("mapentry-*.jsonl"))[0]
        row = json.loads(day_file.read_text().splitlines()[0])
        # depth fetch failed -> no book evidence, but the row survived
        assert row["book"] is None
        # the empty book did not break qualification
        assert row["qualifies"] is True
        assert row["skip_reason"] in (None, "recorded", "qualifies")


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


def _book_payload(n_bids: int = 12, n_offers: int = 12) -> dict:
    """A markets.book payload shaped like the live gateway output (px/qty
    strings, bids best-first descending price)."""
    bids = [
        {"px": {"value": f"{round(0.41 - 0.01 * i, 4):.4f}", "currency": "USD"},
         "qty": f"{100.0:.4f}"}
        for i in range(n_bids)
    ]
    offers = [
        {"px": {"value": f"{round(0.42 + 0.01 * i, 4):.4f}", "currency": "USD"},
         "qty": f"{200.0:.4f}"}
        for i in range(n_offers)
    ]
    return {"marketData": {"marketSlug": "m", "bids": bids, "offers": offers}}


class TestParseBook:
    def test_parse_book_full_depth(self) -> None:
        book = parse_book(_book_payload())
        assert book["bid"] == pytest.approx(0.41)
        assert book["ask"] == pytest.approx(0.42)
        assert book["mid"] == pytest.approx(0.4150)
        assert book["spread"] == pytest.approx(0.0100)
        assert book["bid_size"] == pytest.approx(100.0)
        assert book["ask_size"] == pytest.approx(200.0)
        assert book["bid_top5_total"] == pytest.approx(500.0)
        assert book["ask_top5_total"] == pytest.approx(1000.0)
        assert book["imbalance"] == pytest.approx(0.3333)  # rounded to 4 dp

    def test_parse_book_compacts_depth(self) -> None:
        book = parse_book(_book_payload(n_bids=20, n_offers=20))
        assert len(book["depth"]["bids"]) <= 10
        assert len(book["depth"]["offers"]) <= 10
        for lv in book["depth"]["bids"] + book["depth"]["offers"]:
            assert isinstance(lv["price"], float)
            assert isinstance(lv["size"], float)
        # best levels first on both sides
        assert book["depth"]["bids"][0]["price"] == pytest.approx(0.41)
        assert book["depth"]["offers"][0]["price"] == pytest.approx(0.42)

    def test_parse_book_empty_and_malformed(self) -> None:
        book = parse_book({})
        assert book["bid"] is None and book["ask"] is None
        assert book["mid"] is None and book["spread"] is None
        assert book["imbalance"] is None
        assert book["depth"]["bids"] == [] and book["depth"]["offers"] == []
        # dead levels (zero price) are dropped, not counted
        junk = {"marketData": {"bids": [{"px": {"value": "0.0000"}, "qty": "5"}],
                               "offers": [{"px": {"value": "garbage"}, "qty": "x"}]}}
        b2 = parse_book(junk)
        assert b2["bid"] is None and b2["ask"] is None

    def test_parse_book_drops_malformed_levels_fail_closed(self) -> None:
        # Non-dict px (bare string) used to raise AttributeError out of parse_book,
        # voiding all depth. Both must now be dropped silently, never raised.
        junk = {"marketData": {
            "bids": [
                {"px": {"value": "0.41"}, "qty": "100"},   # clean -> kept
                {"px": "0.40", "qty": "50"},              # non-dict px -> dropped
                None,                                     # None element -> dropped
            ],
            "offers": [
                {"px": {"value": "0.42"}, "qty": "200"},  # clean -> kept
                "0.43",                                   # bare string -> dropped
            ],
        }}
        book = parse_book(junk)
        assert book["imbalance"] == pytest.approx(0.3333)  # from the kept levels only
        assert book["depth"]["bids"] == [{"price": 0.41, "size": 100.0}]
        assert book["depth"]["offers"] == [{"price": 0.42, "size": 200.0}]
        assert book["bid"] == pytest.approx(0.41)
        assert book["ask"] == pytest.approx(0.42)

    def test_parse_book_dict_as_bids_drops_keys(self) -> None:
        # If bids/offers arrive as a dict, iterating yields its keys (strings);
        # each is a non-dict level and must be dropped, not raised.
        book = parse_book({
            "marketData": {"bids": {"L1": {"px": {"value": "0.41"}, "qty": "100"}},
                           "offers": []},
        })
        assert book["bid"] is None
        assert book["depth"]["bids"] == []
        assert book["depth"]["offers"] == []

    def test_parse_book_non_dict_px_alone_does_not_raise(self) -> None:
        # Whole-sides malformed: only a string px and a None element, no clean level.
        book = parse_book({
            "marketData": {"bids": [{"px": "0.99", "qty": "1"}, None],
                           "offers": [[1, 2, 3]]},  # offers a list entry, not dict
        })
        assert book["bid"] is None and book["ask"] is None
        assert book["depth"]["bids"] == []
        assert book["depth"]["offers"] == []


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

    def test_attach_book_carries_depth_without_changing_entry(self) -> None:
        row = build_row(self.INSTANCE, ts_ns=99)
        summary = parse_book(_book_payload())
        cheap = row["entry_price_max"] - 0.01
        attach_book(row, "m-map2", "long", bid=cheap - 0.02, ask=cheap, book=summary)
        # full-depth evidence persisted...
        assert row["book"] is summary
        assert row["book"]["bid"] == pytest.approx(0.41)
        assert row["book"]["depth"]["bids"]
        # ...but the frozen entry fields come from the bare pair, untouched.
        assert row["bid"] == pytest.approx(cheap - 0.02)
        assert row["ask"] == pytest.approx(cheap)
        assert row["side_ask"] == pytest.approx(cheap)
        assert row["qualifies"] is True
        # book stays None when depth evidence is absent
        row2 = build_row(self.INSTANCE, ts_ns=99)
        attach_book(row2, "m-map2", "long", bid=0.9, ask=0.99)
        assert row2["book"] is None

    def test_overtime_row_has_no_fair(self) -> None:
        ot = {
            **self.INSTANCE,
            "state": {"ct": {"score": 13}, "t": {"score": 13}},
        }
        row = build_row(ot, ts_ns=99)
        assert row["p_map"] is None and row["entry_price_max"] is None
