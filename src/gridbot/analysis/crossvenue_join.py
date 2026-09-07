"""Join intl Polymarket CS2 winner markets to their Polymarket US counterparts.

Read-only plumbing for H-CROSSVENUE1 (design:
``docs/superpowers/specs/2026-09-07-crossvenue1-design.md``). This module
establishes only WHICH markets correspond and WHICH WAY ROUND they are. It
computes no basis, no lead and no edge — the hypothesis is not pre-registered
yet, and measuring before the freeze would burn the holdout.

ORIENTATION IS THE WHOLE POINT. Both venues quote a "Yes" price, but Yes *of
what*. Intl markets are two-outcome token pairs under a
``Counter-Strike: A vs B (BO3) - Tournament`` title, where ``outcomes[i]`` pairs
positionally with ``clob_token_ids[i]``, so the Yes token belongs to a NAMED
TEAM. PMUS markets are team-winner books whose ``sides`` map team -> long/short,
and the venue's price is ALWAYS the Yes/long price (registry
H-MAPENTRY1-NOTE7). If intl's Yes is team A while PMUS's Yes is team B, every
basis taken from the pair is sign-inverted — and a sign-inverted basis does not
look broken, it looks like a stable tradeable signal.

This repo has already paid for that class of bug once: NOTE7 records 15 of 24
armed-night shorts posted at the Yes price instead of ``1 - cost``, crossing the
book and being venue-rejected while the ledger read "placed". So both sides are
resolved to a named team and a pair that cannot be resolved is REFUSED, never
guessed. Refusals are returned, not dropped, so coverage is measurable.

The team matcher is imported from ``gridbot.executor.us_mapping`` rather than
reimplemented: it already carries the hard-won reverse-containment rule (the
venue truncates org suffixes — "Overtake" for GRID's "Overtake Sector").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from gridbot.executor.us_mapping import COVERAGE_FLOOR, _tokens

# "Counter-Strike: A vs B (BO3) - Tournament" — 244/244 live titles parsed
# 2026-09-07 with zero failures. The (BOn) marker and the " - " tail are both
# optional because some tournaments omit them.
_EVENT_RE = re.compile(
    r"^\s*counter.?strike\s*:\s*(?P<a>.+?)\s+vs\.?\s+(?P<b>.+?)"
    r"\s*(?:\(\s*BO\d\s*\))?\s*(?:-\s*(?P<rest>.*))?$",
    re.IGNORECASE,
)
_MAP_RE = re.compile(r"\bmap\s*(\d+)\s*winner\b", re.IGNORECASE)
# A winner question carries the title form; props ("Games Total: O/U 2.5",
# "Map 1: Odd/Even Total Kills?") do not. Validated against gamma's own
# groupItemTitle over 1,509 open markets: 1,509 agree, 0 disagree.
_WINNER_Q_RE = re.compile(r"^\s*counter.?strike\s*:.*\bvs\b", re.IGNORECASE)


@dataclass(frozen=True)
class IntlMarket:
    """One intl CS2 winner market, with its Yes side resolved to a team."""

    teams: tuple[str, str]
    yes_team: str
    yes_token: str
    condition_id: str
    kind: str                 # "match" | "map"
    map_number: int | None
    question: str


@dataclass(frozen=True)
class JoinedMarket:
    """An intl market paired with a PMUS book, with the sign settled."""

    intl: IntlMarket
    pmus_market_slug: str
    pmus_yes_team: str
    grid_series_id: str
    kind: str
    orientation: str          # "aligned" | "inverted"


def _same_team(a: str, b: str) -> bool:
    """Token-coverage identity, symmetric, with the reverse-containment rule.

    Reverse containment matters because the venues truncate org suffixes
    differently; requiring FULL containment stops a stray shared token
    ("Esports", "Academy") from manufacturing a match on its own.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta <= tb or tb <= ta:
        return True
    overlap = len(ta & tb)
    return overlap / min(len(ta), len(tb)) >= COVERAGE_FLOOR


