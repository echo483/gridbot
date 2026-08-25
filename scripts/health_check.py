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
        import keyring

        from gridbot import config as gconfig

        secret = keyring.get_password(
            gconfig.KEYRING_SERVICE, "HERMES_WEBHOOK_GRIDBOT_HEALTH_SECRET"
        )
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
    ok = not problems
    stamp = datetime.now(UTC).strftime("%H:%M UTC")
    if not ok and prev.get("ok", True):
        _send("🔴 gridbot health: " + "; ".join(problems) + f" ({stamp})")
    elif ok and not prev.get("ok", True):
        _send(f"✅ gridbot health recovered ({stamp})")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"ok": ok, "problems": problems, "checked": stamp, **flags})
    )


if __name__ == "__main__":
    main()
