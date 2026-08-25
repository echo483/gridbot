"""Walk captured GRID day files → corpus rows for finished CS2 series → Supabase.

Idempotent per series via ``data/corpus/.ingested.json``; the DB UPSERT on
row_key makes re-sends no-ops regardless. Rows are also appended to a local
day-partitioned jsonl (the derived-layer audit trail). Test fixtures
(config.GRID_TEST_SERIES_IDS) are never ingested.

Run: uv run python -m gridbot.corpus.ingest [--days N] [--dry-run] [--series ID]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config
from gridbot.corpus.build import build_series_rows
from gridbot.corpus.sink import upsert_rows

CORPUS_DIR = config.DATA_DIR / "corpus"
STATE_FILE = CORPUS_DIR / ".ingested.json"


def _load_series(files: list[Path]) -> dict[str, dict]:
    """series_id → {states (capture order), first_file, first_req_ns}."""
    series: dict[str, dict] = {}
    for path in files:
        with open(path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "ss" or rec.get("title") != "cs2":
                    continue
                sid = str(rec.get("series_id"))
                if sid in config.GRID_TEST_SERIES_IDS:
                    continue
                state = (rec.get("data") or {}).get("seriesState")
                if not state:
                    continue
                entry = series.setdefault(
                    sid, {"states": [], "first_file": str(path), "first_req_ns": None}
                )
                entry["states"].append(state)
                if entry["first_req_ns"] is None and rec.get("req_ns"):
                    entry["first_req_ns"] = int(rec["req_ns"])
    return series


def _provenance(entry: dict) -> dict:
    first_ns = entry["first_req_ns"]
    captured_at = (
        datetime.fromtimestamp(first_ns / 1e9, tz=UTC) if first_ns else datetime.now(UTC)
    )
    sha = hashlib.sha256()
    with open(entry["first_file"], "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            sha.update(block)
    return {
        "captured_at": captured_at.isoformat(),
        "source_file": entry["first_file"],
        "source_sha256": sha.hexdigest(),
        "ingested_at": datetime.now(UTC).isoformat(),
    }


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"series": {}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=3, help="recent day files to scan (default 3)")
    ap.add_argument("--dry-run", action="store_true", help="build + report, write nothing")
    ap.add_argument("--series", help="ingest only this series id (even if already ingested)")
    args = ap.parse_args()

    files = sorted((config.DATA_DIR / "grid").glob("series_state-*.jsonl"))[-args.days :]
    if not files:
        print("no capture files found")
        return
    series = _load_series(files)
    state = _load_state()

    finished = {
        sid: e for sid, e in series.items() if any(s.get("finished") for s in e["states"])
    }
    todo = {
        sid: e
        for sid, e in sorted(finished.items())
        if (args.series is None and sid not in state["series"]) or args.series == sid
    }
    print(
        f"scanned {len(files)} file(s): {len(series)} cs2 series, "
        f"{len(finished)} finished, {len(todo)} to ingest"
    )

    total = 0
    for sid, entry in todo.items():
        rows = build_series_rows(sid, entry["states"])
        prov = _provenance(entry)
        for row in rows:
            row.update(prov)
        print(f"  series {sid}: {len(entry['states'])} snapshots → {len(rows)} rows")
        if args.dry_run:
            continue
        written = upsert_rows(rows)
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        if rows:
            local = CORPUS_DIR / f"training_rounds-{datetime.now(UTC):%Y%m%d}.jsonl"
            with open(local, "a") as fh:
                for row in rows:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
        state["series"][sid] = {"rows": written, "at": prov["ingested_at"]}
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))
        total += written
    print("dry run — nothing written" if args.dry_run else f"upserted {total} rows to Supabase")


if __name__ == "__main__":
    main()
