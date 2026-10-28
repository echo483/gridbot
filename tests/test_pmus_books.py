"""Hermetic tests for the PMUS book recorder (no network, no secrets, no real data)."""

from __future__ import annotations

import json
import time

from gridbot.capture.pmus_books import (
    BookTape,
    LiveTracker,
    Recorder,
    RoundRobin,
    slugs_for,
)

SID = "2999839"
REQ_NS = 1788423268881643594

# --- real-shaped fixtures (data/grid/series_state-20260903.jsonl and
# data/pmus/mappings-20260903.jsonl, trimmed to the fields the recorder uses) --


def _game_team(name: str, side: str, score: int) -> dict:
    return {"id": None, "name": name, "side": side, "score": score, "won": False,
            "__typename": "GameTeamStateCs2"}


def _segment(seq: int, winner: str, loser: str, winner_side: str, loser_side: str) -> dict:
    return {
        "sequenceNumber": seq, "type": "round", "started": True, "finished": True,
        "duration": "PT1M28.476S",
        "teams": [
            {"id": None, "name": loser, "side": loser_side, "score": None, "won": False,
             "__typename": "SegmentTeamStateCs2"},
            {"id": None, "name": winner, "side": winner_side, "score": None, "won": True,
             "__typename": "SegmentTeamStateCs2"},
        ],
    }


def _game(seq: int | None, finished: bool) -> dict:
    return {
        "sequenceNumber": seq, "started": True, "finished": finished,
        "map": {"name": "cache"}, "clock": {"currentSeconds": 60},
        "teams": [_game_team("Lynn Vision", "terrorists", 0),
                  _game_team("DEPO", "counter-terrorists", 3)],
        "segments": [_segment(1, "DEPO", "Lynn Vision", "counter-terrorists", "terrorists")],
    }


def _state(sid: str = SID, started: bool = True, finished: bool = False,
           live_map: int | None = 3, games_before: int = 2) -> dict:
    """Series with ``games_before`` finished maps and (optionally) one live map."""
    games = [_game(i, True) for i in range(1, games_before + 1)]
    if live_map is not None:
        games.append(_game(live_map, False))
    return {
        "id": sid, "format": "best-of-3", "started": started, "finished": finished,
        "valid": True,
        "teams": [{"id": None, "name": "Lynn Vision", "score": 1, "won": False},
                  {"id": None, "name": "DEPO", "score": 1, "won": False}],
        "games": games,
    }


def _ss(sid: str = SID, req_ns: int = REQ_NS, **state_over) -> dict:
    return {
        "type": "ss", "series_id": sid, "title": "cs2", "req_ns": req_ns, "latency_ms": 296,
        "ts_ns": req_ns + 296_000_000,
        "data": {"seriesState": _state(sid=sid, **state_over)},
    }


def _map_slug(n: int) -> str:
    return f"astatc-cs2-lv-depo-2026-09-03-map{n}"


MATCH_SLUG = "aec-cs2-lv-depo-2026-09-03"


def _mapping(sid: str = SID, maps: tuple[int, ...] = (1, 2, 3), match: bool = True) -> dict:
    us = {
        f"map{n}": {"market_slug": _map_slug(n), "kind": "map", "tick": 0.01, "min_qty": 0.01,
                    "sides": {"Lynn Vision": "long"}}
        for n in maps
    }
    if match:
        us["match"] = {"market_slug": MATCH_SLUG, "kind": "match", "tick": 0.01,
                       "min_qty": 0.01, "sides": {"Lynn Vision": "long", "DEPO": "short"}}
    return {"type": "grid_pmus_mapping", "ts_ns": 1788393741837419227, "grid_series_id": sid,
            "grid_teams": ["Lynn Vision", "DEPO"], "grid_start": "2026-09-03T08:00:00Z",
            "match": {"score": 1.0}, "us_markets": us}


# --- liveness ----------------------------------------------------------------


