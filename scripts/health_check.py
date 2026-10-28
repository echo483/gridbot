"""Self-healing watchdog: verify capture health, restart once on failure, and
alert on STATE TRANSITIONS only (no spam).

Checks: service active; data freshness (pm + grid); disk headroom.
State persists in data/.health_state.json. Run every 5 min by
gridbot-health.timer. Deterministic, no model calls, no secrets in code.
Alerts go through `gridbot.notify`; escalation is a webhook gated on
GRIDBOT_ESCALATE_URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config, notify

STATE_FILE = config.DATA_DIR / ".health_state.json"
PM_FRESH_S = 15 * 60      # REST snapshots every 10 min guarantee writes
GRID_FRESH_S = 25 * 60    # discovery every 15 min guarantees writes
MIN_FREE_GIB = 5
ESCALATE_URL_ENV = "GRIDBOT_ESCALATE_URL"


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
    """POST the escalation webhook (HMAC-V2 signed) after self-heal has failed.

    Transport is configuration: set GRIDBOT_ESCALATE_URL to an https endpoint
    to enable it. Unset means a no-op, so the watchdog never depends on an
    endpoint that may not exist. Whatever consumes the webhook lives outside
    this repo.
    """
    url = os.environ.get(ESCALATE_URL_ENV, "").strip()
    if not url.startswith("https://"):
        return
    try:
        import hashlib
        import hmac

        import httpx

        from gridbot import config as gconfig

        # The one secrets source; absent or unsafe file -> no escalation.
        secret = gconfig._secret("GRIDBOT_ESCALATE_SECRET")
        if not secret:
            return
        ts = str(int(time.time()))
        body = json.dumps({"service": service, "detail": detail})
        sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        httpx.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Timestamp": ts,
                "X-Webhook-Signature-V2": sig,
            },
            timeout=10,
        )
    except Exception:
        pass


def _send(msg: str) -> None:
    """Best-effort alert on a state transition. Never raises."""
    notify.send(msg, level="alert")


STALE_RESTART_COOLDOWN_S = 3600.0


def _stale_grid_restart_wanted(grid_age: float, capture_active: bool,
                               last_restart: float, now: float,
                               fresh_s: float = GRID_FRESH_S,
                               cooldown_s: float = STALE_RESTART_COOLDOWN_S) -> bool:
    """Pure decision: restart an ACTIVE capture unit whose GRID tape went stale.

    Never more than once per cooldown, and only while the unit is active — an
    inactive unit is the DOWN branch's job.
    """
    return (
        grid_age > fresh_s
        and capture_active
        and now - last_restart > cooldown_s
    )


def _problems(prev: dict) -> tuple[list[str], dict]:
    problems: list[str] = []
    now = time.time()
    escalated: list[str] = []
    healed_at: dict[str, float] = {
        s: t for s, t in (prev.get("healed_at") or {}).items() if now - t < 24 * 3600
    }
    prev_escalated = set(prev.get("escalated") or [])
    for service in SERVICES:
        if _is_active(service):
            continue
        if service in prev_escalated:
            # Already escalated to the operator; don't race them with
            # restarts or re-escalate every 5 minutes.
            problems.append(f"{service} DOWN (escalated; awaiting operator)")
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
        # An ACTIVE unit whose tape has gone stale is wedged, not healthy
        # (2026-09-03: heartbeats for ~6h while every GRID poll failed). A
        # restart is the fix, at most once per cooldown; a unit that then
        # fails to come up is the DOWN branch's job on the next pass.
        if _stale_grid_restart_wanted(
            grid_age, _is_active("gridbot-capture.service"),
            healed_at.get("gridbot-capture.service:stale", 0), now,
        ):
            if _restart("gridbot-capture.service"):
                _send(f"🩹 GRID data stale {int(grid_age / 60)}m with capture ACTIVE "
                      "— capture restarted")
            else:
                problems.append("capture restart after stale GRID data FAILED")
                _escalate("gridbot-capture.service",
                          "stale GRID data; restart did not bring the unit up")
            healed_at["gridbot-capture.service:stale"] = now
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
