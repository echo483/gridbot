"""Tests for scripts/timing_chain.py on synthetic day files under tmp_path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (pytest
# only adds tests/ to sys.path).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from gridbot.corpus import rows as r
from scripts import timing_chain as tc

SCRIPT = _project_root / "scripts" / "timing_chain.py"
SID = "2999839"
NS = 1_000_000_000
T0 = 1_788_423_268_881_643_594
DAY = "20260903"
SLUG = "astatc-cs2-vexar-falcons-2026-09-03-map1"
CT, T = "counter-terrorists", "terrorists"


# --- GRID fixtures (real shape, trimmed) --------------------------------------


def _seg_team(name: str, side: str, won: bool) -> dict:
    return {"id": None, "name": name, "side": side, "score": None, "won": won,
            "__typename": "SegmentTeamStateCs2"}


def _segment(seq: int, winner: str, loser: str, winner_side: str, finished: bool = True) -> dict:
    loser_side = T if winner_side == CT else CT
    return {"sequenceNumber": seq, "type": "round", "started": True, "finished": finished,
            "duration": "PT1M28.476S",
            "teams": [_seg_team(loser, loser_side, False), _seg_team(winner, winner_side, finished)]}


def _game_team(name: str, side: str, score: int) -> dict:
    return {"id": None, "name": name, "side": side, "score": score, "won": False,
            "__typename": "GameTeamStateCs2"}


def _rounds(n: int, winner: str = "Vexar", loser: str = "Falcons",
            winner_side: str = CT) -> list[tuple[str, str, str]]:
    return [(winner, loser, winner_side)] * n


def _ss(req_ns: int, rounds: list[tuple[str, str, str]], ct_name: str = "Vexar",
        t_name: str = "Falcons", map_number: int = 1, sid: str = SID,
        open_round: bool = True) -> dict:
    """A poll whose current game shows ``rounds`` resolved and the next round open.

    Game-level sides are whatever the caller says (``ct_name`` on CT) — the
    D1 trap is exactly that these can disagree with a resolved segment's sides.
    """
    segments = [_segment(i + 1, w, lo, side) for i, (w, lo, side) in enumerate(rounds)]
    if open_round:
        segments.append(_segment(len(rounds) + 1, ct_name, t_name, CT, finished=False))
    ct_score = sum(1 for w, _, _ in rounds if w == ct_name)
    return {
        "type": "ss", "series_id": sid, "title": "cs2", "req_ns": req_ns, "latency_ms": 250,
        "ts_ns": req_ns + 250_000_000,
        "data": {"seriesState": {
            "id": sid, "format": "best-of-3", "started": True, "finished": False, "valid": True,
            "teams": [{"id": None, "name": ct_name, "score": 0, "won": False},
                      {"id": None, "name": t_name, "score": 0, "won": False}],
            "games": [{
                "sequenceNumber": map_number, "started": True, "finished": False,
                "map": {"name": "nuke"}, "clock": {"currentSeconds": 60},
                "teams": [_game_team(ct_name, CT, ct_score),
                          _game_team(t_name, T, len(rounds) - ct_score)],
                "segments": segments,
            }],
        }},
    }


def _mapping(named: str = "Vexar", slug: str = SLUG, sid: str = SID) -> dict:
    return {"type": "grid_pmus_mapping", "ts_ns": T0 - 3600 * NS, "grid_series_id": sid,
            "grid_teams": ["Vexar", "Falcons"], "match": {"score": 1.0},
            "us_markets": {"map1": {"market_slug": slug, "kind": "map", "tick": 0.01,
                                    "min_qty": 0.01, "sides": {named: "long"}}}}


def _bbo(req_ns: int, bid: float | None, ask: float | None, slug: str = SLUG,
         heartbeat: bool = False) -> dict:
    return {"type": "pmus_bbo", "slug": slug, "series_id": SID, "map_number": 1, "kind": "map",
            "bid": bid, "ask": ask, "req_ns": req_ns, "latency_ms": 80, "heartbeat": heartbeat,
            "ts_ns": req_ns + 80_000_000}


def _event(n: int = 4, winner: str = "Vexar", loser: str = "Falcons", t_prev: int = T0,
           t_obs: int = T0 + 12 * NS) -> dict:
    return {"series_id": SID, "map_number": 1, "round_number": n, "winner_team": winner,
            "loser_team": loser, "t_prev_ns": t_prev, "t_obs_ns": t_obs, "poll_gap_s": 12.0,
            "outcome": None, "legs": {"grid_rtt_ms": 250.0}}


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))


# --- step 1 ------------------------------------------------------------------


class TestRoundEndEvents:
    def test_winner_name_comes_from_the_segment_not_the_observing_sides(self):
        # Rounds 1-3 won by Vexar on CT; round 4 won by Vexar AFTER a side swap (as T).
        rounds = _rounds(3) + [("Vexar", "Falcons", T)]
        prev = _ss(T0, rounds[:3])
        # Observing snapshot still lists Vexar on CT: its sides DISAGREE with segment 4.
        obs = _ss(T0 + 12 * NS, rounds, ct_name="Vexar", t_name="Falcons")
        game = r.current_game(obs["data"]["seriesState"])
        _ct, t_node = r.sided_teams(game)
        assert r.round_winners(game)[-1] == "t" and t_node["name"] == "Falcons"  # the trap
        events = tc.round_end_events([obs, prev])  # unsorted on purpose
        assert len(events) == 1
        ev = events[0]
        assert ev["winner_team"] == "Vexar" and ev["loser_team"] == "Falcons"
        assert ev["round_number"] == 4 and ev["map_number"] == 1 and ev["series_id"] == SID
        assert ev["t_prev_ns"] == T0 and ev["t_obs_ns"] == T0 + 12 * NS
        assert ev["poll_gap_s"] == 12.0 and ev["outcome"] is None
        assert ev["legs"]["grid_rtt_ms"] == 250.0

    def test_first_poll_of_a_map_emits_nothing(self):
        assert tc.round_end_events([_ss(T0, _rounds(3))]) == []
        # a second map's first poll is a fresh bracket too
        events = tc.round_end_events([_ss(T0, _rounds(3)), _ss(T0 + 12 * NS, _rounds(2),
                                                                map_number=2)])
        assert events == []

    def test_unchanged_count_emits_nothing(self):
        assert tc.round_end_events([_ss(T0, _rounds(3)), _ss(T0 + 12 * NS, _rounds(3))]) == []

    def test_jump_of_two_is_skipped_as_multi_round_gap(self):
        events = tc.round_end_events([_ss(T0, _rounds(3)), _ss(T0 + 12 * NS, _rounds(5))])
        assert len(events) == 1
        assert events[0]["outcome"] == "skipped:multi_round_gap"
        assert events[0]["round_number"] == 5 and events[0]["winner_team"] is None

    def test_two_won_flags_is_ambiguous(self):
        obs = _ss(T0 + 12 * NS, _rounds(4))
        seg4 = obs["data"]["seriesState"]["games"][0]["segments"][3]
        for team in seg4["teams"]:
            team["won"] = True
        events = tc.round_end_events([_ss(T0, _rounds(3)), obs])
        assert [ev["outcome"] for ev in events] == ["skipped:ambiguous_winner"]

    def test_grid_test_series_are_excluded(self):
        rows = [_ss(T0, _rounds(3), sid="28"), _ss(T0 + 12 * NS, _rounds(4), sid="28")]
        assert tc.round_end_events(rows) == []

    def test_consecutive_rounds_each_bracketed_by_their_own_polls(self):
        rows = [_ss(T0 + i * 12 * NS, _rounds(3 + i)) for i in range(3)]
        events = tc.round_end_events(rows)
        assert [(ev["round_number"], ev["t_prev_ns"], ev["t_obs_ns"]) for ev in events] == [
            (4, T0, T0 + 12 * NS), (5, T0 + 12 * NS, T0 + 24 * NS)]


# --- step 2 ------------------------------------------------------------------


class TestFirstMove:
    def test_first_tick_move_is_picked_and_sub_tick_jitter_is_not(self):
        t_prev, t_obs = T0, T0 + 12 * NS
        times = [t_prev - NS, t_prev + 2 * NS, t_prev + 6 * NS, t_prev + 8 * NS]
        mids = [0.50, 0.505, 0.51, 0.53]
        res = tc.first_move(times, mids, t_prev, t_obs)
        assert res["mid_before"] == 0.50
        assert res["t_move_ns"] == t_prev + 6 * NS
        assert res["move_size"] == 0.01 and res["mid_after"] == 0.51
        assert res["mid_at_obs"] == 0.53

    def test_row_at_exactly_t_prev_counts_as_before(self):
        res = tc.first_move([T0, T0 + NS], [0.40, 0.45], T0, T0 + 12 * NS)
        assert res["mid_before"] == 0.40 and res["t_move_ns"] == T0 + NS

    def test_no_pre_book_when_every_row_is_after_t_prev(self):
        res = tc.first_move([T0 + NS, T0 + 2 * NS], [0.40, 0.45], T0, T0 + 12 * NS)
        assert res["mid_before"] is None and res["t_move_ns"] is None

    def test_move_outside_the_window_is_not_a_move(self):
        t_prev, t_obs = T0, T0 + 12 * NS
        times = [t_prev - NS, t_obs + 301 * NS]
        res = tc.first_move(times, [0.50, 0.60], t_prev, t_obs)
        assert res["t_move_ns"] is None and res["mid_before"] == 0.50
        res = tc.first_move([t_prev - NS, t_obs + 300 * NS], [0.50, 0.60], t_prev, t_obs)
        assert res["t_move_ns"] == t_obs + 300 * NS


class TestJoinBooks:
    def test_negative_lead_when_the_book_moves_before_our_poll_shows_the_round_end(self):
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51, heartbeat=True),  # heartbeat = valid pre-book
                _bbo(T0 + 5 * NS, 0.51, 0.53)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "moved"
        assert ev["slug"] == SLUG and ev["us_side"] == "long"
        assert ev["lead_vs_obs_s"] == -7.0 and ev["lead_vs_prev_s"] == 5.0
        assert ev["mid_before"] == 0.50 and ev["mid_after"] == 0.52 and ev["move_size"] == 0.02
        assert ev["direction_agrees"] is True

    def test_positive_lead_when_the_book_moves_after_our_poll(self):
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51), _bbo(T0 + 20 * NS, 0.53, 0.55)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "moved" and ev["lead_vs_obs_s"] == 8.0

    def test_no_move_300s_when_the_book_is_quiet(self):
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51), _bbo(T0 + 100 * NS, 0.49, 0.51, heartbeat=True),
                _bbo(T0 + 12 * NS + 301 * NS, 0.55, 0.57)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "no_move_300s"
        assert ev["mid_before"] == 0.50 and ev["t_move_ns"] is None
        assert "lead_vs_obs_s" not in ev

    def test_no_pre_book_and_no_book_skips(self):
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series([_bbo(T0 + NS, 0.49, 0.51)]))
        assert ev["outcome"] == "skipped:no_pre_book"
        ev = _event()
        tc.join_books([ev], {}, tc.book_series([_bbo(T0 - NS, 0.49, 0.51)]))
        assert ev["outcome"] == "skipped:no_book"
        ev = _event()
        m = _mapping()
        m["us_markets"] = {"match": {"market_slug": "x", "kind": "match", "sides": {}}}
        tc.join_books([ev], {SID: m}, tc.book_series([_bbo(T0 - NS, 0.49, 0.51)]))
        assert ev["outcome"] == "skipped:no_book"

    def test_one_sided_rows_carry_no_mid(self):
        rows = [_bbo(T0 - 2 * NS, None, 0.51), _bbo(T0 - NS, 0.49, 0.51),
                _bbo(T0 + 3 * NS, None, None), _bbo(T0 + 4 * NS, 0.51, 0.53)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "moved" and ev["t_move_ns"] == T0 + 4 * NS

    def test_named_loser_means_short_and_a_downward_move_agrees(self):
        # The venue names Falcons; Vexar (the winner) is the No side -> us_side short.
        rows = [_bbo(T0 - 2 * NS, 0.59, 0.61), _bbo(T0 + 3 * NS, 0.57, 0.59)]
        ev = _event(winner="Vexar", loser="Falcons")
        tc.join_books([ev], {SID: _mapping(named="Falcons")}, tc.book_series(rows))
        assert ev["us_side"] == "short" and ev["outcome"] == "moved"
        assert ev["move_size"] == -0.02 and ev["direction_agrees"] is True
        # an UPWARD move on a short-oriented event disagrees
        rows = [_bbo(T0 - 2 * NS, 0.59, 0.61), _bbo(T0 + 3 * NS, 0.61, 0.63)]
        ev = _event(winner="Vexar", loser="Falcons")
        tc.join_books([ev], {SID: _mapping(named="Falcons")}, tc.book_series(rows))
        assert ev["us_side"] == "short" and ev["direction_agrees"] is False

    def test_already_skipped_events_are_left_alone(self):
        ev = {**_event(), "outcome": "skipped:multi_round_gap", "winner_team": None,
              "loser_team": None}
        tc.join_books([ev], {SID: _mapping()}, tc.book_series([_bbo(T0 - NS, 0.49, 0.51)]))
        assert ev["outcome"] == "skipped:multi_round_gap" and "slug" not in ev


# --- step 3 + summary --------------------------------------------------------


def _pipeline_rows(t_obs: int) -> tuple[list[dict], list[dict], list[dict]]:
    grid_ts = t_obs + 250_000_000  # ss.ts_ns
    inst = {"type": "instance", "hypothesis_id": "H-MAPENTRY1-SLATE", "series_id": SID,
            "map_number": 1, "round_number": 5, "side": "ct", "team": "Vexar",
            "opponent": "Falcons", "dedup_extra": "slate", "grid_ts_ns": grid_ts,
            "ts_ns": grid_ts + 300_000_000}
    me = {"type": "mapentry", "hypothesis_id": "H-MAPENTRY1", "src_hypothesis_id":
          "H-MAPENTRY1-SLATE", "series_id": SID, "map_number": 1, "round_number": 5,
          "dedup_extra": "slate", "market_slug": SLUG, "us_side": "long", "qualifies": True,
          "src_ts_ns": inst["ts_ns"], "grid_ts_ns": grid_ts, "ts_ns": inst["ts_ns"] + 150_000_000}
    intent = {"type": "order_intent", "hypothesis_id": "H-MAPENTRY1", "series_id": SID,
              "map_number": 1, "round_number": 5, "market_slug": SLUG, "us_side": "long",
              "dedup_key": f"H-MAPENTRY1:{SID}:1:long:{SLUG}", "status": "dry_run",
              "ts_ns": me["ts_ns"] + 2_000_000_000}
    return [inst], [me], [intent]


class TestPipelineLegs:
    def test_legs_join_and_missing_legs_stay_missing(self):
        ev = _event()
        insts, mes, intents = _pipeline_rows(ev["t_obs_ns"])
        tc.join_pipeline([ev], insts, mes, intents)
        assert ev["legs"] == {"grid_rtt_ms": 250.0, "detect_ms": 300.0, "recorder_ms": 150.0,
                              "engine_ms": 2000.0, "total_ms": 2700.0}
        ev = _event()
        tc.join_pipeline([ev], insts, [], [])
        assert ev["legs"]["detect_ms"] == 300.0 and ev["legs"]["recorder_ms"] is None
        assert ev["legs"]["engine_ms"] is None and ev["legs"]["total_ms"] is None
        ev = _event()
        tc.join_pipeline([ev], [], mes, intents)
        assert ev["legs"]["detect_ms"] is None and ev["legs"]["engine_ms"] is None

    def test_intent_from_another_round_or_too_late_is_not_joined(self):
        ev = _event()
        insts, mes, intents = _pipeline_rows(ev["t_obs_ns"])
        intents[0]["round_number"] = 7
        tc.join_pipeline([ev], insts, mes, intents)
        assert ev["legs"]["engine_ms"] is None
        insts, mes, intents = _pipeline_rows(ev["t_obs_ns"])
        intents[0]["ts_ns"] = mes[0]["ts_ns"] + 200 * NS
        tc.join_pipeline([ev], insts, mes, intents)
        assert ev["legs"]["engine_ms"] is None


class TestSummary:
    def test_percentiles_and_shares(self):
        evs = []
        for i, lead in enumerate([-3.0, 1.0, 5.0, 9.0]):
            ev = {**_event(n=[3, 8, 20, 22][i]), "outcome": "moved", "lead_vs_obs_s": lead,
                  "lead_vs_prev_s": lead + 12.0, "direction_agrees": i != 3}
            evs.append(ev)
        evs.append({**_event(n=6), "outcome": "no_move_300s"})
        evs.append({**_event(n=7), "outcome": "skipped:no_book"})
        s = tc.summarize(DAY, evs)
        assert s["n_round_ends"] == 6 and s["outcomes"] == {
            "moved": 4, "no_move_300s": 1, "skipped:no_book": 1}
        assert s["moved"]["lead_vs_obs_s"] == {"p10": -1.8, "p50": 3.0, "p90": 7.8}
        assert s["moved"]["book_first_share"] == 0.25
        assert s["moved"]["direction_agreement_rate"] == 0.75
        assert s["by_phase"]["early (round <= 6)"]["n"] == 1
        assert s["by_phase"]["mid (7-18)"]["n"] == 1
        assert s["by_phase"]["late (>= 19)"]["n"] == 2
        assert s["legs"]["grid_rtt_ms"] == {"label": tc.LEGS[0][1], "n": 6, "median_ms": 250.0}
        assert s["legs"]["total_ms"]["n"] == 0 and s["legs"]["total_ms"]["median_ms"] is None
        text = tc.render(s)
        assert "LOWER bound" in text and "| moved | 4 |" in text
        assert "missing legs" in text and "total_ms" in text


# --- end to end --------------------------------------------------------------


def _day(tmp_path: Path, with_books: bool = True, with_pipeline: bool = True) -> Path:
    prev, obs = _ss(T0, _rounds(3)), _ss(T0 + 12 * NS, _rounds(4))
    _write(tmp_path / "grid" / f"series_state-{DAY}.jsonl", [prev, obs])
    _write(tmp_path / "pmus" / f"mappings-{DAY}.jsonl", [_mapping()])
    if with_books:
        _write(tmp_path / "pmus" / f"books-{DAY}.jsonl",
               [_bbo(T0 - 2 * NS, 0.49, 0.51, heartbeat=True), _bbo(T0 + 5 * NS, 0.51, 0.53)])
    if with_pipeline:
        insts, mes, intents = _pipeline_rows(obs["req_ns"])
        _write(tmp_path / "shadow" / f"instances-{DAY}.jsonl", insts)
        _write(tmp_path / "shadow" / f"mapentry-{DAY}.jsonl", mes)
        _write(tmp_path / "executor" / f"intents-{DAY}.jsonl", intents)
    return tmp_path


class TestMain:
    def test_full_day_writes_json_and_prints_markdown(self, tmp_path, capsys):
        data = _day(tmp_path)
        assert tc.main([DAY], data_dir=data) == 0
        out = capsys.readouterr().out
        assert f"# timing chain — {DAY}" in out and "LOWER bound" in out
        assert "| moved | 1 |" in out
        doc = json.loads((data / "analysis" / f"timing_chain-{DAY}.json").read_text())
        assert doc["day"] == DAY and len(doc["events"]) == 1
        ev = doc["events"][0]
        assert ev["outcome"] == "moved" and ev["lead_vs_obs_s"] == -7.0
        assert ev["winner_team"] == "Vexar" and ev["us_side"] == "long"
        assert ev["legs"] == {"grid_rtt_ms": 250.0, "detect_ms": 300.0, "recorder_ms": 150.0,
                              "engine_ms": 2000.0, "total_ms": 2700.0}
        s = doc["summary"]
        assert s["n_round_ends"] == 1 and s["n_joined"] == 1
        assert s["moved"]["lead_vs_obs_s"]["p50"] == -7.0 and s["moved"]["book_first_share"] == 1.0
        assert s["legs"]["total_ms"]["median_ms"] == 2700.0

    def test_missing_pipeline_files_are_reported_as_missing_legs(self, tmp_path, capsys):
        data = _day(tmp_path, with_pipeline=False)
        assert tc.main([DAY], data_dir=data) == 0
        out = capsys.readouterr().out
        assert "missing legs (no joinable rows): detect_ms, recorder_ms, engine_ms, total_ms" in out

    def test_empty_pmus_bbo_input_exits_1_loudly(self, tmp_path, capsys):
        data = _day(tmp_path, with_books=False)
        assert tc.main([DAY], data_dir=data) == 1
        err = capsys.readouterr().err
        assert "NO pmus_bbo rows" in err and DAY in err
        assert not (data / "analysis" / f"timing_chain-{DAY}.json").exists()
        # a books file holding only error rows is still an empty measurement
        _write(data / "pmus" / f"books-{DAY}.jsonl",
               [{"type": "pmus_bbo_error", "slug": SLUG, "error": "TimeoutError", "ts_ns": T0}])
        assert tc.main([DAY], data_dir=data) == 1

    def test_one_empty_day_among_good_days_still_exits_1(self, tmp_path):
        data = _day(tmp_path)
        assert tc.main([DAY, "20260904"], data_dir=data) == 1

    def test_bad_arguments(self, tmp_path):
        assert tc.main([], data_dir=tmp_path) == 2
        assert tc.main(["2026-09"], data_dir=tmp_path) == 2

    def test_script_process_exit_code_is_1_on_empty_day(self, tmp_path):
        data = _day(tmp_path, with_books=False)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), DAY],
            env={**os.environ, "GRIDBOT_DATA_DIR": str(data)},
            capture_output=True, text=True, timeout=120, check=False,
        )
        assert proc.returncode == 1, proc.stderr
        assert "NO pmus_bbo rows" in proc.stderr



def test_unresolvable_segment_hole_names_the_right_winner():
    """rows.round_winners skips a finished segment with no won flag, so the
    positional count stops naming segments. Segment 2 is finished but has no
    winner; round 3 resolves between the polls. Positional lookup would read
    segment 2 (no winner -> ambiguous); the seq-based lookup must find round 3.
    """
    hole = _segment(2, "Vexar", "Falcons", CT, finished=True)
    for team in hole["teams"]:
        team["won"] = False  # finished, winner unresolvable
    seg1 = _segment(1, "Vexar", "Falcons", CT)
    seg3 = _segment(3, "Falcons", "Vexar", T)
    open4 = _segment(4, "Vexar", "Falcons", CT, finished=False)
    prev = _ss(T0, [("Vexar", "Falcons", CT)])
    prev["data"]["seriesState"]["games"][0]["segments"] = [seg1, hole,
                                                         _segment(3, "Vexar", "Falcons", CT, finished=False)]
    obs = _ss(T0 + 12 * NS, [("Vexar", "Falcons", CT)])
    obs["data"]["seriesState"]["games"][0]["segments"] = [seg1, hole, seg3, open4]
    events = tc.round_end_events([prev, obs])
    assert len(events) == 1
    ev = events[0]
    assert ev["outcome"] is None
    assert ev["round_number"] == 2          # positional (join key)
    assert ev["round_seq"] == 3             # the segment that actually resolved
    assert (ev["winner_team"], ev["loser_team"]) == ("Falcons", "Vexar")


# --- step 2b: oriented horizon moves + >=3-tick moves -------------------------


class TestOrientedHorizonMoves:
    def test_short_event_orients_moves_toward_the_winner_positive(self):
        # Falcons named -> us_side short; mid falling = book moving toward Vexar.
        rows = [_bbo(T0 - 2 * NS, 0.59, 0.61),   # mid_before = 0.60
                _bbo(T0 + 5 * NS, 0.57, 0.59),   # mid 0.58 (the 1-tick move, pre-t_obs)
                _bbo(T0 + 40 * NS, 0.55, 0.57),  # t_obs + 28s
                _bbo(T0 + 70 * NS, 0.53, 0.55),  # t_obs + 58s
                _bbo(T0 + 125 * NS, 0.53, 0.55, heartbeat=True)]  # t_obs + 113s: covered
        ev = _event(winner="Vexar", loser="Falcons")
        tc.join_books([ev], {SID: _mapping(named="Falcons")}, tc.book_series(rows))
        assert ev["us_side"] == "short" and ev["outcome"] == "moved"
        assert ev["move_30s"] == 0.04    # raw 0.56 - 0.60 = -0.04, oriented -> +0.04
        assert ev["move_60s"] == 0.06    # raw 0.54 - 0.60 = -0.06 -> +0.06
        assert ev["move_120s"] == 0.06   # heartbeat proves the book sat at 0.54 through +120s

    def test_long_event_keeps_the_raw_sign(self):
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51), _bbo(T0 + 5 * NS, 0.51, 0.53),
                _bbo(T0 + 40 * NS, 0.55, 0.57),
                _bbo(T0 + 120 * NS, 0.55, 0.57, heartbeat=True)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["us_side"] == "long"
        assert ev["move_30s"] == 0.06 and ev["move_60s"] == 0.06 and ev["move_120s"] == 0.06

    def test_horizon_rows_picked_by_req_ns_le_t_obs_plus_h(self):
        t_obs = T0 + 12 * NS
        res = tc.horizon_moves([t_obs - NS, t_obs + 30 * NS], [0.50, 0.55], t_obs, 0.50)
        assert res["move_30s"] == 0.05  # row exactly AT the cutoff is in
        res = tc.horizon_moves([t_obs - NS, t_obs + 30 * NS + 1], [0.50, 0.55], t_obs, 0.50)
        assert res["move_30s"] == 0.0   # 1ns past the cutoff is out; the 31s-old pre-row is
        #                                 recent enough to prove coverage -> unchanged, not None
        # the LAST row inside the window wins
        res = tc.horizon_moves([t_obs + 5 * NS, t_obs + 29 * NS], [0.52, 0.55], t_obs, 0.50)
        assert res["move_30s"] == 0.05

    def test_coverage_gap_is_none_but_a_quiet_covered_book_is_zero(self):
        t_obs = T0 + 12 * NS
        # Last row before the +30s/+60s horizons is 70s old: the recorder was not
        # covering the slug (heartbeat is 30s) -> None. The +120s horizon has a
        # 20s-old row -> a real observation.
        res = tc.horizon_moves([t_obs - 40 * NS, t_obs + 100 * NS], [0.50, 0.55], t_obs, 0.50)
        assert res["move_30s"] is None and res["move_60s"] is None and res["move_120s"] == 0.05
        # Pre-event row plus one row at t_obs+200s: nothing proves coverage at any horizon.
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51),
                _bbo(T0 + 12 * NS + 200 * NS, 0.49, 0.51)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "no_move_300s"
        assert ev["move_30s"] is None and ev["move_60s"] is None and ev["move_120s"] is None
        # A single row at t_obs+40s: too old for +30s (44s-old pre-row), fresh for +60s,
        # 80s old at +120s -> only the +60s horizon is an observation.
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51), _bbo(T0 + 12 * NS + 40 * NS, 0.51, 0.53)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "moved"
        assert ev["move_30s"] is None and ev["move_60s"] == 0.02 and ev["move_120s"] is None
        # A quiet book under heartbeat coverage is a 0.0 move at every horizon — the
        # case a "row strictly inside the window" rule would have dropped, biasing
        # every horizon toward movers.
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51)] + [
            _bbo(T0 + 12 * NS + k * NS, 0.49, 0.51, heartbeat=True) for k in (14, 44, 74, 104)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "no_move_300s"
        assert ev["move_30s"] == 0.0 and ev["move_60s"] == 0.0 and ev["move_120s"] == 0.0


class TestThreeTickMove:
    def test_threshold_ignores_one_tick_jitter_and_catches_the_three_tick_move(self):
        t_prev, t_obs = T0, T0 + 12 * NS
        times = [t_prev - NS, t_prev + 4 * NS, t_prev + 9 * NS, t_prev + 20 * NS]
        mids = [0.50, 0.51, 0.52, 0.53]  # 1-tick, then cumulative 2-tick, then 3-tick
        res = tc.first_move(times, mids, t_prev, t_obs)
        assert res["t_move_ns"] == t_prev + 4 * NS and res["move_size"] == 0.01
        assert res["t_move3_ns"] == t_prev + 20 * NS
        assert res["move_size_3"] == 0.03 and res["mid_after_3"] == 0.53

    def test_jitter_only_window_reports_no_three_tick_move(self):
        t_prev, t_obs = T0, T0 + 12 * NS
        times = [t_prev - NS, t_prev + 4 * NS, t_prev + 9 * NS]
        mids = [0.50, 0.51, 0.49]  # 1-tick wobble both ways, never 3 ticks
        res = tc.first_move(times, mids, t_prev, t_obs)
        assert res["t_move_ns"] == t_prev + 4 * NS
        assert res["t_move3_ns"] is None and res["move_size_3"] is None
        assert res["mid_after_3"] is None

    def test_join_books_sets_lead3_fields_only_when_a_three_tick_move_lands(self):
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51),   # mid 0.50
                _bbo(T0 + 3 * NS, 0.50, 0.52),   # mid 0.51: 1-tick up
                _bbo(T0 + 25 * NS, 0.52, 0.54)]  # mid 0.53: >=3 ticks up
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "moved"
        assert ev["t_move_ns"] == T0 + 3 * NS and ev["t_move3_ns"] == T0 + 25 * NS
        assert ev["lead_vs_obs_s"] == -9.0 and ev["lead3_vs_obs_s"] == 13.0
        assert ev["direction_agrees"] is True and ev["direction3_agrees"] is True
        # short event: a >=3-tick DOWN move agrees with the winner
        rows = [_bbo(T0 - 2 * NS, 0.59, 0.61), _bbo(T0 + 25 * NS, 0.55, 0.57)]
        ev = _event(winner="Vexar", loser="Falcons")
        tc.join_books([ev], {SID: _mapping(named="Falcons")}, tc.book_series(rows))
        assert ev["move_size_3"] == -0.04 and ev["direction3_agrees"] is True
        assert ev["lead3_vs_obs_s"] == 13.0
        # a moved event with only a 1-tick move carries no lead3 keys
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51), _bbo(T0 + 3 * NS, 0.50, 0.52)]
        ev = _event()
        tc.join_books([ev], {SID: _mapping()}, tc.book_series(rows))
        assert ev["outcome"] == "moved" and "lead3_vs_obs_s" not in ev
        assert "direction3_agrees" not in ev and ev["t_move3_ns"] is None


class TestBootstrapCI:
    def test_deterministic_with_seed_17_and_brackets_the_point(self):
        pairs = [("s1", 0.01), ("s1", 0.02), ("s2", -0.03), ("s3", 0.05), ("s3", 0.00)]
        a = tc.bootstrap_ci(pairs)
        assert a == tc.bootstrap_ci(pairs)  # seed 17 -> identical across calls
        assert a["n"] == 5 and a["n_series"] == 3
        assert a["point"] == pytest.approx(0.01)
        lo, hi = a["ci95"]
        assert lo <= hi and lo <= a["point"] + 1e-9

    def test_single_series_has_no_interval_and_says_so(self):
        blk = tc.bootstrap_ci([("s1", 0.01), ("s1", 0.03)])
        assert blk["point"] == 0.02 and blk["ci95"] is None and blk["n_series"] == 1
        assert tc._fmt_ci(blk) == "0.02 [no interval: 1 series] (n=2, series=1)"
        assert tc._fmt(-0.0) == "0" and tc._fmt(-0.0004) == "0" and tc._fmt(-0.004) == "-0.004"

    def test_ci_shrinks_to_the_point_when_every_series_agrees(self):
        pairs = [(f"s{i}", 0.04) for i in range(12)]
        for stat in ("mean", "median"):
            blk = tc.bootstrap_ci(pairs, stat)
            assert blk["point"] == 0.04 and blk["ci95"] == [0.04, 0.04]
            assert blk["n"] == 12 and blk["n_series"] == 12
        assert tc.bootstrap_ci([]) is None


class TestSummaryAdditions:
    def test_horizon_block_and_moved3_block_in_summary(self):
        evs = []
        for i, (mv120, lead3) in enumerate([(0.04, 5.0), (0.02, None), (-0.01, 20.0), (0.03, -3.0)]):
            ev = {**_event(n=[4, 8, 12, 16][i], t_obs=T0 + i * NS),
                  "series_id": f"s{i % 2}", "outcome": "moved", "us_side": "long",
                  "move_30s": mv120 / 2, "move_60s": mv120, "move_120s": mv120,
                  "lead_vs_obs_s": lead3 if lead3 is not None else 1.0,
                  "lead_vs_prev_s": 2.0, "direction_agrees": True}
            if lead3 is not None:
                ev["lead3_vs_obs_s"] = lead3
                ev["direction3_agrees"] = i != 3
            evs.append(ev)
        s = tc.summarize(DAY, evs)
        hz = s["horizons"]
        assert hz["n"] == 4 and hz["by_horizon"]["120s"]["n_with_row"] == 4
        blk = hz["by_horizon"]["120s"]["mean_oriented"]
        assert blk["n"] == 4 and blk["n_series"] == 2
        assert blk["point"] == pytest.approx(0.02)
        # oriented 120s moves >= +0.03: events with 0.04 and 0.03 -> 2/4
        assert hz["share_oriented_120s_ge_3t"] == 0.5 and hz["n_oriented_120s"] == 4
        m3 = s["moved3"]
        assert m3["n"] == 3 and m3["lead3_vs_obs_s"]["p10"] == -1.4
        assert m3["book_first_share"] == 0.333
        assert m3["direction_agreement_rate"] == 0.667
        text = tc.render(s)
        assert "size and horizon reading" in text and "series-clustered bootstrap" in text
        assert "n=4, series=2" in text  # n and n_series beside the CI
        assert "first >=3-tick move" in text
        assert "share of events with oriented +120s move >= +0.03" in text


class TestNextRoundBounded:
    """move_next_round: horizon cut at the NEXT round end of the same map."""

    def test_bounded_at_next_t_prev_and_later_rows_do_not_count(self):
        # ev1: round 4 observed at T0+12s; ev2: round 5 with t_prev = T0+100s.
        # The book rows after ev2's t_prev (T0+150s, T0+170s) must NOT count —
        # the horizon stops at ev2's t_prev, so the bounded move reads 0.53.
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51),          # mid_before 0.50
                _bbo(T0 + 5 * NS, 0.51, 0.53),          # ev1's 1-tick move (pre-t_obs)
                _bbo(T0 + 85 * NS, 0.52, 0.54),         # mid 0.53, 15s before ev2 t_prev
                _bbo(T0 + 150 * NS, 0.56, 0.58),        # after ev2 t_prev: must be ignored
                _bbo(T0 + 170 * NS, 0.58, 0.60)]
        ev1 = _event(n=4, t_prev=T0, t_obs=T0 + 12 * NS)
        ev2 = _event(n=5, t_prev=T0 + 100 * NS, t_obs=T0 + 112 * NS)
        books = tc.book_series(rows)
        tc.join_books([ev2, ev1], {SID: _mapping()}, books)  # unsorted on purpose
        assert ev1["move_next_round"] == 0.03            # 0.53 - 0.50, bounded at ev2 t_prev
        assert ev1["next_round_gap_s"] == 88.0           # (T0+100s - (T0+12s))
        # ev2 is the last event of the map: no next round to bound at.
        assert ev2["move_next_round"] is None and ev2["next_round_gap_s"] is None

    def test_short_event_orients_the_bounded_sign(self):
        # Falcons named -> us_side short; mid FALLING from 0.60 toward Vexar is
        # oriented positive, exactly like move_30s.
        rows = [_bbo(T0 - 2 * NS, 0.59, 0.61),           # mid_before 0.60
                _bbo(T0 + 5 * NS, 0.57, 0.59),
                _bbo(T0 + 30 * NS, 0.57, 0.59),          # covered +30s horizon: mid 0.58
                _bbo(T0 + 95 * NS, 0.55, 0.57)]          # mid 0.56 at the next t_prev
        ev1 = _event(n=4, winner="Vexar", loser="Falcons", t_prev=T0, t_obs=T0 + 12 * NS)
        ev2 = _event(n=5, winner="Vexar", loser="Falcons",
                     t_prev=T0 + 100 * NS, t_obs=T0 + 112 * NS)
        tc.join_books([ev1, ev2], {SID: _mapping(named="Falcons")}, tc.book_series(rows))
        assert ev1["us_side"] == "short" and ev1["outcome"] == "moved"
        assert ev1["move_next_round"] == 0.04            # raw 0.56 - 0.60 = -0.04 -> +0.04
        assert ev1["move_30s"] == 0.02                   # raw 0.58 - 0.60 = -0.02 -> +0.02

    def test_coverage_gap_before_the_next_t_prev_is_none(self):
        # ev1's last book row before ev2's t_prev is 90s old (heartbeat is 30s):
        # the recorder was not covering the slug -> None, not a fabricated move.
        rows = [_bbo(T0 - 2 * NS, 0.49, 0.51),
                _bbo(T0 + 5 * NS, 0.51, 0.53),
                _bbo(T0 + 10 * NS, 0.51, 0.53, heartbeat=True),  # 90s before ev2 t_prev
                _bbo(T0 + 190 * NS, 0.55, 0.57)]                 # after ev2 t_prev anyway
        ev1 = _event(n=4, t_prev=T0, t_obs=T0 + 12 * NS)
        ev2 = _event(n=5, t_prev=T0 + 100 * NS, t_obs=T0 + 112 * NS)
        tc.join_books([ev1, ev2], {SID: _mapping()}, tc.book_series(rows))
        assert ev1["move_next_round"] is None and ev1["next_round_gap_s"] == 88.0

    def test_next_map_or_series_is_not_a_next_round(self):
        # A later event of a DIFFERENT map (or series) must not bound this event.
        rows1 = [_bbo(T0 - 2 * NS, 0.49, 0.51), _bbo(T0 + 5 * NS, 0.51, 0.53)]
        ev1 = _event(n=4, t_prev=T0, t_obs=T0 + 12 * NS)
        tc.join_books([ev1], {SID: _mapping()}, tc.book_series(rows1))
        assert ev1["move_next_round"] is None and ev1["next_round_gap_s"] is None
        # Same on the series axis: same map_number, different series_id.
        other_sid = "2999840"
        other_slug = "astatc-cs2-vexar-falcons-2026-09-03-map1-B"
        rows2 = [_bbo(T0 - 2 * NS, 0.49, 0.51, slug=other_slug),
                 _bbo(T0 + 5 * NS, 0.51, 0.53, slug=other_slug),
                 _bbo(T0 + 90 * NS, 0.55, 0.57, slug=other_slug)]
        ev1 = _event(n=4, t_prev=T0, t_obs=T0 + 12 * NS)
        ev2 = _event(n=5, t_prev=T0 + 100 * NS, t_obs=T0 + 112 * NS)
        ev2["series_id"] = other_sid
        mapping = _mapping()
        other_mapping = {**mapping, "grid_series_id": other_sid,
                         "us_markets": {"map1": {**mapping["us_markets"]["map1"],
                                                 "market_slug": other_slug}}}
        tc.join_books([ev2, ev1], {SID: mapping, other_sid: other_mapping},
                      tc.book_series(rows1 + rows2))
        assert ev1["move_next_round"] is None and ev1["next_round_gap_s"] is None

    def test_summary_next_round_block_and_render_section(self):
        evs = []
        for i, mv in enumerate([0.04, 0.02, 0.00, -0.03]):
            evs.append({**_event(n=[4, 8, 12, 16][i], t_obs=T0 + i * NS),
                        "series_id": f"s{i % 2}", "outcome": "moved", "us_side": "long",
                        "move_next_round": mv, "next_round_gap_s": 90.0,
                        "lead_vs_obs_s": 1.0, "lead_vs_prev_s": 2.0,
                        "direction_agrees": True})
        s = tc.summarize(DAY, evs)
        blk = s["next_round"]
        assert blk["n"] == 4 and blk["n_with_row"] == 4
        assert blk["mean_oriented"]["n"] == 4 and blk["mean_oriented"]["n_series"] == 2
        assert blk["mean_oriented"]["point"] == pytest.approx(0.0075)
        # share with move_next_round >= +0.03: only 0.04 qualifies -> 1/4
        assert blk["share_ge_3t"] == 0.25
        # agreement among NONZERO (0.04, 0.02, -0.03): 2/3 -> 0.667, n_nonzero=3
        assert blk["direction_agreement_rate"] == 0.667 and blk["n_nonzero"] == 3
        text = tc.render(s)
        assert "next-round-bounded move" in text
        assert "move_next_round >= +0.03" in text and "n_nonzero=3" in text
        # the section sits directly after the first >=3-tick move section
        assert text.index("first >=3-tick move") < text.index("next-round-bounded move") \
            < text.index("by map phase")


# --- join_model: toward-the-model orientation (Addendum 2) -------------------


def _model_row(rn: int = 5, p_map: float | None = 0.7, us_side: str = "long",
               ts_ns: int = T0 + 5 * NS, slug: str = SLUG, sid: str = SID,
               map_number: int = 1, src: str = "H-MODELENTRY1", **overrides) -> dict:
    """A shape-faithful H-MODELENTRY1 mapentry row — the only src with the model's q."""
    row = {"type": "mapentry", "hypothesis_id": "H-MAPENTRY1", "src_hypothesis_id": src,
           "series_id": sid, "map_number": map_number, "round_number": rn, "side": "ct",
           "team": "Vexar", "opponent": "Falcons", "a": 1, "b": 0, "q_round": 0.5,
           "p_map": p_map, "dedup_extra": "ct:0.5", "market_slug": slug, "us_side": us_side,
           "bid": 0.5, "ask": 0.5, "side_ask": 0.5, "entry_price_max": 0.5,
           "qualifies": True, "skip_reason": None, "src_ts_ns": T0, "grid_ts_ns": T0,
           "ts_ns": ts_ns}
    row.update(overrides)
    return row