class TestLiveTracker:
    def test_started_series_is_live_on_its_current_map(self):
        tr = LiveTracker()
        tr.ingest(_ss(live_map=3), seen=0.0)
        assert tr.live_maps(1.0) == {SID: 3}

    def test_finished_series_is_polled_through_the_grace_then_dropped(self):
        tr = LiveTracker(finished_grace_s=600.0)
        tr.ingest(_ss(live_map=3), seen=0.0)
        assert tr.live_maps(1.0) == {SID: 3}
        # series over: every game finished -> keep the LAST map's book + match book on tape
        tr.ingest(_ss(finished=True, live_map=None, games_before=3), seen=2.0)
        assert tr.live_maps(3.0) == {SID: 3}
        tr.ingest(_ss(finished=True, live_map=None, games_before=3), seen=300.0)  # re-polled: grace not reset
        assert tr.live_maps(601.9) == {SID: 3}
        assert tr.live_maps(602.1) == {}
        assert tr.live_maps(700.0) == {}  # dropped for good, even if asked again

    def test_finished_series_that_was_never_seen_live_still_gets_the_grace(self):
        tr = LiveTracker(finished_grace_s=600.0)
        tr.ingest(_ss(finished=True, live_map=None, games_before=2), seen=10.0)
        assert tr.live_maps(11.0) == {SID: 2}
        assert tr.live_maps(611.0) == {}

    def test_not_started_series_is_not_live_until_it_starts(self):
        tr = LiveTracker()
        tr.ingest(_ss(started=False, live_map=None, games_before=0), seen=0.0)
        assert tr.live_maps(1.0) == {}
        tr.ingest(_ss(live_map=1, games_before=0), seen=2.0)
        assert tr.live_maps(3.0) == {SID: 1}

    def test_silent_series_goes_stale_after_stale_s(self):
        tr = LiveTracker(stale_s=900.0)
        tr.ingest(_ss(live_map=2), seen=100.0)
        assert tr.live_maps(1000.0) == {SID: 2}
        assert tr.live_maps(1000.1) == {}

    def test_grid_test_series_are_ignored(self):
        tr = LiveTracker()
        for sid in ("2", "28", "99"):
            tr.ingest(_ss(sid=sid, live_map=1), seen=0.0)
        assert tr.live_maps(1.0) == {}

    def test_between_maps_keeps_the_last_finished_map_live(self):
        tr = LiveTracker()
        tr.ingest(_ss(live_map=None), seen=0.0)  # between maps: games 1 and 2 finished, 3 not started
        assert tr.live_maps(1.0) == {SID: 2}
        tr.ingest(_ss(live_map=3), seen=2.0)  # map 3 starts: the live game wins again
        assert tr.live_maps(3.0) == {SID: 3}

    def test_started_series_with_no_games_at_all_is_not_live(self):
        tr = LiveTracker()
        tr.ingest(_ss(live_map=None, games_before=0), seen=0.0)
        assert tr.live_maps(1.0) == {}

    def test_missing_sequence_number_is_skipped(self):
        tr = LiveTracker()
        row = _ss(live_map=None)
        row["data"]["seriesState"]["games"].append(_game(None, False))
        tr.ingest(row, seen=0.0)
        assert tr.live_maps(1.0) == {}

    def test_non_ss_records_are_ignored(self):
        tr = LiveTracker()
        tr.ingest({"type": "instance", "series_id": SID}, seen=0.0)
        tr.ingest({"type": "discovery", "series_id": SID, "data": {}}, seen=0.0)
        assert tr.live_maps(1.0) == {}


# --- slug selection ----------------------------------------------------------


