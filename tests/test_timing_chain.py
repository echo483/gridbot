"""Tests for scripts/timing_chain.py on synthetic day files under tmp_path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `scripts` is importable (pytest
# only adds tests/ to sys.path; mirror the existing test_wave_detect.py).
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
