"""Runner for the GRID ↔ Polymarket US mapping snapshot (read-only).

Bridges the repo's async GRID graphql layer to us_mapping's sync callable
and runs one snapshot: GRID CS2 series × PMUS esports events → best match
per series + the tradeable US markets → data/pmus/mappings-YYYYMMDD.jsonl.

Run: uv run python scripts/pmus_map_run.py
"""

from __future__ import annotations

import asyncio

import httpx
from polymarket_us import PolymarketUS

from gridbot.executor import us_mapping
from gridbot.graphql import post as gql_post


def _grid_post_sync(url: str, query: str, variables: dict | None = None) -> dict:
    async def _one() -> dict:
        async with httpx.AsyncClient() as hc:
            return await gql_post(hc, url, query, variables)

    return asyncio.run(_one())


def main() -> None:
    counts = us_mapping.snapshot(PolymarketUS(), _grid_post_sync)
    print(
        f"mapping snapshot: {counts['grid_series']} GRID series x "
        f"{counts['pmus_events']} PMUS events -> {counts['matched']} matched, "
        f"{counts['us_markets']} tradeable US markets"
    )


if __name__ == "__main__":
    main()
