"""Keyring lock-state watchdog: probe the Secret Service collection lock
state (boolean only, zero secret material) and alert Telegram on transitions.

Probe: the ``org.freedesktop.Secret.Collection`` ``Locked`` property over
D-Bus. The collection path comes from ``secretstorage.get_default_collection``
on a jeepney session-bus connection; ``Locked`` is then read via
``org.freedesktop.DBus.Properties.Get`` on that object path (jeepney, NOT
dbus/gi). No passphrase is needed to read it and no credential is ever read,
printed, stored, or alerted.

Fail loud: a D-Bus timeout (>5s reaper bound) or error is treated as LOCKED
and follows the alert path — a wedged bus (the 08-26 failure mode) must
never be silent.

Exit codes (contract with scripts/health_check.py):
  0 = unlocked/ok (or locked but no alert due this cycle; or recovered)
  2 = locked-path AND an alert was sent this cycle (its own Telegram send)
  3 = probe error, no alert sent this cycle

State persists in data/ops/keyring_watch_state.json so the 15-min re-alert
cadence survives the health timer's separate process invocations.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import cast

PROBE_TIMEOUT_S = 5.0  # hard bound via reaper thread (config._secret pattern)
RE_ALERT_S = 900  # 15-min re-alert cadence while locked

STATE_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "ops" / "keyring_watch_state.json"
)

LOCKED = "locked"
OK = "ok"

SECRETS_BUS_NAME = "org.freedesktop.secrets"
COLLECTION_IFACE = "org.freedesktop.Secret.Collection"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# Alert text is redaction-checked by tests (frozen acceptance test, spec §4):
# zero occurrences of sk-, xapi, token, password in these strings, hence the
# recovery command is phrased "(passphrase on stdin)".
RECOVERY = (
    "gnome-keyring-daemon --replace --unlock (passphrase on stdin); "
    "capture self-heals"
)
LOCK_ALERT = (
    "LOCKED gridbot keyring - Secret Service collection locked; GRID capture "
    f"will fail closed when the 600s cache expires. Recovery: {RECOVERY}. "
    "Re-alerting every 15 min while locked."
)
ERROR_ALERT = (
    "LOCKED gridbot keyring PROBE - Secret Service unresponsive; D-Bus probe "
    f"timed out or failed, treating as locked (fail loud). Recovery: {RECOVERY}."
)
RECOVERED_ALERT = (
    "Recovered gridbot keyring - collection unlocked, secret reads healthy again."
)


class ProbeError(RuntimeError):
    """D-Bus probe failed or exceeded the hard time bound (fail loud)."""


def _probe_locked_once() -> bool:
    """One Secret Service read; raises ProbeError on any failure.

    Lazy imports keep startup fast and keep the hermetic test suite off the
    real D-Bus entirely.
    """
    import secretstorage
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    bus = open_dbus_connection()  # jeepney default: the session bus
    try:
        collection = secretstorage.get_default_collection(bus)
        address = DBusAddress(
            collection.collection_path,
            bus_name=SECRETS_BUS_NAME,
            interface=PROPERTIES_IFACE,
        )
        reply = bus.send_and_get_reply(
            new_method_call(address, "Get", "ss", (COLLECTION_IFACE, "Locked"))
        )
        if reply.header.message_type == MessageType.error:
            raise ProbeError(
                "D-Bus error reply: " + str(getattr(reply, "error_name", "unknown"))
            )
        (_, value), = reply.body
        return bool(value)
    finally:
        bus.close()


def probe_locked(timeout_s: float = PROBE_TIMEOUT_S) -> bool:
    """Return True when the default collection reports locked.

    Runs the D-Bus read in a daemon reaper thread joined at ``timeout_s``: a
    wedged Secret Service must not hang the probe (the 08-26 failure mode).
    Any D-Bus failure or expiry raises ProbeError — callers treat that as
    locked (fail loud), never as silent success.
    """
    box: dict[str, object] = {}

    def _run() -> None:
        try:
            box["locked"] = _probe_locked_once()
        except Exception as exc:  # noqa: BLE001 — any failure is a probe error
            box["exc"] = exc

    worker = threading.Thread(target=_run, daemon=True, name="keyring-watch-probe")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise ProbeError(f"probe timed out after {timeout_s:g}s, treating as locked")
    if "exc" in box:
        err = cast(BaseException, box["exc"])
        raise ProbeError(f"probe failed: {type(err).__name__}") from err
    return bool(box["locked"])


def _now() -> float:
    """Monotonic clock; module indirection so tests can pin time."""
    return time.monotonic()


def _send(msg: str) -> bool:
    """Telegram alert via the hermes CLI; best-effort, never raises.

    Returns True only when the send actually succeeded: a broken alert
    channel must NOT be reported as "alerted" (exit 2), so health_check's
    generic red summary still fires and the outage stays loud.
    """
    try:
        r = subprocess.run(
            ["/home/ops/.local/bin/hermes", "send", "-t", "telegram", msg],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001 — best-effort by design
        return False


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    # Shape guard (review B): a torn or hostile-but-valid-JSON file must not
    # crash the child — degrade to fresh state (next lock re-alerts as edge).
    if not isinstance(state, dict) or not isinstance(
        state.get("last_alert"), (int, float)
    ):
        return {}
    return state


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write (review B): tmp + rename so a torn write can never leave
    # a half-JSON state file behind.
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def cmd_check() -> int:
    """--check: probe once; no state, no alert. 0 unlocked / 2 locked / 3 error."""
    try:
        locked = bool(probe_locked())
    except ProbeError:
        return 3
    return 2 if locked else 0


def cmd_notify() -> int:
    """--notify: probe + state transitions + Telegram alerts.

    Lock edge (ok -> locked) alerts immediately; while locked, re-alerts at
    >= RE_ALERT_S since the last alert (timestamps persist in state); the
    unlock edge fires exactly one recovered alert. Probe errors take the
    locked path (fail loud): the ok -> error edge alerts immediately, later
    errors ride the re-alert cadence and never spam.
    """
    state = _load_state()
    prev = state.get("state", OK)
    now = _now()
    try:
        locked = bool(probe_locked())
        probe_failed = False
    except ProbeError:
        locked = True
        probe_failed = True
    alerted = False
    if locked:
        edge = prev != LOCKED
        due = now - float(state.get("last_alert", 0.0)) >= RE_ALERT_S
        if (edge or due) and _send(ERROR_ALERT if probe_failed else LOCK_ALERT):
            alerted = True
            state["last_alert"] = now
        if edge:
            state["since"] = now
        state["state"] = LOCKED
    else:
        if prev == LOCKED and _send(RECOVERED_ALERT):
            alerted = True
        state["state"] = OK
        state["since"] = now
        state.pop("last_alert", None)
    _save_state(state)
    if alerted and locked:
        return 2  # locked-path alert sent this cycle; health_check suppresses its summary
    if locked and probe_failed:
        return 3  # probe error, no alert sent this cycle
    return 0


def cmd_reset() -> int:
    """--reset: clear persisted state (the next notify run starts from ok)."""
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe the Secret Service collection lock state (boolean only) "
        "and alert Telegram on transitions."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="probe once; exit 0 unlocked / 2 locked / 3 probe error")
    group.add_argument("--notify", action="store_true",
                       help="probe + state transitions + Telegram alerts")
    group.add_argument("--reset", action="store_true",
                       help="clear the persisted state file")
    args = parser.parse_args(argv)
    if args.reset:
        return cmd_reset()
    if args.check:
        return cmd_check()
    return cmd_notify()


if __name__ == "__main__":
    raise SystemExit(main())