"""Suite-wide hermeticity: no test may read the operator's real secrets file.

Every test starts with GRIDBOT_SECRETS_FILE pointing at a path that does not
exist, so every secret reads as absent (the fail-closed default). Tests that
need a secret write their own file under tmp_path (see test_config_secrets.py).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch, tmp_path):
    from gridbot import config

    monkeypatch.setenv(config.SECRETS_FILE_ENV, str(tmp_path / "no-secrets.env"))
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    yield
