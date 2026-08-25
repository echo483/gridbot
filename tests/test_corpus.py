"""Corpus row semantics: spec-faithful port of cs2agent's contract."""

from __future__ import annotations

import pytest

from gridbot.corpus.build import build_series_rows, row_key
from gridbot.corpus.rows import FEATURE_NAMES, build_features, round_winners


def player(alive: bool = True, hp: int = 100, items: tuple[str, ...] = ()) -> dict:
    return {
        "alive": alive,
        "currentHealth": hp,
        "inventory": {"items": [{"name": n} for n in items]},
    }


def team(side: str, name: str = "X", score: int = 0, money: int = 0, loadout: int = 0,
         players: list | None = None) -> dict:
    return {
        "__typename": "TeamStateCs2",
        "side": side,
        "name": name,
        "score": score,
        "money": money,
        "loadoutValue": loadout,
        "players": players or [],
    }


def rseg(seq: int, winner_side: str | None, finished: bool = True) -> dict:
    teams = []
    for side in ("Counter-Terrorists", "Terrorists"):
        won = winner_side is not None and side.lower().startswith(winner_side_prefix(winner_side))
        teams.append({"__typename": "SegmentTeamStateCs2", "side": side, "won": won})
    return {"type": "round", "sequenceNumber": seq, "finished": finished, "teams": teams}


def winner_side_prefix(side: str) -> str:
    return "counter" if side == "ct" else "terror"


def game(seq: int = 1, started: bool = True, finished: bool = False, teams: list | None = None,
         segments: list | None = None, clock: dict | None = None) -> dict:
    g = {
        "sequenceNumber": seq,
        "started": started,
        "finished": finished,
        "teams": teams or [],
        "segments": segments or [],
    }
    if clock is not None:
        g["clock"] = clock
    return g


def snap(sid: str = "777", finished: bool = False, games: list | None = None) -> dict:
    return {"id": sid, "started": True, "finished": finished, "games": games or []}


