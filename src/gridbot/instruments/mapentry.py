"""H-MAPENTRY1 instrument math — frozen mapwin-race-v1.

Registered 2026-08-26T03:35Z in docs/experiments/registry.jsonl BEFORE this
module existed (iron rail 2). Every constant below is part of that frozen
registration: changing any of them requires a registry amendment before the
changed instrument runs.

Aggregation: with (a, b) the entry side's current map score and q its
recorded round probability, P(map) = q*R(a+1, b) + (1-q)*R(a, b+1) where R
is the race-to-13 win probability with future rounds iid at
p_fut = (a+4)/(a+b+8), and 12-12 (regulation over) frozen at 0.5. Scores
beyond 12-12 are overtime — outside the frozen domain — and return None.

Venue rule (frozen, conservative reading): only the LIVE map's map_winner
book qualifies; match_winner books are used only for BO1 and BO1 detection
is not wired, so v1 records map books exclusively.
"""

from __future__ import annotations

import json
from pathlib import Path

from gridbot.executor.us_mapping import COVERAGE_FLOOR, _coverage, _tokens

HYPOTHESIS_ID = "H-MAPENTRY1"
RACE_TARGET = 13  # MR12: first to 13 rounds wins the map
PRIOR_WEIGHT = 8  # Beta smoothing toward 0.5 for future-round win share
OT_P = 0.5  # 12-12 regulation exhausted -> frozen overtime coin
ENTRY_MARGIN = 0.04  # qualifying quote must sit >= 4 points below fair


def p_future(a: int, b: int) -> float:
    """Frozen iid win probability for future rounds: (a+4)/(a+b+8)."""
    return (a + PRIOR_WEIGHT / 2) / (a + b + PRIOR_WEIGHT)


def race_win_prob(a: int, b: int, p: float) -> float:
    """P(side reaches 13 round wins before the opponent | score a:b, iid p).

    12-12 is checked BEFORE the terminals: reaching it means regulation is
    exhausted and the frozen value is the overtime coin, not a 13th-round
    race continuation.
    """
    memo: dict[tuple[int, int], float] = {}

    def r(x: int, y: int) -> float:
        if x == RACE_TARGET - 1 and y == RACE_TARGET - 1:
            return OT_P
        if x >= RACE_TARGET:
            return 1.0
        if y >= RACE_TARGET:
            return 0.0
        if (x, y) not in memo:
            memo[(x, y)] = p * r(x + 1, y) + (1 - p) * r(x, y + 1)
        return memo[(x, y)]

    return r(a, b)


def map_win_prob(a: int, b: int, q: float) -> float | None:
    """Frozen aggregation; None outside the domain (overtime past 12-12)."""
    if a >= RACE_TARGET or b >= RACE_TARGET:
        return None
    if a == RACE_TARGET - 1 and b == RACE_TARGET - 1:
        return OT_P
    p = p_future(a, b)
    return q * race_win_prob(a + 1, b, p) + (1 - q) * race_win_prob(a, b + 1, p)


# --- venue joins -------------------------------------------------------------


