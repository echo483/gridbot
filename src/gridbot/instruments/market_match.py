"""Match a GRID series (two team names) to a Polymarket catalog market.

Token-set containment over question/title/outcomes. Heuristic by design:
every instance records the match score so weak matches can be excluded at
verdict time instead of silently trusted.
"""

from __future__ import annotations

import re

_STOP = {"esports", "team", "gaming", "clan", "cs2", "cs", "the"}


def _tokens(text: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return {w for w in words if len(w) > 1 and w not in _STOP}


def _coverage(team: str, hay: set[str]) -> float:
    team_tokens = _tokens(team)
    if not team_tokens:
        return 0.0
    return len(team_tokens & hay) / len(team_tokens)


def match_market(catalog: dict[str, dict], team_a: str, team_b: str) -> dict | None:
    """Best catalog row mentioning BOTH teams; None below the 0.6 threshold."""
    best, best_score = None, 0.0
    for row in catalog.values():
        hay = _tokens(
            " ".join(
                [str(row.get("question") or ""), str(row.get("event_title") or "")]
                + [str(o) for o in row.get("outcomes") or []]
            )
        )
        score = min(_coverage(team_a, hay), _coverage(team_b, hay))
        if score > best_score:
            best, best_score = row, score
    if best is None or best_score < 0.6:
        return None
    return {
        "condition_id": best.get("condition_id"),
        "question": best.get("question"),
        "outcomes": best.get("outcomes"),
        "clob_token_ids": best.get("clob_token_ids"),
        "match_score": round(best_score, 3),
    }