def test_feature_vector_exact() -> None:
    ct = team(
        "Counter-Terrorists", "Alpha", score=12, money=20_000, loadout=13_400,
        players=[
            player(hp=100, items=("AWP",)),
            player(hp=80),
            player(hp=50),
            player(hp=20),
            player(alive=False, hp=0, items=("awp",)),
        ],
    )
    t = team(
        "terrorists", "Beta", score=7, money=5_000, loadout=13_399,
        players=[player(items=("ak47",)) for _ in range(5)],
    )
    segs = [rseg(i, "t" if i == 17 else "ct") for i in range(1, 20)]
    segs.append(rseg(20, None, finished=False))  # round in progress
    g = game(seq=2, teams=[ct, t], segments=segs, clock={"currentSeconds": 55})

    f = build_features(g)
    assert tuple(f) == FEATURE_NAMES
    assert f["alive_ratio"] == pytest.approx(-0.2)
    assert f["man_advantage"] == -1.0
    assert f["econ_ratio"] == pytest.approx((20_000 // 5 - 5_000 // 5) / 10_000)
    assert f["ct_full_buy"] == 1.0  # 13400 is exactly the pinned full-buy cut
    assert f["t_full_buy"] == 0.0  # 13399 misses it
    assert f["awp_diff"] == 1.0
    assert f["bomb_planted"] == 0.0 and f["bomb_time_remaining"] == 0.0
    assert f["time_remaining"] == 55.0
    assert f["round_number"] == 20.0  # feature uses last round segment's sequenceNumber
    assert f["rounds_played"] == 19.0
    assert f["score_lead"] == 5.0
    assert f["is_match_point"] == 1.0  # exactly 12 rounds won
    # last three winners: ct, ct, ct except seq17=t → recent = [t? no: 17,18,19] = t@17? nope:
    # winners list is seq 1..19 with 17=t → last three = 17(t),18(ct),19(ct)
    assert f["momentum_3"] == pytest.approx((2 * 2 - 3) / 3)
    assert f["hp_alive_diff"] == pytest.approx((250 - 500) / 500)
    assert f["awp_alive_diff"] == 1.0  # dead awp carrier doesn't count for alive diff
    assert f["equip_value_diff"] == pytest.approx((13_400 - 13_399) / 20_000)
    assert f["rating_diff"] == 0.0 and f["map_rating_diff"] == 0.0
    assert f["prior_round_p"] == 0.5


def test_defaults_when_team_nodes_absent() -> None:
    g = game(teams=[], segments=[], clock=None)
    f = build_features(g)
    assert f["alive_ratio"] == 0.0  # both sides default to 5 alive
    assert f["man_advantage"] == 0.0
    assert f["econ_ratio"] == 0.0
    assert f["time_remaining"] == 115.0
    assert f["round_number"] == 1.0
    assert f["is_match_point"] == 0.0


def test_match_point_only_at_exactly_twelve() -> None:
    for score, expect in ((11, 0.0), (12, 1.0), (13, 0.0), (15, 0.0)):
        g = game(teams=[team("Counter-Terrorists", score=score), team("Terrorists", score=0)])
        assert build_features(g)["is_match_point"] == expect


def test_round_winners_skips_unresolvable() -> None:
    g = game(segments=[
        rseg(1, "ct"),
        {"type": "round", "sequenceNumber": 2, "finished": True,
         "teams": [{"__typename": "SegmentTeamStateCs2", "side": "spectator", "won": True}]},
        rseg(3, "t"),
    ])
    assert round_winners(g) == ["ct", "t"]


def test_row_key_bin_suffix() -> None:
    assert row_key("123", 2, 14, 0) == "123:2:14"
    assert row_key("123", 2, 14, 3) == "123:2:14:b3"


def _live_series(polls_per_round: int) -> list[dict]:
    """Polls of round 1 on map 1, then a snapshot resolving it (ct wins)."""
    states = []
    for i in range(polls_per_round):
        g = game(
            teams=[team("Counter-Terrorists", "A"), team("Terrorists", "B")],
            segments=[rseg(1, None, finished=False)],
            clock={"currentSeconds": 100 - i},  # distinguishes polls
        )
        states.append(snap(games=[g]))
    resolved = game(
        teams=[team("Counter-Terrorists", "A", score=1), team("Terrorists", "B")],
        segments=[rseg(1, "ct")],
    )
    states.append(snap(games=[resolved]))
    return states


def test_oracle_labels_and_binning_eight_polls() -> None:
    rows = build_series_rows("777", _live_series(8))
    # 8 pre-resolution polls → 4 bins, last poll of each pair represents it
    assert [r["row_key"] for r in rows] == ["777:1:1", "777:1:1:b1", "777:1:1:b2", "777:1:1:b3"]
    assert all(r["label"] == 1.0 for r in rows)
    assert [r["features"]["time_remaining"] for r in rows] == [99.0, 97.0, 95.0, 93.0]
    assert rows[0]["ct_team"] == "A" and rows[0]["t_team"] == "B"


def test_binning_two_polls_lands_bins_zero_and_two() -> None:
    rows = build_series_rows("777", _live_series(2))
    assert [r["row_key"] for r in rows] == ["777:1:1", "777:1:1:b2"]


def test_unobserved_next_round_emits_nothing() -> None:
    states = _live_series(3)[:-1]  # drop the resolving snapshot → no oracle entry
    assert build_series_rows("777", states) == []


def test_finished_only_series_emits_nothing() -> None:
    g = game(finished=True, teams=[team("Counter-Terrorists", score=13)],
             segments=[rseg(i, "ct") for i in range(1, 14)])
    states = [snap(finished=True, games=[g])]
    assert build_series_rows("777", states) == []


def test_first_observation_fixes_winner() -> None:
    states = _live_series(2)
    flipped = game(
        teams=[team("Counter-Terrorists", "A"), team("Terrorists", "B", score=1)],
        segments=[rseg(1, "t")],
    )
    states.append(snap(games=[flipped]))  # later snapshot claims t won — must not override
    rows = build_series_rows("777", states)
    assert rows and all(r["label"] == 1.0 for r in rows)


def test_mixed_series_ids_rejected() -> None:
    states = [snap(sid="1"), snap(sid="2")]
    with pytest.raises(ValueError, match="series"):
        build_series_rows("1", states)
