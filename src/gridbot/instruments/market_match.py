"""Match a GRID series (two team names) to a Polymarket catalog market.

Token-set containment over question/title/outcomes. Heuristic by design:
every instance records the match score AND the market kind so wrong matches
can be excluded at verdict time instead of silently trusted. Kind matters:
the registered hypotheses settle at ROUND level, so pricing them against a
match- or map-winner book fabricates edge (tonight-code-review F1).
"""

from __future__ import annotations

import re

_STOP = {"esports", "team", "gaming", "clan", "cs2", "cs", "the"}


def market_kind(row: dict) -> str:
    """"round" / "map" / "match", derived from the market's question text."""
    text = f"{row.get('question') or ''} {row.get('event_title') or ''}"
    if re.search(r"\bround\b", text, re.IGNORECASE):
        return "round"
    if re.search(r"\bmap\b", text, re.IGNORECASE):
        return "map"
    return "match"


def _tokens(text: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return {w for w in words if len(w) > 1 and w not in _STOP}


def _coverage(team: str, hay: set[str]) -> float:
    team_tokens = _tokens(team)
    if not team_tokens:
        return 0.0
    return len(team_tokens & hay) / len(team_tokens)


def team_outcome_token(market: dict | None, team: str) -> str | None:
    """The clob token whose outcome name matches ``team``; None if ambiguous."""
    if not market:
        return None
    outcomes = market.get("outcomes") or []
    tokens = market.get("clob_token_ids") or []
    for outcome, token in zip(outcomes, tokens, strict=False):
        if _coverage(team, _tokens(str(outcome))) >= 0.6:
            return str(token)
    return None


def match_market(
    catalog: dict[str, dict], team_a: str, team_b: str, required_kind: str | None = None
) -> dict | None:
    """Best catalog row mentioning BOTH teams; None below the 0.6 threshold.

    With ``required_kind`` set, rows of any other market kind are skipped —
    an honest "no quotable market" beats a wrong-settlement price.
    """
    best, best_score = None, 0.0
    for row in catalog.values():
        if required_kind is not None and market_kind(row) != required_kind:
            continue
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
        "kind": market_kind(best),
    }