class TestSlugSelection:
    def test_live_map_book_then_match_book(self):
        specs = slugs_for({SID: 2}, {SID: _mapping()})
        assert specs == [
            {"slug": _map_slug(2), "series_id": SID, "map_number": 2, "kind": "map"},
            {"slug": MATCH_SLUG, "series_id": SID, "map_number": 2, "kind": "match"},
        ]

    def test_missing_entries_are_skipped(self):
        # map3 not listed by the venue -> only the match book
        assert [s["kind"] for s in slugs_for({SID: 3}, {SID: _mapping(maps=(1, 2))})] == ["match"]
        # no match book -> only the map book
        assert [s["kind"] for s in slugs_for({SID: 1}, {SID: _mapping(match=False)})] == ["map"]
        # unmapped series -> nothing
        assert slugs_for({SID: 1}, {}) == []
        # empty slug string -> skipped
        m = _mapping()
        m["us_markets"]["map1"]["market_slug"] = ""
        assert [s["kind"] for s in slugs_for({SID: 1}, {SID: m})] == ["match"]

    def test_round_robin_caps_and_rotates_over_every_slug(self):
        specs = [{"slug": f"s{i}", "series_id": str(i), "map_number": 1, "kind": "map"}
                 for i in range(10)]
        rr = RoundRobin(cap=8)
        c1 = [s["slug"] for s in rr.pick(specs)]
        c2 = [s["slug"] for s in rr.pick(specs)]
        c3 = [s["slug"] for s in rr.pick(specs)]
        assert len(c1) == len(c2) == len(c3) == 8
        assert c1 == [f"s{i}" for i in range(8)]
        assert c2 == ["s8", "s9", "s0", "s1", "s2", "s3", "s4", "s5"]
        assert c3 == ["s6", "s7", "s8", "s9", "s0", "s1", "s2", "s3"]
        assert set(c1) | set(c2) == {f"s{i}" for i in range(10)}  # every slug within 2 cycles

    def test_round_robin_under_cap_returns_everything(self):
        specs = [{"slug": f"s{i}", "series_id": "1", "map_number": 1, "kind": "map"}
                 for i in range(3)]
        assert RoundRobin(cap=8).pick(specs) == specs


# --- write policy ------------------------------------------------------------


class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, obj: dict) -> None:
        obj.setdefault("ts_ns", time.time_ns())
        self.rows.append(obj)


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


SPEC = {"slug": _map_slug(2), "series_id": SID, "map_number": 2, "kind": "map"}


class TestBookTape:
    def test_change_only_plus_heartbeat(self):
        clock, w = FakeClock(0.0), FakeWriter()
        tape = BookTape(w, clock=clock, heartbeat_s=30.0)
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS, 83) is True
        assert w.rows[-1] == {
            "type": "pmus_bbo", "slug": _map_slug(2), "series_id": SID, "map_number": 2,
            "kind": "map", "bid": 0.47, "ask": 0.55, "req_ns": REQ_NS, "latency_ms": 83,
            "heartbeat": False, "ts_ns": w.rows[-1]["ts_ns"],
        }
        clock.t = 10.0
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS + 1, 80) is False  # unchanged, inside window
        clock.t = 29.9
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS + 2, 80) is False
        clock.t = 30.0
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS + 3, 80) is True  # heartbeat
        assert w.rows[-1]["heartbeat"] is True and w.rows[-1]["req_ns"] == REQ_NS + 3
        clock.t = 31.0
        assert tape.observe(SPEC, 0.48, 0.55, REQ_NS + 4, 80) is True  # changed -> immediate
        assert w.rows[-1]["heartbeat"] is False and w.rows[-1]["bid"] == 0.48
        clock.t = 40.0
        assert tape.observe(SPEC, 0.48, 0.55, REQ_NS + 5, 80) is False
        clock.t = 61.0
        assert tape.observe(SPEC, 0.48, 0.55, REQ_NS + 6, 80) is True  # 30s since last write
        assert w.rows[-1]["heartbeat"] is True
        assert len(w.rows) == 4

    def test_slugs_are_tracked_independently(self):
        clock, w = FakeClock(0.0), FakeWriter()
        tape = BookTape(w, clock=clock)
        other = {**SPEC, "slug": MATCH_SLUG, "kind": "match"}
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS, 80) is True
        assert tape.observe(other, 0.47, 0.55, REQ_NS, 80) is True
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS, 80) is False

    def test_dead_book_none_pair_is_written_once(self):
        clock, w = FakeClock(0.0), FakeWriter()
        tape = BookTape(w, clock=clock)
        assert tape.observe(SPEC, None, None, REQ_NS, 80) is True
        assert w.rows[-1]["bid"] is None and w.rows[-1]["ask"] is None
        assert tape.observe(SPEC, None, None, REQ_NS, 80) is False

    def test_error_row_carries_class_name_only_and_keeps_change_state(self):
        clock, w = FakeClock(0.0), FakeWriter()
        tape = BookTape(w, clock=clock)
        tape.observe(SPEC, 0.47, 0.55, REQ_NS, 80)
        tape.error(SPEC, ValueError("body echoing request https://gateway/secret?x=1"))
        row = w.rows[-1]
        assert row["type"] == "pmus_bbo_error" and row["slug"] == _map_slug(2)
        assert row["error"] == "ValueError"
        assert "secret" not in json.dumps(row) and "gateway" not in json.dumps(row)
        assert tape.observe(SPEC, 0.47, 0.55, REQ_NS, 80) is False  # error did not reset


