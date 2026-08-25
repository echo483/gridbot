"""Pull corpus tables from Supabase to local jsonl for offline analysis.

Read-only; paged 1000/request; key from keyring. Output: data/analysis/<table>.jsonl

Run: uv run python scripts/corpus_pull.py [--table training_rounds] [--limit N]
"""

from __future__ import annotations

import argparse
import json

import httpx

from gridbot import config

OUT_DIR = config.DATA_DIR / "analysis"
PAGE = 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", default="training_rounds")
    ap.add_argument("--limit", type=int, default=0, help="0 = all rows")
    args = ap.parse_args()

    key = config.require("SUPABASE_SERVICE_ROLE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.table}.jsonl"

    n = 0
    with httpx.Client(timeout=30.0) as client, open(out, "w") as fh:
        start = 0
        while True:
            r = client.get(
                f"{config.SUPABASE_URL}/rest/v1/{args.table}",
                params={"select": "*", "order": "id"},
                headers={**headers, "Range": f"{start}-{start + PAGE - 1}"},
            )
            if r.status_code >= 300:
                raise SystemExit(f"pull failed: HTTP {r.status_code} at offset {start}")
            rows = r.json()
            fh.writelines(json.dumps(row, sort_keys=True) + "\n" for row in rows)
            n += len(rows)
            start += PAGE
            if len(rows) < PAGE or (args.limit and n >= args.limit):
                break
    print(f"{out}: {n} rows")


if __name__ == "__main__":
    main()
