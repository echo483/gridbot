"""Tests for config._secret outage resilience.

2026-08-26: the armed engine died mid-session when KWallet's Secret
Service dropped off D-Bus (SecretServiceNotAvailableException) inside the
per-cycle arming gate. The fix: a keyring OUTAGE falls back to the last
good in-process value; a healthy keyring answering None (secret deleted)
still clears the cache so deletion keeps disarming.
"""

from __future__ import annotations

import sys
import types

import pytest

from gridbot import config

NAME = "X_GRIDBOT_TEST_SECRET"


def _fake_keyring(get_password) -> types.ModuleType:
    mod = types.ModuleType("keyring")
    mod.get_password = get_password
    return mod


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.setattr(config, "KEYRING_TIMEOUT_S", 0.2)
    config._SECRET_CACHE.pop(NAME, None)
    config._KEYRING_INFLIGHT.pop(NAME, None)
    config._DEGRADED.discard(NAME)
    yield
    config._SECRET_CACHE.pop(NAME, None)
    config._KEYRING_INFLIGHT.pop(NAME, None)
    config._DEGRADED.discard(NAME)


def test_env_override_wins(monkeypatch) -> None:
    monkeypatch.setenv(NAME, "from-env")
    assert config._secret(NAME) == "from-env"


def test_outage_falls_back_to_last_good_value(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(lambda s, n: "v1"))
    assert config._secret(NAME) == "v1"

    def boom(service, name):
        raise RuntimeError("Message recipient disconnected from message bus")

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(boom))
    assert config._secret(NAME) == "v1"  # armed session survives the outage


def test_outage_with_no_cache_fails_closed(monkeypatch) -> None:
    def boom(service, name):
        raise RuntimeError("dbus gone")

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(boom))
    assert config._secret(NAME) is None


def test_blocking_read_times_out_to_cache(monkeypatch) -> None:
    # 2026-08-26 afternoon mode: reads BLOCK forever behind a locked
    # collection's GUI prompt. The engine must get the cached value back
    # within the timeout instead of wedging the cycle.
    import threading
    import time as _time

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(lambda s, n: "v1"))
    assert config._secret(NAME) == "v1"

    release = threading.Event()

    def hang(service, name):
        release.wait(10.0)
        return "v1"

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(hang))
    t0 = _time.monotonic()
    assert config._secret(NAME) == "v1"  # cache, not the blocked read
    assert _time.monotonic() - t0 < 2.0
    # while the read is still stuck, further calls stay fast on cache
    t0 = _time.monotonic()
    assert config._secret(NAME) == "v1"
    assert _time.monotonic() - t0 < 0.1
    release.set()


def test_wedged_cache_fallback_is_age_bounded(monkeypatch) -> None:
    # Peer review 08-26 finding 1: while the keyring is WEDGED, deletion
    # cannot be observed — so the cache fallback must expire. An armed
    # engine may run at most KEYRING_CACHE_MAX_AGE_S on an unconfirmed
    # credential before failing closed.
    import threading

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(lambda s, n: "v1"))
    assert config._secret(NAME) == "v1"

    release = threading.Event()

    def hang(service, name):
        release.wait(10.0)
        return "v1"

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(hang))
    assert config._secret(NAME) == "v1"  # within the age bound: cache serves
    monkeypatch.setattr(config, "KEYRING_CACHE_MAX_AGE_S", 0.0)
    assert config._secret(NAME) is None  # bound passed: fail closed
    release.set()


def test_healthy_none_clears_cache_and_disarms(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(lambda s, n: "v1"))
    assert config._secret(NAME) == "v1"
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(lambda s, n: None))
    assert config._secret(NAME) is None  # deletion still reads as absent
    # and the stale value must not resurface on a later outage
    monkeypatch.setitem(
        sys.modules, "keyring",
        _fake_keyring(lambda s, n: (_ for _ in ()).throw(RuntimeError("out"))),
    )
    assert config._secret(NAME) is None


def test_wedged_read_is_abandoned_and_a_fresh_read_recovers(monkeypatch) -> None:
    """2026-09-03: one D-Bus call that never returned blinded the capture
    daemon for ~6h — every later _secret() saw the stuck worker, said "read
    still blocked", and never asked the keyring again, while a fresh process
    read the key in 0.1s. After KEYRING_ABANDON_S the stuck worker must be
    abandoned and a NEW read issued; when the keyring answers, the process
    recovers in place."""
    import threading

    gate = threading.Event()
    calls = {"n": 0}

    def get_password(service, name):
        calls["n"] += 1
        if calls["n"] == 1:
            gate.wait()  # the first read never returns (wedged D-Bus)
            return None
        return "fresh"

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(get_password))
    monkeypatch.setattr(config, "KEYRING_ABANDON_S", 120.0)
    config._ABANDONED.clear()
    assert config._secret(NAME) is None          # times out, nothing cached
    assert config._secret(NAME) is None          # still blocked, no new read
    assert calls["n"] == 1
    monkeypatch.setattr(config, "KEYRING_ABANDON_S", 0.0)  # window elapsed
    assert config._secret(NAME) == "fresh"       # abandoned + fresh read
    assert calls["n"] == 2
    gate.set()


def test_abandonment_is_capped(monkeypatch) -> None:
    import threading

    gate = threading.Event()

    def get_password(service, name):
        gate.wait()

    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(get_password))
    monkeypatch.setattr(config, "KEYRING_ABANDON_S", 0.0)
    monkeypatch.setattr(config, "KEYRING_MAX_ABANDONED", 2)
    config._ABANDONED.clear()
    for _ in range(6):
        assert config._secret(NAME) is None
    alive = sum(1 for th in threading.enumerate() if th.name == f"keyring:{NAME}")
    assert alive <= 3  # 2 abandoned + the current inflight one — never unbounded
    gate.set()