# --- recorder cycle with a fake gateway client -------------------------------


def _payload(bid: str = "0.47", ask: str = "0.55") -> dict:
    return {"marketData": {"bestBid": {"value": bid}, "bestAsk": {"value": ask}}}


class FakeMarkets:
    def __init__(self, payloads: dict[str, dict] | None = None) -> None:
        self.payloads = payloads or {}
        self.raise_once: dict[str, Exception] = {}
        self.calls: list[str] = []

    def bbo(self, slug: str) -> dict:
        self.calls.append(slug)
        exc = self.raise_once.pop(slug, None)
        if exc is not None:
            raise exc
        return self.payloads.get(slug, _payload())


class FakeClient:
    def __init__(self, payloads: dict[str, dict] | None = None) -> None:
        self.markets = FakeMarkets(payloads)


def _write_jsonl(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.writelines(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)


def _recorder(tmp_path, client: FakeClient, clock: FakeClock, ss_rows: list[dict],
              mappings: list[dict], **kw) -> tuple[Recorder, FakeWriter]:
    grid, pmus = tmp_path / "grid", tmp_path / "pmus"
    _write_jsonl(grid / "series_state-20260903.jsonl", ss_rows)
    _write_jsonl(pmus / "mappings-20260903.jsonl", mappings)
    w = FakeWriter()
    kw.setdefault("from_start", True)
    rec = Recorder(client=client, writer=w, grid_dir=grid, pmus_dir=pmus, clock=clock,
                   sleep=lambda _s: None, **kw)
    return rec, w


class TestRecorderCycle:
    def test_polls_live_map_and_match_books_and_writes_parsed_rows(self, tmp_path):
        client, clock = FakeClient(), FakeClock(100.0)
        rec, w = _recorder(tmp_path, client, clock, [_ss(live_map=2)], [_mapping()])
        summary = rec.cycle()
        assert summary["live"] == {SID: 2}
        assert summary["polled"] == [_map_slug(2), MATCH_SLUG]
        assert client.markets.calls == [_map_slug(2), MATCH_SLUG]
        assert summary["written"] == 2 and summary["errors"] == 0
        by_slug = {row["slug"]: row for row in w.rows}
        assert by_slug[_map_slug(2)]["kind"] == "map"
        assert by_slug[MATCH_SLUG]["kind"] == "match"
        for row in w.rows:
            assert row["type"] == "pmus_bbo"
            assert (row["bid"], row["ask"]) == (0.47, 0.55)
            assert row["series_id"] == SID and row["map_number"] == 2
            assert isinstance(row["req_ns"], int) and row["req_ns"] > 1_700_000_000_000_000_000
            assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
            assert row["heartbeat"] is False
        # unchanged book, inside the heartbeat window -> nothing new
        clock.t = 102.0
        assert rec.cycle()["written"] == 0
        # heartbeat window elapsed -> one heartbeat per slug
        clock.t = 131.0
        assert rec.cycle()["written"] == 2
        assert [row["heartbeat"] for row in w.rows[-2:]] == [True, True]
        rec.close()

    def test_fetch_failure_writes_error_row_and_the_loop_continues(self, tmp_path):
        client, clock = FakeClient(), FakeClock(0.0)
        client.markets.raise_once[_map_slug(2)] = ConnectionError("timeout to gateway")
        rec, w = _recorder(tmp_path, client, clock, [_ss(live_map=2)], [_mapping()])
        summary = rec.cycle()
        assert summary["errors"] == 1 and summary["written"] == 1
        assert [row["type"] for row in w.rows] == ["pmus_bbo_error", "pmus_bbo"]
        assert w.rows[0]["slug"] == _map_slug(2) and w.rows[0]["error"] == "ConnectionError"
        assert "timeout" not in json.dumps(w.rows[0])
        assert w.rows[1]["slug"] == MATCH_SLUG
        # next cycle: the failed slug is fetched again and recorded
        clock.t = 2.0
        summary = rec.cycle()
        assert summary["errors"] == 0 and summary["written"] == 1
        assert w.rows[-1]["slug"] == _map_slug(2) and w.rows[-1]["type"] == "pmus_bbo"
        rec.close()

    def test_zero_prices_are_dead_book_placeholders(self, tmp_path):
        client = FakeClient({_map_slug(2): _payload("0", "0")})
        rec, w = _recorder(tmp_path, client, FakeClock(0.0), [_ss(live_map=2)],
                           [_mapping(match=False)])
        rec.cycle()
        assert len(w.rows) == 1 and (w.rows[0]["bid"], w.rows[0]["ask"]) == (None, None)
        rec.close()

    def test_finished_series_is_polled_on_its_last_map_until_the_grace_ends(self, tmp_path):
        client = FakeClient()
        clock = FakeClock(0.0)
        rec, w = _recorder(tmp_path, client, clock,
                           [_ss(live_map=2), _ss(req_ns=REQ_NS + 1, finished=True, live_map=None)],
                           [_mapping()])
        summary = rec.cycle()
        assert summary["live"] == {SID: 2}
        assert summary["polled"] == [_map_slug(2), MATCH_SLUG]
        assert [row["map_number"] for row in w.rows] == [2, 2]
        clock.t = 601.0  # past FINISHED_GRACE_S: dropped, nothing polled
        summary = rec.cycle()
        assert summary["live"] == {} and summary["polled"] == []
        rec.close()

    def test_cap_round_robins_across_cycles(self, tmp_path):
        sids = [str(3000000 + i) for i in range(5)]
        ss_rows = [_ss(sid=s, req_ns=REQ_NS + i, live_map=1) for i, s in enumerate(sids)]
        mappings = []
        for i, s in enumerate(sids):
            m = _mapping(sid=s, maps=(1,))
            m["us_markets"]["map1"]["market_slug"] = f"map-{i}"
            m["us_markets"]["match"]["market_slug"] = f"match-{i}"
            mappings.append(m)
        client = FakeClient()
        rec, _w = _recorder(tmp_path, client, FakeClock(0.0), ss_rows, mappings, cap=8)
        c1 = rec.cycle()
        c2 = rec.cycle()
        assert c1["candidates"] == 10 and len(c1["polled"]) == 8 and len(c2["polled"]) == 8
        assert set(c1["polled"]) | set(c2["polled"]) == {f"map-{i}" for i in range(5)} | {
            f"match-{i}" for i in range(5)}
        rec.close()

    def test_startup_primes_liveness_from_the_tail_of_the_newest_ss_file(self, tmp_path):
        # Follow(from_start=False) sits at EOF; without the prime a --once run polls nothing.
        fresh = _ss(live_map=2, req_ns=time.time_ns() - 5_000_000_000)
        stale = _ss(sid="3000001", live_map=1, req_ns=time.time_ns() - 1_200_000_000_000)
        rec, w = _recorder(tmp_path, FakeClient(), FakeClock(5000.0), [stale, fresh],
                           [_mapping(), _mapping(sid="3000001")], from_start=False)
        summary = rec.cycle()
        assert summary["live"] == {SID: 2}  # 20-minute-old row is past STALE_S
        assert summary["polled"] == [_map_slug(2), MATCH_SLUG]
        assert len(w.rows) == 2
        rec.close()

    def test_no_prime_means_eof_tail_sees_nothing_until_new_rows_land(self, tmp_path):
        rec, _ = _recorder(tmp_path, FakeClient(), FakeClock(0.0), [_ss(live_map=2)],
                           [_mapping()], from_start=False, prime=False)
        assert rec.cycle()["polled"] == []
        _write_jsonl(tmp_path / "grid" / "series_state-20260903.jsonl",
                     [_ss(live_map=3, req_ns=REQ_NS + 1)])
        assert rec.cycle()["polled"] == [_map_slug(3), MATCH_SLUG]
        rec.close()
