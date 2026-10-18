"""Tests for scripts/keyring_watch.py — the keyring lock-state watchdog.

Hermetic: the D-Bus probe (probe_locked) is monkeypatched in every test —
no live Secret Service is ever touched. The zero-secret-string check is the
frozen acceptance-test clause from the keyring-resilience spec (§4): alert
text must contain none of sk-, xapi, token, password.
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import keyring_watch

SECRET_SHAPED = ("sk-", "xapi", "token", "password")


def _has_secret_shape(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in SECRET_SHAPED)


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "keyring_watch_state.json"
    monkeypatch.setattr(keyring_watch, "STATE_FILE", path)
    return path


@pytest.fixture
def sent(monkeypatch):
    """Fake _send recording messages. Set messages.fail_next = True to make
    exactly one send fail (FIX-1/FIX-2 paths: dead Telegram channel)."""

    class SendLog(list):
        fail_next = False

    messages = SendLog()

    def fake_send(msg: str) -> bool:
        if messages.fail_next:
            messages.fail_next = False
            return False
        messages.append(msg)
        return True

    monkeypatch.setattr(keyring_watch, "_send", fake_send)
    return messages


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(keyring_watch, "_now", lambda: now[0])

    def advance(seconds: float) -> None:
        now[0] += seconds

    return advance


def test_check_unlocked_exits_zero(state_file, sent, monkeypatch):
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: False
    )
    assert keyring_watch.main(["--check"]) == 0
    assert sent == []
    assert not state_file.exists()  # --check never touches state


def test_check_locked_exits_two(state_file, sent, monkeypatch):
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    assert keyring_watch.main(["--check"]) == 2
    assert sent == []


def test_check_probe_error_exits_three(state_file, sent, monkeypatch):
    def boom(timeout_s=5.0, collection_label=None):
        raise keyring_watch.ProbeError("bus wedged")

    monkeypatch.setattr(keyring_watch, "probe_locked", boom)
    assert keyring_watch.main(["--check"]) == 3
    assert sent == []


def test_probe_hard_bound_on_timeout(monkeypatch):
    """A D-Bus read that never returns must be reaped at the time bound and
    surface as ProbeError (fail loud), never hang the caller (08-26 mode)."""

    def wedged(collection_label=None):
        time.sleep(30)

    monkeypatch.setattr(keyring_watch, "_probe_locked_once", wedged)
    with pytest.raises(keyring_watch.ProbeError, match="timed out"):
        keyring_watch.probe_locked(timeout_s=0.05)


def test_notify_lock_edge_alerts_once(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    assert keyring_watch.main(["--notify"]) == 2  # locked + alerted
    assert len(sent) == 1
    assert "gnome-keyring-daemon --replace --unlock" in sent[0]  # recovery one-liner
    # Within the 15-min cadence and still locked: no second ping; exit 3
    # (locked-or-error, nothing sent this cycle — new FIX-1 contract).
    assert keyring_watch.main(["--notify"]) == 3
    assert len(sent) == 1
    state = json.loads(state_file.read_text())
    assert state["state"] == "locked"
    assert state["last_alert"] == 1000.0


def test_notify_realert_cadence_uses_state_timestamps(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 1
    clock(899)  # t=1899: 899s since last alert — below the 900s bar, silent
    assert keyring_watch.main(["--notify"]) == 3  # locked, nothing sent (FIX-1)
    assert len(sent) == 1
    clock(1)  # t=1900: exactly 900s since the state timestamp — re-alert fires
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 2
    assert sent[1] == sent[0]  # same lock message, not a recovery


def test_notify_recovered_fires_exactly_once(state_file, sent, clock, monkeypatch):
    locked_then_unlocked = iter([True, False, False])
    monkeypatch.setattr(
        keyring_watch,
        "probe_locked",
        lambda timeout_s=5.0, collection_label=None: next(locked_then_unlocked),
    )
    assert keyring_watch.main(["--notify"]) == 2  # lock edge
    assert len(sent) == 1
    assert keyring_watch.main(["--notify"]) == 0  # unlock edge -> recovered
    assert len(sent) == 2
    assert "Recovered" in sent[1]
    assert keyring_watch.main(["--notify"]) == 0  # still unlocked: silent
    assert len(sent) == 2
    state = json.loads(state_file.read_text())
    assert state["state"] == "ok"
    assert "last_alert" not in state


def test_notify_probe_error_fails_loud(state_file, sent, clock, monkeypatch):
    def boom(timeout_s=5.0, collection_label=None):
        raise keyring_watch.ProbeError("bus wedged")

    monkeypatch.setattr(keyring_watch, "probe_locked", boom)
    # First cycle after healthy state: probe error must ALERT (fail loud),
    # not return 3 silently.
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 1
    assert "unresponsive" in sent[0]
    # Once recovered, the unlock transition still gets its one alert.
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: False
    )
    assert keyring_watch.main(["--notify"]) == 0
    assert len(sent) == 2
    assert "Recovered" in sent[1]


def test_alert_text_has_no_secret_shaped_strings(state_file, sent, clock, monkeypatch):
    """Frozen acceptance test (spec §4, redaction registry): sk-, xapi,
    token, password must not appear in any alert content."""
    for text in (
        keyring_watch.LOCK_ALERT,
        keyring_watch.ERROR_ALERT,
        keyring_watch.RECOVERED_ALERT,
    ):
        assert not _has_secret_shape(text), text
    # Full edge + re-alert + recovered scenario covers every sent message.
    states = iter([True, True, False])
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: next(states)
    )
    keyring_watch.main(["--notify"])
    clock(900)
    keyring_watch.main(["--notify"])
    keyring_watch.main(["--notify"])
    assert len(sent) == 3
    for msg in sent:
        assert not _has_secret_shape(msg), msg


def test_state_roundtrip(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    keyring_watch.main(["--notify"])
    first = json.loads(state_file.read_text())
    assert first == {"state": "locked", "since": 1000.0, "last_alert": 1000.0}
    keyring_watch.main(["--notify"])  # within cadence: timestamps persist unchanged
    assert json.loads(state_file.read_text()) == first
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: False
    )
    keyring_watch.main(["--notify"])
    third = json.loads(state_file.read_text())
    assert third["state"] == "ok"
    assert "last_alert" not in third


def test_reset_clears_state(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    keyring_watch.main(["--notify"])
    assert state_file.exists()
    assert keyring_watch.main(["--reset"]) == 0
    assert not state_file.exists()
    # After a reset the next locked probe is a fresh edge again (alerts once).
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 2


# --- GAP-1 / GAP-2 drill flags (hermetic; real D-Bus never touched) --------


def test_check_collection_label_routes_probe(monkeypatch, state_file, sent):
    """--collection-label must reach probe_locked; unknown label is a probe
    error (exit 3, fail loud)."""
    seen: list[str | None] = []

    def fake_probe(timeout_s=5.0, collection_label=None):
        seen.append(collection_label)
        return True

    monkeypatch.setattr(keyring_watch, "probe_locked", fake_probe)
    assert keyring_watch.main(["--check", "--collection-label", "gridbot-drill"]) == 2
    assert seen == ["gridbot-drill"]

    def boom(timeout_s=5.0, collection_label=None):
        raise keyring_watch.ProbeError("no such collection")

    monkeypatch.setattr(keyring_watch, "probe_locked", boom)
    assert keyring_watch.main(["--check", "--collection-label", "gridbot-drill"]) == 3


def test_probe_locked_unknown_label_fails_loud(monkeypatch):
    """_probe_locked_once with an unknown label raises (locked path), and
    probe_locked surfaces it as ProbeError.

    The bus opener is stubbed. This test deliberately exercises the real
    _probe_locked_once, but without the stub it first calls
    open_dbus_connection(), so on any runner with no session bus jeepney raises
    KeyError from the environment and the label logic is never reached — the
    assertion then fails for a reason that has nothing to do with the code
    under test. Stubbing keeps the file's hermetic promise true.
    """
    import jeepney.io.blocking as jeepney_blocking

    monkeypatch.setattr(
        jeepney_blocking,
        "open_dbus_connection",
        lambda *a, **k: types.SimpleNamespace(close=lambda: None),
    )
    fake_ss = types.SimpleNamespace(
        get_default_collection=lambda bus: None,
        get_all_collections=lambda bus: [
            types.SimpleNamespace(get_label=lambda: "other")
        ],
    )
    monkeypatch.setitem(sys.modules, "secretstorage", fake_ss)

    def raises(label):
        real_once(label)

    real_once = keyring_watch._probe_locked_once
    with pytest.raises(LookupError):
        raises("gridbot-drill")
    # probe_locked wraps any failure into ProbeError (fail loud).
    monkeypatch.setattr(
        keyring_watch, "_probe_locked_once", lambda collection_label=None: raises(collection_label)
    )
    with pytest.raises(keyring_watch.ProbeError, match="LookupError"):
        keyring_watch.probe_locked(timeout_s=0.5, collection_label="gridbot-drill")


def test_notify_state_file_flag_isolates_drill_state(
    state_file, sent, clock, monkeypatch, tmp_path
):
    """--state-file must write ONLY the drill state file: the production
    state file must stay untouched (no crosstalk, no spurious recovered)."""
    drill = tmp_path / "keyring_watch_drill_state.json"
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    assert keyring_watch.main(["--notify", "--state-file", str(drill)]) == 2
    assert drill.exists()
    assert not state_file.exists()  # production state untouched
    drill_state = json.loads(drill.read_text())
    assert drill_state["state"] == "locked"
    # Second cycle within cadence: silent, still isolated (exit 3 = locked,
    # nothing sent).
    assert keyring_watch.main(["--notify", "--state-file", str(drill)]) == 3
    assert not state_file.exists()
    # Reset with the flag clears only the drill file.
    assert keyring_watch.main(["--reset", "--state-file", str(drill)]) == 0
    assert not drill.exists()


def test_default_state_path_unchanged(state_file, sent, clock, monkeypatch):
    """No flags = production behavior unchanged: default state file used."""
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    assert keyring_watch.main(["--notify"]) == 2
    assert state_file.exists()


def test_probe_locked_label_forwarded_to_probe_once(monkeypatch):
    """probe_locked forwards collection_label into _probe_locked_once."""
    seen: list[str | None] = []
    monkeypatch.setattr(
        keyring_watch,
        "_probe_locked_once",
        lambda collection_label=None: (seen.append(collection_label), False)[1],
    )
    assert keyring_watch.probe_locked(timeout_s=1.0, collection_label="drill") is False
    assert seen == ["drill"]
    assert keyring_watch.probe_locked(timeout_s=1.0) is False
    assert seen == ["drill", None]


# --- FIX-1/FIX-2/FIX-3 behavioral seams (2026-09-01 adversarial review) -----


def test_locked_with_failed_send_exits_three_and_keeps_edge(
    state_file, sent, clock, monkeypatch
):
    """FIX-1: locked-path cycle whose Telegram send FAILS must exit 3 (not 0)
    and leave state locked WITHOUT last_alert, so the edge/cadence retries."""
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    sent.fail_next = True
    assert keyring_watch.main(["--notify"]) == 3
    assert sent == []  # nothing reached Telegram
    state = json.loads(state_file.read_text())
    assert state["state"] == "locked"
    assert "last_alert" not in state
    # The following cycle retries the (still-due) alert even without a clock
    # advance: last_alert never landed, so `due` holds via the 0.0 default.
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 1
    assert "gnome-keyring-daemon --replace --unlock" in sent[0]


def test_locked_still_within_cadence_after_delivered_alert_exits_zero(
    state_file, sent, clock, monkeypatch
):
    """Locked, alert already delivered, cadence NOT yet due: nothing to send
    this cycle is a benign locked-path cycle — exit 3 per the new contract
    (locked-or-error, nothing sent), NOT 0; health_check may show the
    keyring line but the state proves the alert went out."""
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda timeout_s=5.0, collection_label=None: True
    )
    assert keyring_watch.main(["--notify"]) == 2
    clock(100)
    assert keyring_watch.main(["--notify"]) == 3
    assert len(sent) == 1


def test_recovered_send_failure_parks_and_retries_exactly_once(
    state_file, sent, clock, monkeypatch
):
    """FIX-2: the unlock-edge recovered send failing must set
    recovered_unsent (state stays OK) and a later OK cycle retries it
    exactly once, clearing the flag on success."""
    locked_then_unlocked = iter([True, False, False, False])
    monkeypatch.setattr(
        keyring_watch,
        "probe_locked",
        lambda timeout_s=5.0, collection_label=None: next(locked_then_unlocked),
    )
    assert keyring_watch.main(["--notify"]) == 2  # lock edge
    sent.fail_next = True
    assert keyring_watch.main(["--notify"]) == 0  # unlock edge, recovered LOST
    assert len(sent) == 1  # only the lock alert; the recovered send failed
    state = json.loads(state_file.read_text())
    assert state["state"] == "ok"
    assert state["recovered_unsent"] is True
    clock(600)
    # Next OK cycle retries the recovered ping and clears the flag.
    assert keyring_watch.main(["--notify"]) == 0
    assert len(sent) == 2
    assert "Recovered" in sent[1]
    state = json.loads(state_file.read_text())
    assert "recovered_unsent" not in state
    # And it does NOT retry again.
    clock(600)
    assert keyring_watch.main(["--notify"]) == 0
    assert len(sent) == 2


def test_relock_supersedes_pending_recovered_ping(state_file, sent, monkeypatch):
    """FIX-2 clause: a re-lock clears recovered_unsent (a re-lock supersedes
    the stale recovery ping; the lock alert takes over)."""
    probe = iter([True, False, True, True])
    monkeypatch.setattr(
        keyring_watch,
        "probe_locked",
        lambda timeout_s=5.0, collection_label=None: next(probe),
    )
    assert keyring_watch.main(["--notify"]) == 2  # lock edge, delivered
    sent.fail_next = True
    assert keyring_watch.main(["--notify"]) == 0  # unlock edge, recovered parked
    assert json.loads(state_file.read_text())["recovered_unsent"] is True
    assert keyring_watch.main(["--notify"]) == 2  # re-lock edge: lock alert wins
    state = json.loads(state_file.read_text())
    assert state["state"] == "locked"
    assert "recovered_unsent" not in state
    assert "Recovered" not in "".join(sent)


def test_load_state_preserves_healthy_ok_state(state_file, tmp_path):
    """FIX-3: a legitimate OK state (no last_alert) must round-trip through
    _load_state instead of being discarded to {}."""
    healthy = {"state": "ok", "since": 1234.0}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(healthy))
    assert keyring_watch._load_state(path) == healthy


def test_load_state_preserves_locked_without_last_alert(tmp_path):
    """FIX-3: locked state with no last_alert (FIX-1's send-failed path)
    survives the load — no spurious edge on the next cycle."""
    locked = {"state": "locked", "since": 1000.0}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(locked))
    assert keyring_watch._load_state(path) == locked


def test_load_state_still_discards_torn_shapes(tmp_path):
    """FIX-3 guard stays sharp: non-dict, non-numeric last_alert when present,
    and unknown state values still degrade to {}."""
    path = tmp_path / "s.json"
    for bad in (
        [1, 2],
        {"state": "locked", "last_alert": "soon"},
        {"state": "weird", "last_alert": 5.0},
    ):
        path.write_text(json.dumps(bad))
        assert keyring_watch._load_state(path) == {}, bad
    # And a numeric last_alert without a state key is fine (legacy shape).
    path.write_text(json.dumps({"last_alert": 5.0}))
    assert keyring_watch._load_state(path) == {"last_alert": 5.0}