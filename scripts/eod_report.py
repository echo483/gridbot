"""EOD digest for Telegram: capture health, corpus growth, market activity.

Deterministic (no LLM). Stdout is the message body — keep it under ~3500 chars.
Run: uv run python scripts/eod_report.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config


def _service_state() -> str:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "gridbot-capture.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() or "unknown"


def _day_stem(directory: Path, stem: str) -> Path:
    return directory / f"{stem}-{datetime.now(UTC).strftime('%Y%m%d')}.jsonl"


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _grid_stats() -> list[str]:
    path = _day_stem(config.DATA_DIR / "grid", "series_state")
    polls = 0
    errors = 0
    stale = 0
    series_by_title: dict[str, set] = {}
    live_seen: set[str] = set()
    for row in _iter_jsonl(path):
        kind = row.get("type")
        if kind == "ss":
            polls += 1
            series_by_title.setdefault(row.get("title", "?"), set()).add(row.get("series_id"))
            state = (row.get("data") or {}).get("seriesState") or {}
            if state.get("started") and not state.get("finished"):
                live_seen.add(row.get("series_id"))
        elif kind == "ss_error":
            errors += 1
        elif kind == "stale_alarm":
            stale += 1
    per_title = ", ".join(f"{t}:{len(s)}" for t, s in sorted(series_by_title.items())) or "none"
    lines = [f"GRID: {polls} polls · series {per_title} · {len(live_seen)} went live"]
    if errors or stale:
        lines.append(f"  ⚠ {errors} poll errors, {stale} stale alarms")
    return lines


def _pm_stats() -> list[str]:
    books = _day_stem(config.DATA_DIR / "pm", "books")
    n_ws = n_snap = 0
    for row in _iter_jsonl(books):
        t = row.get("type")
        if t == "ws":
            n_ws += 1
        elif t in ("rest_books", "rest_book"):
            n_snap += 1
    active = Counter()
    for row in _iter_jsonl(_day_stem(config.DATA_DIR / "pm", "catalog")):
        v = float(row.get("volume24hr") or 0)
        if v > 0:
            active[row.get("question") or row.get("event_title") or "?"] = int(v)
    lines = [f"PM books: {n_ws} ws msgs, {n_snap} snapshots"]
    for q, v in active.most_common(3):
        lines.append(f"  · ${v:,} — {q[:60]}")
    return lines


def _corpus_counts() -> str:
    try:
        import httpx
        import keyring

        key = keyring.get_password(config.KEYRING_SERVICE, "SUPABASE_SERVICE_ROLE_KEY")
        if not key:
            return "corpus: no key"
        h = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact", "Range": "0-0"}
        out = []
        for t in ("training_rounds", "events"):
            r = httpx.get(
                f"{config.SUPABASE_URL}/rest/v1/{t}", params={"select": "id"}, headers=h, timeout=10
            )
            total = (r.headers.get("content-range") or "/?").split("/")[-1]
            out.append(f"{t}={total}")
        return "corpus: " + " ".join(out)
    except Exception as exc:  # noqa: BLE001 — report must never crash on a probe
        return f"corpus: probe failed ({str(exc)[:60]})"


def main() -> None:
    state = _service_state()
    icon = "✅" if state == "active" else "🔴"
    du = shutil.disk_usage(config.DATA_DIR)
    lines = [
        f"{icon} gridbot EOD — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC",
        f"capture: {state} · disk {du.free // 2**30}GiB free",
        *_grid_stats(),
        *_pm_stats(),
        _corpus_counts(),
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
