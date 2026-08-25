"""Assemble corpus rows from ONE series' capture-ordered raw seriesState dicts.

Production semantics (cs2agent n_bins=4 parity): only rich pre-resolution polls
become rows. Each live poll of a not-yet-resolved round is a candidate; labels
come from the winner oracle — the FIRST snapshot to observe a round resolved
fixes its winner forever; a round whose winner is never observed emits nothing
(labels are never fabricated). Per round, the polls are split into n_bins
equal-width position bins and the LAST poll per bin represents it (bin 0 keeps
the bare ``series:map:round`` row_key; bins>0 append ``:b<n>``). Rounds never
observed live emit nothing — that is the production trade: every stored row
carries a real in-round feature vector.
"""

from __future__ import annotations

from gridbot.corpus import rows as r

N_BINS = 4  # cs2agent production capture used --n-bins 4; must stay constant


def row_key(series_id: str, map_number: int, round_number: int, bin_index: int) -> str:
    base = f"{series_id}:{map_number}:{round_number}"
    return base if bin_index == 0 else f"{base}:b{bin_index}"


def build_series_rows(series_id: str, states: list[dict], n_bins: int = N_BINS) -> list[dict]:
    """Corpus row dicts (identity + features + label, no provenance) for one series."""
    for state in states:
        sid = state.get("id")
        if sid is not None and str(sid) != series_id:
            raise ValueError(f"snapshot for series {sid} in batch for {series_id}")

    # Winner oracle over ALL snapshots in capture order: first observation wins.
    oracle: dict[tuple[int, int], str] = {}
    for state in states:
        for game in state.get("games") or []:
            map_number = int(game.get("sequenceNumber") or 1)
            for i, side in enumerate(r.round_winners(game)):
                oracle.setdefault((map_number, i + 1), side)

    # Candidates: one per live poll with a current game, keyed to the round in
    # progress (len(round_winners)+1 — unresolved by construction).
    candidates: list[dict] = []
    for state in states:
        if state.get("finished"):
            continue
        game = r.current_game(state)
        if game is None:
            continue  # pre-map / between-maps / terminal boundary
        map_number = int(game.get("sequenceNumber") or 1)
        round_number = len(r.round_winners(game)) + 1
        winner = oracle.get((map_number, round_number))
        if winner is None:
            continue  # next round's resolution never observed
        ct_team, t_team = r.team_names(game)
        candidates.append(
            {
                "series_id": series_id,
                "map_number": map_number,
                "round_number": round_number,
                "features": r.build_features(game),
                "label": 1.0 if winner == "ct" else 0.0,
                "ct_team": ct_team,
                "t_team": t_team,
            }
        )

    groups: dict[tuple[int, int], list[dict]] = {}
    for cand in candidates:
        groups.setdefault((cand["map_number"], cand["round_number"]), []).append(cand)

    out: list[dict] = []
    for group in groups.values():
        total = len(group)
        by_bin: dict[int, dict] = {}
        for i, cand in enumerate(group):
            by_bin[(i * n_bins) // total] = cand  # last poll per bin wins
        for bin_index in sorted(by_bin):
            row = dict(by_bin[bin_index])
            row["row_key"] = row_key(series_id, row["map_number"], row["round_number"], bin_index)
            out.append(row)
    return out
