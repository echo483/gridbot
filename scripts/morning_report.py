"""Consolidated overnight morning digest.

Deterministic (no LLM, no network reads, no writes). Stdout is the message
body — keep it under ~3500 chars. Covers the overnight window (default 10h).
Run: uv run python scripts/morning_report.py [--since-hours N] [--send]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gridbot import config, notify


def window_start(now: datetime, since_hours: float) -> datetime:
    """Return the start of the reporting window."""
    return now - timedelta(hours=since_hours)


def in_window_ns(ts_ns: int, since: datetime, now: datetime) -> bool:
    """True if an epoch-ns timestamp falls inside (since, now]."""
    ts = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
    return since < ts <= now


def parse_iso_at(value: object) -> datetime | None:
    """Parse an ISO 'at' timestamp to an aware UTC datetime, else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def iter_jsonl(path: Path):
    """Yield parsed JSON lines from a file; skip bad lines; empty if missing."""
    if not path.exists():
        return
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def day_files(directory: Path, stem: str, since: datetime) -> list[Path]:
    """Existing stem-YYYYMMDD.jsonl day files at/after the window start date."""
    if not directory.is_dir():
        return []
    lo = since.astimezone(UTC).strftime("%Y%m%d")
    files = []
    for p in sorted(directory.glob(f"{stem}-*.jsonl")):
        day = p.stem.split("-")[-1]
        if len(day) == 8 and day.isdigit() and day >= lo:
            files.append(p)
    return files


def shadow_lines(since: datetime, now: datetime, data_dir: Path) -> list[str]:
    """Overnight shadow-evidence section: instance count + verdict cells."""
    lines: list[str] = []
    n = 0
    for path in sorted((data_dir / "shadow").glob("instances-*.jsonl")):
        for row in iter_jsonl(path):
            if (
                row.get("type") == "instance"
                and isinstance(row.get("ts_ns"), int)
                and in_window_ns(row["ts_ns"], since, now)
            ):
                n += 1
    lines.append(f"shadow: {n} instances overnight")
    try:
        from gridbot.instruments.verdict import compact_summary

        lines.extend(compact_summary())
    except Exception:
        lines.append("  verdict n/a")
    return lines


def capture_stats(recs: list[dict], since: datetime, now: datetime) -> dict:
    """Aggregate ss poll / live-series / error / stale counts within window.

    ``live`` groups distinct non-test series ids by title — capture polls
    multiple titles (cs2, dota), so a single count would mislabel them.
    """
    test_ids = set(config.GRID_TEST_SERIES_IDS)
    polls = errors = stale = 0
    live: dict[str, set[str]] = {}
    for row in recs:
        ts = row.get("ts_ns")
        ts_ok = isinstance(ts, int) and in_window_ns(ts, since, now)
        kind = row.get("type")
        if kind == "ss":
            if not ts_ok:
                continue
            polls += 1
            sid = str(row.get("series_id"))
            if sid in test_ids:
                continue
            state = (row.get("data") or {}).get("seriesState") or {}
            if state.get("started") and not state.get("finished"):
                live.setdefault(str(row.get("title", "unknown")), set()).add(sid)
        elif kind == "ss_error":
            if ts_ok:
                errors += 1
        elif kind == "stale_alarm":
            if ts_ok:
                stale += 1
    return {"polls": polls, "live": live, "errors": errors, "stale": stale}


def capture_lines(stats: dict | None) -> list[str]:
    """Overnight capture section from precomputed window stats."""
    if stats is None:
        return ["capture: n/a (no series_state files)"]
    live_txt = " ".join(f"{t}:{len(s)}" for t, s in sorted(stats["live"].items())) or "none"
    warn = ""
    if stats["errors"] or stats["stale"]:
        warn = f" · ⚠ {stats['errors']} errors, {stats['stale']} stale"
    return [f"capture: {stats['polls']} ss polls · live series {live_txt}{warn}"]


