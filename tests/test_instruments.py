"""Detector, book-top, and market-match semantics for the shadow engine."""

from __future__ import annotations

from gridbot.instruments.books import BookTops
from gridbot.instruments.detectors import detect
from gridbot.instruments.market_match import match_market


def player(alive: bool = True) -> dict:
    return {"alive": alive, "currentHealth": 100 if alive else 0, "inventory": {"items": []}}


def team(side: str, name: str, score: int = 0, loadout: int = 0, alive: int = 5) -> dict:
    return {
        "__typename": "TeamStateCs2",
        "side": side,
        "name": name,
        "score": score,
        "money": 0,
        "loadoutValue": loadout,
        "players": [player(i < alive) for i in range(5)],
    }


def rseg(seq: int, winner_side: str | None, finished: bool = True) -> dict:
    teams = []
    for side, tag in (("Counter-Terrorists", "ct"), ("Terrorists", "t")):
        teams.append(
            {
                "__typename": "SegmentTeamStateCs2",
                "side": side,
                "won": winner_side == tag,
            }
        )
    return {"type": "round", "sequenceNumber": seq, "finished": finished, "teams": teams}


def snap(ct: dict, t: dict, round_number: int, winners: list[str] | None = None,
         clock: float | None = 60.0) -> dict:
    winners = winners or []
    segments = [rseg(i + 1, w) for i, w in enumerate(winners)]
    segments.append(rseg(round_number, None, finished=False))
    game = {
        "sequenceNumber": 1,
        "started": True,
        "finished": False,
        "teams": [ct, t],
        "segments": segments,
    }
    if clock is not None:
        game["clock"] = {"currentSeconds": clock}
    return {"id": "555", "started": True, "finished": False, "games": [game]}


def winners_for(ct_score: int, t_score: int) -> list[str]:
    return ["ct"] * ct_score + ["t"] * t_score


def test_manadv_triggers_for_trailing_side_with_two_men() -> None:
    ct = team("Counter-Terrorists", "Down Bad", score=2, alive=5)
    t = team("Terrorists", "Front Run", score=5, alive=3)
    inst = detect(snap(ct, t, 8, winners_for(2, 5), clock=45.0))
    manadv = [i for i in inst if i["hypothesis_id"] == "H-MANADV1"]
    assert len(manadv) == 1
    assert manadv[0]["side"] == "ct" and manadv[0]["team"] == "Down Bad"
    assert manadv[0]["fair_value"] == 0.93


def test_manadv_blocked_by_low_clock_and_incoherence() -> None:
    ct = team("Counter-Terrorists", "A", score=2, alive=5)
    t = team("Terrorists", "B", score=5, alive=3)
    assert not detect(snap(ct, t, 8, winners_for(2, 5), clock=10.0))
    # scoreboard ahead of winners: transition window — nothing may fire
    ct_leak = team("Counter-Terrorists", "A", score=3, alive=5)
    assert not detect(snap(ct_leak, t, 8, winners_for(2, 5), clock=45.0))


def test_econbuy_grades_by_gap() -> None:
    ct = team("Counter-Terrorists", "Rich", score=3, loadout=16_000)
    t = team("Terrorists", "Poor", score=3, loadout=3_000)
    inst = detect(snap(ct, t, 7, winners_for(3, 3), clock=110.0))
    econ = [i for i in inst if i["hypothesis_id"] == "H-ECONBUY1"]
    assert len(econ) == 1
    assert econ[0]["team"] == "Rich" and econ[0]["gap_bucket"] == ">=10k"
    assert econ[0]["fair_value"] == 0.849

    t_force = team("Terrorists", "Force", score=3, loadout=9_500)
    inst = detect(snap(ct, t_force, 7, winners_for(3, 3), clock=110.0))
    econ = [i for i in inst if i["hypothesis_id"] == "H-ECONBUY1"]
    assert econ[0]["gap_bucket"] == "4-10k" and econ[0]["fair_value"] == 0.710