def parse_intl_market(row: dict[str, Any]) -> IntlMarket | None:
    """An intl catalog row -> IntlMarket, or None when it is not a winner book.

    Returns None rather than raising: the catalog carries every market on the
    event, and props are the majority (80% of CS2 tokens measured 2026-09-07).
    """
    question = str(row.get("question") or "")
    if not _WINNER_Q_RE.search(question):
        return None
    m = _EVENT_RE.match(str(row.get("event_title") or ""))
    if not m:
        return None
    team_a, team_b = m.group("a").strip(), m.group("b").strip()

    outcomes = [str(o) for o in (row.get("outcomes") or [])]
    tokens = [str(t) for t in (row.get("clob_token_ids") or [])]
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    # outcomes[i] pairs positionally with clob_token_ids[i]; the Yes token is
    # outcomes[0]'s. Refuse anything whose outcomes are not the two teams —
    # a generic ["Yes","No"] pair carries no team identity to orient against.
    if not (_same_team(outcomes[0], team_a) and _same_team(outcomes[1], team_b)):
        return None

    map_hit = _MAP_RE.search(question)
    return IntlMarket(
        teams=(team_a, team_b),
        yes_team=outcomes[0],
        yes_token=tokens[0],
        condition_id=str(row.get("condition_id") or ""),
        kind="map" if map_hit else "match",
        map_number=int(map_hit.group(1)) if map_hit else None,
        question=question,
    )


def _pmus_books(mapping_row: dict[str, Any]):
    """Yield (kind, market_slug, sides) for each usable PMUS book on a mapping."""
    for kind, book in (mapping_row.get("us_markets") or {}).items():
        if not isinstance(book, dict):
            continue
        slug, sides = book.get("market_slug"), book.get("sides")
        if slug and isinstance(sides, dict):
            yield kind, str(slug), {str(k): str(v) for k, v in sides.items()}


def join_markets(
    intl_rows: list[dict[str, Any]],
    mapping_rows: list[dict[str, Any]],
) -> tuple[list[JoinedMarket], list[dict[str, Any]]]:
    """Pair intl winner markets with PMUS books; settle the sign or refuse.

    Returns ``(joined, refusals)``. Refusals carry a ``reason`` so coverage and
    its failure modes are both measurable — a silently dropped pair is how a
    join gets trusted without being verified.
    """
    joined: list[JoinedMarket] = []
    refusals: list[dict[str, Any]] = []

    for row in intl_rows:
        market = parse_intl_market(row)
        if market is None:
            continue

        hit = None
        for mapping in mapping_rows:
            grid_teams = [str(t) for t in (mapping.get("grid_teams") or [])]
            if len(grid_teams) != 2:
                continue
            pair = (
                _same_team(market.teams[0], grid_teams[0])
                and _same_team(market.teams[1], grid_teams[1])
            ) or (
                _same_team(market.teams[0], grid_teams[1])
                and _same_team(market.teams[1], grid_teams[0])
            )
            if pair:
                hit = mapping
                break
        if hit is None:
            refusals.append({"reason": "no_pmus_match", "question": market.question})
            continue

        books = [b for b in _pmus_books(hit) if b[0] == market.kind]
        if not books:
            refusals.append({"reason": "no_pmus_book_of_kind",
                             "question": market.question, "kind": market.kind})
            continue
        _, slug, sides = books[0]

        longs = [t for t, s in sides.items() if s == "long"]
        shorts = [t for t, s in sides.items() if s == "short"]
        if len(longs) != 1 or len(shorts) != 1:
            # A one-sided book carries no Yes team to orient against. Refuse:
            # guessing here is precisely how a sign inversion gets shipped.
            refusals.append({"reason": "pmus_sides_incomplete",
                             "question": market.question, "slug": slug})
            continue
        pmus_yes = longs[0]

        if _same_team(market.yes_team, pmus_yes):
            orientation = "aligned"
        elif _same_team(market.yes_team, shorts[0]):
            orientation = "inverted"
        else:
            refusals.append({"reason": "yes_team_unresolved",
                             "question": market.question, "slug": slug})
            continue

        joined.append(JoinedMarket(
            intl=market,
            pmus_market_slug=slug,
            pmus_yes_team=pmus_yes,
            grid_series_id=str(hit.get("grid_series_id") or ""),
            kind=market.kind,
            orientation=orientation,
        ))

    return joined, refusals


def intl_price_as_pmus_yes(intl_yes_price: float, orientation: str) -> float:
    """Restate an intl Yes price in the PMUS Yes convention.

    The flip belongs HERE, before any basis is taken — never afterwards, where a
    missed inversion silently becomes a signal. Unknown orientation raises
    rather than defaulting, so an unresolved pair cannot leak into a number.
    """
    if orientation == "aligned":
        return intl_yes_price
    if orientation == "inverted":
        return 1.0 - intl_yes_price
    raise ValueError(f"unknown orientation {orientation!r}; refusing to guess a sign")
