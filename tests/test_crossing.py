"""First-crossing semantics for the H-MODELENTRY1 tracker (scripted predictor)."""

from __future__ import annotations

from gridbot.instruments.crossing import CrossingTracker


def team(side: str, name: str, score: int = 0) -> dict:
    return {
        "__typename": "TeamStateCs2",
        "side": side,
        "name": name,
        "score": score,
        "money": 0,
        "loadoutValue": 0,
        "players": [
            {"alive": True, "currentHealth": 100, "inventory": {"items": []}} for _ in range(5)
        ],
    }


def rseg(seq: int, winner_side: str | None, finished: bool = True) -> dict:
    return {
        "type": "round",
        "sequenceNumber": seq,
        "finished": finished,
        "teams": [
            {"__typename": "SegmentTeamStateCs2", "side": "Counter-Terrorists",
             "won": winner_side == "ct"},
            {"__typename": "SegmentTeamStateCs2", "side": "Terrorists", "won": winner_side == "t"},
        ],
    }


def snap(round_number: int = 1, ct_score: int = 0, t_score: int = 0,
         clock: float = 60.0) -> dict:
    winners = ["ct"] * ct_score + ["t"] * t_score
    segments = [rseg(i + 1, w) for i, w in enumerate(winners)]
    segments.append(rseg(round_number, None, finished=False))
    return {
        "id": "888",
        "started": True,
        "finished": False,
        "games": [
            {
                "sequenceNumber": 1,
                "started": True,
                "finished": False,
                "teams": [team("Counter-Terrorists", "CT-Team", ct_score),
                          team("Terrorists", "T-Team", t_score)],
                "segments": segments,
                "clock": {"currentSeconds": clock},
            }
        ],
    }


def scripted(values: list[float]) -> CrossingTracker:
    it = iter(values)
    return CrossingTracker(lambda _features: next(it))


def test_upward_crossings_fire_once_per_cell() -> None:
    tracker = scripted([0.5, 0.7, 0.8, 0.9, 0.9])
    assert tracker.observe(snap()) == []  # first observation: no prev
    first = tracker.observe(snap())
    assert [(i["side"], i["threshold"]) for i in first] == [("ct", 0.65)]
    assert first[0]["q_prev"] == 0.5 and first[0]["q"] == 0.7
    assert first[0]["dedup_extra"] == "ct:0.65"
    second = tracker.observe(snap())
    assert [(i["side"], i["threshold"]) for i in second] == [("ct", 0.75)]
    third = tracker.observe(snap())
    assert [(i["side"], i["threshold"]) for i in third] == [("ct", 0.85)]
    assert tracker.observe(snap()) == []  # steady state: nothing new


def test_t_side_crossing_from_ct_probability_drop() -> None:
    tracker = scripted([0.5, 0.3])
    tracker.observe(snap())
    inst = tracker.observe(snap())
    assert [(i["side"], i["threshold"]) for i in inst] == [("t", 0.65)]
    assert inst[0]["team"] == "T-Team" and inst[0]["opponent"] == "CT-Team"


def test_oscillation_does_not_refire() -> None:
    tracker = scripted([0.5, 0.7, 0.6, 0.7])
    tracker.observe(snap())
    assert len(tracker.observe(snap())) == 1  # 0.65 fires
    assert tracker.observe(snap()) == []  # dip
    assert tracker.observe(snap()) == []  # re-cross: cell already fired


def test_guards_block_low_clock_and_transition_state() -> None:
    tracker = scripted([0.5, 0.9, 0.9])
    tracker.observe(snap())
    assert tracker.observe(snap(clock=5.0)) == []  # clock guard
    incoherent = snap(ct_score=1)  # scoreboard ahead of winners for round 1
    assert tracker.observe(incoherent) == []


def test_new_round_needs_fresh_baseline() -> None:
    tracker = scripted([0.9, 0.9])
    assert tracker.observe(snap(round_number=2, ct_score=1)) == []
    # same round second observation with no crossing (0.9 -> 0.9): nothing
    assert tracker.observe(snap(round_number=2, ct_score=1)) == []
