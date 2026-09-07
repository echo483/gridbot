"""Polymarket esports market discovery via the public gamma API (no auth).

Emits catalog rows (event/market/tokens) and returns the active asset-id set for
the book recorder. Permissive by design: recording a thin book is cheap; missing
a market is not recoverable.

Discovery MUST be tag-scoped. Sweeping the unfiltered ``/events?closed=false``
list with offset paging — what this module did until 2026-09-07 — cannot reach
a CS2 match book at all, for two independently verified reasons:

- Gamma hard-caps offset paging: ``offset=2500`` returns HTTP 422
  ``{"type":"validation error","error":"offset too large, use /events/keyset
  for deeper pagination"}``.
- The unfiltered list is dominated by politics and mainstream sports (our own
  catalog capture on 2026-09-06: 4,113 ``sports`` + 3,109 ``politics`` rows of
  10,223), so low-liquidity CS2 matches sort below the reachable ceiling.

The measured cost: ``data/pm/catalog-20260903.jsonl`` and ``-20260906.jsonl``
hold 151 and 261 CS2-tagged rows and EVERY one is the same season-long future
("Will FaZe win a Tier 1 event in 2026?"). Zero match books, for the whole life
of the capture. This is the same bug class already fixed on the US venue — see
``executor/us_mapping`` ("CS2 is buried under MLB/NFL... silently starved the
first watch lane") — and the same remedy: query the narrow lane, never the
firehose.

``tag_slug=esports`` is that lane (live-verified 2026-09-07): 743 open events,
465 of them CS2 matches on the venue's ``Counter-Strike: A vs B (BOn) -
Tournament`` template, 79 of those still alive. Every ``counter-strike-2`` /
``cs2`` / ``counter-strike`` tagged event was a strict subset of it, so one tag
sweep suffices today; ``DISCOVERY_TAG_SLUGS`` is a tuple so a future tag split
is a one-word change. The whole lane is ~8 pages — the 2000-offset ceiling is
not binding, so bounded offset paging stays (``/events/keyset`` was verified to
work, returning ``{"events": [...], "next_cursor": "..."}``, but is not needed
and is not depended on). ``_is_esports`` is kept as a client-side backstop
against gamma silently ignoring ``tag_slug`` — the exact way ``seriesSlug``
failed on the US venue.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from gridbot import config
from gridbot.capture.files import DayWriter

log = logging.getLogger(__name__)

ESPORTS_TAG_SLUGS = {"esports", "counter-strike", "league-of-legends", "dota-2", "valorant", "cs2"}
ESPORTS_TITLE_RE = re.compile(
    r"counter.?strike|\bcs2\b|\bcsgo\b|dota|league of legends|\blol\b|valorant|"
    r"\biem\b|\bblast\b|\besl\b|\bpgl\b|\blck\b|\blpl\b|\blec\b|\blta\b|worlds|"
    # \b-bounded: bare `major` matched "Next Senate Majority Leader?", which alone
    # contributed 2,773 of the 10,223 rows in catalog-20260906.jsonl.
    r"\bmajor\b|esports",
    re.IGNORECASE,
)
# 616 open CS2 winner tokens measured on the live esports lane 2026-09-07; the
# cap is winner-first (capture_priority), so props are evicted before books.
MAX_ASSETS = 800

# The tag lanes swept per pass. One entry today (verified: every CS2-tagged open
# event also carries `esports`); extra lanes dedupe by event id, so adding one
# is safe.
DISCOVERY_TAG_SLUGS = ("esports",)
PAGE_LIMIT = 100
# Gamma refuses offsets past ~2000 (HTTP 422, "use /events/keyset"). The esports
# lane is ~743 open events, so this bounds the loop rather than truncating it.
MAX_OFFSET = 2000

# CS2 priority for the MAX_ASSETS cut. The esports lane carries ~6,150 open
# tokens against a 400 cap, and a plain volume ranking admits only 56 of 582
# CS2 books (measured 2026-09-07) — LoL, poker and chess outbid the tier-2 CS2
# matches this system exists to trade. Same token convention as
# ``us_mapping``: tag slugs first, venue title template second.
CS2_TAG_SLUGS = {"counter-strike-2", "counter-strike", "cs2", "csgo"}
# "Counter-Strike: Villainous vs Axial (BO3) - PGL Masters Bucharest ...": the
# venue's match-event template. 465/465 CS2 title-prefixed events matched it on
# 2026-09-07; the ~28 CS2 futures and props matched none.
CS2_MATCH_TITLE_RE = re.compile(r"^\s*counter.?strike\s*:.*\bvs\b", re.IGNORECASE)
CS2_TITLE_RE = re.compile(r"counter.?strike|\bcs2\b|\bcsgo\b", re.IGNORECASE)


def _as_list(v: Any) -> list:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []
    return v or []


def _is_esports(event: dict) -> bool:
    slugs = {t.get("slug", "") for t in event.get("tags") or []}
    if slugs & ESPORTS_TAG_SLUGS:
        return True
    return bool(ESPORTS_TITLE_RE.search(event.get("title") or ""))


def _is_alive(event: dict, now: datetime) -> bool:
    end = event.get("endDate")
    if end:
        try:
            end_dt = datetime.fromisoformat(end)
            if end_dt < now - timedelta(hours=12):
                return False
        except ValueError:
            pass
    return True


def is_cs2_match(title: str) -> bool:
    """True for the venue's CS2 match-event title template (not futures)."""
    return bool(CS2_MATCH_TITLE_RE.search(title or ""))


