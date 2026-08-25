"""Offsite raw-capture backup: finished day files → Supabase Storage, gzipped.

The corpus is the moat; raw day files are its ground truth and live on one
machine. This uploads every COMPLETED day's jsonl (never today's growing
files) to the private ``gridbot-raw`` bucket, once, tracked in
``data/.backup_state.json``. Idempotent; secret-free errors.

Run: uv run python scripts/backup_raw.py [--dry-run]
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gridbot import config

BUCKET = "gridbot-raw"
STATE_FILE = config.DATA_DIR / ".backup_state.json"
STREAMS = ("grid", "pm", "shadow", "corpus")


def _headers(key: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _ensure_bucket(client: httpx.Client, key: str) -> None:
    r = client.post(
        f"{config.SUPABASE_URL}/storage/v1/bucket",
        headers=_headers(key),
        json={"id": BUCKET, "name": BUCKET, "public": False},
    )
    if r.status_code not in (200, 201, 400, 409):  # 400/409 = already exists
        raise SystemExit(f"bucket ensure failed: HTTP {r.status_code}")


def _finished_files() -> list[Path]:
    today = f"{datetime.now(UTC):%Y%m%d}"
    files: list[Path] = []
    for stream in STREAMS:
        directory = config.DATA_DIR / stream
        if not directory.exists():
            continue
        files.extend(
            p for p in sorted(directory.glob("*.jsonl")) if not p.stem.endswith(today)
        )
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state = {"uploaded": []}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    uploaded = set(state["uploaded"])

    todo = [p for p in _finished_files() if str(p.relative_to(config.DATA_DIR)) not in uploaded]
    print(f"{len(todo)} finished day file(s) to back up")
    if args.dry_run or not todo:
        for p in todo:
            print(f"  would upload {p}")
        return

    key = config.require("SUPABASE_SERVICE_ROLE_KEY")
    with httpx.Client(timeout=120.0) as client:
        _ensure_bucket(client, key)
        for path in todo:
            rel = str(path.relative_to(config.DATA_DIR))
            blob = gzip.compress(path.read_bytes(), compresslevel=6)
            r = client.post(
                f"{config.SUPABASE_URL}/storage/v1/object/{BUCKET}/{rel}.gz",
                headers={**_headers(key), "Content-Type": "application/gzip",
                         "x-upsert": "true"},
                content=blob,
            )
            if r.status_code >= 300:
                print(f"  FAILED {rel}: HTTP {r.status_code}")
                continue
            uploaded.add(rel)
            print(f"  uploaded {rel}.gz ({len(blob) // 1024} KiB)")
    STATE_FILE.write_text(json.dumps({"uploaded": sorted(uploaded)}, indent=1))


if __name__ == "__main__":
    main()
