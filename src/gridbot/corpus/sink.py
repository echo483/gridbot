"""Chunked PostgREST UPSERT of corpus rows into Supabase training_rounds.

Wire parity with cs2agent's sink: ``on_conflict=row_key`` + merge-duplicates
(INSERT ... ON CONFLICT DO UPDATE), 500-row chunks. Every row passes a
redaction round-trip before leaving the process; errors carry counts and
status codes only — never response bodies.
"""

from __future__ import annotations

import json

import httpx

from gridbot import config
from gridbot.redaction import redact_text

UPSERT_CHUNK_SIZE = 500

# Exactly the 12 DB columns — no id, no bin (bin lives in the row_key suffix).
DB_COLUMNS = (
    "series_id",
    "map_number",
    "round_number",
    "features",
    "label",
    "ct_team",
    "t_team",
    "captured_at",
    "source_file",
    "source_sha256",
    "ingested_at",
    "row_key",
)


class CorpusSinkError(RuntimeError):
    """Raised with a secret-free message on any upsert failure."""


def _redact_row(row: dict) -> dict:
    payload = {k: row.get(k) for k in DB_COLUMNS}
    return json.loads(redact_text(json.dumps(payload, default=str, sort_keys=True)))


def upsert_rows(rows: list[dict]) -> int:
    """UPSERT rows into training_rounds; returns rows written."""
    if not rows:
        return 0
    key = config.require("SUPABASE_SERVICE_ROLE_KEY")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    written = 0
    with httpx.Client(timeout=30.0) as client:
        for start in range(0, len(rows), UPSERT_CHUNK_SIZE):
            chunk = [_redact_row(r) for r in rows[start : start + UPSERT_CHUNK_SIZE]]
            resp = client.post(
                f"{config.SUPABASE_URL}/rest/v1/training_rounds",
                params={"on_conflict": "row_key"},
                headers=headers,
                json=chunk,
            )
            if resp.status_code >= 300:
                raise CorpusSinkError(
                    f"upsert chunk {start // UPSERT_CHUNK_SIZE} failed: "
                    f"HTTP {resp.status_code} ({len(chunk)} rows)"
                )
            written += len(chunk)
    return written
