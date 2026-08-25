"""Deterministic trigger-state detectors for the registered hypotheses.

Pure functions over raw seriesState snapshots — no model, no LLM, no I/O.
Each detector emits an instance dict when its registered trigger state is
first observable on a coherent in-round snapshot (same transition guard as
the corpus builder: the scoreboard counts exactly the resolved rounds and
the current segment IS the labeled round — CORPUS-F1-LEAKFILTER).

Fair values are the frozen corpus anchors from
docs/experiments/2026-08-24-corpus-inference-v1.md. Verdicts on the
hypotheses come ONLY from held-out instance records, never from here.
"""

from __future__ import annotations

from gridbot.corpus import rows as r


def _side_state(team: dict | None) -> dict:
    if team is None:
        return {"name": "", "score": 0, "alive": 5, "loadout": 0}
    players = team.get("players") or []
    return {
        "name": str(team.get("name") or ""),
        "score": int(team.get("score") or 0),
        "alive": sum(1 for p in players if p.get("alive")),
        "loadout": int(team.get("loadoutValue") or 0),
    }


def detect(state: dict) -> list[dict]:
    """All hypothesis instances triggered by this snapshot (often empty)."""
    if state.get("finished"):
        return []
    game = r.current_game(state)
    if game is None:
        return []
    ct_node, t_node = r.sided_teams(game)
    ct, t = _side_state(ct_node), _side_state(t_node)
    winners = r.round_winners(game)
    round_number = len(winners) + 1
    map_number = int(game.get("sequenceNumber") or 1)

    # Coherence guard (CORPUS-F1): refuse transition-window snapshots.
    round_segs = [
        s for s in game.get("segments") or [] if str(s.get("type") or "").lower() == "round"
    ]
    seg_round = int(round_segs[-1].get("sequenceNumber") or 1) if round_segs else 1
    if ct["score"] + t["score"] != round_number - 1 or seg_round != round_number:
        return []

    clock = (game.get("clock") or {}).get("currentSeconds")
    base = {
        "series_id": str(state.get("id") or ""),
        "map_number": map_number,
        "round_number": round_number,
        "clock_seconds": clock,
        "state": {"ct": ct, "t": t},
    }
    return [
        *_manadv(base, ct, t, clock),
        *_econbuy(base, ct, t, clock, round_number),
        *_pistolcarry(base, ct, t, winners, round_number),
    ]


def _manadv(base: dict, ct: dict, t: dict, clock: float | None) -> list[dict]:
    """H-MANADV1: score-trailing team holds a >=2-man alive advantage, >=20s clock."""
    if clock is not None and clock < 20:
        return []
    for mine, theirs, side in ((ct, t, "ct"), (t, ct, "t")):
        if mine["score"] - theirs["score"] <= -3 and mine["alive"] - theirs["alive"] >= 2:
            return [
                {
                    **base,
                    "hypothesis_id": "H-MANADV1",
                    "side": side,
                    "team": mine["name"],
                    "opponent": theirs["name"],
                    "fair_value": 0.93,
                }
            ]
    return []


def _econbuy(
    base: dict, ct: dict, t: dict, clock: float | None, round_number: int
) -> list[dict]:
    """H-ECONBUY1: clean-open regulation non-pistol round, exactly one full buy."""
    if round_number in (1, 13) or round_number > 24:
        return []
    if ct["alive"] != 5 or t["alive"] != 5:
        return []
    if clock is not None and clock < 90:
        return []
    ct_full = ct["loadout"] >= r.BUY_FULL_MIN
    t_full = t["loadout"] >= r.BUY_FULL_MIN
    if ct_full == t_full:
        return []
    buyer, eco, side = (ct, t, "ct") if ct_full else (t, ct, "t")
    gap = buyer["loadout"] - eco["loadout"]
    bucket = ">=10k" if gap >= 10_000 else "4-10k" if gap >= 4_000 else "<4k"
    fair = {">=10k": 0.849, "4-10k": 0.710}.get(bucket)
    return [
        {
            **base,
            "hypothesis_id": "H-ECONBUY1",
            "side": side,
            "team": buyer["name"],
            "opponent": eco["name"],
            "equip_gap": gap,
            "gap_bucket": bucket,
            "fair_value": fair,
        }
    ]


def _pistolcarry(
    base: dict, ct: dict, t: dict, winners: list[str], round_number: int
) -> list[dict]:
    """H-PISTOLCARRY1: round 2/14 clean open; pistol winner priced by loser's buy."""
    if round_number not in (2, 14):
        return []
    if ct["alive"] != 5 or t["alive"] != 5:
        return []
    pistol_index = 0 if round_number == 2 else 12
    if len(winners) <= pistol_index:
        return []
    winner_side = winners[pistol_index]
    winner, loser = (ct, t) if winner_side == "ct" else (t, ct)
    loser_full = loser["loadout"] >= r.BUY_FULL_MIN
    return [
        {
            **base,
            "hypothesis_id": "H-PISTOLCARRY1",
            "side": winner_side,
            "team": winner["name"],
            "opponent": loser["name"],
            "loser_full_buy": loser_full,
            "fair_value": 0.702 if loser_full else 0.874,
        }
    ]
