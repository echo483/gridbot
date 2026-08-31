"""Self-healing watchdog: verify capture health, restart once on failure,
alert Telegram on STATE TRANSITIONS only (no spam).

Checks: service active; data freshness (pm + grid); disk headroom.
State persists in data/.health_state.json. Run every 5 min by
gridbot-health.timer. Deterministic, no LLM, no secrets.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


SERVICES = ("gridbot-capture.service", "gridbot-shadow.service")


def _is_active(service: str) -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", service],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() == "active"


def _restart(service: str) -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "restart", service],
        capture_output=True,
        text=True,
        check=False,
    )
    time.sleep(8)
    return r.returncode == 0 and _is_active(service)


def _escalate(service: str, detail: str) -> None:
    """POST the hermes webhook (HMAC-V2) so a triage agent investigates.

    Fires only after deterministic self-heal has already failed; the agent
    diagnoses and may restart gridbot units, nothing else.
    """
    try:
        import hashlib
        import hmac

        import httpx

        from gridbot import config as gconfig

        # Read through config._secret, NEVER a raw keyring.get_password: a
        # locked Secret Service makes raw D-Bus reads BLOCK FOREVER (2026-08-26
        # and again 2026-08-31), and a hang is not an exception — the `except`
        # below would not catch it, so the 5-minute watchdog would wedge
        # permanently at exactly the moment it is needed. config._secret runs
        # the read in a 5s reaper thread with a bounded last-good cache.
        secret = gconfig._secret("HERMES_WEBHOOK_GRIDBOT_HEALTH_SECRET")
        if not secret:
            return
        ts = str(int(time.time()))
        body = json.dumps({"service": service, "detail": detail})
        sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        httpx.post(
            "http://127.0.0.1:8644/webhooks/gridbot-health",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Timestamp": ts,
                "X-Webhook-Signature-V2": sig,
            },
            timeout=10,
        )
    except Exception:  # noqa: BLE001, S110 — best-effort by design: never break the watchdog
        pass


def _send(msg: str) -> None:
    subprocess.run(
        ["/home/ops/.local/bin/hermes", "send", "-t", "telegram", msg],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _watch_keyring() -> bool:
    """Run the keyring lock watchdog (scripts/keyring_watch.py --notify).

    Best-effort: any nonzero exit or crash must never break the 5-minute
    timer. Returns True when the watchdog sent its own Telegram alert this
    cycle (exit 2 = locked + alerted), so the generic red summary below is
    suppressed in the same cycle — one ping for the keyring, not two.
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "keyring_watch.py"
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--notify"],
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
        return r.returncode == 2
    except subprocess.TimeoutExpired:
        return False
    except Exception:  # noqa: BLE001 — best-effort by design
        return False


def _problems(prev: dict) -> tuple[list[str], dict]:
    problems: list[str] = []
    now = time.time()
    graphical = _is_active("graphical-session.target")
    escalated: list[str] = []
    healed_at: dict[str, float] = {
        s: t for s, t in (prev.get("healed_at") or {}).items() if now - t < 24 * 3600
    }
    prev_escalated = set(prev.get("escalated") or [])
    for service in SERVICES:
        if _is_active(service):
            continue
        if not graphical:
            # Keyring-anchored units must NEVER be force-started outside the
            # graphical session: they would run with a locked keyring and go
            # silently GRID-dark (review F4). Alert; do not start.
            problems.append(f"{service} down (no graphical session — log in at console)")
            continue
        if service in prev_escalated:
            # A triage agent owns this outage; don't race it with restarts
            # or re-escalate every 5 minutes (review F5).
            problems.append(f"{service} DOWN (escalated; triage in progress)")
            escalated.append(service)
            continue
        if _restart(service):
            if now - healed_at.get(service, 0) > 3600:
                _send(f"🩹 {service} was down — auto-restarted OK")
            healed_at[service] = now
        else:
            problems.append(f"{service} DOWN (auto-restart failed)")
            escalated.append(service)
            _escalate(service, "watchdog self-heal failed; unit not active after restart")
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
    return problems, {"escalated": escalated, "healed_at": healed_at}


def main() -> None:
    prev = {"ok": True}
    if STATE_FILE.exists():
        try:
            prev = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    problems, flags = _problems(prev)
    # Keyring lock watchdog runs after the self-heal block, before the
    # summary: its transition alerts ride Telegram; when it alerted this
    # cycle (exit 2) the generic red summary is suppressed to keep the
    # phone quiet (keyring_watch's own message carries the recovery line).
    keyring_alerted = _watch_keyring()
    ok = not problems
    stamp = datetime.now(UTC).strftime("%H:%M UTC")
    if not ok and prev.get("ok", True) and not keyring_alerted:
        _send("🔴 gridbot health: " + "; ".join(problems) + f" ({stamp})")
    elif ok and not prev.get("ok", True):
        _send(f"✅ gridbot health recovered ({stamp})")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"ok": ok, "problems": problems, "checked": stamp, **flags})
    )


if __name__ == "__main__":
    main()
