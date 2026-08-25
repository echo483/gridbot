"""Series-state snapshot → 20-feature corpus row primitives.

Ported faithfully from cs2agent (grid_series_state / features / buy_classifier)
per the 2026-08-24 corpus-semantics extraction. The feature schema and its
order are a CONTRACT shared with the existing 22k-row corpus: append-only,
never reorder, never rename. All features are framed from the CT side of the
labeled round; label 1.0 means the CT-side team won that round.
"""

from __future__ import annotations

FEATURE_NAMES = (
    # legacy 17
    "alive_ratio",
    "man_advantage",
    "econ_ratio",
    "ct_full_buy",
    "t_full_buy",
    "awp_diff",
    "bomb_planted",
    "bomb_time_remaining",
    "time_remaining",
    "round_number",
    "rounds_played",
    "score_lead",
    "is_match_point",
    "momentum_3",
    "rating_diff",
    "map_rating_diff",
    "prior_round_p",
    # new 3
    "hp_alive_diff",
    "awp_alive_diff",
    "equip_value_diff",
)

# Pinned from cs2agent's empirical artifact (data_cache/buy_thresholds.json,
# derived 2026-07-03: force 7025 / half 10275 / full 13400). The existing
# corpus was built at full_min=13400; changing the cut would fork the
# ct/t_full_buy distribution against every row already in training_rounds.
BUY_FULL_MIN = 13_400

DEFAULT_TIME_REMAINING = 115.0

_CS_SUFFIXES = ("Cs2", "Csgo")


def is_cs_node(node: dict) -> bool:
    return str(node.get("__typename") or "").endswith(_CS_SUFFIXES)


def side_of(team: dict) -> str | None:
    side = str(team.get("side") or "").strip().lower()
    if side == "counter-terrorists":
        return "ct"
    if side == "terrorists":
        return "t"
    return None


def current_game(state: dict) -> dict | None:
    """Last game that has started and not finished, else None."""
    cur = None
    for game in state.get("games") or []:
        if game.get("started") and not game.get("finished"):
            cur = game
    return cur


def round_winners(game: dict) -> list[str]:
    """Winning side per finished round segment, in segment list order.

    Winner = side of the FIRST cs-typed segment team with truthy ``won``; a
    segment whose winner side is unresolvable is skipped — never guessed.
    """
    winners: list[str] = []
    for seg in game.get("segments") or []:
        if str(seg.get("type") or "").lower() != "round" or not seg.get("finished"):
            continue
        for team in seg.get("teams") or []:
            if is_cs_node(team) and team.get("won"):
                side = side_of(team)
                if side:
                    winners.append(side)
                break
    return winners


def round_winners_by_seq(game: dict) -> dict[int, str]:
    """Winning side keyed by the segment's OWN sequenceNumber.

    Oracles must use this, not the positional list: an unresolvable-winner
    segment leaves a hole here instead of shifting every later round's label
    onto the wrong key (tonight-code-review F6 — labels are never fabricated).
    """
    out: dict[int, str] = {}
    for seg in game.get("segments") or []:
        if str(seg.get("type") or "").lower() != "round" or not seg.get("finished"):
            continue
        for team in seg.get("teams") or []:
            if is_cs_node(team) and team.get("won"):
                side = side_of(team)
                if side:
                    out[int(seg.get("sequenceNumber") or 0)] = side
                break
    return out


def _has_awp_name(name: str) -> bool:
    n = name.strip().lower()
    return n == "awp" or "awp" in n.split()


def _player_has_awp(player: dict) -> bool:
    items = (player.get("inventory") or {}).get("items") or []
    return any(_has_awp_name(str(item.get("name") or "")) for item in items)


def sided_teams(game: dict) -> tuple[dict | None, dict | None]:
    cs_teams = [t for t in game.get("teams") or [] if is_cs_node(t)]
    ct = next((t for t in cs_teams if side_of(t) == "ct"), None)
    t = next((t for t in cs_teams if side_of(t) == "t"), None)
    return ct, t


def team_names(game: dict) -> tuple[str, str]:
    ct, t = sided_teams(game)
    return str((ct or {}).get("name") or ""), str((t or {}).get("name") or "")


def build_features(game: dict) -> dict[str, float]:
    """The 20-feature vector for the game's in-progress round (CT-framed).

    Absent side team node → alive defaults to 5, everything else to 0
    (cs2agent parity). rating/map_rating are 0.0 and prior_round_p 0.5 on the
    ingestion path; bomb state is unobservable on GRID OA (always 0.0).
    """
    ct, t = sided_teams(game)

    def alive(team: dict | None) -> int:
        if team is None:
            return 5
        return sum(1 for p in team.get("players") or [] if p.get("alive"))

    def hp_alive(team: dict | None) -> int:
        if team is None:
            return 0
        return sum(
            int(p.get("currentHealth") or 0) for p in team.get("players") or [] if p.get("alive")
        )

    def econ(team: dict | None) -> tuple[int, bool, bool, int, bool]:
        """avg_money, full_buy, has_awp, loadout_value, awp_alive."""
        if team is None:
            return 0, False, False, 0, False
        players = team.get("players") or []
        loadout = int(team.get("loadoutValue") or 0)
        return (
            int(team.get("money") or 0) // 5,
            loadout >= BUY_FULL_MIN,
            any(_player_has_awp(p) for p in players),
            loadout,
            any(_player_has_awp(p) for p in players if p.get("alive")),
        )

    ct_alive, t_alive = alive(ct), alive(t)
    ct_money, ct_full, ct_awp, ct_loadout, ct_awp_alive = econ(ct)
    t_money, t_full, t_awp, t_loadout, t_awp_alive = econ(t)
    ct_score = int((ct or {}).get("score") or 0)
    t_score = int((t or {}).get("score") or 0)

    round_segs = [
        s for s in game.get("segments") or [] if str(s.get("type") or "").lower() == "round"
    ]
    feature_round = int(round_segs[-1].get("sequenceNumber") or 1) if round_segs else 1

    clock_seconds = (game.get("clock") or {}).get("currentSeconds")
    time_remaining = float(clock_seconds) if clock_seconds is not None else DEFAULT_TIME_REMAINING

    recent = round_winners(game)[-3:]
    momentum = (
        0.0 if not recent else (2.0 * sum(1 for w in recent if w == "ct") - len(recent)) / len(recent)
    )

    return {
        "alive_ratio": (ct_alive - t_alive) / 5.0,
        "man_advantage": float(ct_alive - t_alive),
        "econ_ratio": 0.0 if ct_money + t_money == 0 else (ct_money - t_money) / 10_000,
        "ct_full_buy": 1.0 if ct_full else 0.0,
        "t_full_buy": 1.0 if t_full else 0.0,
        "awp_diff": float(ct_awp) - float(t_awp),
        "bomb_planted": 0.0,
        "bomb_time_remaining": 0.0,
        "time_remaining": time_remaining,
        "round_number": float(feature_round),
        "rounds_played": float(ct_score + t_score),
        "score_lead": float(ct_score - t_score),
        "is_match_point": 1.0 if max(0, 13 - ct_score) == 1 or max(0, 13 - t_score) == 1 else 0.0,
        "momentum_3": momentum,
        "rating_diff": 0.0,
        "map_rating_diff": 0.0,
        "prior_round_p": 0.5,
        "hp_alive_diff": (hp_alive(ct) - hp_alive(t)) / 500.0,
        "awp_alive_diff": float(ct_awp_alive) - float(t_awp_alive),
        "equip_value_diff": (ct_loadout - t_loadout) / 20_000.0,
    }
