"""Polymarket esports market discovery via the public gamma API (no auth).

Emits catalog rows (event/market/tokens) and returns the active asset-id set for
the book recorder. Permissive by design: recording a thin book is cheap; missing
a market is not recoverable.
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
    r"major|esports",
    re.IGNORECASE,
)
MAX_ASSETS = 400


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


async def fetch_esports_catalog(client: httpx.AsyncClient) -> list[dict]:
    """All open, non-stale esports events with their markets flattened."""
    now = datetime.now(UTC)
    out: list[dict] = []
    offset = 0
    while offset < 2000:
        resp = await client.get(
            f"{config.PM_GAMMA_URL}/events",
            params={"closed": "false", "limit": 100, "offset": offset},
            timeout=30.0,
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        for ev in page:
            if not (_is_esports(ev) and _is_alive(ev, now)):
                continue
            for m in ev.get("markets") or []:
                if m.get("closed"):
                    continue
                tokens = [str(t) for t in _as_list(m.get("clobTokenIds"))]
                if not tokens:
                    continue
                out.append(
                    {
                        "event_title": ev.get("title"),
                        "event_slug": ev.get("slug"),
                        "end_date": ev.get("endDate"),
                        "tags": sorted({t.get("slug", "") for t in ev.get("tags") or []}),
                        "question": m.get("question"),
                        "condition_id": m.get("conditionId"),
                        "outcomes": _as_list(m.get("outcomes")),
                        "clob_token_ids": tokens,
                        "liquidity": m.get("liquidityNum") or m.get("liquidity"),
                        "volume24hr": m.get("volume24hr"),
                    }
                )
        offset += 100
    return out


class CatalogTracker:
    """Appends catalog rows on first sight or change; returns active asset ids."""

    def __init__(self, writer: DayWriter) -> None:
        self.writer = writer
        self._seen: dict[str, str] = {}

    def update(self, catalog: Iterable[dict]) -> set[str]:
        assets: set[str] = set()
        ranked = sorted(
            catalog,
            key=lambda r: (float(r.get("volume24hr") or 0), float(r.get("liquidity") or 0)),
            reverse=True,
        )
        for row in ranked:
            cid = row.get("condition_id") or row.get("question") or ""
            fingerprint = json.dumps(row, sort_keys=True, default=str)
            if self._seen.get(cid) != fingerprint:
                self._seen[cid] = fingerprint
                self.writer.append({"type": "catalog", **row})
            if len(assets) + len(row["clob_token_ids"]) <= MAX_ASSETS:
                assets.update(row["clob_token_ids"])
        return assets
