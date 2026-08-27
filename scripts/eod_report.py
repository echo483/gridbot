"""EOD digest for Telegram: capture health, corpus growth, market activity.

Deterministic (no LLM). Stdout is the message body — keep it under ~3500 chars.
Run: uv run python scripts/eod_report.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gridbot import config

# The digest runs 05:00Z — hours past UTC midnight — for the prior PT
# evening, so a single today-file read reports only the post-midnight tail
# and silently drops the 17:00-24:00 PT trading window.
WINDOW_HOURS = 24


def _service_state() -> str:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "gridbot-capture.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() or "unknown"


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _window_rows(directory: Path, stem: str) -> Iterator[dict]:
    """Yield rows from the last 24h, reading every day file the window spans.

    UTC-midnight day partitioning means one window can cross two files; file
    selection is by date (cheap) and each row is then filtered by ts_ns.
    """
    now = datetime.now(UTC)
    since = now - timedelta(hours=WINDOW_HOURS)
    lo = since.strftime("%Y%m%d")
    for path in sorted(directory.glob(f"{stem}-*.jsonl")):
        day = path.stem.split("-")[-1]
        if len(day) != 8 or not day.isdigit() or day < lo:
            continue
        for row in _iter_jsonl(path):
            ts = row.get("ts_ns") if isinstance(row, dict) else None
            # Float-tolerant: a float ts_ns still filters fine, and dropping
            # untimestamped rows would silently under-count vs the old
            # single-file read.
            if isinstance(ts, (int, float)) and not isinstance(ts, bool) \
                    and since < datetime.fromtimestamp(ts / 1e9, tz=UTC) <= now:
                yield row


def _grid_stats() -> list[str]:
    polls = 0
    errors = 0
    stale = 0
    series_by_title: dict[str, set] = {}
    live_seen: set[str] = set()
    for row in _window_rows(config.DATA_DIR / "grid", "series_state"):
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
    n_ws = n_snap = 0
    for row in _window_rows(config.DATA_DIR / "pm", "books"):
        t = row.get("type")
        if t == "ws":
            n_ws += 1
        elif t in ("rest_books", "rest_book"):
            n_snap += 1
    active = Counter()
    for row in _window_rows(config.DATA_DIR / "pm", "catalog"):
        v = float(row.get("volume24hr") or 0)
        if v > 0:
            active[row.get("question") or row.get("event_title") or "?"] = int(v)
    lines = [f"PM books: {n_ws} ws msgs, {n_snap} snapshots"]
    for q, v in active.most_common(3):
        lines.append(f"  · ${v:,} — {q[:60]}")
    return lines


def _shadow_stats() -> list[str]:
    counts = Counter()
    for row in _window_rows(config.DATA_DIR / "shadow", "instances"):
        if row.get("type") == "instance":
            counts[row.get("hypothesis_id") or "?"] += 1
    lines = ["shadow today: " + (
        " ".join(f"{h}={n}" for h, n in sorted(counts.items())) if counts else "0 instances"
    )]
    try:
        from gridbot.instruments.verdict import compact_summary

        lines.extend(compact_summary())
    except Exception as exc:  # noqa: BLE001 — report must never crash on a probe
        lines.append(f"  verdict probe failed ({str(exc)[:60]})")
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
        *_shadow_stats(),
        _corpus_counts(),
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