def _joined_event(n: int = 4, mid_before: float = 0.50, ev_us_side: str = "long",
                  mv30: float = 0.05, mv60: float = 0.05, mv120: float = 0.05,
                  mvnext: float = 0.05, **overrides) -> dict:
    """A round-end event already carrying slug / orientation / oriented horizon moves."""
    ev = {**_event(n=n, t_prev=T0, t_obs=T0 + 12 * NS),
          "outcome": "moved", "slug": SLUG, "us_side": ev_us_side, "mid_before": mid_before,
          "move_30s": mv30, "move_60s": mv60, "move_120s": mv120, "move_next_round": mvnext,
          "next_round_gap_s": 88.0, "lead_vs_obs_s": 1.0, "lead_vs_prev_s": 2.0,
          "direction_agrees": True}
    ev.update(overrides)
    return ev


class TestJoinModel:
    def test_short_row_p_yes_is_1_minus_p_map(self):
        # row us_side short, p_map 0.70 -> the model's Yes fair value is 0.30.
        ev = _joined_event(n=4, mid_before=0.60, ev_us_side="short")
        tc.join_model([ev], [_model_row(rn=5, p_map=0.70, us_side="short")])
        assert ev["p_yes_model"] == pytest.approx(0.30)
        assert ev["model_gap"] == pytest.approx(0.30 - 0.60)   # -0.30
        assert ev["model_dir"] == -1
        assert ev["model_ts_ns"] == T0 + 5 * NS

    def test_toward_model_positive_when_the_model_says_down_and_the_book_moves_down(self):
        # short event: oriented +0.04 means a raw DOWN move (mid falling); the model's
        # q (0.30) sits BELOW mid_before (0.60), so the book moved toward q -> positive.
        ev = _joined_event(n=4, mid_before=0.60, ev_us_side="short", mv30=0.04)
        tc.join_model([ev], [_model_row(rn=5, p_map=0.70, us_side="short")])
        assert ev["model_dir"] == -1
        assert ev["toward_model_30s"] == 0.04
        # and a model that says UP (fair above mid) with the book moving up is positive too
        ev2 = _joined_event(n=4, mid_before=0.60, ev_us_side="long", mv60=0.06)
        tc.join_model([ev2], [_model_row(rn=5, p_map=0.70, us_side="long")])
        assert ev2["model_dir"] == 1
        assert ev2["toward_model_60s"] == pytest.approx(0.06)

    def test_selection_ignores_other_srcs_slugs_rounds_and_takes_earliest_ts_ns(self):
        ev = _joined_event(n=4, mid_before=0.50)
        rows = [
            _model_row(rn=6, p_map=0.2, ts_ns=T0 + 1 * NS),                     # other round
            _model_row(rn=5, p_map=0.9, ts_ns=T0 + 2 * NS, slug="other-slug"),  # other slug
            _model_row(rn=5, p_map=0.8, ts_ns=T0 + 3 * NS,
                       src="H-MAPENTRY1-SLATE"),                                 # other src
            _model_row(rn=5, p_map=0.7, ts_ns=T0 + 9 * NS, us_side="long"),      # later match
            _model_row(rn=5, p_map=0.6, ts_ns=T0 + 4 * NS, us_side="long"),      # earliest match
        ]
        tc.join_model([ev], rows)
        assert ev["model_ts_ns"] == T0 + 4 * NS
        assert ev["p_yes_model"] == pytest.approx(0.6)

    def test_unmapped_rows_p_map_none_and_no_match_set_fields_none(self):
        ev = _joined_event(n=4, mid_before=0.50)
        rows = [_model_row(rn=5, p_map=0.6, ts_ns=T0 + 1 * NS),
                _model_row(rn=5, p_map=0.8, ts_ns=T0 + 2 * NS, market_slug=None),  # unmapped
                _model_row(rn=5, p_map=None, ts_ns=T0 + 3 * NS)]                   # no q
        tc.join_model([ev], rows)
        assert ev["model_ts_ns"] == T0 + 1 * NS and ev["p_yes_model"] == pytest.approx(0.6)
        # no matching row at all: every model field is None
        ev2 = _joined_event(n=4, mid_before=0.50)
        tc.join_model([ev2], [_model_row(rn=6, p_map=0.6)])
        assert ev2["p_yes_model"] is None and ev2["model_dir"] is None
        for h in ("30s", "60s", "120s", "next_round"):
            assert ev2[f"toward_model_{h}"] is None

    def test_non_open_events_are_left_alone(self):
        sk = {**_event(), "outcome": "skipped:no_book"}
        tc.join_model([sk], [_model_row(rn=5)])
        assert "p_yes_model" not in sk

    def test_small_gap_is_model_dir_zero_and_toward_model_none(self):
        ev = _joined_event(n=4, mid_before=0.60, mv30=0.05, mvnext=0.05)
        # gap 0.004: |gap| < 0.005 -> model_dir 0, no direction to project toward q
        tc.join_model([ev], [_model_row(rn=5, p_map=0.604, us_side="long")])
        assert ev["model_gap"] == pytest.approx(0.004)
        assert ev["model_dir"] == 0
        assert ev["toward_model_30s"] is None
        assert ev["toward_model_next_round"] is None


