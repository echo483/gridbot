"""GRID ↔ Polymarket US market mapping (read-only; the visibility bridge).

The executor's wire layer (pmus.place) refuses any intent without a
``market_slug`` + ``us_side`` — and nothing on the US venue can be quoted
until a GRID series is joined to a Polymarket US event. That join lives
here, with three hard-won shape facts baked in (live-verified 2026-08-26):

- PMUS discovery MUST use ``search.query`` and/or a time-boxed
  ``events.list(startTimeMin/Max, orderBy startTime)``. Plain
  ``events.list({active, closed})`` pages 8×100 events and CS2 is buried
  under MLB/NFL; the ``seriesSlug`` query param is silently IGNORED by the
  server (returns the unfiltered page — this silently starved the first
  watch lane: zero events recorded while CS2 was listed all day).
- PMUS team objects carry PandaScore providerIds, but GRID's TeamParticipant
  exposes ONLY ``baseInfo.name`` — no provider join key exists. The join is
  normalized-name token coverage + scheduled-start proximity, scored so
  wrong matches are visible rather than trusted.
- US events carry per-market ``marketSides`` (team, long flag, last price)
  and slugs like ``astatc-cs2-nem-1win-2026-08-26-map1``; market KINDS are
  match / map / total / handicap. **There are NO round-winner books on the
  US venue** — every currently registered hypothesis settles at round
  level, so none of them is quotable on PMUS today. This module maps what
  exists (match + map winners) and refuses to fabricate the rest.

Pure functions first (match/score/type), thin I/O above (fetch + join +
snapshot). No auth, no orders, no secrets: the public gateway only.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gridbot import config

log = logging.getLogger(__name__)

# How far a PMUS event start may sit from the GRID scheduled start to be a
# join candidate at all (venue delays and reschedules are common on tier-2).
START_WINDOW_H = 3.0
# Token coverage floor for a team-name match (same convention as
# instruments.market_match: len(hit ∩ hay) / len(team_tokens)).
COVERAGE_FLOOR = 0.6
# PMUS "series" slugs that carry esports events we can ever trade.
ESPORTS_SERIES_SLUGS = {"cs2", "league-of-legends", "valorant", "call-of-duty"}

_STOP = {"esports", "team", "gaming", "clan", "the", "club", "academy"}


def _tokens(text: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return {w for w in words if len(w) > 1 and w not in _STOP}


def _coverage(team: str, hay: set[str]) -> float:
    team_tokens = _tokens(team)
    if not team_tokens:
        return 0.0
    return len(team_tokens & hay) / len(team_tokens)


def pmus_team_names(event: dict) -> list[str]:
    """Participating team names from a PMUS event (event.teams, else title)."""
    names = [t.get("name") for t in event.get("teams") or [] if t.get("name")]
    if names:
        return [str(n) for n in names]
    title = str(event.get("title") or "")
    if " vs" in title:
        return [p.strip() for p in title.split(" vs ", 1)]
    return []


def _start_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def match_grid_to_event(
    grid_series: dict, event: dict, window_h: float = START_WINDOW_H
) -> dict | None:
    """Score one (GRID series, PMUS event) pair; None when not a candidate.

    A candidate must (a) be an esports-series event, (b) start within
    ``window_h`` of the GRID scheduled start, and (c) name-cover BOTH teams
    at >= COVERAGE_FLOOR. Score is the min team coverage (1.0 = both team
    names fully covered), reduced by start-time skew so an exact-start
    double match outranks a same-teams different-day event.
    """
    if str(event.get("seriesSlug") or "") not in ESPORTS_SERIES_SLUGS:
        return None
    grid_teams = [str(t.get("baseInfo", {}).get("name") or "") for t in grid_series.get("teams") or []]
    grid_teams = [t for t in grid_teams if t]
    pmus_teams = pmus_team_names(event)
    if len(grid_teams) != 2 or len(pmus_teams) < 2:
        return None
    g_start = _start_ts(grid_series.get("startTimeScheduled"))
    p_start = _start_ts(str(event.get("startTime") or event.get("startDate") or ""))
    if g_start is None or p_start is None:
        return None
    skew_h = abs(g_start - p_start) / 3600.0
    if skew_h > window_h:
        return None
    hay = _tokens(" ".join(pmus_teams))
    cov = min(_coverage(t, hay) for t in grid_teams)
    if cov < COVERAGE_FLOOR:
        return None
    score = cov - min(skew_h / window_h, 1.0) * 0.1
    return {
        "grid_series_id": str(grid_series.get("id")),
        "grid_teams": grid_teams,
        "pmus_event_slug": str(event.get("slug") or ""),
        "pmus_title": str(event.get("title") or ""),
        "pmus_teams": pmus_teams[:2],
        "coverage": round(cov, 3),
        "start_skew_h": round(skew_h, 2),
        "score": round(score, 3),
    }


def join_series(
    grid_series: list[dict], events: list[dict], window_h: float = START_WINDOW_H
) -> list[dict]:
    """Best match per GRID series (highest score wins; one per series)."""
    best: dict[str, dict] = {}
    for series in grid_series:
        sid = str(series.get("id"))
        if sid in config.GRID_TEST_SERIES_IDS:
            continue
        for event in events:
            m = match_grid_to_event(series, event, window_h)
            if m and m["score"] > best.get(sid, {}).get("score", -1.0):
                best[sid] = m
    return [best[sid] for sid in sorted(best)]


# --- PMUS market typing -----------------------------------------------------


def us_market_kind(market: dict) -> str:
    """'match' | 'map' | 'total' | 'handicap' | 'other' for a PMUS market."""
    mtype = str(market.get("sportsMarketType") or "")
    if mtype.startswith("esports_map_winner"):
        return "map"
    if mtype == "esports_match_winner":
        return "match"
    if "total" in mtype:
        return "total"
    if "handicap" in mtype:
        return "handicap"
    return "other"


def map_number_of(market: dict) -> int | None:
    """Map index (1-based) for esports_map_winner_N markets."""
    m = re.fullmatch(r"esports_map_winner_(\d+)", str(market.get("sportsMarketType") or ""))
    return int(m.group(1)) if m else None


def us_markets_for_event(event: dict) -> dict:
    """Tradeable US markets of an event, keyed for the executor.

    Returns ``{"match": {...}, "map1": {...}, "map2": {...}}`` where each
    entry carries ``market_slug``, ``kind``, ``tick``,
    ``min_qty`` and ``sides``: team name -> ``"long"`` | ``"short"``
    (match winners) or the named team -> ``"long"``-means-map-win
    (map winners: PMUS maps are Yes/No on ONE team; ``long`` = Yes).
    No round books exist on this venue — callers get no "round" key, ever.
    """
    out: dict[str, dict] = {}
    for market in event.get("markets") or []:
        kind = us_market_kind(market)
        if kind not in ("match", "map"):
            continue
        sides: dict[str, str] = {}
        if kind == "match":
            for side in market.get("marketSides") or []:
                name = (side.get("team") or {}).get("name")
                if name:
                    sides[str(name)] = "long" if side.get("long") else "short"
        else:
            for side in market.get("marketSides") or []:
                name = (side.get("team") or {}).get("name")
                if name and side.get("long"):
                    sides[str(name)] = "long"  # Yes side: this team wins the map
        entry = {
            "market_slug": str(market.get("slug") or ""),
            "kind": kind,
            "tick": market.get("orderPriceMinTickSize"),
            "min_qty": market.get("minimumTradeQty"),
            "sides": sides,
        }
        if kind == "match":
            out["match"] = entry
        else:
            n = map_number_of(market)
            if n is not None:
                out[f"map{n}"] = entry
    return out


# --- I/O: fetch + snapshot ---------------------------------------------------


def fetch_pmus_esports_events(client, team_queries: tuple[str, ...] = ()) -> list[dict]:
    """All esports events via the three discovery lanes that actually work.

    Lane 1: ``search.query`` per series keyword (catches events regardless
    of chronological burial). Lane 2: time-boxed ``events.list`` ordered by
    startTime (catches anything search's ranking misses in the live
    window). Lane 3: ``search.query`` per GRID TEAM NAME — the venue DROPS
    IN-PLAY events from both generic lanes (verified live 2026-08-27:
    keyword search + events.list returned only future games while team-name
    search found every live one instantly; the whole live NA slate went
    unmapped without this lane). Dedupe by slug. NOTE: the ``seriesSlug``
    list-param is ignored server-side — do not "simplify" back to it.
    """
    events: dict[str, dict] = {}
    now = datetime.now(UTC)
    for q in ("cs2", "esports", "valorant", "league of legends"):
        try:
            resp = client.search.query({"query": q, "limit_per_type": 50})
        except Exception as exc:  # noqa: BLE001 — one lane failing must not kill the rest
            log.warning("pmus search lane %r failed: %s", q, type(exc).__name__)
            continue
        for e in resp.get("events") or []:
            if str(e.get("seriesSlug") or "") in ESPORTS_SERIES_SLUGS and e.get("slug"):
                events[str(e["slug"])] = e
    try:
        resp = client.events.list(
            {
                "limit": 100,
                "active": True,
                "closed": False,
                "startTimeMin": (now - timedelta(hours=6)).isoformat(),
                "startTimeMax": (now + timedelta(hours=30)).isoformat(),
                "orderBy": ["startTime"],
                "orderDirection": "asc",
            }
        )
        for e in resp.get("events") or []:
            if str(e.get("seriesSlug") or "") in ESPORTS_SERIES_SLUGS and e.get("slug"):
                events.setdefault(str(e["slug"]), e)
    except Exception as exc:  # noqa: BLE001
        log.warning("pmus events.list lane failed: %s", type(exc).__name__)
    for q in dict.fromkeys(t.strip() for t in team_queries):
        if len(q) < 3 or q.upper().startswith("TBD"):
            continue
        try:
            resp = client.search.query({"query": q, "limit_per_type": 10})
        except Exception as exc:  # noqa: BLE001
            log.warning("pmus team-search lane %r failed: %s", q, type(exc).__name__)
            continue
        for e in resp.get("events") or []:
            if str(e.get("seriesSlug") or "") in ESPORTS_SERIES_SLUGS and e.get("slug"):
                events.setdefault(str(e["slug"]), e)
    return list(events.values())


def live_window_team_names(grid_series: list[dict], now_ts: float) -> tuple[str, ...]:
    """Team names of series scheduled within (-6h, +2h) of ``now_ts``.

    This is the lane-3 query set: live and imminent series are exactly the
    ones the venue's generic discovery lanes drop, and bounding the window
    keeps the per-sweep search-call count small (2 calls per nearby series).
    """
    lo, hi = now_ts - 6 * 3600, now_ts + 2 * 3600
    names: list[str] = []
    for s in grid_series:
        if str(s.get("id")) in config.GRID_TEST_SERIES_IDS:
            continue
        st = _start_ts(s.get("startTimeScheduled"))
        if st is None or not (lo <= st <= hi):
            continue
        for t in s.get("teams") or []:
            name = str((t.get("baseInfo") or {}).get("name") or "")
            if name:
                names.append(name)
    return tuple(dict.fromkeys(names))


def fetch_grid_series(grid_client_post) -> list[dict]:
    """GRID CS2 series in the live+upcoming window via the repo's own layer."""
    import gridbot.capture.grid_queries as gq

    now = datetime.now(UTC)
    gte = (now - timedelta(hours=6)).isoformat(timespec="seconds")
    lte = (now + timedelta(hours=30)).isoformat(timespec="seconds")
    out: list[dict] = []
    after = None
    for _ in range(8):  # 50/page covers a 36h CS2 day comfortably
        data = grid_client_post(
            config.GRID_CENTRAL_DATA_URL,
            gq.DISCOVER_RICH,
            {"titleId": config.TITLE_ID_CS2, "gte": gte, "lte": lte, "after": after},
        )
        conn = data["allSeries"]
        out.extend(edge["node"] for edge in conn["edges"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return out


def snapshot(client, grid_post) -> dict:
    """One mapping pass: GRID series × PMUS events → data/pmus/mappings-*.jsonl.

    Read-only, no auth on the venue side; GRID via the repo's graphql layer
    (key + backoff). Writes one row per GRID series with its best PMUS
    match (or ``null``) plus the tradeable US markets of the matched event,
    and returns the coverage counts for logging.
    """
    grid_series = fetch_grid_series(grid_post)
    team_queries = live_window_team_names(
        grid_series, datetime.now(UTC).timestamp()
    )
    events = fetch_pmus_esports_events(client, team_queries)
    matches = join_series(grid_series, events)
    matched = {m["grid_series_id"]: m for m in matches}
    out_dir = config.DATA_DIR / "pmus"
    out_dir.mkdir(parents=True, exist_ok=True)
    day = f"{datetime.now(UTC):%Y%m%d}"
    path: Path = out_dir / f"mappings-{day}.jsonl"
    n_markets = 0
    with open(path, "a") as fh:
        for series in grid_series:
            sid = str(series.get("id"))
            m = matched.get(sid)
            markets = us_markets_for_event(
                next(e for e in events if e.get("slug") == m["pmus_event_slug"])
            ) if m else {}
            n_markets += len(markets)
            row = {
                "type": "grid_pmus_mapping",
                "ts_ns": time.time_ns(),
                "grid_series_id": sid,
                "grid_teams": [t.get("baseInfo", {}).get("name") for t in series.get("teams") or []],
                "grid_start": series.get("startTimeScheduled"),
                "match": m,
                "us_markets": markets,
            }
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    counts = {
        "grid_series": len(grid_series),
        "pmus_events": len(events),
        "matched": len(matches),
        "us_markets": n_markets,
    }
    log.info("pmus mapping snapshot: %s", counts)
    return counts
