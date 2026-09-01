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
  0 = unlocked/ok (or recovered this cycle)
  2 = locked-path AND an alert was sent this cycle (its own Telegram send)
  3 = locked-path with NO successful alert sent this cycle (probe error or
      the Telegram send itself failed) — "locked-or-error, nothing sent".
      health_check treats 2 AND 3 as a keyring problem; only 2 means the
      alert actually reached Telegram.

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


def _probe_locked_once(collection_label: str | None = None) -> bool:
    """One Secret Service read; raises ProbeError on any failure.

    With ``collection_label`` set, resolve the collection by its Secret
    Service Label among ``get_all_collections`` (drill path: a locked TEST
    collection can never be the default collection). Unknown label raises
    ProbeError (fail loud, locked path). Default behavior (no label) is
    byte-for-byte unchanged.

    Lazy imports keep startup fast and keep the hermetic test suite off the
    real D-Bus entirely.
    """
    import secretstorage
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    bus = open_dbus_connection()  # jeepney default: the session bus
    try:
        if collection_label is None:
            collection = secretstorage.get_default_collection(bus)
        else:
            matches = [
                c
                for c in secretstorage.get_all_collections(bus)
                if c.get_label() == collection_label
            ]
            if not matches:
                raise LookupError(
                    f"no Secret Service collection with label {collection_label!r}"
                )
            collection = matches[0]
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


def probe_locked(
    timeout_s: float = PROBE_TIMEOUT_S, collection_label: str | None = None
) -> bool:
    """Return True when the probed collection reports locked.

    With ``collection_label`` set, the probe targets that labeled collection
    instead of the default one (drill path); an unknown label is a probe
    error (fail loud). Otherwise the default collection is probed, unchanged.

    Runs the D-Bus read in a daemon reaper thread joined at ``timeout_s``: a
    wedged Secret Service must not hang the probe (the 08-26 failure mode).
    Any D-Bus failure or expiry raises ProbeError — callers treat that as
    locked (fail loud), never as silent success.
    """
    box: dict[str, object] = {}

    def _run() -> None:
        try:
            box["locked"] = _probe_locked_once(collection_label)
        except Exception as exc:  # noqa: BLE001 — any failure is a probe error
            box["exc"] = exc

    worker = threading.Thread(
        target=_run, daemon=True, name="keyring-watch-probe"
    )
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


def _load_state(state_path: Path = STATE_FILE) -> dict:
    if not state_path.exists():
        return {}
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    # Shape guard (review B): a torn or hostile-but-valid-JSON file must not
    # crash the child — degrade to fresh state (next lock re-alerts as edge).
    # last_alert is required numeric ONLY when present: a healthy OK state
    # legitimately has none (popped on recovery), and a locked state whose
    # send failed has none either (FIX-1 path) — both must survive the load,
    # or every load would wipe them and force spurious edges.
    if not isinstance(state, dict):
        return {}
    if "last_alert" in state and not isinstance(
        state["last_alert"], (int, float)
    ):
        return {}
    if "state" in state and state["state"] not in (LOCKED, OK):
        return {}
    return state


def _save_state(state: dict, state_path: Path = STATE_FILE) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write (review B): tmp + rename so a torn write can never leave
    # a half-JSON state file behind.
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(state_path)


def cmd_check(collection_label: str | None = None) -> int:
    """--check: probe once; no state, no alert. 0 unlocked / 2 locked / 3 error."""
    try:
        locked = bool(probe_locked(collection_label=collection_label))
    except ProbeError:
        return 3
    return 2 if locked else 0


def cmd_notify(
    collection_label: str | None = None, state_file: Path | None = None
) -> int:
    """--notify: probe + state transitions + Telegram alerts.

    ``state_file`` overrides STATE_FILE (drill path: a drill must never touch
    the production state file, or the next production cycle would see
    prev==locked on the unlocked default collection and fire a spurious
    recovered alert).

    Lock edge (ok -> locked) alerts immediately; while locked, re-alerts at
    >= RE_ALERT_S since the last alert (timestamps persist in state); the
    unlock edge fires exactly one recovered alert. Probe errors take the
    locked path (fail loud): the ok -> error edge alerts immediately, later
    errors ride the re-alert cadence and never spam.
    """
    state_path = state_file if state_file is not None else STATE_FILE
    state = _load_state(state_path)
    prev = state.get("state", OK)
    now = _now()
    try:
        locked = bool(probe_locked(collection_label=collection_label))
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
        # A re-lock supersedes any pending recovered ping (FIX-2).
        state.pop("recovered_unsent", None)
        _save_state(state, state_path)
        if alerted:
            return 2  # locked-path alert sent this cycle; health_check suppresses its summary
        return 3  # locked-path, nothing sent this cycle (send failed, or no alert due / probe error)
    # Unlock path: fire the recovered ping on the edge, and retry it on later
    # OK cycles if the send failed then (FIX-2: never drop the recovery ping).
    recovered_pending = bool(state.get("recovered_unsent"))
    if prev == LOCKED or recovered_pending:
        if _send(RECOVERED_ALERT):
            alerted = True
            state.pop("recovered_unsent", None)
        elif prev == LOCKED:
            # Edge-cycle send failed: park the ping, state is OK already.
            state["recovered_unsent"] = True
    state["state"] = OK
    state["since"] = now
    state.pop("last_alert", None)
    _save_state(state, state_path)
    return 0


def cmd_reset(state_file: Path | None = None) -> int:
    """--reset: clear persisted state (the next notify run starts from ok)."""
    try:
        (state_file if state_file is not None else STATE_FILE).unlink()
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
                       help="probe + state transitions + Telegram alerts; exit 2 "
                       "locked-path alert sent, 3 locked-path with nothing sent "
                       "(send failed or no alert due), 0 unlocked/ok")
    group.add_argument("--reset", action="store_true",
                       help="clear the persisted state file")
    parser.add_argument(
        "--collection-label", default=None,
        help="probe the collection with this Secret Service Label instead of the "
        "default one (drill path); unknown label = probe error, fail loud",
    )
    parser.add_argument(
        "--state-file", default=None, type=Path,
        help="override the persisted-state path (drill path; default: "
        "data/ops/keyring_watch_state.json)",
    )
    args = parser.parse_args(argv)
    if args.reset:
        return cmd_reset(args.state_file)
    if args.check:
        return cmd_check(args.collection_label)
    return cmd_notify(args.collection_label, args.state_file)


if __name__ == "__main__":
    raise SystemExit(main())