class TestModelMomentumSummary:
    def test_buckets_and_render_ordering(self):
        evs = []
        for i, gap in enumerate([0.019, 0.02, 0.04, 0.08]):
            toward_nr = -0.01 if i == 1 else 0.02
            evs.append({**_joined_event(n=[4, 8, 12, 16][i], t_obs=T0 + i * NS),
                        "series_id": f"s{i % 2}", "mid_before": 0.50,
                        "p_yes_model": round(0.50 + gap, 4), "model_gap": gap,
                        "model_ts_ns": T0, "model_dir": 1,
                        "move_30s": gap, "move_60s": gap, "move_120s": gap,
                        "move_next_round": 0.02,
                        "toward_model_30s": gap, "toward_model_60s": gap,
                        "toward_model_120s": gap, "toward_model_next_round": toward_nr})
        s = tc.summarize(DAY, evs)
        mm = s["model_momentum"]
        assert mm["n_joined_to_model"] == 4
        # exact gap 0.04 sits in the >=0.04 entry band (with 0.08): 2/4 overall
        assert mm["share_gap_ge_0_04"] == 0.5
        nr = mm["by_horizon"]["next_round"]
        assert nr["all"]["n"] == 4 and nr["all"]["n_nonzero"] == 4
        assert nr["all"]["mean_toward_model"]["point"] == pytest.approx(0.0125)
        assert nr["all"]["share_positive_nonzero"] == 0.75
        # boundary: 0.019 -> <0.02, 0.02 -> 0.02-0.04, exactly 0.04 -> >=0.04
        assert nr["<0.02"]["n"] == 1 and nr["0.02-0.04"]["n"] == 1
        assert nr[">=0.04"]["n"] == 2 and nr[">=0.04"]["n_nonzero"] == 2
        assert nr[">=0.04"]["mean_toward_model"]["point"] == pytest.approx(0.02)
        assert nr[">=0.04"]["share_positive_nonzero"] == 1.0
        # render: the model-momentum section exists and sits after next-round-bounded,
        # before by map phase; every existing section stays
        text = tc.render(s)
        assert "model-momentum reading" in text and "toward-model next_round" in text
        assert text.index("next-round-bounded move") \
            < text.index("model-momentum reading") < text.index("by map phase")