def is_cs2_winner_market(row: dict) -> bool:
    """True for a CS2 match/map WINNER book — the only kind we can trade.

    A CS2 match event carries ~11 markets and only ~4 are winner books; the rest
    are Games Total, Map Handicap and Odd/Even-Total-Kills props. Measured on the
    live esports lane 2026-09-07: 616 open winner tokens against 2,402 open prop
    tokens, so 80% of CS2 tokens are untradeable for us and were displacing live
    winner books under the cap. Worse, an event stays in gamma's ``closed=false``
    list while only its props remain open, so finished matches (e.g. a 2026-03-10
    slug) kept consuming subscriptions through their odd/even books.

    The market ``question`` separates them exactly: a winner question carries the
    "Counter-Strike: A vs B" title form, a prop does not ("Games Total: O/U 2.5",
    "Map 1: Odd/Even Total Kills?"). Validated against gamma's own
    ``groupItemTitle`` over 1,509 open markets: 1,509 agree, 0 disagree. We match
    on ``question`` rather than ``groupItemTitle`` so the frozen catalog row shape
    is unchanged.
    """
    return bool(CS2_MATCH_TITLE_RE.search(str(row.get("question") or "")))


def capture_priority(row: dict) -> int:
    """3 = CS2 winner book, 2 = other CS2 match market, 1 = other CS2, 0 = rest.

    Drives the MAX_ASSETS cut, so a prop can never displace a tradeable book.
    """
    title = str(row.get("event_title") or "")
    if is_cs2_match(title):
        return 3 if is_cs2_winner_market(row) else 2
    if set(row.get("tags") or []) & CS2_TAG_SLUGS or CS2_TITLE_RE.search(title):
        return 1
    return 0


def rank_for_capture(catalog: Iterable[dict]) -> list[dict]:
    """Catalog rows in book-subscription order: CS2 matches, then by size."""
    return sorted(
        catalog,
        key=lambda r: (
            capture_priority(r),
            float(r.get("volume24hr") or 0),
            float(r.get("liquidity") or 0),
        ),
        reverse=True,
    )


