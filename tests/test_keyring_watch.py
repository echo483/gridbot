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
    messages: list[str] = []

    def fake_send(msg: str) -> bool:
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
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: False)
    assert keyring_watch.main(["--check"]) == 0
    assert sent == []
    assert not state_file.exists()  # --check never touches state


def test_check_locked_exits_two(state_file, sent, monkeypatch):
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: True)
    assert keyring_watch.main(["--check"]) == 2
    assert sent == []


def test_check_probe_error_exits_three(state_file, sent, monkeypatch):
    def boom():
        raise keyring_watch.ProbeError("bus wedged")

    monkeypatch.setattr(keyring_watch, "probe_locked", boom)
    assert keyring_watch.main(["--check"]) == 3
    assert sent == []


def test_probe_hard_bound_on_timeout(monkeypatch):
    """A D-Bus read that never returns must be reaped at the time bound and
    surface as ProbeError (fail loud), never hang the caller (08-26 mode)."""

    def wedged():
        time.sleep(30)

    monkeypatch.setattr(keyring_watch, "_probe_locked_once", wedged)
    with pytest.raises(keyring_watch.ProbeError, match="timed out"):
        keyring_watch.probe_locked(timeout_s=0.05)


def test_notify_lock_edge_alerts_once(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: True)
    assert keyring_watch.main(["--notify"]) == 2  # locked + alerted
    assert len(sent) == 1
    assert "gnome-keyring-daemon --replace --unlock" in sent[0]  # recovery one-liner
    # Within the 15-min cadence and still locked: no second ping, no alert exit.
    assert keyring_watch.main(["--notify"]) == 0
    assert len(sent) == 1
    state = json.loads(state_file.read_text())
    assert state["state"] == "locked"
    assert state["last_alert"] == 1000.0


def test_notify_realert_cadence_uses_state_timestamps(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: True)
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 1
    clock(899)  # t=1899: 899s since last alert — below the 900s bar, silent
    assert keyring_watch.main(["--notify"]) == 0
    assert len(sent) == 1
    clock(1)  # t=1900: exactly 900s since the state timestamp — re-alert fires
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 2
    assert sent[1] == sent[0]  # same lock message, not a recovery


def test_notify_recovered_fires_exactly_once(state_file, sent, clock, monkeypatch):
    locked_then_unlocked = iter([True, False, False])
    monkeypatch.setattr(
        keyring_watch, "probe_locked", lambda: next(locked_then_unlocked)
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
    def boom():
        raise keyring_watch.ProbeError("bus wedged")

    monkeypatch.setattr(keyring_watch, "probe_locked", boom)
    # First cycle after healthy state: probe error must ALERT (fail loud),
    # not return 3 silently.
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 1
    assert "unresponsive" in sent[0]
    # Once recovered, the unlock transition still gets its one alert.
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: False)
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
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: next(states))
    keyring_watch.main(["--notify"])
    clock(900)
    keyring_watch.main(["--notify"])
    keyring_watch.main(["--notify"])
    assert len(sent) == 3
    for msg in sent:
        assert not _has_secret_shape(msg), msg


def test_state_roundtrip(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: True)
    keyring_watch.main(["--notify"])
    first = json.loads(state_file.read_text())
    assert first == {"state": "locked", "since": 1000.0, "last_alert": 1000.0}
    keyring_watch.main(["--notify"])  # within cadence: timestamps persist unchanged
    assert json.loads(state_file.read_text()) == first
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: False)
    keyring_watch.main(["--notify"])
    third = json.loads(state_file.read_text())
    assert third["state"] == "ok"
    assert "last_alert" not in third


def test_reset_clears_state(state_file, sent, clock, monkeypatch):
    monkeypatch.setattr(keyring_watch, "probe_locked", lambda: True)
    keyring_watch.main(["--notify"])
    assert state_file.exists()
    assert keyring_watch.main(["--reset"]) == 0
    assert not state_file.exists()
    # After a reset the next locked probe is a fresh edge again (alerts once).
    assert keyring_watch.main(["--notify"]) == 2
    assert len(sent) == 2