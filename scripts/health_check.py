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
import sys
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

        # Read through config._secret, NEVER a raw keyring.get_password: a
        # locked Secret Service makes raw D-Bus reads BLOCK FOREVER (2026-08-26
        # and again 2026-08-31), and a hang is not an exception — the `except`
        # below would not catch it, so the 5-minute watchdog would wedge
        # permanently at exactly the moment it is needed. config._secret runs
        # the read in a 5s reaper thread with a bounded last-good cache.
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


def _watch_keyring() -> int | None:
    """Run the keyring lock watchdog (scripts/keyring_watch.py --notify).

    Best-effort: any nonzero exit or crash must never break the 5-minute
    timer. Returns the watchdog's exit code (2 = locked + alert delivered,
    3 = locked-path with nothing delivered, 0 = unlocked/ok), or None when
    the watchdog itself could not be run.
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
        return r.returncode
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


KEYRING_PROBLEM_PREFIX = "keyring locked/unreachable"


def _keyring_summary_decision(
    keyring_rc: int | None, problems: list[str]
) -> tuple[list[str], list[str], bool]:
    """Pure decision helper (FIX-1/FIX-4): fold the keyring result into the
    problem list and decide the red-summary outcome.

    Returns (problems, summary_problems, suppress_summary):
      - rc 2: a keyring problem line is recorded, but the keyring's own alert
        already reached Telegram, so it is excluded from the summary text and
        the summary is suppressed ONLY when the keyring is the sole problem
        this cycle (FIX-4: unrelated problems still fire the summary).
      - rc 3: keyring problem present with NOTHING delivered — the line is
        recorded AND included in the summary; never suppressed.
      - rc 0 / None: no keyring line, summary unchanged.
    """
    keyring_line = None
    if keyring_rc == 2:
        keyring_line = f"{KEYRING_PROBLEM_PREFIX} (watchdog alert delivered)"
    elif keyring_rc == 3:
        keyring_line = f"{KEYRING_PROBLEM_PREFIX} (watchdog sent NOTHING this cycle)"
    all_problems = list(problems) + ([keyring_line] if keyring_line else [])
    summary_problems = all_problems if keyring_rc != 2 else list(problems)
    suppress = keyring_rc == 2 and not problems
    return all_problems, summary_problems, suppress


STALE_RESTART_COOLDOWN_S = 3600.0


def _stale_grid_restart_wanted(grid_age: float, capture_active: bool, graphical: bool,
                               last_restart: float, now: float,
                               fresh_s: float = GRID_FRESH_S,
                               cooldown_s: float = STALE_RESTART_COOLDOWN_S) -> bool:
    """Pure decision: restart an ACTIVE capture unit whose GRID tape went stale.

    Never outside the graphical session (a keyring-anchored unit would come
    back locked and silently dark), never more than once per cooldown, and
    only while the unit is active — an inactive unit is the DOWN branch's job.
    """
    return (
        grid_age > fresh_s
        and capture_active
        and graphical
        and now - last_restart > cooldown_s
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
        # 2026-09-03: capture stayed ACTIVE (heartbeats every minute) while
        # every GRID poll failed for ~6h — its in-process keyring read had
        # wedged — and this check only alerted. A fresh process read the key
        # in 0.1s. So: stale GRID data with the unit active is a restart
        # case, once per hour, inside the graphical session only (a locked
        # keyring makes the restarted unit fail fast -> the DOWN branch above
        # escalates on the next pass).
        if _stale_grid_restart_wanted(
            grid_age, _is_active("gridbot-capture.service"), graphical,
            healed_at.get("gridbot-capture.service:stale", 0), now,
        ):
            if _restart("gridbot-capture.service"):
                _send(f"🩹 GRID data stale {int(grid_age / 60)}m with capture ACTIVE "
                      "(wedged keyring read?) — capture restarted")
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
    # Keyring lock watchdog runs after the self-heal block, before the
    # summary: its transition alerts ride Telegram. rc 2 = its own alert was
    # delivered (suppress the duplicate summary ONLY if it is the sole
    # problem); rc 3 = locked-path with nothing delivered — that must stay
    # loud via the generic red summary (FIX-1/FIX-4).
    keyring_rc = _watch_keyring()
    problems, summary_problems, suppress_summary = _keyring_summary_decision(
        keyring_rc, problems
    )
    ok = not problems
    stamp = datetime.now(UTC).strftime("%H:%M UTC")
    if not ok and prev.get("ok", True) and not suppress_summary:
        _send("🔴 gridbot health: " + "; ".join(summary_problems) + f" ({stamp})")
    elif ok and not prev.get("ok", True):
        _send(f"✅ gridbot health recovered ({stamp})")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"ok": ok, "problems": problems, "checked": stamp, **flags})
    )


if __name__ == "__main__":
    main()
