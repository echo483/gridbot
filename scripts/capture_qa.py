"""Deterministic capture-QA fingerprint: stable stdout, changes only on state flips.

Timestamp-free by design — hermes' monitor-script cron hashes the exact bytes
and wakes an agent only when the fingerprint changes. Freshness is bucketed
(OK/STALE), error counts are today's totals, disk is rounded — so steady
healthy growth produces identical output every run.

Run: uv run python scripts/capture_qa.py
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config

FRESH_S = {"grid": 25 * 60, "pm": 15 * 60, "shadow": 24 * 3600}


def _newest_mtime(directory: Path) -> float:
    newest = 0.0
    if directory.exists():
        for p in directory.glob("*.jsonl"):
            newest = max(newest, p.stat().st_mtime)
    return newest


def _today(directory: Path, stem: str) -> Path:
    return directory / f"{stem}-{datetime.now(UTC):%Y%m%d}.jsonl"


def _count_types(path: Path, wanted: tuple[str, ...]) -> dict[str, int]:
    out = dict.fromkeys(wanted, 0)
    if not path.exists():
        return out
    with open(path) as fh:
        for line in fh:
            try:
                kind = json.loads(line).get("type")
            except json.JSONDecodeError:
                continue
            if kind in out:
                out[kind] += 1
    return out


def main() -> None:
    now = time.time()
    lines: list[str] = []
    for key, sub in (("grid", "grid"), ("pm", "pm"), ("shadow", "shadow")):
        age = now - _newest_mtime(config.DATA_DIR / sub)
        lines.append(f"{key}: {'OK' if age < FRESH_S[key] else 'STALE'}")
    grid_today = _count_types(
        _today(config.DATA_DIR / "grid", "series_state"), ("ss_error", "stale_alarm")
    )
    lines.append(f"grid_errors_today: {grid_today['ss_error']}")
    lines.append(f"grid_stale_alarms_today: {grid_today['stale_alarm']}")
    pm_today = _count_types(_today(config.DATA_DIR / "pm", "books"), ("ws_error",))
    lines.append(f"pm_ws_errors_today: {pm_today['ws_error']}")
    free_gib = shutil.disk_usage(config.DATA_DIR).free // 2**30
    lines.append(f"disk_bucket_gib: {free_gib // 5 * 5}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