def test_econbuy_blocked_on_pistols_both_full_and_mid_round() -> None:
    ct = team("Counter-Terrorists", "A", loadout=16_000)
    t = team("Terrorists", "B", loadout=3_000)
    assert not detect(snap(ct, t, 1, [], clock=110.0))  # pistol
    both = team("Terrorists", "B", loadout=15_000)
    ct6 = team("Counter-Terrorists", "A", score=3, loadout=16_000)
    both.update(score=3)
    assert not [
        i
        for i in detect(snap(ct6, both, 7, winners_for(3, 3), clock=110.0))
        if i["hypothesis_id"] == "H-ECONBUY1"
    ]
    t_dead = team("Terrorists", "B", score=3, loadout=3_000, alive=4)
    assert not [
        i
        for i in detect(snap(ct6, t_dead, 7, winners_for(3, 3), clock=110.0))
        if i["hypothesis_id"] == "H-ECONBUY1"
    ]


def test_pistolcarry_conditional_on_loser_buy() -> None:
    ct = team("Counter-Terrorists", "Winner", score=1, loadout=14_000)
    t = team("Terrorists", "Loser", score=0, loadout=6_000)
    inst = detect(snap(ct, t, 2, ["ct"], clock=110.0))
    pistol = [i for i in inst if i["hypothesis_id"] == "H-PISTOLCARRY1"]
    assert len(pistol) == 1
    assert pistol[0]["team"] == "Winner" and pistol[0]["fair_value"] == 0.874

    t_buy = team("Terrorists", "Loser", score=0, loadout=14_000)
    inst = detect(snap(ct, t_buy, 2, ["ct"], clock=110.0))
    pistol = [i for i in inst if i["hypothesis_id"] == "H-PISTOLCARRY1"]
    assert pistol[0]["fair_value"] == 0.702


def test_pistolcarry_only_rounds_two_and_fourteen() -> None:
    ct = team("Counter-Terrorists", "W", score=2, loadout=14_000)
    t = team("Terrorists", "L", score=0, loadout=6_000)
    inst = detect(snap(ct, t, 3, ["ct", "ct"], clock=110.0))
    assert not [i for i in inst if i["hypothesis_id"] == "H-PISTOLCARRY1"]


def test_book_tops_from_all_record_kinds() -> None:
    tops = BookTops()
    tops.update(
        {
            "type": "ws",
            "ts_ns": 1,
            "raw": [
                {
                    "asset_id": "111",
                    "bids": [{"price": "0.40", "size": "10"}, {"price": "0.40", "size": "0"}],
                    "asks": [{"price": "0.55", "size": "5"}, {"price": "0.60", "size": "9"}],
                }
            ],
        }
    )
    assert tops.top("111") == {"bid": 0.40, "bid_size": 10.0, "ask": 0.55, "ask_size": 5.0, "ts_ns": 1}
    tops.update(
        {
            "type": "ws",
            "ts_ns": 2,
            "raw": {
                "market": "0xabc",
                "price_changes": [
                    {"asset_id": "111", "best_bid": "0.42", "best_ask": "0.53"}
                ],
            },
        }
    )
    assert tops.top("111")["bid"] == 0.42 and tops.top("111")["ask"] == 0.53
    tops.update(
        {
            "type": "rest_books",
            "ts_ns": 3,
            "books": [{"asset_id": "222", "bids": [{"price": "0.10", "size": "1"}], "asks": []}],
        }
    )
    assert tops.top("222")["bid"] == 0.10 and "ask" not in tops.top("222")


def test_market_match_requires_both_teams() -> None:
    catalog = {
        "0x1": {
            "condition_id": "0x1",
            "question": "Will Beyond Limits beat Xiaoma Gaming?",
            "event_title": "ESEA Advanced",
            "outcomes": ["Beyond Limits", "Xiaoma Gaming"],
            "clob_token_ids": ["10", "20"],
        },
        "0x2": {
            "condition_id": "0x2",
            "question": "Will a team from LPL win Worlds?",
            "event_title": "Worlds 2026",
            "outcomes": ["Yes", "No"],
            "clob_token_ids": ["30", "40"],
        },
    }
    match = match_market(catalog, "Beyond Limits", "Xiaoma Gaming")
    assert match and match["condition_id"] == "0x1" and match["match_score"] == 1.0
    assert match_market(catalog, "Beyond Limits", "Team Vitality") is None


