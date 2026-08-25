"""One-shot GRID probe: verify the gridbot keyring key works and map title scope.

Prints a JSON summary (never any secret) and writes it to data/audit/.
Budget: a handful of Central Data calls, well under 20/min.

Run: uv run python scripts/grid_probe.py
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from gridbot import config
from gridbot.capture import grid_queries as q
from gridbot.graphql import GraphQLError, post

INTERESTING = re.compile(r"league|lol|dota|valorant|counter|cs", re.IGNORECASE)


async def main() -> None:
    summary: dict = {"probed_at": datetime.now(UTC).isoformat(timespec="seconds")}
    async with httpx.AsyncClient() as client:
        # 1) titles listing (two shape attempts)
        titles: list[dict] = []
        for query, path in ((q.TITLES_PLAIN, "plain"), (q.TITLES_CONNECTION, "connection")):
            try:
                data = await post(client, config.GRID_CENTRAL_DATA_URL, query)
                raw = data.get("titles")
                if isinstance(raw, list):
                    titles = raw
                elif isinstance(raw, dict):
                    titles = [e["node"] for e in raw.get("edges") or [] if e.get("node")]
                summary["titles_shape"] = path
                break
            except GraphQLError as exc:
                summary.setdefault("titles_errors", []).append(f"{path}: {str(exc)[:200]}")
        summary["titles"] = titles

        # 2) per-title series counts, next 3 days (auth check + scope map)
        now = datetime.now(UTC)
        gte = now.isoformat(timespec="seconds")
        lte = (now + timedelta(days=3)).isoformat(timespec="seconds")
        candidates = {"cs2": config.TITLE_ID_CS2}
        for t in titles:
            name = f"{t.get('name', '')} {t.get('nameShortened', '')}"
            if INTERESTING.search(name):
                candidates[t.get("nameShortened") or t.get("name") or str(t.get("id"))] = str(
                    t.get("id")
                )
        counts: dict[str, dict] = {}
        for key, title_id in candidates.items():
            try:
                data = await post(
                    client,
                    config.GRID_CENTRAL_DATA_URL,
                    q.DISCOVER_MINIMAL,
                    {"titleId": title_id, "gte": gte, "lte": lte, "after": None},
                )
                conn = data.get("allSeries") or {}
                counts[key] = {
                    "title_id": title_id,
                    "total_next_3d": conn.get("totalCount"),
                    "ok": True,
                }
            except GraphQLError as exc:
                counts[key] = {"title_id": title_id, "ok": False, "error": str(exc)[:200]}
        summary["series_counts"] = counts

    out_dir = Path(config.DATA_DIR) / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"grid_probe-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
