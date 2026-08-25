"""Self-healing watchdog: verify capture health, restart once on failure,
alert Telegram on STATE TRANSITIONS only (no spam).

Checks: service active; data freshness (pm + grid); disk headroom.
State persists in data/.health_state.json. Run every 5 min by
gridbot-health.timer. Deterministic, no LLM, no secrets.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config

STATE_FILE = config.DATA_DIR / ".health_state.json"
PM_FRESH_S = 15 * 60      # REST snapshots every 10 min guarantee writes
GRID_FRESH_S = 25 * 60    # discovery every 15 min guarantees writes
MIN_FREE_GIB = 5


def _newest_mtime(directory: Path) -> float:
    newest = 0.0
    if directory.exists():
        for p in directory.glob("*.jsonl"):
            newest = max(newest, p.stat().st_mtime)
    return newest


def _is_active() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "gridbot-capture.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() == "active"


def _restart() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "restart", "gridbot-capture.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    time.sleep(8)
    return r.returncode == 0 and _is_active()


def _send(msg: str) -> None:
    subprocess.run(
        ["/home/ops/.local/bin/hermes", "send", "-t", "telegram", msg],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _problems() -> list[str]:
    problems: list[str] = []
    now = time.time()
    if not _is_active():
        healed = _restart()
        if healed:
            _send("🩹 gridbot capture was down — auto-restarted OK")
        else:
            problems.append("capture service DOWN (auto-restart failed)")
    pm_age = now - _newest_mtime(config.DATA_DIR / "pm")
    if pm_age > PM_FRESH_S:
        problems.append(f"PM data stale ({int(pm_age / 60)}m)")
    grid_age = now - _newest_mtime(config.DATA_DIR / "grid")
    if grid_age > GRID_FRESH_S:
        problems.append(f"GRID data stale ({int(grid_age / 60)}m)")
    import shutil

    free_gib = shutil.disk_usage(config.DATA_DIR).free // 2**30
    if free_gib < MIN_FREE_GIB:
        problems.append(f"disk low: {free_gib}GiB free")
    return problems


def main() -> None:
    problems = _problems()
    prev = {"ok": True}
    if STATE_FILE.exists():
        try:
            prev = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    ok = not problems
    stamp = datetime.now(UTC).strftime("%H:%M UTC")
    if not ok and prev.get("ok", True):
        _send("🔴 gridbot health: " + "; ".join(problems) + f" ({stamp})")
    elif ok and not prev.get("ok", True):
        _send(f"✅ gridbot health recovered ({stamp})")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"ok": ok, "problems": problems, "checked": stamp}))


if __name__ == "__main__":
    main()