def _rows_for_event(event: dict) -> list[dict]:
    """Catalog rows for one event. Shape is frozen — the tape depends on it."""
    out: list[dict] = []
    for m in event.get("markets") or []:
        if m.get("closed"):
            continue
        tokens = [str(t) for t in _as_list(m.get("clobTokenIds"))]
        if not tokens:
            continue
        out.append(
            {
                "event_title": event.get("title"),
                "event_slug": event.get("slug"),
                "end_date": event.get("endDate"),
                "tags": sorted({t.get("slug", "") for t in event.get("tags") or []}),
                "question": m.get("question"),
                "condition_id": m.get("conditionId"),
                "outcomes": _as_list(m.get("outcomes")),
                "clob_token_ids": tokens,
                "liquidity": m.get("liquidityNum") or m.get("liquidity"),
                "volume24hr": m.get("volume24hr"),
            }
        )
    return out


async def _fetch_page(client: httpx.AsyncClient, tag_slug: str, offset: int) -> list[dict] | None:
    """One gamma page: rows, ``[]`` at end of list, ``None`` on failure.

    ``[]`` also covers gamma's 422 offset-ceiling reply — the server saying
    "no deeper", which is an end, not an outage. ``None`` means the sweep
    learned nothing (transport error, 5xx, junk body) and the caller must
    discard the whole pass rather than publish a truncated catalog.
    """
    try:
        resp = await client.get(
            f"{config.PM_GAMMA_URL}/events",
            params={
                "tag_slug": tag_slug,
                "closed": "false",
                "limit": PAGE_LIMIT,
                "offset": offset,
            },
            timeout=30.0,
        )
        page = resp.json() if resp.content else None
        if resp.status_code == 422 and isinstance(page, dict):
            # "offset too large, use /events/keyset for deeper pagination"
            log.warning("gamma paging ends at offset %d (%s)", offset, page.get("error"))
            return []
        resp.raise_for_status()
    except Exception as exc:
        log.warning("gamma events page tag=%s offset=%d failed: %r", tag_slug, offset, exc)
        return None
    if not isinstance(page, list):
        log.warning("gamma events page tag=%s offset=%d: unexpected body", tag_slug, offset)
        return None
    return page


async def fetch_esports_catalog(client: httpx.AsyncClient) -> list[dict]:
    """All open, non-stale esports events with their markets flattened.

    Fails soft and ATOMICALLY: any page that errors aborts the pass and
    returns ``[]`` (the tracker then holds the live asset set), because a
    half-read sweep would churn book subscriptions on a transient blip.
    """
    now = datetime.now(UTC)
    events: dict[str, dict] = {}
    for tag_slug in DISCOVERY_TAG_SLUGS:
        offset = 0
        while offset < MAX_OFFSET:
            page = await _fetch_page(client, tag_slug, offset)
            if page is None:
                log.warning("pm discovery pass abandoned (tag=%s, offset=%d)", tag_slug, offset)
                return []
            for i, ev in enumerate(page):
                key = str(ev.get("id") or ev.get("slug") or f"{tag_slug}:{offset}:{i}")
                events.setdefault(key, ev)
            if len(page) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
    out: list[dict] = []
    for ev in events.values():
        if _is_esports(ev) and _is_alive(ev, now):
            out.extend(_rows_for_event(ev))
    return out


class CatalogTracker:
    """Appends catalog rows on first sight or change; returns active asset ids."""

    def __init__(self, writer: DayWriter) -> None:
        self.writer = writer
        self._seen: dict[str, str] = {}
        self._assets: set[str] = set()

    def update(self, catalog: Iterable[dict]) -> set[str]:
        ranked = rank_for_capture(catalog)
        if not ranked:
            # An empty sweep is a discovery failure, not a delisting: hold the
            # live subscriptions instead of tearing every book down.
            return set(self._assets)
        assets: set[str] = set()
        for row in ranked:
            cid = row.get("condition_id") or row.get("question") or ""
            fingerprint = json.dumps(row, sort_keys=True, default=str)
            if self._seen.get(cid) != fingerprint:
                self._seen[cid] = fingerprint
                self.writer.append({"type": "catalog", **row})
            if len(assets) + len(row["clob_token_ids"]) <= MAX_ASSETS:
                assets.update(row["clob_token_ids"])
        self._assets = set(assets)
        return assets