def load_latest_mappings(pmus_dir: Path) -> dict[str, dict]:
    """grid_series_id -> newest matched mapping row across the last TWO day files.

    Two files, not one: a match made before UTC midnight must survive the day
    rollover. The venue's discovery lanes stop listing an event once its games
    go live, so the post-midnight refresh cannot re-match an in-play series —
    reading only ``files[-1]`` blinded the whole 08-27 armed slate at 00:00Z
    (every signal skipped no_pmus_mapping while yesterday's file held valid
    matches). Oldest-first iteration keeps "later snapshot wins".
    """
    files = sorted(pmus_dir.glob("mappings-*.jsonl"))
    if not files:
        return {}
    out: dict[str, dict] = {}
    for path in files[-2:]:
        with open(path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "grid_pmus_mapping" and rec.get("match"):
                    out[str(rec.get("grid_series_id"))] = rec  # later snapshot wins
    return out


def _invert(side: str) -> str:
    return "short" if side == "long" else "long"


def _best_side(team: str, sides: dict[str, str]) -> str | None:
    """Token-coverage match of a GRID team name against PMUS side names."""
    best, best_cov = None, 0.0
    team_toks = _tokens(team)
    for name in sorted(sides):
        cov = min(_coverage(team, _tokens(name)), _coverage(name, team_toks))
        if cov >= COVERAGE_FLOOR and cov > best_cov:
            best, best_cov = sides[name], cov
    return best


def market_for_instance(
    mapping: dict, map_number: int, team: str, opponent: str
) -> tuple[str, str] | None:
    """(market_slug, us_side) for the LIVE map's winner book, else None.

    PMUS map books name ONE team (Yes side = long). When the instance's team
    is the named one, buy long; when the OPPONENT is named, our team's win
    is the No side — buy short on the same slug.
    """
    entry = (mapping.get("us_markets") or {}).get(f"map{int(map_number)}")
    if not entry or not entry.get("market_slug"):
        return None
    sides = entry.get("sides") or {}
    direct = _best_side(team, sides)
    if direct:
        return str(entry["market_slug"]), direct
    named_opp = _best_side(opponent, sides)
    if named_opp:
        return str(entry["market_slug"]), _invert(named_opp)
    return None


# --- book handling -----------------------------------------------------------


def parse_bbo(payload: dict) -> tuple[float | None, float | None]:
    """(best_bid, best_ask) of the LONG side from a markets.bbo payload.

    Zero prices are dead-book placeholders on this venue (min tick 0.01)
    and are treated as absent.
    """
    md = (payload or {}).get("marketData") or {}

    def val(key: str) -> float | None:
        raw = (md.get(key) or {}).get("value")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    return val("bestBid"), val("bestAsk")


def side_ask(us_side: str, bid: float | None, ask: float | None) -> float | None:
    """Effective ask for OUR side: long pays the ask; short pays 1 - bid."""
    if us_side == "long":
        return ask
    return None if bid is None else round(1.0 - bid, 4)


# --- row building ------------------------------------------------------------


def instance_scores(instance: dict) -> tuple[int, int] | None:
    """(entry side's score, opponent's score) from the recorded state."""
    side = instance.get("side")
    if side not in ("ct", "t"):
        return None
    other = "t" if side == "ct" else "ct"
    state = instance.get("state") or {}
    try:
        return int(state[side]["score"]), int(state[other]["score"])
    except (KeyError, TypeError, ValueError):
        return None


def round_probability(instance: dict) -> float | None:
    """Frozen input: MODELENTRY's model q, event lanes' recorded fair_value."""
    q = instance.get("q")
    if q is None:
        q = instance.get("fair_value")
    try:
        q = float(q)
    except (TypeError, ValueError):
        return None
    return q if 0.0 < q < 1.0 else None


def build_row(instance: dict, ts_ns: int) -> dict:
    """Base mapentry row from one shadow instance (books attached later).

    Rows are recorded for EVERY evaluated instance — including unmapped and
    out-of-domain ones — because the frozen starvation clause needs an
    honest denominator.
    """
    scores = instance_scores(instance)
    q = round_probability(instance)
    p_map = None
    if scores is not None and q is not None:
        p_map = map_win_prob(scores[0], scores[1], q)
    return {
        "type": "mapentry",
        "hypothesis_id": HYPOTHESIS_ID,
        "src_hypothesis_id": instance.get("hypothesis_id"),
        "series_id": instance.get("series_id"),
        "map_number": instance.get("map_number"),
        "round_number": instance.get("round_number"),
        "side": instance.get("side"),
        "team": instance.get("team"),
        "opponent": instance.get("opponent"),
        "a": None if scores is None else scores[0],
        "b": None if scores is None else scores[1],
        "q_round": q,
        "p_map": None if p_map is None else round(p_map, 4),
        "dedup_extra": instance.get("dedup_extra", ""),
        "market_slug": None,
        "us_side": None,
        "bid": None,
        "ask": None,
        "side_ask": None,
        "entry_price_max": None if p_map is None else round(p_map - ENTRY_MARGIN, 4),
        "qualifies": False,
        "skip_reason": None,
        "src_ts_ns": instance.get("ts_ns"),
        "grid_ts_ns": instance.get("grid_ts_ns"),
        "ts_ns": ts_ns,
    }


def attach_book(row: dict, market_slug: str, us_side: str,
                bid: float | None, ask: float | None) -> None:
    """Attach venue evidence and evaluate the frozen entry rule in place."""
    row["market_slug"] = market_slug
    row["us_side"] = us_side
    row["bid"] = bid
    row["ask"] = ask
    eff = side_ask(us_side, bid, ask)
    row["side_ask"] = eff
    row["qualifies"] = (
        eff is not None
        and row["entry_price_max"] is not None
        and eff <= row["entry_price_max"]
    )