def test_market_kind_and_required_kind_filter() -> None:
    from gridbot.instruments.market_match import market_kind

    match_row = {
        "condition_id": "0xm",
        "question": "Will Beyond Limits beat Xiaoma Gaming?",
        "event_title": "ESEA Advanced",
        "outcomes": ["Beyond Limits", "Xiaoma Gaming"],
        "clob_token_ids": ["10", "20"],
    }
    round_row = {
        "condition_id": "0xr",
        "question": "Beyond Limits vs Xiaoma Gaming: round 5 winner?",
        "event_title": "ESEA Advanced",
        "outcomes": ["Beyond Limits", "Xiaoma Gaming"],
        "clob_token_ids": ["30", "40"],
    }
    assert market_kind(match_row) == "match"
    assert market_kind(round_row) == "round"
    # match-only catalog + round requirement -> honest None, never a match book
    assert match_market({"0xm": match_row}, "Beyond Limits", "Xiaoma Gaming",
                        required_kind="round") is None
    picked = match_market({"0xm": match_row, "0xr": round_row},
                          "Beyond Limits", "Xiaoma Gaming", required_kind="round")
    assert picked and picked["condition_id"] == "0xr" and picked["kind"] == "round"


def test_follow_carries_torn_lines(tmp_path) -> None:
    from gridbot.instruments.shadow import Follow

    day = tmp_path / "series_state-20260101.jsonl"
    day.write_text('{"a": 1}\n{"b": ')
    follow = Follow(tmp_path, "series_state", from_start=True)
    assert follow.lines() == [{"a": 1}]  # torn tail carried, not dropped
    with open(day, "a") as fh:
        fh.write('2}\n')
    assert follow.lines() == [{"b": 2}]  # fragment completed on a later poll
    follow.close()


def test_follow_rollover_reads_through_without_an_empty_poll(tmp_path) -> None:
    """Day-boundary F6 + audit review F1.

    A reader suspended across a full UTC day finds day2 AND day3 already
    written; draining must happen read-through — one poll crosses both files
    (bounded by max_lines) instead of wasting an idle poll per boundary.
    ShadowEngine.replay exits after two consecutive idle cycles, so an empty
    intermediate day file followed by roll-on-idle terminated replay before
    the newest day was ever reached.
    """
    from gridbot.instruments.shadow import Follow

    (tmp_path / "series_state-20260826.jsonl").write_text('{"day": 1}\n')
    follow = Follow(tmp_path, "series_state", from_start=True)
    assert follow.lines() == [{"day": 1}]  # day1 drained
    (tmp_path / "series_state-20260827.jsonl").write_text('{"day": 2}\n')
    (tmp_path / "series_state-20260828.jsonl").write_text('{"day": 3}\n')
    assert follow.lines() == [{"day": 2}, {"day": 3}]  # crosses both in one poll
    follow.close()


def test_follow_rollover_survives_empty_intermediate_day_file(tmp_path) -> None:
    """Replay-break scenario from the adversarial review: daemon restarted at
    00:00Z wrote nothing to day2 before day3 began — read-through must still
    reach day3 with no idle polls to accumulate."""
    from gridbot.instruments.shadow import Follow

    (tmp_path / "series_state-20260826.jsonl").write_text('{"day": 1}\n')
    follow = Follow(tmp_path, "series_state", from_start=True)
    assert follow.lines() == [{"day": 1}]
    (tmp_path / "series_state-20260827.jsonl").write_text("")  # empty day file
    (tmp_path / "series_state-20260828.jsonl").write_text('{"day": 3}\n')
    assert follow.lines() == [{"day": 3}]
    follow.close()


def test_follow_torn_tail_blocks_read_through_until_resolved(tmp_path) -> None:
    """A held partial must finish in its own file before any later day file is
    entered (day files are disjoint, so the fragment cannot belong there),
    while a complete-but-unread tail still reads through immediately."""
    from gridbot.instruments.shadow import Follow

    day1 = tmp_path / "series_state-20260826.jsonl"
    day1.write_text('{"day": 1}\n{"torn": ')
    follow = Follow(tmp_path, "series_state", from_start=True)
    assert follow.lines() == [{"day": 1}]  # torn tail carried, no read-through
    (tmp_path / "series_state-20260827.jsonl").write_text('{"day": 2}\n')
    assert follow.lines() == []  # partial still pending: stay on day1
    with open(day1, "a") as fh:
        fh.write('1}\n')
    assert follow.lines() == [{"torn": 1}, {"day": 2}]  # finish day1, cross over
    follow.close()
