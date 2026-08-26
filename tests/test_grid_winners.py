"""Tests for the gridbot first-observation map-winner oracle."""
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `scripts` is importable.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.grid_winners import process_lines


def _line(series, seq, score_w, score_l, *, win_a=True, finished=True, ts=1000):
    """Build a single-series-state JSONL line with a one-game best-of-3."""
    return json.dumps(
        {
            "type": "ss",
            "series_id": series,
            "ts_ns": ts,
            "data": {
                "seriesState": {
                    "format": "best-of-3",
                    "finished": False,
                    "teams": [],
                    "games": [
                        {
                            "sequenceNumber": seq,
                            "map": {"name": "anubis"},
                            "started": True,
                            "finished": finished,
                            "teams": [
                                {
                                    "name": "A",
                                    "score": score_w,
                                    "won": win_a,
                                    "side": "terrorists",
                                },
                                {
                                    "name": "B",
                                    "score": score_l,
                                    "won": not win_a,
                                    "side": "counter-terrorists",
                                },
                            ],
                        }
                    ],
                }
            },
        }
    )


def _serie(lines):
    return process_lines(lines)


def test_happy_path_13_7():
    res = _serie([_line("S1", 1, 13, 7, ts=100)])
    w = res["winners"]["S1:1"]
    assert w["winner"] == "A"
    assert w["loser"] == "B"
    assert w["winner_score"] == 13
    assert w["loser_score"] == 7
    assert w["overtime"] is False
    assert w["first_seen_ts_ns"] == 100
    assert res["excluded"] == []
    assert res["contradictions"] == 0
    assert res["series_seen"] == 1


def test_overtime_final_16_14():
    res = _serie([_line("S2", 1, 16, 14, ts=200)])
    w = res["winners"]["S2:1"]
    assert (w["winner_score"], w["loser_score"]) == (16, 14)
    assert w["winner"] == "A"
    assert w["overtime"] is True
    assert res["excluded"] == []


def test_impossible_13_12_excluded():
    res = _serie([_line("S3", 1, 13, 12, ts=300)])
    assert "S3:1" not in res["winners"]
    assert {"series_id": "S3", "map_number": 1, "reason": "impossible_score:13-12"} in res[
        "excluded"
    ]


def test_ambiguous_winner_two_winners_excluded():
    # Finished with BOTH teams won==true -> ambiguous.
    lines = [
        json.dumps(
            {
                "type": "ss",
                "series_id": "S4",
                "ts_ns": 1,
                "data": {
                    "seriesState": {
                        "games": [
                            {
                                "sequenceNumber": 1,
                                "finished": True,
                                "teams": [
                                    {"name": "A", "score": 13, "won": True},
                                    {"name": "B", "score": 7, "won": True},
                                ],
                            }
                        ]
                    }
                },
            }
        )
    ]
    res = _serie(lines)
    assert "S4:1" not in res["winners"]
    assert {"series_id": "S4", "map_number": 1, "reason": "ambiguous_winner"} in res[
        "excluded"
    ]


def test_ambiguous_winner_zero_winners_excluded():
    res = _serie([_line("S5", 1, 13, 7, ts=1, win_a=False, finished=True)])
    # win_a=False makes A lose and B win; craft a zero-winner line instead.
    zero = json.dumps(
        {
            "type": "ss",
            "series_id": "S5",
            "ts_ns": 2,
            "data": {
                "seriesState": {
                    "games": [
                        {
                            "sequenceNumber": 2,
                            "finished": True,
                            "teams": [
                                {"name": "A", "score": 6, "won": False},
                                {"name": "B", "score": 6, "won": False},
                            ],
                        }
                    ]
                }
            },
        }
    )
    res = _serie([zero])
    assert "S5:2" not in res["winners"]
    assert {"series_id": "S5", "map_number": 2, "reason": "ambiguous_winner"} in res[
        "excluded"
    ]


def test_first_observation_wins_over_later_contradiction():
    res = _serie(
        [
            _line("S6", 1, 13, 3, ts=1),   # first-obs: A 13-3
            _line("S6", 1, 13, 3, ts=2),   # repeated snapshot, identical result
            _line("S6", 1, 13, 3, win_a=False, ts=3),  # B would win -> contradiction
        ]
    )
    w = res["winners"]["S6:1"]
    assert w["winner"] == "A"
    assert w["winner_score"] == 13
    assert w["loser_score"] == 3
    assert w["first_seen_ts_ns"] == 1
    assert res["contradictions"] == 1
    # The contradicting line must NOT create a separate winner entry.
    assert list(res["winners"].keys()) == ["S6:1"]


def test_malformed_and_non_ss_lines_skipped():
    lines = [
        "{not json",
        "",
        '{"type": "other", "series_id": "JUNK"}',
        "null",
        '{"type": "ss"}',  # ss but no games
        _line("S7", 1, 13, 5, ts=5),  # the only meaningful line
        '{"type": "ss", "series_id": "S8", "data": {"seriesState": {"games": null}}}',
    ]
    res = _serie(lines)
    assert "S7:1" in res["winners"]
    assert "JUNK:1" not in res["winners"]
    assert "S8:1" not in res["winners"]
    # Only the two real "ss" lines with content count toward lines_read.
    assert res["lines_read"] == 3
    assert res["series_seen"] == 2


def test_distinct_games_in_one_series():
    lines = [
        json.dumps(
            {
                "type": "ss",
                "series_id": "S9",
                "ts_ns": 1,
                "data": {
                    "seriesState": {
                        "games": [
                            {
                                "sequenceNumber": 1,
                                "finished": True,
                                "teams": [
                                    {"name": "A", "score": 13, "won": True},
                                    {"name": "B", "score": 8, "won": False},
                                ],
                            },
                            {
                                "sequenceNumber": 2,
                                "finished": True,
                                "teams": [
                                    {"name": "A", "score": 9, "won": False},
                                    {"name": "B", "score": 13, "won": True},
                                ],
                            },
                        ]
                    }
                },
            }
        )
    ]
    res = _serie(lines)
    assert res["winners"]["S9:1"]["winner"] == "A"
    assert res["winners"]["S9:2"]["winner"] == "B"
    assert res["contradictions"] == 0