def corpus_lines(since: datetime, now: datetime, data_dir: Path) -> list[str]:
    """Overnight corpus section from the ingest state file.

    ``.ingested.json`` nests per-series records under a top-level "series"
    key: {"series": {sid: {"at": iso, "rows": n}}}.
    """
    lines: list[str] = []
    try:
        state = json.loads((data_dir / "corpus" / ".ingested.json").read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    series = state.get("series") if isinstance(state, dict) else None
    series = series if isinstance(series, dict) else {}
    in_win = []
    for sid, rec in series.items():
        at = parse_iso_at(rec.get("at") if isinstance(rec, dict) else None)
        if at and since < at <= now:
            rows = rec.get("rows") if isinstance(rec, dict) else None
            in_win.append(f"{sid}:{rows if rows is not None else '?'}")
    if in_win:
        lines.append("corpus: ingested overnight " + " ".join(in_win))
    else:
        lines.append("corpus: 0 series ingested overnight")
    if series:
        total_rows = sum(
            r.get("rows") or 0 for r in series.values() if isinstance(r, dict)
        )
        lines.append(f"  ingested total: {len(series)} series / {total_rows} rows")
    return lines


def _unit_result(unit: str) -> str:
    r = subprocess.run(
        ["systemctl", "--user", "show", "-p", "Result", "--value", unit],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return r.stdout.strip() or "unknown"


def ops_lines(data_dir: Path) -> list[str]:
    """Ops section: oneshot unit Results and open watchdog escalations."""
    units = ["gridbot-health.service", "gridbot-timing-chain.service"]
    parts = [f"{u.split('.')[0].removeprefix('gridbot-')}={_unit_result(u)}" for u in units]
    try:
        health = json.loads((data_dir / ".health_state.json").read_text())
        escalated = health.get("escalated") if isinstance(health, dict) else None
        if isinstance(escalated, list) and escalated:
            parts.append(f"escalated={','.join(str(s) for s in escalated)}")
    except (OSError, json.JSONDecodeError):
        pass
    return [f"ops: {' · '.join(parts)}"]


def final_verdict(stats: dict, ops: list[str]) -> str:
    """One-line verdict: quiet night unless errors/stale/failed/escalated."""
    issues: list[str] = []
    if stats.get("errors"):
        issues.append(f"{stats['errors']} ss errors")
    if stats.get("stale"):
        issues.append(f"{stats['stale']} stale alarms")
    for line in ops:
        for part in line.split(" · "):
            if "=failed" in part or "=signal" in part or part.startswith("escalated="):
                issues.append(part)
    return "verdict: quiet night" if not issues else f"verdict: {issues[0]}" + (
        f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""
    )


def build_report(since_hours: float, now: datetime, data_dir: Path) -> str:
    """Assemble the full digest body (pure except verdict/systemctl probes)."""
    since = window_start(now, since_hours)
    try:
        recs = [
            r for p in day_files(data_dir / "grid", "series_state", since) for r in iter_jsonl(p)
        ]
        stats = capture_stats(recs, since, now) if recs else None
        cap = capture_lines(stats)
    except Exception:
        stats, cap = None, ["capture: n/a"]
    try:
        ops = ops_lines(data_dir)
    except Exception:
        ops = ["ops: n/a"]
    try:
        du = shutil.disk_usage(data_dir)
        disk_line = f"disk: {du.free // 2**30}GiB free"
    except OSError:
        disk_line = "disk: n/a"
    return "\n".join(
        [
            f"🌅 gridbot morning — {now.astimezone():%Y-%m-%d %H:%M %Z} (last {since_hours:g}h)",
            *shadow_lines(since, now, data_dir),
            *cap,
            *corpus_lines(since, now, data_dir),
            *ops,
            disk_line,
            final_verdict(stats or {}, ops),
        ]
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since-hours", type=float, default=10.0, help="window length (default 10)")
    ap.add_argument("--send", action="store_true", help=f"also deliver via {notify.WEBHOOK_ENV}")
    args = ap.parse_args()
    body = build_report(args.since_hours, datetime.now(UTC), config.DATA_DIR)
    print(body)
    if args.send:
        notify.send(body, level="digest")


if __name__ == "__main__":
    main()
