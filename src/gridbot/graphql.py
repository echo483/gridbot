"""Minimal GraphQL POST helper for GRID with secret-free errors and 429 backoff."""

from __future__ import annotations

import asyncio
import logging

import httpx

from gridbot import config

log = logging.getLogger(__name__)


class GraphQLError(RuntimeError):
    """GraphQL-level errors. Message contains server 'errors' only — never headers."""


async def post(
    client: httpx.AsyncClient,
    url: str,
    query: str,
    variables: dict | None = None,
    *,
    max_tries: int = 4,
) -> dict:
    """POST a GraphQL query; return the ``data`` dict. Backs off on 429/5xx."""
    if not url.startswith("https://"):
        raise ValueError("refusing to send x-api-key over non-HTTPS transport")
    headers = {"x-api-key": config.grid_api_key(), "Content-Type": "application/json"}
    payload: dict = {"query": query, "variables": variables or {}}
    delay = 2.0
    for attempt in range(1, max_tries + 1):
        resp = await client.post(url, json=payload, headers=headers, timeout=20.0)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_tries:
            log.warning("GRID %s -> %s, backoff %.0fs (try %d)", url, resp.status_code, delay, attempt)
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if resp.status_code != 200:
            # Body may echo request details; keep the error to status + first bytes of body,
            # which never contain the key (key travels only in the request header).
            raise GraphQLError(f"HTTP {resp.status_code} from GRID: {resp.text[:300]}")
        body = resp.json()
        if body.get("errors"):
            raise GraphQLError(f"GraphQL errors: {body['errors']!r}"[:500])
        return body.get("data") or {}
    raise GraphQLError("GRID retries exhausted")
