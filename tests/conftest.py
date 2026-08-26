"""Suite-wide hermeticity: no test may touch the real OS keyring.

A real Secret Service read hangs the whole suite when the keyring daemon
is locked or down (observed 2026-08-26: a locked gnome-keyring made every
D-Bus read block forever behind a GUI prompt). Tests exercise keyring
behaviour by monkeypatching sys.modules["keyring"] themselves (see
test_config_secrets.py); everything else gets this inert default.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    fake = types.ModuleType("keyring")
    fake.get_password = lambda service, name: None
    monkeypatch.setitem(sys.modules, "keyring", fake)
    yield
